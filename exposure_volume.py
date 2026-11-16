bl_info = {
    "name": "Exposure Volume",
    "location": "View3D > Sidebar (N) > Exposure",
    "description": "Select ~20-40 rim points (Vertex select) roughly around "
                   "the surgical opening; the add-on welds the scan internally, "
                   "links the points along the real surface into a closed rim "
                   "ring, flood-fills the scanned cavity the ring encloses, "
                   "caps the rim (and any holes) with a closure membrane, and "
                   "reports the enclosed volume with watertightness QC. Exports "
                   "the solid to STL/PLY/OBJ/glTF/FBX.",
    "category": "Mesh",
}

import bpy
import bmesh
import heapq
import math
import os
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from bpy.props import (
    PointerProperty, BoolProperty, StringProperty, FloatProperty,
    IntProperty, EnumProperty,
)
from bpy.types import Panel, Operator, PropertyGroup


# ============================================================
# Geometry helpers
# ============================================================

def best_fit_plane(points):
    """Return (centroid, normal) best-fit plane for a list of Vector points."""
    n = len(points)
    c = Vector((0, 0, 0))
    for p in points:
        c += p
    c /= n
    # covariance
    xx = xy = xz = yy = yz = zz = 0.0
    for p in points:
        r = p - c
        xx += r.x * r.x
        xy += r.x * r.y
        xz += r.x * r.z
        yy += r.y * r.y
        yz += r.y * r.z
        zz += r.z * r.z
    # smallest-eigenvector of covariance via determinant-of-cofactors approach
    det_x = yy * zz - yz * yz
    det_y = xx * zz - xz * xz
    det_z = xx * yy - xy * xy
    det_max = max(det_x, det_y, det_z)
    if det_max <= 0:
        return c, Vector((0, 0, 1))
    if det_max == det_x:
        normal = Vector((det_x, xz * yz - xy * zz, xy * yz - xz * yy))
    elif det_max == det_y:
        normal = Vector((xz * yz - xy * zz, det_y, xy * xz - yz * xx))
    else:
        normal = Vector((xy * yz - xz * yy, xy * xz - yz * xx, det_z))
    if normal.length < 1e-12:
        return c, Vector((0, 0, 1))
    return c, normal.normalized()


def plane_axes(normal):
    """Return orthonormal in-plane axes (u, v) with v = normal x u."""
    ref = Vector((1, 0, 0))
    if abs(normal.dot(ref)) > 0.9:
        ref = Vector((0, 1, 0))
    u = (ref - normal * ref.dot(normal)).normalized()
    v = normal.cross(u).normalized()
    return u, v


def project_points_2d(points, centroid, normal):
    """Project 3D points into the (u, v) coordinate system of the best-fit
    plane. Returns a list of (x, y) tuples."""
    u, v = plane_axes(normal)
    return [((p - centroid).dot(u), (p - centroid).dot(v)) for p in points]


# ------------------------------------------------------------
# Ear-clipping triangulation
# ------------------------------------------------------------

