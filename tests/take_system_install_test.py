"""Install/enable/disable smoke test for the packaged Take System ZIP."""

import importlib
import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
ZIP_PATH = WORKSPACE / "dist" / "blender_take_system_v0_6_1.zip"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


result = bpy.ops.preferences.addon_install(
    filepath=str(ZIP_PATH),
    overwrite=True,
)
require(result == {"FINISHED"}, f"ZIP install failed: {result}")

result = bpy.ops.preferences.addon_enable(module="blender_take_system")
require(result == {"FINISHED"}, f"Add-on enable failed: {result}")
require(
    "blender_take_system" in bpy.context.preferences.addons,
    "Enabled add-on is absent from preferences",
)
require(
    hasattr(bpy.types.Scene, "take_system"),
    "Add-on registration did not attach Scene settings",
)
module = sys.modules.get("blender_take_system")
require(module is not None, "Installed add-on module was not imported")
require(
    Path(module.__file__).resolve()
    != (WORKSPACE / "blender_take_system" / "__init__.py").resolve(),
    "Install test imported workspace source instead of the packaged addon",
)
# In a background script, Blender's event loop does not get a chance to run the
# zero-delay bootstrap timer before this immediate assertion. Call the exact
# scheduled callback once to validate it outside addon_utils' restricted context.
module._bootstrap_scenes_timer()
require(
    len(bpy.context.scene.take_system.takes) == 1
    and bpy.context.scene.take_system.takes[0].is_main,
    "Installed add-on did not bootstrap Main",
)

require(module.bl_info["version"] == (0, 6, 1), "Installed version mismatch")
require(module.bl_info["blender"] == (4, 0, 0), "Minimum version mismatch")

release_types = (
    module.ui.TS_UL_takes,
    module.ui.TS_UL_overrides,
    module.ui.TS_PT_take_manager,
    module.ui.TS_PT_take_scene_settings,
    module.ui.TS_PT_take_batch_render,
    module.ui.TS_PT_take_overrides,
    module.operators.TS_OT_apply_selected_take,
    module.operators.TS_OT_duplicate_take,
    module.operators.TS_OT_delete_take,
    module.operators.TS_OT_reparent_take,
    module.operators.TS_OT_remove_override,
    module.operators.TS_OT_open_manager,
    module.operators.TS_OT_capture_recent_action,
    module.operators.TS_OT_toggle_recording,
    module.operators.TS_OT_flush_recording,
    module.operators.TS_OT_configure_take_camera,
    module.operators.TS_OT_clear_take_camera,
    module.operators.TS_OT_edit_render_profile,
    module.operators.TS_OT_capture_render_settings,
    module.operators.TS_OT_clear_render_settings,
    module.operators.TS_OT_render_included_takes,
)
missing_release_types = [
    cls.__name__
    for cls in release_types
    if not cls.is_registered
]
require(
    not missing_release_types,
    f"Release UI/operator classes were not registered: {missing_release_types}",
)
require(
    module.ui.TS_PT_take_manager.bl_space_type == "PROPERTIES"
    and module.ui.TS_PT_take_manager.bl_context == "scene",
    "Take Manager is not registered in Properties > Scene",
)
require(
    module.ui.TS_PT_take_overrides.bl_parent_id
    == module.ui.TS_PT_take_manager.bl_idname,
    "Override inspector is not nested under the Take Manager",
)

# Blender may reload only the package module during an in-place ZIP update.
# Verify __init__.py explicitly refreshes its cached submodules and classes.
operator_class_before_reload = module.operators.TS_OT_apply_selected_take
module.unregister()
module = importlib.reload(module)
module.register()
require(
    module.operators.TS_OT_apply_selected_take is not operator_class_before_reload,
    "Package reload retained a stale operators submodule",
)

engine = module.engine
scene = bpy.context.scene
main = engine.ensure_main_take(scene)
main_uuid = main.uuid
mesh = bpy.data.meshes.new("TS_InstalledMesh")
mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
obj = bpy.data.objects.new("TS_InstalledObject", mesh)
scene.collection.objects.link(obj)
base_material = bpy.data.materials.new("TS_InstalledBase")
variant_material = bpy.data.materials.new("TS_InstalledVariant")
mesh.materials.append(base_material)
obj.material_slots[0].link = "OBJECT"
obj.material_slots[0].material = base_material
obj.location = (0.0, 0.0, 0.0)
base_camera_data = bpy.data.cameras.new("TS_InstalledBaseCameraData")
variant_camera_data = bpy.data.cameras.new("TS_InstalledVariantCameraData")
base_camera = bpy.data.objects.new(
    "TS_InstalledBaseCamera",
    base_camera_data,
)
variant_camera = bpy.data.objects.new(
    "TS_InstalledVariantCamera",
    variant_camera_data,
)
scene.collection.objects.link(base_camera)
scene.collection.objects.link(variant_camera)
scene.camera = base_camera

