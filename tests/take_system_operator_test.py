"""Headless smoke test for the Phase 1/2 Blender operators."""

import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators


def require(condition, message):
    if not condition:
        raise AssertionError(message)


blender_take_system.register()
try:
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)

    mesh = bpy.data.meshes.new("TS_OperatorMesh")
    mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new("TS_OperatorObject", mesh)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.hide_render = False

    result = bpy.ops.take_system.add_take(
        "EXEC_DEFAULT",
        name="Operator Take",
        parent_mode="MAIN",
    )
    require(result == {"FINISHED"}, f"Add operator failed: {result}")
    take = engine.active_take(scene)
    require(
        take.name == "Operator Take" and take.parent_uuid == main.uuid,
        "Add operator produced the wrong take",
    )

    result = bpy.ops.take_system.capture_path_override(
        "EXEC_DEFAULT",
        target_id_type="Object",
        target_id_name=obj.name,
        data_path="hide_render",
    )
    require(result == {"FINISHED"}, f"Initial capture operator failed: {result}")
    obj.hide_render = True
    result = bpy.ops.take_system.capture_path_override(
        "EXEC_DEFAULT",
        target_id_type="Object",
        target_id_name=obj.name,
        data_path="hide_render",
    )
    require(result == {"FINISHED"}, f"Recapture operator failed: {result}")
    require(
        len(take.overrides) == 1 and len(main.overrides) == 1,
        "Capture operator did not upsert/Main-seed correctly",
    )

    result = bpy.ops.take_system.apply_take(
        "EXEC_DEFAULT",
        take_uuid=main.uuid,
    )
    require(result == {"FINISHED"}, f"Main operator apply failed: {result}")
    require(not obj.hide_render, "Main operator apply did not restore base state")

    result = bpy.ops.take_system.apply_take(
        "EXEC_DEFAULT",
        take_uuid=take.uuid,
    )
    require(result == {"FINISHED"}, f"Child operator apply failed: {result}")
    require(obj.hide_render, "Child operator apply did not restore variant state")

    result = bpy.ops.take_system.apply_active_take("EXEC_DEFAULT")
    require(result == {"FINISHED"}, f"Active apply operator failed: {result}")

    linked_scene = SimpleNamespace(
        take_system=object(),
        library=object(),
        override_library=None,
        is_editable=False,
    )
    linked_context = SimpleNamespace(
        scene=linked_scene,
        button_pointer=None,
        button_prop=None,
    )
    for operator_class in (
        operators.TS_OT_initialize,
        operators.TS_OT_add_take,
        operators.TS_OT_apply_take,
        operators.TS_OT_apply_active_take,
        operators.TS_OT_configure_take_camera,
        operators.TS_OT_clear_take_camera,
        operators.TS_OT_capture_render_settings,
        operators.TS_OT_clear_render_settings,
        operators.TS_OT_render_included_takes,
        operators.TS_OT_capture_button_override,
        operators.TS_OT_capture_path_override,
    ):
        require(
            not operator_class.poll(linked_context),
            f"{operator_class.__name__} accepted a linked read-only scene",
        )
    print("TAKE_SYSTEM_OPERATOR_OK")
finally:
    blender_take_system.unregister()