def _tri_area2_2d(a, b, c):
    """Twice the signed area of 2D triangle (a, b, c). Positive = CCW."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_tri_2d(p, a, b, c, eps):
    """True if p lies inside (or on the boundary of) CCW triangle (a, b, c)."""
    return (_tri_area2_2d(a, b, p) >= -eps and
            _tri_area2_2d(b, c, p) >= -eps and
            _tri_area2_2d(c, a, p) >= -eps)


def polygon_signed_area_2d(pts2d):
    """Signed area of a 2D polygon (positive = CCW)."""
    a = 0.0
    n = len(pts2d)
    for i in range(n):
        x1, y1 = pts2d[i]
        x2, y2 = pts2d[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return 0.5 * a


def _segments_intersect_2d(p1, p2, p3, p4):
    """Proper-intersection test for segments p1p2 and p3p4 (excluding shared
    endpoints, which callers filter out by skipping adjacent edges)."""
    d1 = _tri_area2_2d(p3, p4, p1)
    d2 = _tri_area2_2d(p3, p4, p2)
    d3 = _tri_area2_2d(p1, p2, p3)
    d4 = _tri_area2_2d(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def polygon_is_simple_2d(pts2d):
    """Check that an ordered polygon does not self-intersect. O(n^2)."""
    n = len(pts2d)
    for i in range(n):
        a1 = pts2d[i]
        a2 = pts2d[(i + 1) % n]
        for j in range(i + 1, n):
            # skip adjacent edges (they share an endpoint)
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            b1 = pts2d[j]
            b2 = pts2d[(j + 1) % n]
            if _segments_intersect_2d(a1, a2, b1, b2):
                return False
    return True


def ear_clip_triangulate_2d(pts2d):
    """Ear-clipping triangulation of a simple 2D polygon given as an ordered
    list of (x, y) tuples. Valid for ANY simple polygon (convex, concave,
    L-shaped, kidney-shaped).

    Returns a list of (i, j, k) index triples into pts2d whose 2D winding is
    CCW, or None on failure. No dependencies beyond stdlib math."""
    n = len(pts2d)
    if n < 3:
        return None
    area2 = 2.0 * polygon_signed_area_2d(pts2d)
    if abs(area2) < 1e-14:
        return None  # degenerate (collinear) rim
    idx = list(range(n))
    if area2 < 0:
        idx.reverse()  # work in CCW order; returned triples stay CCW
    eps = abs(area2) * 1e-9  # scale-relative degeneracy threshold
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 100000:
        guard += 1
        m = len(idx)
        found = False
        flattest_k = 0
        flattest_cross = None
        for k in range(m):
            i0 = idx[(k - 1) % m]
            i1 = idx[k]
            i2 = idx[(k + 1) % m]
            a, b, c = pts2d[i0], pts2d[i1], pts2d[i2]
            cr = _tri_area2_2d(a, b, c)
            if flattest_cross is None or abs(cr) < flattest_cross:
                flattest_cross = abs(cr)
                flattest_k = k
            if cr <= eps:
                continue  # reflex or degenerate corner: not an ear
            contains = False
            for j in idx:
                if j == i0 or j == i1 or j == i2:
                    continue
                if _point_in_tri_2d(pts2d[j], a, b, c, eps):
                    contains = True
                    break
            if not contains:
                tris.append((i0, i1, i2))
                idx.pop(k)
                found = True
                break
        if not found:
            # Robustness fallback for near-degenerate (collinear) corners:
            # clip the flattest vertex so progress is always made.
            k = flattest_k
            m = len(idx)
            i0 = idx[(k - 1) % m]
            i1 = idx[k]
            i2 = idx[(k + 1) % m]
            tris.append((i0, i1, i2))
            idx.pop(k)
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    if len(tris) != n - 2:
        return None
    return tris


# ------------------------------------------------------------
# Normal orientation (side of the loop to probe)
# ------------------------------------------------------------

def orient_normal(bvh, centroid, normal, span, bb_center, flip_override=False):
    """Resolve the SIGN of the loop-plane normal so it points up/out of the
    cavity. One downward probe from just above the centroid per candidate
    sign: the correct 'up' normal is the candidate whose downward ray hits
    the cavity floor (the closer hit wins a tie). The bbox-center heuristic
    remains only as a last-resort fallback, and the user can force a flip
    via the 'Flip side' toggle (Advanced)."""
    eps = span * 1e-4

    def probe(candidate):
        loc, _n, _i, dist = bvh.ray_cast(centroid + candidate * eps,
                                         -candidate, span * 3.0)
        return dist if loc is not None else None

    d_pos = probe(normal)
    d_neg = probe(-normal)
    if d_pos is not None and (d_neg is None or d_pos <= d_neg):
        out = normal
    elif d_neg is not None:
        out = -normal
    else:
        # no hits either way: fall back to the bbox-center heuristic
        out = -normal if (centroid - bb_center).dot(normal) < 0 else normal
    if flip_override:
        out = -out
    return out


# ------------------------------------------------------------
# Mesh validation / QC
# ------------------------------------------------------------

def count_nonmanifold_edges(bm):
    """Number of edges that do NOT have exactly 2 incident faces."""
    return sum(1 for e in bm.edges if len(e.link_faces) != 2)


def finalize_solid_bmesh(bm):
    """Make face orientation consistent and outward, then validate and
    measure. Returns (volume_world, watertight_ok, nonmanifold_edge_count).
    The recalc + negative-volume-flip makes the final orientation and sign
    robust regardless of the initial membrane-winding guess. Volume is only
    meaningful when watertight_ok is True — the UI flags it otherwise."""
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    vol = bm.calc_volume(signed=True)
    if vol < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        vol = -vol
    nonman = count_nonmanifold_edges(bm)
    return vol, (nonman == 0), nonman


# ============================================================
# Step 1 — validate a hand-selected closed edge loop (fast path)
# ============================================================

def walk_edge_loop(edge_set):
    """Walk a set of edges expected to form ONE closed, non-branching loop.
    Returns (ordered_vertex_list, None) on success or (None, error)."""
    v2e = {}
    for e in edge_set:
        for v in e.verts:
            v2e.setdefault(v, []).append(e)
    for v, es in v2e.items():
        if len(es) != 2:
            return None, ("the selection branches or has open ends (a "
                          f"vertex touches {len(es)} selected edge(s) "
                          "instead of 2)")
    start = next(iter(edge_set))
    loop = [start.verts[0], start.verts[1]]
    used = {start}
    cur = loop[-1]
    while True:
        nxt_edge = None
        for e in v2e[cur]:
            if e not in used:
                nxt_edge = e
                break
        if nxt_edge is None:
            break
        used.add(nxt_edge)
        cur = nxt_edge.other_vert(cur)
        if cur is loop[0]:
            break
        loop.append(cur)
    if cur is not loop[0] or len(used) != len(edge_set):
        return None, "the selection contains more than one loop"
    return loop, None


def validate_selected_loop(sel_edges):
    """If the user DID hand-select a clean closed edge loop, use it verbatim.
    Returns (ordered_vertex_list, barrier_edge_set, None) or (None, None,
    error)."""
    if len(sel_edges) < 3:
        return None, None, "not enough selected edges"
    barrier = set(sel_edges)
    loop, err = walk_edge_loop(barrier)
    if err:
        return None, None, err
    return loop, barrier, None


# ============================================================
# Step 1b — welded working copy + landmark relocation
# ============================================================
# Raw scans have split/coincident vertices and cracks that disconnect the
# edge graph. We therefore measure on a WELDED COPY of the mesh (the user's
# real mesh is never modified) and re-locate the selected rim points onto the
# copy by nearest position.


def make_welded_copy(bm, obj, weld_dist):
    """Return (work_bmesh, own_flag, merged_vertex_count). `work_bmesh` is a
    standalone copy of the edit mesh with near-coincident vertices merged, so
    cracks and duplicate vertices no longer disconnect the surface. Free it
    (own_flag is True) when done. The user's real mesh is untouched."""
    try:
        wb = bm.copy()
    except Exception:
        wb = bmesh.new()
        wb.from_mesh(obj.data)
    if weld_dist and weld_dist > 0.0:
        weld = weld_dist
    else:
        diag = (Vector(obj.bound_box[6]) - Vector(obj.bound_box[0])).length
        weld = (diag if diag > 0 else 1.0) * 1e-4
    merged = 0
    try:
        before = len(wb.verts)
        bmesh.ops.remove_doubles(wb, verts=list(wb.verts), dist=weld)
        merged = before - len(wb.verts)
    except Exception:
        pass
    wb.verts.ensure_lookup_table()
    wb.edges.ensure_lookup_table()
    wb.faces.ensure_lookup_table()
    return wb, True, merged


def landmarks_on_bmesh(wb, sel_cos, hist_cos):
    """Map selected vertex POSITIONS onto the welded working mesh (nearest
    vertex) and return (ordered_landmarks, used_history, error)."""
    wb.verts.ensure_lookup_table()
    n = len(wb.verts)
    if n == 0:
        return None, False, "The mesh has no vertices to measure."
    kd = KDTree(n)
    for i, v in enumerate(wb.verts):
        kd.insert(v.co, i)
    kd.balance()

    def nearest(co):
        _c, idx, _d = kd.find(co)
        return wb.verts[idx]

    landmarks = []
    seen = set()
    for co in sel_cos:
        w = nearest(co)
        if w not in seen:
            seen.add(w)
            landmarks.append(w)
    hist = []
    seen2 = set()
    for co in hist_cos:
        w = nearest(co)
        if w not in seen2:
            seen2.add(w)
            hist.append(w)
    if len(landmarks) < 3:
        return None, False, (
            "Fewer than 3 distinct rim points after welding — select more "
            "points around the opening, or lower 'Weld distance' (Advanced).")
    ordered, used_history = order_landmarks(landmarks, hist)
    return ordered, used_history, None