take = engine.create_take(
    scene,
    "Installed Functional",
    parent_uuid=main_uuid,
    make_active=True,
)
take_uuid = take.uuid
engine.capture_override(scene, obj, "location", take_uuid)
obj.location = (4.0, 5.0, 6.0)
engine.capture_override(scene, obj, "location", take_uuid)
engine.capture_override(scene, obj, "material_slots[0].material", take_uuid)
obj.material_slots[0].material = variant_material
engine.capture_override(scene, obj, "material_slots[0].material", take_uuid)
engine.configure_take_camera(scene, take_uuid, variant_camera)
engine.capture_render_settings(scene, take_uuid)
scene.render.resolution_x = 640
scene.render.resolution_y = 360
engine.capture_render_settings(scene, take_uuid)
take = engine.find_take(scene, take_uuid)
take.render_output_path = str(
    WORKSPACE / ".take_system_test" / "installed_phase5"
)
engine.apply_take(scene, main_uuid, strict=True)
require(
    tuple(obj.location) == (0.0, 0.0, 0.0)
    and obj.material_slots[0].material == base_material,
    "Packaged Main apply failed",
)
require(
    scene.camera == base_camera
    and scene.render.resolution_x == 1920
    and scene.render.resolution_y == 1080,
    "Packaged Main Phase 5 restoration failed",
)
engine.apply_take(scene, take_uuid, strict=True)
require(
    tuple(obj.location) == (4.0, 5.0, 6.0)
    and obj.material_slots[0].material == variant_material,
    "Packaged variant apply failed",
)
require(
    scene.camera == variant_camera
    and scene.render.resolution_x == 640
    and scene.render.resolution_y == 360,
    "Packaged variant Phase 5 apply failed",
)
packaged_revision = engine.scene_mutation_revision(scene)
packaged_write = engine.write_path_value
packaged_write_calls = []


def record_packaged_write(target_id, data_path, value):
    packaged_write_calls.append((target_id, data_path))
    return packaged_write(target_id, data_path, value)


engine.write_path_value = record_packaged_write
try:
    noop_report = engine.apply_take(scene, take_uuid, strict=True)
finally:
    engine.write_path_value = packaged_write
require(
    noop_report.ok
    and not packaged_write_calls
    and engine.scene_mutation_revision(scene) == packaged_revision,
    "Packaged same-value take application performed live assignments",
)

# Exercise the Phase 4 contract from the installed archive itself: every
# ancestor contributes, while the deepest override for a conflicting key wins.
child = engine.create_take(
    scene,
    "Installed Child",
    parent_uuid=take_uuid,
    make_active=True,
)
child_uuid = child.uuid
engine.capture_override(scene, obj, "location", child_uuid)
obj.location = (7.0, 8.0, 9.0)
engine.capture_override(scene, obj, "location", child_uuid)
engine.capture_override(scene, obj, "hide_render", child_uuid)
obj.hide_render = True
engine.capture_override(scene, obj, "hide_render", child_uuid)

grandchild = engine.create_take(
    scene,
    "Installed Grandchild",
    parent_uuid=child_uuid,
    make_active=True,
)
grandchild_uuid = grandchild.uuid
engine.capture_override(scene, obj, "location", grandchild_uuid)
obj.location = (-1.0, -2.0, -3.0)
engine.capture_override(scene, obj, "location", grandchild_uuid)

resolved = engine.resolve_take(scene, grandchild_uuid)
resolved_by_path = {
    entry.override.data_path: entry
    for entry in resolved.values()
    if entry.override.target_id == obj
}
require(
    resolved_by_path["location"].take_uuid == grandchild_uuid,
    "Packaged resolver did not select the deepest conflicting transform",
)
require(
    resolved_by_path["hide_render"].take_uuid == child_uuid,
    "Packaged resolver did not inherit the child visibility override",
)
require(
    resolved_by_path["material_slots[0].material"].take_uuid == take_uuid,
    "Packaged resolver did not inherit the parent material override",
)

