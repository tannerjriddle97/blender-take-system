"""Persistent RNA data model for the Blender Take System."""

import bpy
from bpy.props import (
    BoolProperty,
    BoolVectorProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
    StringProperty,
)


OVERRIDE_VALUE_TYPES = (
    ("FLOAT", "Float", "Floating-point value"),
    ("INT", "Integer", "Integer value"),
    ("BOOL", "Boolean", "Boolean value"),
    ("STRING", "String", "Text value"),
    ("ENUM", "Enum", "RNA enum identifier"),
    ("VECTOR", "Vector", "Two-, three-, or four-component numeric value"),
    ("COLOR", "Color", "RGB or RGBA color value"),
    ("POINTER", "Datablock", "Pointer to a Blender datablock, including None"),
)


def _camera_object_poll(_self, candidate):
    return candidate is None or (
        isinstance(candidate, bpy.types.Object) and candidate.type == "CAMERA"
    )


class TS_PG_TakeOverride(bpy.types.PropertyGroup):
    """One property override owned by one take."""

    uuid: StringProperty(name="Override ID", options={"HIDDEN"})
    target_ref_uuid: StringProperty(
        name="Target/Path Identity",
        description=(
            "Stable internal identity shared by overrides of the same target/path"
        ),
        options={"HIDDEN"},
    )

    # A generic ID pointer preserves datablock identity across renames and saves.
    # The parallel text fields are diagnostics and future JSON-export metadata.
    target_id: PointerProperty(name="Target", type=bpy.types.ID)
    target_id_type: StringProperty(name="Target Type")
    target_id_name: StringProperty(name="Target Name")
    target_library_path: StringProperty(name="Target Library")
    target_reference_set: BoolProperty(
        name="Target Reference Was Set",
        default=False,
        options={"HIDDEN"},
    )
    data_path: StringProperty(
        name="RNA Data Path",
        description="Path relative to the target datablock",
    )

    prop_type: EnumProperty(
        name="Stored Type",
        items=OVERRIDE_VALUE_TYPES,
        default="FLOAT",
    )
    rna_subtype: StringProperty(
        name="RNA Subtype",
        description="Original RNA subtype, retained for diagnostics",
        options={"HIDDEN"},
    )

    value_float: FloatProperty(name="Float")
    value_float_text: StringProperty(
        name="Exact Float",
        description="Hexadecimal float payload for double-precision round trips",
        options={"HIDDEN"},
    )
    value_int: IntProperty(name="Integer")
    value_bool: BoolProperty(name="Boolean")
    value_string: StringProperty(name="String")
    value_vector: FloatVectorProperty(name="Vector", size=4)
    value_int_vector: IntVectorProperty(name="Integer Vector", size=4)
    value_bool_vector: BoolVectorProperty(name="Boolean Vector", size=4)
    value_array_text: StringProperty(
        name="Exact Float Array",
        description="Hexadecimal component payload for double-precision arrays",
        options={"HIDDEN"},
    )
    value_color: FloatVectorProperty(
        name="Color",
        size=4,
        subtype="COLOR",
        soft_min=0.0,
        soft_max=1.0,
    )
    array_length: IntProperty(
        name="Component Count",
        default=0,
        min=0,
        max=4,
        options={"HIDDEN"},
    )
    array_component_type: StringProperty(
        name="Array Component Type",
        default="FLOAT",
        options={"HIDDEN"},
    )

    value_pointer: PointerProperty(name="Datablock Value", type=bpy.types.ID)
    pointer_is_none: BoolProperty(
        name="Stored Value Is None",
        default=False,
        options={"HIDDEN"},
    )
    pointer_id_type: StringProperty(name="Value Type", options={"HIDDEN"})
    pointer_id_name: StringProperty(name="Value Name", options={"HIDDEN"})
    pointer_library_path: StringProperty(name="Value Library", options={"HIDDEN"})


class TS_PG_Take(bpy.types.PropertyGroup):
    """One take in the scene-local hierarchy."""

    uuid: StringProperty(name="Take ID", options={"HIDDEN"})
    parent_uuid: StringProperty(
        name="Parent Take ID",
        description="Stable UUID link; empty only for Main",
        options={"HIDDEN"},
    )
    is_main: BoolProperty(name="Is Main", default=False, options={"HIDDEN"})
    is_recording: BoolProperty(
        name="Record",
        description=(
            "Automatically capture supported user property changes while this "
            "non-Main take is applied"
        ),
        default=False,
    )
    include_in_render: BoolProperty(
        name="Include in Batch Render",
        default=True,
    )
    render_output_path: StringProperty(
        name="Batch Output",
        description=(
            "Optional output path or filename for this take; when blank the "
            "batch renderer derives a unique path from the resolved Scene "
            "render filepath and take name"
        ),
        subtype="FILE_PATH",
        options={"PATH_SUPPORTS_BLEND_RELATIVE"},
    )
    overrides: CollectionProperty(type=TS_PG_TakeOverride)

    use_camera_override: BoolProperty(
        name="Override Camera",
        default=False,
    )
    camera_override: PointerProperty(
        name="Take Camera",
        type=bpy.types.Object,
        poll=_camera_object_poll,
    )


class TS_PG_TakeSystem(bpy.types.PropertyGroup):
    """Scene-owned root for all persistent take data."""

    schema_version: IntProperty(
        name="Schema Version",
        default=2,
        min=1,
        options={"HIDDEN"},
    )
    main_take_uuid: StringProperty(name="Main Take ID", options={"HIDDEN"})
    takes: CollectionProperty(type=TS_PG_Take)
    active_take_uuid: StringProperty(name="Active Take ID", options={"HIDDEN"})
    active_take_index: IntProperty(
        name="Active Take",
        default=0,
        min=0,
        options={"HIDDEN"},
    )
    active_override_index: IntProperty(
        name="Active Override",
        default=0,
        min=0,
        options={"HIDDEN"},
    )


CLASSES = (
    TS_PG_TakeOverride,
    TS_PG_Take,
    TS_PG_TakeSystem,
)