# ============================================================
# Step 1c — approximate rim ring from scattered landmark vertices
# ============================================================

def _point_segment_distance(p, a, b):
    """Distance from point p to segment ab (all mathutils Vectors)."""
    ab = b - a
    l2 = ab.length_squared
    if l2 <= 1e-18:
        return (p - a).length
    t = (p - a).dot(ab) / l2
    t = max(0.0, min(1.0, t))
    return (p - (a + ab * t)).length


def order_landmarks(landmarks, history_verts):
    """Return the landmarks in circumferential rim order, plus a flag saying
    whether the user's click order was used.

    If Blender's selection history covers every landmark (the user
    Ctrl/Shift+Clicked the points one by one), that click order is used
    verbatim — it is the only unambiguous ordering for highly concave rims.
    Otherwise the landmarks are projected onto their best-fit plane, sorted by
    angle around their centroid, and the ordering is repaired with 2-opt
    uncrossing passes until the projected polygon is simple."""
    lm_set = set(landmarks)
    hist = []
    seen = set()
    for v in history_verts:
        if v in lm_set and v not in seen:
            hist.append(v)
            seen.add(v)
    if len(hist) == len(lm_set) and len(hist) >= 3:
        return hist, True

    pts = [v.co.copy() for v in landmarks]
    centroid, normal = best_fit_plane([q.copy() for q in pts])
    p2 = project_points_2d(pts, centroid, normal)
    order = sorted(range(len(landmarks)),
                   key=lambda i: (math.atan2(p2[i][1], p2[i][0]),
                                  p2[i][0] ** 2 + p2[i][1] ** 2))
    # 2-opt uncrossing: while two edges of the projected tour cross, reverse
    # the sub-tour between them. Each swap removes a crossing, so the ordering
    # converges to a simple polygon that follows the perimeter.
    n = len(order)
    for _ in range(n * n):
        crossed = False
        for i in range(n):
            for j in range(i + 1, n):
                if j - i == 1 or (i == 0 and j == n - 1):
                    continue  # adjacent tour edges share an endpoint
                a1, a2 = p2[order[i]], p2[order[(i + 1) % n]]
                b1, b2 = p2[order[j]], p2[order[(j + 1) % n]]
                if _segments_intersect_2d(a1, a2, b1, b2):
                    order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                    crossed = True
                    break
            if crossed:
                break
        if not crossed:
            break
    return [landmarks[i] for i in order], False


def _geodesic_path(va, vb, rim_centroid, gate):
    """Shortest path from vertex va to vertex vb along EXISTING mesh edges
    (Dijkstra over vertex adjacency).

    Edge cost = edge length times two gentle penalties: (1) squared deviation
    of the edge midpoint from the straight va-vb corridor, so the path hugs
    the point-to-point segment; (2) a flat multiplier when the midpoint lies
    markedly closer to the rim centroid than the endpoints, discouraging
    shortcuts INWARD across the opening.

    No vertices are forbidden: paths may share the surface with other points
    and with previously built segments. `gate` bounds the search to a
    neighbourhood of the corridor (Euclidean distance to either endpoint);
    pass float('inf') to search the whole surface.

    Returns a list of edges, or None if no path exists."""
    a, b = va.co, vb.co
    corridor_len = max((b - a).length, 1e-9)
    r_ref = min((a - rim_centroid).length, (b - rim_centroid).length)

    def edge_cost(e):
        v1, v2 = e.verts
        mid = (v1.co + v2.co) * 0.5
        c = (v2.co - v1.co).length
        d = _point_segment_distance(mid, a, b)
        c *= 1.0 + 8.0 * (d / corridor_len) ** 2       # stay in the corridor
        if (mid - rim_centroid).length < 0.75 * r_ref:
            c *= 4.0                                   # no inward shortcuts
        return c

    dist = {va: 0.0}
    prev = {}
    heap = [(0.0, 0, va)]
    tie = 1
    inf = float("inf")
    while heap:
        d0, _t, v = heapq.heappop(heap)
        if v is vb:
            break
        if d0 > dist.get(v, inf):
            continue
        for e in v.link_edges:
            w = e.other_vert(v)
            if w is not vb and gate != inf:
                if (w.co - a).length > gate and (w.co - b).length > gate:
                    continue  # outside the local search window
            nd = d0 + edge_cost(e)
            if nd < dist.get(w, inf) - 1e-15:
                dist[w] = nd
                prev[w] = (v, e)
                heapq.heappush(heap, (nd, tie, w))
                tie += 1
    if vb not in prev and vb is not va:
        return None
    path = []
    v = vb
    while v is not va:
        pv, e = prev[v]
        path.append(e)
        v = pv
    path.reverse()
    return path


def _greedy_cycle(n, connect):
    """Build a closed cycle over a subsequence of 0..n-1 (kept in order) such
    that every consecutive kept pair (including the wrap-around) is
    connectable via connect(i, j) — which returns a path (list) or None.
    Points that cannot be reached from the current anchor are DROPPED, and
    the tail is trimmed until the ring closes. Returns (kept_indices, paths,
    dropped_indices), or (None, None, dropped) if no cycle of >= 3 points
    closes."""
    if n < 3:
        return None, None, list(range(n))
    kept = [0]
    paths = []
    cur = 0
    dropped = []
    for cand in range(1, n):
        pth = connect(cur, cand)
        if pth is not None:
            paths.append(pth)
            kept.append(cand)
            cur = cand
        else:
            dropped.append(cand)
    # Close the ring, trimming the tail if the last kept point cannot reach
    # the first.
    while len(kept) >= 3:
        pth = connect(kept[-1], kept[0])
        if pth is not None:
            paths.append(pth)
            return kept, paths, dropped
        dropped.append(kept.pop())
        if paths:
            paths.pop()
        cur = kept[-1]
    return None, None, dropped


