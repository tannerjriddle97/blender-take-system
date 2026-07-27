"""Headless regression tests for the Phase 3 Take Manager APIs."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators, ui


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def clear_scene():
    for datablock in tuple(bpy.data.objects):
        bpy.data.objects.remove(datablock, do_unlink=True)
    for datablock in tuple(bpy.data.meshes):
        if datablock.users == 0:
            bpy.data.meshes.remove(datablock)


def make_mesh_object(scene, name):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        (),
        ((0, 1, 2),),
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def take_index(scene, take_uuid):
    for index, take in enumerate(scene.take_system.takes):
        if take.uuid == take_uuid:
            return index
    raise AssertionError(f"Take is not in the scene collection: {take_uuid}")


def expect_hierarchy_error(callback, message):
    try:
        callback()
    except engine.TakeHierarchyError:
        return
    raise AssertionError(message)


def expect_operator_rejection(callback, expected_text, message):
    """Accept Blender's version-dependent CANCELLED/error-report behavior."""

    try:
        result = callback()
    except RuntimeError as exc:
        require(
            expected_text in str(exc),
            f"{message}: unexpected operator error: {exc}",
        )
        return
    require(result == {"CANCELLED"}, f"{message}: got {result}")


blender_take_system.register()
try:
    clear_scene()
    scene = bpy.context.scene
    state = scene.take_system
    main = engine.ensure_main_take(scene)
    main_uuid = main.uuid

    control = make_mesh_object(scene, "TS_UI_Control")
    control.hide_render = False

    # Store one inherited baseline and one child value so selection changes can
    # be distinguished from an actual take application.
    take_a = engine.create_take(
        scene,
        name="A",
        parent_uuid=main_uuid,
        make_active=True,
    )
    take_a_uuid = take_a.uuid
    engine.capture_override(scene, control, "hide_render", take_a_uuid)
    control.hide_render = True
    engine.capture_override(scene, control, "hide_render", take_a_uuid)
    require(control.hide_render, "A's test value was not set")

    internal_override_counts = tuple(
        len(take.overrides) for take in state.takes
    )
    try:
        engine.capture_override(
            scene,
            scene,
            "take_system.active_take_uuid",
            take_a_uuid,
        )
    except engine.TakePathError:
        pass
    else:
        raise AssertionError("Take System state was accepted as an override")
    require(
        tuple(len(take.overrides) for take in state.takes)
        == internal_override_counts,
        "Rejected self-referential capture mutated override records",
    )

    take_b = engine.create_take(
        scene,
        name="B",
        parent_uuid=main_uuid,
        make_active=False,
    )
    take_b_uuid = take_b.uuid
    state.active_take_index = take_index(scene, take_b_uuid)
    require(
        engine.selected_take(scene).uuid == take_b_uuid,
        "The UI selection did not resolve to B",
    )
    require(
        state.active_take_uuid == take_a_uuid,
        "Selecting B changed the applied take identity",
    )
    require(
        control.hide_render,
        "Selecting B changed live scene state without an apply operation",
    )

    result = bpy.ops.take_system.apply_selected_take("EXEC_DEFAULT")
    require(result == {"FINISHED"}, f"Apply-selected failed: {result}")
    require(
        state.active_take_uuid == take_b_uuid,
        "Apply-selected did not make B the applied take",
    )
    require(
        not control.hide_render,
        "Apply-selected did not resolve B's inherited Main value",
    )

    # Add Child is specifically driven by the manager selection, not by a
    # separately applied take UUID.
    state.active_take_index = take_index(scene, take_b_uuid)
    result = bpy.ops.take_system.add_take(
        "EXEC_DEFAULT",
        name="B Child",
        parent_mode="SELECTED",
    )
    require(result == {"FINISHED"}, f"Add-child operator failed: {result}")
    take_b_child = engine.active_take(scene)
    take_b_child_uuid = take_b_child.uuid
    require(
        take_b_child.parent_uuid == take_b_uuid,
        "Add Child did not use the UI-selected take as its parent",
    )

    # Add A's child after B's child. Collection order now differs from tree
    # order, which makes this a meaningful preorder regression.
    take_a_child = engine.create_take(
        scene,
        name="A Child",
        parent_uuid=take_a_uuid,
        make_active=False,
    )
    take_a_child_uuid = take_a_child.uuid
    rows = engine.take_hierarchy_rows(scene)
    require(
        [row.take.name for row in rows]
        == ["Main", "A", "A Child", "B", "B Child"],
        "Take Manager hierarchy is not stable depth-first preorder",
    )
    require(
        [row.depth for row in rows] == [0, 1, 2, 1, 2],
        "Take Manager hierarchy depths are incorrect",
    )
    require(
        all(not row.issue for row in rows),
        "A valid hierarchy was reported as damaged",
    )

    # Duplicating copies only the selected take's local records. Stable target
    # references remain shared while take and override identities are fresh.
    take_a = engine.find_take(scene, take_a_uuid)
    source_override = take_a.overrides[0]
    source_override_uuid = source_override.uuid
    source_ref_uuid = source_override.target_ref_uuid
    source_target_pointer = source_override.target_id.as_pointer()
    result = bpy.ops.take_system.duplicate_take(
        "EXEC_DEFAULT",
        take_uuid=take_a_uuid,
    )
    require(result == {"FINISHED"}, f"Duplicate operator failed: {result}")
    duplicate = engine.active_take(scene)
    duplicate_uuid = duplicate.uuid
    require(
        duplicate_uuid != take_a_uuid and duplicate.parent_uuid == main_uuid,
        "Duplicate identity or sibling parent is incorrect",
    )
    require(
        len(duplicate.overrides) == 1,
        "Duplicate did not copy exactly the source's direct override",
    )
    duplicate_override = duplicate.overrides[0]
    require(
        duplicate_override.uuid != source_override_uuid,
        "Duplicate reused the source override UUID",
    )
    require(
        duplicate_override.target_ref_uuid == source_ref_uuid,
        "Duplicate changed the stable target reference",
    )
    require(
        duplicate_override.target_id.as_pointer() == source_target_pointer,
        "Duplicate did not preserve the referenced target datablock",
    )
    require(
        not any(take.parent_uuid == duplicate_uuid for take in state.takes),
        "Duplicate unexpectedly copied the source's child hierarchy",
    )
    require(
        control.hide_render,
        "Duplicating and applying A did not restore A's stored value",
    )

    # A strict apply failure during duplication must remove the partial copy
    # and preserve a manager selection that differs from the applied take.
    state.active_take_index = take_index(scene, take_b_uuid)
    failed_duplicate_active_uuid = state.active_take_uuid
    failed_duplicate_selected_uuid = engine.selected_take(scene).uuid
    failed_duplicate_take_count = len(state.takes)
    source_override.data_path = "definitely_missing_property"
    try:
        engine.duplicate_take(scene, take_a_uuid, make_active=True)
    except engine.TakeSystemError:
        pass
    else:
        raise AssertionError("Broken duplicate unexpectedly applied")
    finally:
        source_override.data_path = "hide_render"
    require(
        len(state.takes) == failed_duplicate_take_count,
        "Failed duplication left a partial take behind",
    )
    require(
        state.active_take_uuid == failed_duplicate_active_uuid,
        "Failed duplication changed the applied take",
    )
    require(
        engine.selected_take(scene).uuid == failed_duplicate_selected_uuid,
        "Failed duplication overwrote the independent UI selection",
    )

    # Exercise a valid UI reparent operation, then the engine-level constraints
    # used by both the operator dialog and mutation API.
    result = bpy.ops.take_system.reparent_take(
        "EXEC_DEFAULT",
        take_uuid=take_a_child_uuid,
        parent_uuid=take_b_uuid,
    )
    require(result == {"FINISHED"}, f"Reparent operator failed: {result}")
    require(
        engine.find_take(scene, take_a_child_uuid).parent_uuid == take_b_uuid,
        "Valid reparent did not update the parent UUID",
    )
    expect_hierarchy_error(
        lambda: engine.reparent_take(scene, take_b_uuid, take_a_child_uuid),
        "Reparenting a take below its descendant was accepted",
    )
    expect_hierarchy_error(
        lambda: engine.reparent_take(scene, take_a_uuid, take_a_uuid),
        "Self-parenting was accepted",
    )
    expect_hierarchy_error(
        lambda: engine.reparent_take(scene, main_uuid, take_a_uuid),
        "Reparenting Main was accepted",
    )
    require(
        engine.find_take(scene, take_b_uuid).parent_uuid == main_uuid,
        "Rejected reparent operation mutated B",
    )
    require(
        engine.find_take(scene, main_uuid).parent_uuid == "",
        "Rejected reparent operation mutated Main",
    )

    # Deletion adopts direct children into the deleted take's parent.
    delete_parent = engine.create_take(
        scene,
        name="Delete Parent",
        parent_uuid=take_a_uuid,
        make_active=False,
    )
    delete_parent_uuid = delete_parent.uuid
    delete_child = engine.create_take(
        scene,
        name="Delete Child",
        parent_uuid=delete_parent_uuid,
        make_active=False,
    )
    delete_child_uuid = delete_child.uuid
    result = bpy.ops.take_system.delete_take(
        "EXEC_DEFAULT",
        take_uuid=delete_parent_uuid,
    )
    require(result == {"FINISHED"}, f"Delete operator failed: {result}")
    require(
        engine.find_take(scene, delete_parent_uuid) is None,
        "Delete operator left the requested take in the collection",
    )
    require(
        engine.find_take(scene, delete_child_uuid).parent_uuid == take_a_uuid,
        "Deleted take's child was not adopted by the surviving parent",
    )

    # Main baselines cannot disappear while any child has the same stable
    # reference. Removing an active local override must immediately reapply the
    # inherited value.
    main = engine.find_take(scene, main_uuid)
    take_a = engine.find_take(scene, take_a_uuid)
    duplicate = engine.find_take(scene, duplicate_uuid)
    main_override_uuid = main.overrides[0].uuid
    take_a_override_uuid = take_a.overrides[0].uuid
    duplicate_override_uuid = duplicate.overrides[0].uuid

    expect_operator_rejection(
        lambda: bpy.ops.take_system.remove_override(
            "EXEC_DEFAULT",
            take_uuid=main_uuid,
            override_uuid=main_override_uuid,
        ),
        "Remove descendant overrides",
        "Main baseline removal was not blocked while descendants use it",
    )
    require(len(main.overrides) == 1, "Blocked Main removal changed its records")

    state.active_override_index = 99
    result = bpy.ops.take_system.remove_override(
        "EXEC_DEFAULT",
        take_uuid=duplicate_uuid,
        override_uuid=duplicate_override_uuid,
    )
    require(result == {"FINISHED"}, f"Active override removal failed: {result}")
    require(
        state.active_take_uuid == duplicate_uuid,
        "Removing an override changed the active take identity",
    )
    require(
        not control.hide_render,
        "Removing the active local override did not reapply Main inheritance",
    )
    require(
        state.active_override_index == 0,
        "Override selection index was not clamped after removal",
    )

    expect_operator_rejection(
        lambda: bpy.ops.take_system.remove_override(
            "EXEC_DEFAULT",
            take_uuid=main_uuid,
            override_uuid=main_override_uuid,
        ),
        "Remove descendant overrides",
        "Main baseline removal ignored a remaining descendant override",
    )
    result = bpy.ops.take_system.remove_override(
        "EXEC_DEFAULT",
        take_uuid=take_a_uuid,
        override_uuid=take_a_override_uuid,
    )
    require(result == {"FINISHED"}, f"Child override removal failed: {result}")
    result = bpy.ops.take_system.remove_override(
        "EXEC_DEFAULT",
        take_uuid=main_uuid,
        override_uuid=main_override_uuid,
    )
    require(
        result == {"FINISHED"},
        f"Unreferenced Main baseline removal failed: {result}",
    )
    require(
        len(main.overrides) == 0,
        "Unreferenced Main baseline remained after removal",
    )

    # Headless registration and metadata checks catch packaging/registration
    # regressions without depending on a visible Blender window.
    for ui_class in (
        ui.TS_UL_takes,
        ui.TS_UL_overrides,
        ui.TS_PT_take_master_render,
        ui.TS_PT_take_manager,
        ui.TS_PT_take_scene_settings,
        ui.TS_PT_take_capture_changes,
        ui.TS_PT_take_batch_render,
        ui.TS_PT_take_overrides,
    ):
        require(
            ui_class.is_registered,
            f"{ui_class.__name__} is not registered",
        )
    require(
        ui.TS_PT_take_manager.bl_space_type == "PROPERTIES"
        and ui.TS_PT_take_manager.bl_region_type == "WINDOW"
        and ui.TS_PT_take_manager.bl_context == "scene",
        "Take Manager is not registered in Properties > Scene",
    )
    require(
        ui.TS_PT_take_master_render.bl_order
        < ui.TS_PT_take_manager.bl_order
        < ui.TS_PT_take_scene_settings.bl_order
        < ui.TS_PT_take_capture_changes.bl_order
        < ui.TS_PT_take_batch_render.bl_order
        < ui.TS_PT_take_overrides.bl_order,
        "Take System panels do not follow the intended task hierarchy",
    )
    for panel in (
        ui.TS_PT_take_scene_settings,
        ui.TS_PT_take_capture_changes,
        ui.TS_PT_take_batch_render,
        ui.TS_PT_take_overrides,
    ):
        require(
            "DEFAULT_CLOSED" in panel.bl_options,
            f"{panel.__name__} does not use progressive disclosure",
        )
    for operator_class in (
        operators.TS_OT_apply_selected_take,
        operators.TS_OT_duplicate_take,
        operators.TS_OT_delete_take,
        operators.TS_OT_reparent_take,
        operators.TS_OT_remove_override,
        operators.TS_OT_open_manager,
        operators.TS_OT_configure_take_camera,
        operators.TS_OT_clear_take_camera,
        operators.TS_OT_edit_render_profile,
        operators.TS_OT_capture_render_settings,
        operators.TS_OT_clear_render_settings,
        operators.TS_OT_set_batch_inclusion,
        operators.TS_OT_preflight_batch,
        operators.TS_OT_render_included_takes,
    ):
        require(
            operator_class.is_registered,
            f"{operator_class.__name__} is not registered",
        )

    print("TAKE_SYSTEM_UI_OK")
finally:
    blender_take_system.unregister()