engine.apply_take(scene, main_uuid, strict=True)
require(
    tuple(obj.location) == (0.0, 0.0, 0.0)
    and not obj.hide_render
    and obj.material_slots[0].material == base_material,
    "Packaged Main restoration failed after a deep take",
)
engine.apply_take(scene, take_uuid, strict=True)
require(
    tuple(obj.location) == (4.0, 5.0, 6.0)
    and not obj.hide_render
    and obj.material_slots[0].material == variant_material,
    "Packaged parent inheritance apply failed",
)
engine.apply_take(scene, child_uuid, strict=True)
require(
    tuple(obj.location) == (7.0, 8.0, 9.0)
    and obj.hide_render
    and obj.material_slots[0].material == variant_material,
    "Packaged child inheritance apply failed",
)
engine.apply_take(scene, grandchild_uuid, strict=True)
require(
    tuple(obj.location) == (-1.0, -2.0, -3.0)
    and obj.hide_render
    and obj.material_slots[0].material == variant_material,
    "Packaged deepest-wins apply failed",
)

# Verify collection enabled-state behavior through the installed
# archive. LayerCollection.exclude is inverted from the Outliner checkbox and
# is scoped independently to each View Layer occurrence.
collection = bpy.data.collections.new('TS_Installed "CMF" Ω')
scene.collection.children.link(collection)
primary_view_layer = scene.view_layers[0]
alternate_view_layer = scene.view_layers.new('TS_Installed "Alt" Ω')
primary_layer_collection = primary_view_layer.layer_collection.children[
    collection.name
]
alternate_layer_collection = alternate_view_layer.layer_collection.children[
    collection.name
]
primary_collection_path = engine.layer_collection_data_path(
    scene,
    primary_layer_collection,
)
alternate_collection_path = engine.layer_collection_data_path(
    scene,
    alternate_layer_collection,
)
require(
    primary_collection_path != alternate_collection_path,
    "Packaged collection paths did not distinguish View Layers",
)
require(
    scene.path_resolve(primary_collection_path) is False
    and scene.path_resolve(alternate_collection_path) is False,
    "Packaged escaped collection paths did not resolve to enabled states",
)
try:
    engine.layer_collection_data_path(
        scene,
        primary_view_layer.layer_collection,
    )
except engine.UnsupportedValueError:
    pass
else:
    raise AssertionError("Packaged helper accepted the View Layer root")

collection_take = engine.create_take(
    scene,
    "Installed Collection Disabled",
    parent_uuid=main_uuid,
    make_active=True,
)
collection_take_uuid = collection_take.uuid
engine.capture_override(
    scene,
    scene,
    primary_collection_path,
    collection_take_uuid,
)
primary_layer_collection.exclude = True
engine.capture_override(
    scene,
    scene,
    primary_collection_path,
    collection_take_uuid,
)
collection_override = engine.find_override(
    engine.find_take(scene, collection_take_uuid),
    scene,
    primary_collection_path,
)
require(
    collection_override is not None
    and module.ui._override_value_text(collection_override) == "Disabled",
    "Packaged override inspector did not translate exclude=True to Disabled",
)

collection_main_report = engine.apply_take(scene, main_uuid, strict=True)
require(
    not primary_layer_collection.exclude
    and not alternate_layer_collection.exclude,
    (
        "Packaged Main did not restore enabled collection states: "
        f"primary={bool(primary_layer_collection.exclude)!r}, "
        f"alternate={bool(alternate_layer_collection.exclude)!r}, "
        f"primary_path={bool(scene.path_resolve(primary_collection_path))!r}, "
        f"alternate_path={bool(scene.path_resolve(alternate_collection_path))!r}, "
        f"applied={collection_main_report.applied}, "
        f"skipped={collection_main_report.skipped}"
    ),
)
engine.apply_take(scene, collection_take_uuid, strict=True)
require(
    primary_layer_collection.exclude
    and not alternate_layer_collection.exclude,
    "Packaged take did not preserve per-View-Layer collection state",
)

# Renaming a path segment must fail atomically instead of rebinding by name.
original_collection_name = collection.name
collection.name = f"{original_collection_name} Renamed"
obj.location = (42.0, 43.0, 44.0)
primary_layer_collection.exclude = False
try:
    engine.apply_take(scene, collection_take_uuid, strict=True)
except engine.TakeApplyError:
    pass
else:
    raise AssertionError("Packaged strict apply accepted a stale collection path")
require(
    tuple(obj.location) == (42.0, 43.0, 44.0)
    and not primary_layer_collection.exclude,
    "Packaged stale collection path caused a partial strict apply",
)
collection.name = original_collection_name
engine.apply_take(scene, grandchild_uuid, strict=True)

# Exercise Phase 5 include filtering and transactional restoration from the
# installed archive without writing external files.
for candidate in scene.take_system.takes:
    candidate.include_in_render = False