def build_barrier_ring(bm, ordered_landmarks):
    """Join the ordered landmarks into a closed BARRIER RING of real edges,
    skipping any point that cannot be reached along the surface. Returns
    (barrier_edge_set, dropped_count, None) or (None, dropped_count, error)."""
    n = len(ordered_landmarks)
    centroid = Vector((0, 0, 0))
    for v in ordered_landmarks:
        centroid = centroid + v.co
    centroid = centroid / n

    def connect(i, j):
        va = ordered_landmarks[i]
        vb = ordered_landmarks[j]
        if va is vb:
            return []
        gate = 6.0 * max((vb.co - va.co).length, 1e-9)
        pth = _geodesic_path(va, vb, centroid, gate)
        if pth is None:
            # widen to the whole connected surface before giving up on a pair
            pth = _geodesic_path(va, vb, centroid, float("inf"))
        return pth

    kept, paths, dropped = _greedy_cycle(n, connect)
    if kept is None:
        return None, len(dropped), (
            "Could not trace a closed rim through the selected points on one "
            "connected surface — the scan is fragmented here (split vertices, "
            "cracks, or separate pieces). Try: (1) click ~20-40 points one by "
            "one IN ORDER around the opening; (2) raise 'Weld distance' in "
            "Advanced to merge wider cracks; (3) keep every point on the same "
            "connected surface. Then Measure again.")
    edges = [e for pth in paths for e in pth]
    if not edges:
        return None, len(dropped), (
            "The selected rim points collapsed to a single spot. Spread "
            "~20-40 points around the opening and Measure again.")
    return set(edges), len(dropped), None


# ============================================================
# Step 2 — barrier-bounded flood fill over the real scan
# ============================================================

def patch_boundary_edges(interior_faces):
    """Edges with exactly ONE adjacent face inside the patch."""
    interior = set(interior_faces)
    boundary = set()
    for f in interior:
        for e in f.edges:
            k = 0
            for lf in e.link_faces:
                if lf in interior:
                    k += 1
            if k == 1:
                boundary.add(e)
    return boundary


def flood_fill_enclosed_patch(seed_face, barrier_edges):
    """Flood fill over ACTUAL face adjacency starting at seed_face, NEVER
    crossing a barrier (rim-ring) edge. No radial bound, no depth cap, no
    synthetic sidewall: the fill is limited purely by the rim ring and the
    mesh's own connectivity, so the patch follows the real anatomy in every
    direction (including undercuts). Returns the set of reached faces."""
    interior = {seed_face}
    stack = [seed_face]
    while stack:
        f = stack.pop()
        for e in f.edges:
            if e in barrier_edges:
                continue  # the rim ring is an impassable barrier
            for nf in e.link_faces:
                if nf not in interior:
                    interior.add(nf)
                    stack.append(nf)
    return interior


def outer_border_edge_set(bm):
    """The scan's OUTER naked border, as an edge set. Naked edges (fewer than
    two faces) are grouped into connected loops; the longest loop is the outer
    border. A correct cavity patch must not reach it — if the flood does, the
    rim ring had a gap and the fill leaked out. Returns an empty set for a
    fully closed scan (no naked edges)."""
    naked = [e for e in bm.edges if len(e.link_faces) < 2]
    if not naked:
        return set()
    v2e = {}
    for e in naked:
        for v in e.verts:
            v2e.setdefault(v, []).append(e)
    seen = set()
    best = None
    best_len = -1.0
    for e in naked:
        if e in seen:
            continue
        comp = []
        stack = [e]
        seen.add(e)
        while stack:
            ce = stack.pop()
            comp.append(ce)
            for v in ce.verts:
                for ne in v2e.get(v, ()):
                    if ne not in seen:
                        seen.add(ne)
                        stack.append(ne)
        tot = 0.0
        for ed in comp:
            tot += (ed.verts[0].co - ed.verts[1].co).length
        if tot > best_len:
            best_len = tot
            best = comp
    return set(best) if best else set()


def find_cavity_patch(bm, bvh, barrier, centroid, normal, span, outer_border):
    """Find the cavity-side face patch bounded by the rim ring.

    Seed strategy: (1) probe straight down from the rim centroid along -normal
    and take the hit face (the cavity floor is unambiguously inside the ring);
    (2) fall back to the faces flanking a barrier edge, trying the one further
    below the rim plane first.

    Acceptance: a flooded patch is accepted as long as it did NOT leak to the
    scan's outer border (open scans) or fill the entire mesh (closed scans).
    Returns (interior_face_set, None) or (None, error)."""
    bm.faces.ensure_lookup_table()
    total = len(bm.faces)
    eps = span * 1e-4
    seeds = []
    loc, _n, idx, _d = bvh.ray_cast(centroid + normal * eps, -normal,
                                    span * 3.0)
    if loc is not None and idx is not None and 0 <= idx < total:
        seeds.append(bm.faces[idx])
    e0 = next(iter(barrier))
    flank = list(e0.link_faces)
    flank.sort(key=lambda f: (f.calc_center_median() - centroid).dot(normal))
    seeds.extend(flank)

    tried = []
    for s in seeds:
        if s is None:
            continue
        if any(s in pch for pch in tried):
            continue
        patch = flood_fill_enclosed_patch(s, barrier)
        tried.append(patch)
        if not patch:
            continue
        bnd = patch_boundary_edges(patch)
        if outer_border:
            leaked = bool(bnd & outer_border)
        else:
            leaked = (len(patch) >= total)  # filled the whole closed scan
        if not leaked:
            return patch, None

    return None, (
        "The fill escaped past the rim, so the selected points don't fully "
        "enclose the opening. Add a few more points to close the biggest gaps "
        "in the rim (especially where it bends sharply), keep them in order "
        "around the opening, then Measure again. If the WRONG side was filled, "
        "toggle 'Flip side' (Advanced).")


# ============================================================
# Step 3 — order the patch boundary into closed loops
# ============================================================

def order_boundary_loops(boundary_edges):
    """Order a set of patch-boundary edges into closed loops of vertices.
    A flooded patch's free boundary is one big loop (the rim) plus, possibly,
    small loops around interior holes. Returns a list of ordered vertex
    lists. Robust to the occasional branch vertex (edges are simply consumed
    until none remain)."""
    v2e = {}
    for e in boundary_edges:
        for v in e.verts:
            v2e.setdefault(v, []).append(e)
    remaining = set(boundary_edges)
    loops = []
    while remaining:
        e0 = next(iter(remaining))
        remaining.discard(e0)
        start = e0.verts[0]
        loopv = [start, e0.other_vert(start)]
        cur = loopv[-1]
        while cur is not start:
            nxt = None
            for e in v2e.get(cur, ()):
                if e in remaining:
                    nxt = e
                    break
            if nxt is None:
                break
            remaining.discard(nxt)
            cur = nxt.other_vert(cur)
            if cur is start:
                break
            loopv.append(cur)
        loops.append(loopv)
    return loops


