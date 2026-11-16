bl_info = {
    "name": "Real Scale Calibrator",
    "author": "3D Atlas of Neurological Surgery / ChatGPT",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Real Scale",
    "description": "Uniformly calibrates selected photogrammetry models from a known real-world length and measured uncalibrated length.",
    "category": "Object",
}

import bpy


class RSC_Properties(bpy.types.PropertyGroup):
    real_object_scale: bpy.props.FloatProperty(
        name="Real object scale",
        description="True real-world length of the calibration reference, e.g. ruler length",
        default=3.0,
        min=0.000001,
        precision=4,
        unit='LENGTH'
    )

    point_to_point_measure: bpy.props.FloatProperty(
        name="Point-to-point measure",
        description="Length measured in Blender before calibration",
        default=60.0,
        min=0.000001,
        precision=4,
        unit='LENGTH'
    )

    apply_scale_after: bpy.props.BoolProperty(
        name="Apply scale after calibration",
        description="Applies Ctrl+A > Scale automatically after resizing, so object scale returns to 1.000",
        default=True
    )

    last_scale_factor: bpy.props.FloatProperty(
        name="Last scale factor",
        default=1.0,
        precision=8
    )


class RSC_OT_CalibrateScale(bpy.types.Operator):
    bl_idname = "object.real_scale_calibrate"
    bl_label = "Calibrate Scale"
    bl_description = "Scale selected object(s) using Real object scale / Point-to-point measure"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.rsc_properties
        real_len = props.real_object_scale
        measured_len = props.point_to_point_measure

        if measured_len <= 0 or real_len <= 0:
            self.report({'ERROR'}, "Both values must be greater than zero.")
            return {'CANCELLED'}

        selected = [obj for obj in context.selected_objects if obj.type in {'MESH', 'EMPTY', 'CURVE', 'SURFACE', 'META', 'FONT'}]
        if not selected:
            self.report({'ERROR'}, "Select at least one object to calibrate.")
            return {'CANCELLED'}

        scale_factor = real_len / measured_len
        props.last_scale_factor = scale_factor

        active_before = context.view_layer.objects.active

        for obj in selected:
            obj.scale.x *= scale_factor
            obj.scale.y *= scale_factor
            obj.scale.z *= scale_factor

        if props.apply_scale_after:
            # Apply scale object-by-object to avoid context issues.
            bpy.ops.object.select_all(action='DESELECT')
            for obj in selected:
                obj.select_set(True)
                context.view_layer.objects.active = obj
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
                obj.select_set(False)

            for obj in selected:
                obj.select_set(True)
            context.view_layer.objects.active = active_before if active_before in selected else selected[0]

        self.report({'INFO'}, f"Calibrated with scale factor {scale_factor:.8f}")
        return {'FINISHED'}


class RSC_PT_Panel(bpy.types.Panel):
    bl_label = "Real Scale Calibrator"
    bl_idname = "RSC_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Real Scale"

    def draw(self, context):
        layout = self.layout
        props = context.scene.rsc_properties

        layout.label(text="Photogrammetry scale correction")
        layout.prop(props, "real_object_scale")
        layout.prop(props, "point_to_point_measure")
        layout.prop(props, "apply_scale_after")
        layout.operator("object.real_scale_calibrate", icon='FULLSCREEN_ENTER')

        layout.separator()
        layout.label(text=f"Scale factor: {props.last_scale_factor:.8f}")

        if context.object:
            obj = context.object
            layout.separator()
            layout.label(text="Active object dimensions:")
            layout.label(text=f"X: {obj.dimensions.x:.4f}")
            layout.label(text=f"Y: {obj.dimensions.y:.4f}")
            layout.label(text=f"Z: {obj.dimensions.z:.4f}")


classes = (
    RSC_Properties,
    RSC_OT_CalibrateScale,
    RSC_PT_Panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rsc_properties = bpy.props.PointerProperty(type=RSC_Properties)


def unregister():
    del bpy.types.Scene.rsc_properties
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