installed_parent = engine.find_take(scene, take_uuid)
installed_grandchild = engine.find_take(scene, grandchild_uuid)
installed_parent.include_in_render = True
installed_grandchild.include_in_render = True
installed_parent.render_output_path = str(
    WORKSPACE / ".take_system_test" / "installed_parent"
)
installed_grandchild.render_output_path = str(
    WORKSPACE / ".take_system_test" / "installed_grandchild"
)
scene.render.resolution_x = 777
original_batch_camera = scene.camera
original_batch_path = scene.render.filepath
original_batch_active = scene.take_system.active_take_uuid
batch_seen = []


def record_installed_render(callback_scene, item):
    batch_seen.append(
        (
            item.take_uuid,
            callback_scene.camera,
            callback_scene.render.resolution_x,
            item.output_path,
        )
    )
    return {"FINISHED"}


batch_report = engine.render_take_batch(scene, record_installed_render)
require(
    batch_report.ok
    and [entry[0] for entry in batch_seen]
    == [take_uuid, grandchild_uuid],
    "Packaged Phase 5 batch include filtering/order failed",
)
require(
    all(
        camera == variant_camera and resolution_x == 640
        for _take_uuid, camera, resolution_x, _output in batch_seen
    ),
    "Packaged Phase 5 batch did not apply inherited camera/render settings",
)
require(
    scene.camera == original_batch_camera
    and scene.render.resolution_x == 777
    and scene.render.filepath == original_batch_path
    and scene.take_system.active_take_uuid == original_batch_active,
    "Packaged Phase 5 batch did not restore exact live state",
)

take_count = len(scene.take_system.takes)

result = bpy.ops.preferences.addon_disable(module="blender_take_system")
require(result == {"FINISHED"}, f"Add-on disable failed: {result}")
require(
    not hasattr(bpy.types.Scene, "take_system"),
    "Unregister left Scene settings attached",
)
require(
    module._take_system_load_post not in bpy.app.handlers.load_post
    and module._take_system_depsgraph_update_post
    not in bpy.app.handlers.depsgraph_update_post
    and module._take_system_undo_redo_post not in bpy.app.handlers.undo_post
    and module._take_system_undo_redo_post not in bpy.app.handlers.redo_post
    and module._take_system_frame_change_post
    not in bpy.app.handlers.frame_change_post
    and module._take_system_save_pre not in bpy.app.handlers.save_pre
    and not bpy.app.timers.is_registered(module._bootstrap_scenes_timer)
    and not bpy.app.timers.is_registered(
        module._take_system_recording_timer
    ),
    "Unregister left a lifecycle callback attached",
)
require(
    module.recent.runtime_state_count() == 0
    and module.recording.runtime_state_count() == 0
    and module.recording.message_bus_subscription_count() == 0,
    "Unregister left recent-action/recording runtime state behind",
)

# Updating an enabled add-on uses this disable/enable cycle. Verify that the
# Scene-owned RNA payload survives it before exercising the same path live.
result = bpy.ops.preferences.addon_enable(module="blender_take_system")
require(result == {"FINISHED"}, f"Add-on re-enable failed: {result}")
reloaded_module = sys.modules["blender_take_system"]
reloaded_module._bootstrap_scenes_timer()
require(
    len(scene.take_system.takes) == take_count
    and reloaded_module.engine.find_take(scene, take_uuid) is not None
    and reloaded_module.engine.find_take(scene, collection_take_uuid) is not None,
    "Disable/re-enable did not preserve take data",
)
require(
    [
        item.uuid
        for item in reloaded_module.engine.take_chain(scene, grandchild_uuid)
    ]
    == [
        reloaded_module.engine.ensure_main_take(scene).uuid,
        take_uuid,
        child_uuid,
        grandchild_uuid,
    ],
    "Disable/re-enable did not preserve the installed take hierarchy",
)
reloaded_module.engine.apply_take(scene, grandchild_uuid, strict=True)
require(
    tuple(obj.location) == (-1.0, -2.0, -3.0)
    and obj.hide_render
    and obj.material_slots[0].material == variant_material,
    "Re-enabled add-on could not apply the preserved deep take",
)
require(
    not primary_layer_collection.exclude
    and not alternate_layer_collection.exclude,
    "Re-enabled deep take did not inherit Main collection states",
)
reloaded_module.engine.apply_take(scene, collection_take_uuid, strict=True)
require(
    primary_layer_collection.exclude
    and not alternate_layer_collection.exclude,
    "Re-enabled add-on could not apply the preserved collection state",
)
result = bpy.ops.preferences.addon_disable(module="blender_take_system")
require(result == {"FINISHED"}, f"Final add-on disable failed: {result}")
print("TAKE_SYSTEM_INSTALL_OK")