# ============================================================
# Step 4 — assemble the solid: real faces + closure membranes
# ============================================================

def _bbox_diag(pts3d):
    """Diagonal length of the axis-aligned bounding box of 3D points."""
    if not pts3d:
        return 0.0
    mnx = mny = mnz = float("inf")
    mxx = mxy = mxz = float("-inf")
    for p in pts3d:
        mnx = min(mnx, p.x); mny = min(mny, p.y); mnz = min(mnz, p.z)
        mxx = max(mxx, p.x); mxy = max(mxy, p.y); mxz = max(mxz, p.z)
    return Vector((mxx - mnx, mxy - mny, mxz - mnz)).length


def _loop_perimeter(loop, xform):
    s = 0.0
    m = len(loop)
    for i in range(m):
        s += (xform(loop[i].co) - xform(loop[(i + 1) % m].co)).length
    return s


def build_cavity_solid_multi(interior_faces, boundary_loops, matrix_world=None):
    """Assemble the final closed solid:

    * copy the interior (cavity) faces' own vertices/winding AS-IS — they
      carry the real scanned geometry; no wall or envelope is synthesized, and
    * cap EVERY free boundary loop of the patch with a triangulated membrane
      over its true 3D vertices. The longest loop is the rim opening; any
      others are incidental holes in the scan.

    Per loop: ear-clip if the projected polygon is simple and no chord would
    coincide with an existing copied edge; otherwise fall back to a fan from a
    new center vertex (its spokes cannot pre-exist, so the cap stays
    manifold). finalize_solid_bmesh() fixes the global winding/volume sign.

    Returns (bmesh, rim_simple, rim_planarity, rim_vertex_count, holes_filled,
    error)."""
    if matrix_world is not None:
        xform = lambda co: matrix_world @ co
    else:
        xform = lambda co: co.copy()

    if not boundary_loops:
        return None, True, 0.0, 0, 0, (
            "The captured cavity surface has no free boundary to close. "
            "Re-select the rim points and try again.")

    interior_set = set(interior_faces)
    rim_index = max(range(len(boundary_loops)),
                    key=lambda i: _loop_perimeter(boundary_loops[i], xform))

    nb = bmesh.new()
    vmap = {}
    rim_simple = True
    rim_planar = 0.0
    holes = 0
    rim_count = len(boundary_loops[rim_index])
    try:
        for f in interior_faces:
            nvs = []
            for v in f.verts:
                nv = vmap.get(v)
                if nv is None:
                    nv = nb.verts.new(xform(v.co))
                    vmap[v] = nv
                nvs.append(nv)
            nb.faces.new(nvs)

        for li, loop in enumerate(boundary_loops):
            m = len(loop)
            if m < 3:
                continue
            pts3d = [xform(v.co) for v in loop]
            c, nrm = best_fit_plane([q.copy() for q in pts3d])
            pts2d = project_points_2d(pts3d, c, nrm)
            simple = polygon_is_simple_2d(pts2d)
            tri = ear_clip_triangulate_2d(pts2d)
            use_fan = (tri is None) or (not simple)

            if not use_fan:
                vset = set(loop)
                loop_pos = {v: i for i, v in enumerate(loop)}
                existing = set()
                for v in loop:
                    for e in v.link_edges:
                        o = e.other_vert(v)
                        if o not in vset:
                            continue
                        if not any(lf in interior_set for lf in e.link_faces):
                            continue
                        d = abs(loop_pos[v] - loop_pos[o])
                        if d != 1 and d != m - 1:
                            existing.add(frozenset((loop_pos[v], loop_pos[o])))
                if existing:
                    for (i, j, k) in tri:
                        hit = False
                        for a, b in ((i, j), (j, k), (k, i)):
                            d = abs(a - b)
                            if d != 1 and d != m - 1 and \
                               frozenset((a, b)) in existing:
                                use_fan = True
                                hit = True
                                break
                        if hit:
                            break

            lid = [vmap[v] for v in loop]
            mat_idx = 0 if li == rim_index else 1  # 1 = interior-hole fill cap
            if use_fan:
                cc = Vector((0, 0, 0))
                for q in pts3d:
                    cc += q
                cc /= m
                center = nb.verts.new(cc)
                for i in range(m):
                    fnew = nb.faces.new((center, lid[i], lid[(i + 1) % m]))
                    fnew.material_index = mat_idx
            else:
                for (i, j, k) in tri:
                    fnew = nb.faces.new((lid[i], lid[j], lid[k]))
                    fnew.material_index = mat_idx

            if li == rim_index:
                rim_simple = simple
                diag = _bbox_diag(pts3d)
                if diag > 1e-12:
                    rim_planar = max((abs((p - c).dot(nrm)) for p in pts3d),
                                     default=0.0) / diag
            else:
                holes += 1
    except ValueError as e:
        nb.free()
        return None, rim_simple, rim_planar, 0, 0, (
            f"Solid construction failed (degenerate/duplicate face: {e}). "
            "Re-select the rim points (add a few more, spaced evenly) and try "
            "again.")
    return nb, rim_simple, rim_planar, rim_count, holes, None


# ============================================================
# Units
# ============================================================

def scene_unit_volume(context, vol_world):
    """Convert a world-space (Blender-unit) volume to scene length-unit cubed."""
    us = context.scene.unit_settings
    scale = us.scale_length if us.scale_length else 1.0
    vol_m3 = vol_world * (scale ** 3)
    unit = us.length_unit
    factors = {
        'KILOMETERS': 1e-9, 'METERS': 1.0, 'CENTIMETERS': 1e6,
        'MILLIMETERS': 1e9, 'MICROMETERS': 1e18,
    }
    f = factors.get(unit, 1.0)
    return vol_m3 * f, (unit.lower() if unit else "bu")


# ============================================================
# Properties
# ============================================================

