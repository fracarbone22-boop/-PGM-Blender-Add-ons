# PGM Blender Add-ons

B**lender-based metric calibration and surgical-exposure volume measurement for photogrammetric 3D models of anatomical specimens**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Blender](https://img.shields.io/badge/Blender-4.0%2B-orange.svg)](https://www.blender.org/)
[![Python](https://img.shields.io/badge/Python-bpy-3776AB.svg)](https://docs.blender.org/api/current/)

Three open-source Python add-ons for [Blender](https://www.blender.org/) that turn a photogrammetric mesh of an anatomical specimen or surgical field into a metrically faithful digital specimen and let you measure the volume of a surgical exposure on it. They accompany the manuscript:

> Carbone F, Trandzhiev M, Spiriev T, Armocida D, Milev M, Laleva L, Wolf-Vollenbröker M, de Notaris M, Spetzger U, Nakov V, Cornelius JF. *Calibration of Smartphone-Based Photogrammetry 3D Models for Quantitative Distance, Surface, and Volumetric Measurements in Neurosurgical Specimens: a Workflow Pipeline and Literature Meta-Analysis.* (2026)

---

## Contents

- [Introduction](#introduction)
- [The add-ons at a glance](#the-add-ons-at-a-glance)
- [Requirements](#requirements)
- [Installation](#installation)
- [Recommended workflow](#recommended-workflow)
- [Usage](#usage)
  - [1. Real Scale Calibrator](#1-real-scale-calibrator)
  - [2. PGM Calibrate](#2-pgm-calibrate)
  - [3. Exposure Volume](#3-exposure-volume)
- [How the measurements are computed](#how-the-measurements-are-computed)[](#validation)
- [Limitations and intended use](#limitations-and-intended-use)
- [Citing](#citing)
- [Authors and attribution](#authors-and-attribution)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Introduction

Photogrammetry reconstructs the three-dimensional shape of an object from many overlapping photographs. In neurosurgical anatomy it has become a standard way to produce photorealistic 3D models of dissections and operative approaches for teaching, virtual-reality training and preoperative planning. Because the mesh is derived from real photographs, it preserves both the surface texture and the geometry of the scanned region.

What photogrammetry does **not** preserve by default is absolute scale. A Structure-from-Motion reconstruction is internally consistent but dimensionless: the model may be ten times larger or smaller than the real specimen. Before any distance, surface or volume can be read off the mesh, the model has to be rescaled against a reference of known length that was present in the scene when the photographs were taken.

This repository provides the tools we developed for that step and for what becomes possible once it is done:

- **Two calibration add-ons** derive an isotropic scale factor from an in-scene metric reference (for example a bendable ruler placed in the dissection field) and apply it uniformly to the mesh, so that one Blender unit corresponds to one millimetre. *PGM Calibrate* additionally writes a per-model audit report so that every calibration is traceable.
- **One volumetric add-on** quantifies the volume of a surgical exposure on the calibrated model: the user marks the rim of the craniotomy, and the add-on closes the opening into a watertight solid, checks it, reports the enclosed volume in cm³ and exports the solid in standard 3D formats.

The pipeline was developed and validated on photogrammetric models of nine standard neurosurgical approaches performed on formalin-fixed body-donor specimens. Photographs were taken with a smartphone (iPhone 15 Pro, 80–100 images per field), reconstructed with the Abound app and exported as `.fbx`, but nothing in the add-ons is specific to that camera or reconstruction software: any textured mesh that contains a reference of known length can be calibrated. In the accompanying study, model-derived linear measurements differed from caliper measurements on the physical specimens by a mean of −0.53 mm (mean absolute error 1.17 mm, r = 0.992), and a PRISMA-compliant meta-analysis of 29 published validation studies placed the pooled absolute error of photogrammetry across software packages and cameras at 1.21 mm. The add-ons are therefore intended to make quantitative, reproducible morphometry of anatomical specimens accessible with consumer hardware and free software, and to allow such analyses to be run retrospectively on existing collections of photogrammetric models.

---

## The add-ons at a glance

| File | Add-on name | Sidebar tab | Purpose | Min. Blender |
|---|---|---|---|---|
| `real_scale_calibrator.py` | **Real Scale Calibrator** | `View3D ▸ Sidebar (N) ▸ Real Scale` | Enter a measured length and its true length; the selected objects are scaled uniformly by *L*<sub>real</sub> / *L*<sub>meas</sub>. Simplest possible calibrator. | 3.6 |
| `pgm_calibrate.py` | **PGM Calibrate** | `View3D ▸ Sidebar (N) ▸ PGM` | Pick the two endpoints of the reference with the 3D cursor; the add-on measures them, computes and applies the scale factor, bakes it into the vertex coordinates, sets scene units to millimetres, backs up the `.blend` and writes a JSON + TXT calibration report.  | 4.0 |
| `exposure_volume.py` | **Exposure Volume** | `View3D ▸ Sidebar (N) ▸ Exposure` | Select 20–40 vertices around the rim of a surgical opening on a calibrated mesh; the add-on links them along the surface into a closed rim, floods the cavity, caps it and reports the enclosed volume with watertightness checks. Exports the solid to STL/PLY/OBJ/glTF/FBX. | 4.0 |

The two calibrators do the same thing in two different ways. *Real Scale Calibrator* leaves the measuring to you (Blender's Measure tool with `Ctrl` snapping) and only applies the factor; *PGM Calibrate* does the measuring itself from two cursor positions and documents everything. Use whichever suits your workflow.

---

## Requirements

- **Blender 4.0 or later** (the study was run in Blender 5; *Real Scale Calibrator* also works from 3.6). Download from <https://www.blender.org/download/>.
- No Python packages beyond those bundled with Blender (`bpy`, `bmesh`, `mathutils` and the standard library).
- A photogrammetric mesh (`.fbx`, `.obj`, `.glb`, `.ply`, …) that contains a **metric reference of known length** inside the scanned region. We used a bendable metric ruler laid in the dissection field; a caliper set to a known opening or any object of certified length works equally well.

---

## Installation

Each add-on is a single, self-contained `.py` file. Install the ones you need.

**Blender 4.2 and later**

1. Download `real_scale_calibrator.py`, `pgm_calibrate.py` and/or `exposure_volume.py` from this repository (or clone it).
2. `Edit ▸ Preferences ▸ Add-ons`, open the **▾** drop-down menu in the top-right corner and choose **Install from Disk…**.
3. Select the `.py` file and enable the add-on with its checkbox. Repeat for each file.
4. In the 3D Viewport press `N` to open the sidebar: the tabs **Real Scale**, **PGM** and **Exposure** appear.

**Blender 4.0 – 4.1**

Same as above, but the button is `Edit ▸ Preferences ▸ Add-ons ▸ Install…`.

**Running without installing**

Any of the files can also be opened in Blender's *Scripting* workspace (`Text ▸ Open`) and executed with **Run Script** (`Alt+P`). The panel appears for the current session. This is convenient for trying the add-ons or for editing them.

---

## Recommended workflow

1. **Acquire** the field with the metric reference in view, reconstruct the mesh in your photogrammetry software and export it (`.fbx` in our case).
2. **Import** the mesh into a new Blender file (`File ▸ Import`) and **save the `.blend`** — *PGM Calibrate* refuses to run on an unsaved file because it needs a location for its backup and report.
3. **Calibrate** with *Real scale Calibrator or PGM Calibrate. Check the reported scale factor against your expectation (for Abound/Object-Capture exports *S*<sub>c</sub> typically falls between 0.1 and 100).
4. **Verify** the calibration by measuring a second known distance on the model with Blender's Measure tool before doing anything else.
5. **Measure.** Linear and surface measurements can now be read directly in millimetres. For the volume of a surgical exposure, run *Exposure Volume*.
6. **Archive** the `.blend`, the `*__calibration_report.json/.txt` and any exported solids with the specimen record.

---

## Usage

### 1. Real Scale Calibrator

*Sidebar tab: **Real Scale** · Blender ≥ 3.6*

1. Measure the reference on the **uncalibrated** mesh with Blender's **Measure** tool (toolbar, or `Shift+Spacebar ▸ M`). Hold **`Ctrl`** while placing each endpoint so that it snaps to the nearest vertex or edge of the mesh — this is what makes the measurement reproducible. Read the length that Blender displays.
2. In the panel, enter that value as **Point-to-point measure** and the true physical length of the reference as **Real object scale**. Both fields use the scene's length unit — keep them in the same unit.
3. Leave **Apply scale after calibration** enabled (default) so that the scale is baked into the geometry and the object's scale returns to 1.000.
4. Select the object(s) to calibrate and press **Calibrate Scale**.

The panel then shows the scale factor that was applied and the dimensions of the active object. The operation is undoable (`Ctrl+Z`). Mesh, empty, curve, surface, metaball and text objects are all accepted, so a group of objects can be calibrated together.

### 2. PGM Calibrate

*Sidebar tab: **PGM** · Blender ≥ 4.0*

1. **Save the `.blend` file first.**
2. Fill in the panel: **Reference label** (free text, e.g. "3 cm measuring tape"), **Real length (mm)** of the reference, and optionally the **Operator** name — all three go into the report.
3. Place the **3D cursor** exactly on one end of the reference. The most precise way is to enter Edit Mode, select the vertex at the end of the reference, press `Shift+S ▸ Cursor to Selected` and return to Object Mode; `Shift+Right-click` on the surface also works. Press **Mark P0 (cursor)**.
4. Move the cursor to the other end of the reference and press **Mark P1 (cursor)**.
5. The panel now shows both stored coordinates, the measured length *L*<sub>measured</sub> and a preview of the scale factor *S*<sub>c</sub>. A warning appears if *S*<sub>c</sub> looks implausible (< 0.01 or > 1000), which almost always means a wrong endpoint.
6. Select the mesh object(s) to calibrate and press **Calibrate (apply Sc)**.

What happens on calibration:

- a one-time backup `<file>__before_calibration.blend` is written next to your file (never overwritten on later runs);
- the scene units are set to **metric, millimetres** (1 Blender unit = 1 mm), so viewport read-outs match the report;
- every selected mesh is scaled by *S*<sub>c</sub> about the world origin and the transform is **applied**, i.e. the vertex coordinates themselves now hold real-world millimetres;
- `<file>__calibration_report.json` and `<file>__calibration_report.txt` are written with the UTC timestamp, Blender version, operator, reference label and length, P0, P1, *L*<sub>measured</sub>, *S*<sub>c</sub>, and for every object its vertex count, mesh volume before and after calibration and a SHA-1 hash of the post-calibration vertex coordinates;
- the `.blend` is saved and P0/P1 are cleared so that stale points cannot leak into the next model.

The add-on refuses to proceed if either point is still at the world origin, if P0 and P1 coincide, if *S*<sub>c</sub> falls outside 0.001–10 000, if no mesh is selected or if the file is unsaved. **Reset P0/P1** clears the stored points. The calibration can be undone in the session with `Ctrl+Z`, but note that the `.blend` on disk has already been saved in its calibrated state; the untouched original is the `*__before_calibration.blend` backup, and the report files remain on disk.

### 3. Exposure Volume

*Sidebar tab: **Exposure** · Blender ≥ 4.0 · run on a **calibrated** mesh*

1. Select the mesh, enter **Edit Mode** (`Tab`) and switch to **Vertex select** (`1`).
2. **`Ctrl+Click` 20–40 vertices one by one, in order, around the rim** of the surgical opening (for a craniotomy: along the bone edge). An orange line follows your selection. The loop does **not** have to be closed — the add-on joins the last point back to the first along the shortest path on the surface. More rim points give a more faithful boundary.
3. Press **Measure Exposure Volume**.

The result box reports:

| Field | Meaning |
|---|---|
| **Volume** | Enclosed volume in the scene's length unit cubed (mm³ after *PGM Calibrate*; divide by 1000 for cm³). |
| **Watertight: yes / NO** | Whether the closed solid has no open edges. **If it says NO, do not use the value.** |
| **Rim points** | Number of rim landmarks actually used. |
| **Stray points skipped** | Selected vertices that were too far from the rim loop and were ignored. |
| **Interior holes filled** | Holes in the scanned cavity surface that were capped by the add-on. **Show holes** highlights the filled patches in yellow so you can judge whether they are acceptable. |
| warnings | *Rim outline self-intersects* (re-pick the points in order); *Rim is non-planar — lid position approximate* (strongly curved rims). |

By default the measured solid is added to the scene as a separate object (**Create solid object**) so you can inspect it, hide it or delete it with **Clear**. To export it, enter a **Specimen ID**, choose a **Format** (STL, PLY, OBJ, glTF `.glb`, FBX) and press **Export Solid (3D)**; a save dialog opens. When re-importing an exported solid into Blender use scale 1.0 and leave *Scene Unit* off.

**Advanced** (collapsed by default)

- **Flip side** — the add-on probes which side of the rim the cavity lies on; if it chose the wrong side, enable this and re-measure.
- **Weld distance** — the scan is welded on an internal copy before measuring (0 = automatic). Raise it if you get *"could not link … rim points"* (the mesh has cracks wider than the automatic threshold); lower it if distinct points get merged. Your original mesh is never modified.
- **Create solid object** — disable to measure without leaving a solid in the scene (export then becomes unavailable).

---

## How the measurements are computed

**Calibration.** Both calibrators compute a single isotropic factor

&nbsp;&nbsp;&nbsp;&nbsp;*S*<sub>c</sub> = *L*<sub>real</sub> / *L*<sub>measured</sub>

and multiply the X, Y and Z scale of each selected object by it. *Real Scale Calibrator* takes *L*<sub>measured</sub> from the value you type; *PGM Calibrate* computes it as the Euclidean distance between the two stored cursor positions. Applying the transform bakes the factor into the vertex coordinates. Because photogrammetric reconstructions are isotropic up to scale, a single factor is sufficient; no shearing or anisotropic correction is attempted.

**Exposure volume.** The selected vertices are treated as approximate rim landmarks. The add-on (i) welds near-coincident vertices on an internal copy of the mesh, (ii) orders the landmarks and connects consecutive ones along geodesic paths on the real surface to form a closed barrier ring, (iii) fits a plane to the ring and probes with a BVH tree to decide on which side the cavity lies, (iv) flood-fills the mesh faces enclosed by the ring to isolate the scanned cavity, (v) caps the rim and any interior holes with triangulated closure membranes, (vi) checks for non-manifold edges, and (vii) returns the signed volume of the resulting closed solid, converted to the scene's length unit. The quantity measured is the cavity enclosed by the user-defined rim and the scanned surface below it, i.e. the space available through the opening.

---

## Limitations and intended use

- The add-ons are **research tools for anatomical specimens and laboratory work**. They are not medical devices and must not be used for clinical decision-making.
- Calibration is only as good as the reference: use a rigid or taut reference of certified length, photograph it in the same focal plane as the region of interest, and pick its endpoints on well-defined mesh vertices.
- *Exposure Volume* has so far been compared only with a second mesh-closure method on the same meshes, not with a physical volumetric standard. Treat its output as a reproducible, relative measure for comparing approaches; report the closure convention (cavity enclosed by the rim) alongside the numbers.
- Always check **Watertight: yes** and inspect filled holes with **Show holes** before accepting a volume.
- Deformable or smooth, texture-poor surfaces reconstruct less accurately than rigid, textured bone; the meta-analysis in the accompanying study quantifies this.

---

## Citing

If you use these add-ons in published work, please cite the accompanying article (reference will be updated on publication) and, optionally, the software itself. A machine-readable [`CITATION.cff`](CITATION.cff) is included; GitHub's *Cite this repository* button reads it.

**Article**

> Carbone F, Trandzhiev M, Spiriev T, Armocida D, Milev M, Laleva L, Wolf-Vollenbröker M, de Notaris M, Spetzger U, Nakov V, Cornelius JF. Calibration of Smartphone-Based Photogrammetry 3D Models for Quantitative Distance, Surface, and Volumetric Measurements in Neurosurgical Specimens: a Workflow Pipeline and Literature Meta-Analysis. 2026.

**Software**

> Carbone F, et al. PGM Blender Add-ons: Real Scale Calibrator, PGM Calibrate and Exposure Volume (version 1.0). 2026. https://github.com/<github-user>/<repo>

---

## Authors and attribution

The add-ons were developed by the authors of the accompanying study at the Institute for Anatomy I and the Department of Neurosurgery, Heinrich Heine University Düsseldorf (Germany).

**Maintainer and corresponding authors:** Dr. Francesco Carbone, MD — Department of Neurosurgery, Karlsruher Neurozentrum, Städtisches Klinikum Karlsruhe, Moltkestraße 90, 76133 Karlsruhe, Germany — <francesco.carbone615@gmail.com>. Dr. Toma Spiriev, MD, PhD, MHM, FEBNS — Department of Neurosurgery, Acibadem City Clinic, Tokuda Hospital, Sofia, Bulgaria — [spiriev@gmail.com](spiriev@gmail.com).

**Use of AI coding assistants.** The code was written by the authors with the assistance of large-language-model coding tools (ChatGPT, OpenAI; Claude, Anthropic). All code was reviewed, tested and validated by the authors, who take full responsibility for it. The AI tools are not authors of this software.

**Third-party software.** The add-ons run inside and depend on [Blender](https://www.blender.org/) (© Blender Foundation, GNU GPL). *Exposure Volume* was benchmarked against Blender's bundled *3D-Print Toolbox* add-on. The photogrammetric meshes in the study were produced with Abound (Abound Labs Inc., New York, NY, USA), which is not part of this repository and is not required to use the add-ons.

---

## License

Copyright © 2026 3D Atlas NGO and the co-authors of the accompanying study.

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License as published by the 3D Atlas NGO, either version 3 of the License, or (at your option) any later version** (SPDX: `GPL-3.0-or-later`). It is distributed in the hope that it will be useful, but **without any warranty**; without even the implied warranty of merchantability or fitness for a particular purpose. See the [`LICENSE`](LICENSE) file for the full text.

The GPL was chosen because the add-ons are built on Blender's Python API; the Blender Foundation asks that published add-ons using the API be released under a GPL-compatible license, and the Blender Extensions platform requires `GPL-3.0-or-later`.

---

## Acknowledgments

The authors express their sincere gratitude to the body donors and their families for their generous contribution to medical education, training and research.

---

*Questions, bug reports and suggestions are welcome through the repository's [Issues](https://github.com/<github-user>/<repo>/issues) page. When reporting a problem, please include your Blender version, the add-on involved and — where possible — the `*__calibration_report.txt` or a screenshot of the Exposure Volume result box.*