class EXV_Props(PropertyGroup):
    # --- Advanced (collapsed by default) ---
    show_advanced: BoolProperty(
        name="Advanced",
        description="Show advanced options",
        default=False)
    flip_normal: BoolProperty(
        name="Flip side",
        description="Probe the opposite side of the rim if the automatic "
                    "cavity-side detection picked the wrong side",
        default=False)
    make_preview: BoolProperty(
        name="Create solid object",
        description="Keep the measured solid visible in the scene",
        default=True)
    weld_dist: FloatProperty(
        name="Weld distance",
        description="Merge scan vertices closer than this on an internal copy "
                    "before measuring (0 = automatic). Raise it if you get "
                    "'could not link ... rim points' (the scan has wider "
                    "cracks); lower it if distinct points get merged. Your "
                    "real mesh is never modified",
        default=0.0, min=0.0, precision=5)

    # --- Export ---
    specimen_id: StringProperty(name="Specimen ID", default="")
    export_format: EnumProperty(
        name="Format",
        items=[
            ('STL', "STL (.stl)", "Widely supported"),
            ('PLY', "PLY (.ply)", "Keeps color"),
            ('OBJ', "OBJ (.obj)", "Broad compatibility"),
            ('GLTF', "glTF (.glb)", "Self-contained"),
            ('FBX', "FBX (.fbx)", "Maya / Unity"),
        ], default='STL')

    # --- Results ---
    res_has: BoolProperty(default=False)
    res_vol_scene: FloatProperty(default=0.0)
    res_unit: StringProperty(default="bu")
    res_rim: IntProperty(default=0)
    res_solid: StringProperty(default="")
    res_watertight: BoolProperty(default=False)
    res_rim_simple: BoolProperty(default=True)
    res_holes: IntProperty(default=0)
    res_rim_planar: FloatProperty(default=0.0)
    res_dropped: IntProperty(default=0)


# ============================================================
# Operators
# ============================================================

class EXV_OT_measure(Operator):
    bl_idname = "exv.measure"
    bl_label = "Measure Exposure Volume"
    bl_description = ("Use the selected vertices as approximate rim points "
                      "(~20-40 around the opening): weld the scan internally, "
                      "link the points along the real surface into a closed "
                      "rim ring, flood-fill the scanned cavity the ring "
                      "encloses, cap the rim and any holes, and report the "
                      "enclosed volume with watertightness QC")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Make a mesh object active.")
            return {'CANCELLED'}
        if obj.mode != 'EDIT':
            self.report({'ERROR'}, "Enter Edit Mode and select the rim points "
                                   "around the opening (Vertex select mode).")
            return {'CANCELLED'}

        p = context.scene.exv_props
        mw = obj.matrix_world

        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        sel_edges = [e for e in bm.edges if e.select]
        sel_verts = [v for v in bm.verts if v.select]
        history_verts = [ele for ele in bm.select_history
                         if isinstance(ele, bmesh.types.BMVert)
                         and ele.select]

        work_bm = None
        own_bm = False
        dropped = 0
        merged = 0

        # --- Step 1: resolve the rim ---
        # Fast path: a clean hand-selected closed edge loop is used verbatim on
        # the original mesh (its connectivity is already fine).
        fast_loop = None
        if len(sel_edges) >= 3:
            loop0, barrier0, err0 = validate_selected_loop(sel_edges)
            if err0 is None:
                fast_loop = (loop0, barrier0)

        if fast_loop is not None:
            work_bm = bm
            rim_ref = fast_loop[0]
            barrier = fast_loop[1]
            used_history = False
        else:
            if len(sel_verts) < 3:
                self.report({'ERROR'}, "Select at least 3 rim points: Vertex "
                            "select (1) and Ctrl/Shift+Click ~20-40 points "
                            "around the opening, in order.")
                return {'CANCELLED'}
            sel_cos = [v.co.copy() for v in sel_verts]
            hist_cos = [v.co.copy() for v in history_verts]
            work_bm, own_bm, merged = make_welded_copy(bm, obj, p.weld_dist)
            ordered, used_history, err = landmarks_on_bmesh(
                work_bm, sel_cos, hist_cos)
            if err:
                if own_bm:
                    work_bm.free()
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            barrier, dropped, err = build_barrier_ring(work_bm, ordered)
            if err:
                if own_bm:
                    work_bm.free()
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            rim_ref = ordered

        # --- Step 2: cavity-side seed + barrier-bounded flood fill ---
        pts_local = [v.co for v in rim_ref]
        centroid_l, normal_l = best_fit_plane([q.copy() for q in pts_local])
        diag = Vector(obj.bound_box[6]) - Vector(obj.bound_box[0])
        span_l = diag.length if diag.length > 0 else 1.0
        bb_center_l = sum((Vector(c) for c in obj.bound_box), Vector()) / 8.0
        bvh = BVHTree.FromBMesh(work_bm)
        normal_l = orient_normal(bvh, centroid_l, normal_l, span_l,
                                 bb_center_l, flip_override=p.flip_normal)

        outer = outer_border_edge_set(work_bm)
        interior, err = find_cavity_patch(work_bm, bvh, barrier, centroid_l,
                                          normal_l, span_l, outer)
        if err:
            if own_bm:
                work_bm.free()
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        # --- Step 3: order the patch's own boundary into closed loops ---
        bnd = patch_boundary_edges(interior)
        loops = order_boundary_loops(bnd)
        if not loops:
            if own_bm:
                work_bm.free()
            self.report({'ERROR'}, "The captured cavity surface has no free "
                                   "boundary to close. Re-select the rim "
                                   "points and try again.")
            return {'CANCELLED'}

        # --- Step 4: real faces + closure membranes, in world space ---
        nb, rim_simple, rim_planar, n_rim, holes, err = build_cavity_solid_multi(
            interior, loops, matrix_world=mw)
        n_interior = len(interior)
        if own_bm:
            work_bm.free()
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        vol_world, watertight_ok, nonman = finalize_solid_bmesh(nb)
        vol_scene, unit = scene_unit_volume(context, vol_world)

        me = bpy.data.meshes.new("ExposureSolid")
        nb.to_mesh(me)
        nb.free()
        solid_ob = bpy.data.objects.new("ExposureSolid", me)
        context.scene.collection.objects.link(solid_ob)

        mat = bpy.data.materials.new("ExposureSolid_Mat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.9, 0.15, 0.1, 1.0)
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = 0.4
        # Blender 4.2+ (including 5.0) removed the legacy EEVEE material API:
        # Material.blend_method no longer exists and raises AttributeError.
        # Use surface_render_method when present; fall back on Blender <= 4.1.
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = 'BLENDED'
        elif hasattr(mat, "blend_method"):
            mat.blend_method = 'BLEND'
        solid_ob.data.materials.append(mat)
        # Second slot: a bright, opaque highlight for the interior-hole fill
        # membranes (their faces were tagged material_index = 1), so filled
        # gaps stand out on the translucent red solid and can be inspected.
        if holes:
            mat_h = bpy.data.materials.new("ExposureHole_Mat")
            mat_h.use_nodes = True
            hb = mat_h.node_tree.nodes.get("Principled BSDF")
            if hb:
                hb.inputs["Base Color"].default_value = (1.0, 0.85, 0.0, 1.0)
                for en in ("Emission Color", "Emission"):
                    if en in hb.inputs:
                        hb.inputs[en].default_value = (1.0, 0.85, 0.0, 1.0)
                        break
                if "Emission Strength" in hb.inputs:
                    hb.inputs["Emission Strength"].default_value = 0.6
            solid_ob.data.materials.append(mat_h)
        if not p.make_preview:
            solid_ob.hide_set(True)

        p.res_has = True
        p.res_vol_scene = vol_scene
        p.res_unit = unit
        p.res_rim = n_rim
        p.res_solid = solid_ob.name
        p.res_watertight = watertight_ok
        p.res_rim_simple = rim_simple
        p.res_holes = holes
        p.res_rim_planar = rim_planar
        p.res_dropped = dropped

        wt = 'yes' if watertight_ok else f'NO ({nonman} non-manifold edges)'
        how = ('click order' if used_history else 'auto-ordered')
        notes = []
        if merged:
            notes.append(f"{merged:,} welded")
        if dropped:
            notes.append(f"{dropped} stray pt(s) skipped")
        if holes:
            notes.append(f"{holes} hole(s) filled")
        note_s = (", " + ", ".join(notes)) if notes else ""
        self.report({'INFO'},
                    f"Exposure volume = {vol_scene:,.3f} {unit}^3 "
                    f"(rim {n_rim} pts [{how}], {n_interior:,} cavity faces"
                    f"{note_s}, watertight={wt})")
        return {'FINISHED'}


class EXV_OT_export(Operator):
    bl_idname = "exv.export"
    bl_label = "Export Solid (3D)"
    bl_description = ("Export the measured solid as a 3D file. Opens a Save dialog "
                     "so you choose exactly where the file is written.")

    filepath: StringProperty(subtype='FILE_PATH')
    filename: StringProperty()
    directory: StringProperty(subtype='DIR_PATH')

    _EXT = {'STL': '.stl', 'PLY': '.ply', 'OBJ': '.obj',
            'GLTF': '.glb', 'FBX': '.fbx'}

    def _get_solid(self, context):
        p = context.scene.exv_props
        if not p.res_has:
            return None, "Measure first (press Measure Exposure Volume)."
        ob = bpy.data.objects.get(p.res_solid)
        if ob is None:
            return None, ("No solid to export. Turn on 'Create solid object' and "
                          "press Measure again, then Export.")
        if ob.type != 'MESH' or len(ob.data.polygons) == 0:
            return None, "The solid has no faces to export. Re-measure."
        return ob, None

    def invoke(self, context, event):
        ob, err = self._get_solid(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        p = context.scene.exv_props
        ext = self._EXT[p.export_format]
        base = p.specimen_id.strip() or "exposure_volume"
        base = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base)
        home = os.path.expanduser("~")
        self.filepath = os.path.join(home, base + ext)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        p = context.scene.exv_props
        ob, err = self._get_solid(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        ext = self._EXT[p.export_format]
        filepath = self.filepath
        if not filepath:
            self.report({'ERROR'}, "No file path chosen.")
            return {'CANCELLED'}
        if not filepath.lower().endswith(ext):
            filepath += ext
        folder = os.path.dirname(filepath)
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                self.report({'ERROR'}, f"Cannot create folder: {e}")
                return {'CANCELLED'}

        if context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
        try:
            ob.hide_set(False)
        except Exception:
            pass

        prev_active = context.view_layer.objects.active
        prev_sel = list(context.selected_objects)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob

        ok, msg = True, filepath
        try:
            fmt = p.export_format
            ops = bpy.ops
            if fmt == 'STL':
                if hasattr(ops.wm, "stl_export"):
                    ops.wm.stl_export(filepath=filepath,
                                      export_selected_objects=True,
                                      apply_modifiers=True,
                                      global_scale=1.0,
                                      use_scene_unit=False)
                else:
                    ops.export_mesh.stl(filepath=filepath, use_selection=True,
                                        global_scale=1.0, use_scene_unit=False)
            elif fmt == 'PLY':
                if hasattr(ops.wm, "ply_export"):
                    ops.wm.ply_export(filepath=filepath,
                                      export_selected_objects=True,
                                      global_scale=1.0)
                else:
                    ops.export_mesh.ply(filepath=filepath, use_selection=True,
                                        global_scale=1.0)
            elif fmt == 'OBJ':
                if hasattr(ops.wm, "obj_export"):
                    ops.wm.obj_export(filepath=filepath,
                                      export_selected_objects=True,
                                      global_scale=1.0)
                else:
                    ops.export_scene.obj(filepath=filepath, use_selection=True,
                                         global_scale=1.0)
            elif fmt == 'GLTF':
                ops.export_scene.gltf(filepath=filepath, export_format='GLB',
                                      use_selection=True)
            elif fmt == 'FBX':
                ops.export_scene.fbx(filepath=filepath, use_selection=True,
                                     global_scale=1.0, apply_unit_scale=False,
                                     apply_scale_options='FBX_SCALE_NONE')
        except TypeError:
            try:
                if fmt == 'STL' and hasattr(ops.wm, "stl_export"):
                    ops.wm.stl_export(filepath=filepath, export_selected_objects=True)
                elif fmt == 'PLY' and hasattr(ops.wm, "ply_export"):
                    ops.wm.ply_export(filepath=filepath, export_selected_objects=True)
                elif fmt == 'OBJ' and hasattr(ops.wm, "obj_export"):
                    ops.wm.obj_export(filepath=filepath, export_selected_objects=True)
                elif fmt == 'GLTF':
                    ops.export_scene.gltf(filepath=filepath, export_format='GLB',
                                          use_selection=True)
                elif fmt == 'FBX':
                    ops.export_scene.fbx(filepath=filepath, use_selection=True)
            except Exception as e:
                ok, msg = False, f"Export failed: {e}"
        except Exception as e:
            ok, msg = False, f"Export failed: {e}"
        finally:
            ob.select_set(False)
            for o in prev_sel:
                try:
                    o.select_set(True)
                except Exception:
                    pass
            try:
                context.view_layer.objects.active = prev_active
            except Exception:
                pass

        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}
        if not os.path.exists(filepath):
            self.report({'ERROR'},
                        "Export ran but no file was written. "
                        "Check write permissions for the chosen folder.")
            return {'CANCELLED'}
        size = os.path.getsize(filepath)
        self.report({'INFO'},
                    f"Exported ({size:,} bytes). To re-import at the SAME volume, "
                    f"import with Scale = 1.0 and Scene Unit OFF: {filepath}")
        return {'FINISHED'}


class EXV_OT_clear(Operator):
    bl_idname = "exv.clear"
    bl_label = "Clear Solids"

    def execute(self, context):
        removed = 0
        for ob in list(bpy.data.objects):
            if ob.name.startswith("ExposureSolid"):
                bpy.data.objects.remove(ob, do_unlink=True)
                removed += 1
        self.report({'INFO'}, f"Removed {removed} solids.")
        return {'FINISHED'}


class EXV_OT_show_holes(Operator):
    bl_idname = "exv.show_holes"
    bl_label = "Inspect Filled Holes"
    bl_description = ("Select the membrane patches that closed interior holes "
                      "(shown yellow on the solid) and frame them in the view, "
                      "so the filled gaps can be inspected")

    def execute(self, context):
        p = context.scene.exv_props
        ob = bpy.data.objects.get(p.res_solid)
        if ob is None or ob.type != 'MESH':
            self.report({'ERROR'}, "No measured solid found. Measure again "
                                   "with 'Create solid object' on.")
            return {'CANCELLED'}
        if p.res_holes == 0 or len(ob.data.materials) < 2:
            self.report({'INFO'},
                        "No interior holes were filled in this measurement.")
            return {'CANCELLED'}

        # Start from Object Mode, then make the solid the active, visible object
        if context.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
        try:
            ob.hide_set(False)
        except Exception:
            pass
        for o in list(context.selected_objects):
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob

        # Select the hole-fill faces via their material slot, in Face mode
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_mode(type='FACE')
            bpy.ops.mesh.select_all(action='DESELECT')
            ob.active_material_index = 1               # the hole-fill slot
            bpy.ops.object.material_slot_select()
        except Exception as e:
            self.report({'ERROR'}, f"Could not select the filled holes: {e}")
            return {'CANCELLED'}
        try:
            bpy.ops.view3d.view_selected()             # frame them
        except Exception:
            pass

        self.report({'INFO'},
                    f"Selected {p.res_holes} filled hole region(s), "
                    "highlighted in Edit Mode. Press Tab to leave Edit Mode.")
        return {'FINISHED'}


# ============================================================
# Panel — deliberately minimal (one button, one result box)
# ============================================================

class EXV_PT_panel(Panel):
    bl_label = "Exposure Volume"
    bl_idname = "EXV_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Exposure"

    def draw(self, context):
        layout = self.layout
        p = context.scene.exv_props

        box = layout.box()
        box.label(text="How to use", icon='INFO')
        col = box.column(align=True)
        col.label(text="1. Edit Mode, Vertex select (1)")
        col.label(text="2. Ctrl+Click ~20-40 rim points,")
        col.label(text="   one by one, in order")
        col.label(text="3. Press Measure")
        col.label(text="Gaps? Raise Weld distance (Adv.)")

        layout.operator("exv.measure", icon='SNAP_VOLUME')

        if p.res_has:
            box = layout.box()
            box.label(text="Result", icon='CHECKMARK')
            box.label(text=f"Volume: {p.res_vol_scene:,.3f} {p.res_unit}³")
            ico = 'CHECKMARK' if p.res_watertight else 'ERROR'
            box.label(text=f"Watertight: {'yes' if p.res_watertight else 'NO'}",
                      icon=ico)
            box.label(text=f"Rim points: {p.res_rim}")
            if p.res_dropped:
                box.label(text=f"Stray points skipped: {p.res_dropped}",
                          icon='INFO')
            if p.res_holes:
                box.label(text=f"Interior holes filled: {p.res_holes}",
                          icon='INFO')
                box.operator("exv.show_holes", icon='VIEWZOOM')
                box.label(text="Filled gaps are shown yellow")
            if not p.res_watertight:
                box.label(text="Not watertight — do not trust this volume",
                          icon='ERROR')
            if not p.res_rim_simple:
                box.label(text="Rim outline self-intersects — check points",
                          icon='ERROR')
            if p.res_rim_planar > 0.12:
                box.label(text="Rim is non-planar — lid position approximate",
                          icon='INFO')

            box = layout.box()
            box.label(text="Export solid", icon='MESH_DATA')
            box.prop(p, "specimen_id")
            box.prop(p, "export_format")
            box.operator("exv.export", icon='EXPORT')
            box.label(text="Opens a Save dialog", icon='INFO')
            box.label(text="Re-import: Scale 1.0, Scene Unit OFF")

        # --- Advanced (collapsed by default) ---
        box = layout.box()
        row = box.row()
        row.prop(p, "show_advanced",
                 icon='TRIA_DOWN' if p.show_advanced else 'TRIA_RIGHT',
                 emboss=False)
        if p.show_advanced:
            col = box.column(align=True)
            col.prop(p, "flip_normal")
            col.prop(p, "weld_dist")
            col.prop(p, "make_preview")
            box.operator("exv.clear", icon='TRASH')


# ============================================================
# Registration
# ============================================================

classes = (
    EXV_Props,
    EXV_OT_measure,
    EXV_OT_export,
    EXV_OT_clear,
    EXV_OT_show_holes,
    EXV_PT_panel,
)


def register():
    # Make (re-)running the script in Blender's Text Editor safe: if a previous
    # copy is still registered (common while iterating in Scripting), remove it
    # first so register_class() does not raise "already registered".
    try:
        unregister()
    except Exception:
        pass
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.exv_props = PointerProperty(type=EXV_Props)


def unregister():
    if hasattr(bpy.types.Scene, "exv_props"):
        del bpy.types.Scene.exv_props
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
