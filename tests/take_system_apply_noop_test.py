"""Focused regression coverage for no-op take application."""

import math
import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_vector(actual, expected, message):
    require(
        tuple(actual) == tuple(expected),
        f"{message}: got {tuple(actual)!r}, expected {tuple(expected)!r}",
    )


def make_mesh(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        (),
        ((0, 1, 2),),
    )
    mesh.update()
    return mesh


def make_object(scene, name, mesh=None):
    mesh = mesh or make_mesh(f"{name}_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def capture_child_value(scene, take, target, data_path, value):
    engine.capture_override(scene, target, data_path, take.uuid)
    engine.write_path_value(target, data_path, value)
    result = engine.capture_override(
        scene,
        target,
        data_path,
        take.uuid,
    )
    return result.override


def instrument_writes():
    original = engine.write_path_value
    calls = []

    def recording_write(target_id, data_path, value):
        calls.append((target_id, data_path, value))
        return original(target_id, data_path, value)

    engine.write_path_value = recording_write
    return original, calls


def add_corrupt_override(take, target, target_ref_uuid, data_path):
    override = take.overrides.add()
    override.uuid = engine.new_uuid()
    override.target_ref_uuid = target_ref_uuid
    override.data_path = data_path
    engine._set_target_metadata(override, target)
    override.prop_type = "FLOAT"
    override.rna_subtype = ""
    override.value_float = 1.0
    override.value_float_text = float(1.0).hex()
    return override


def collection_path(scene, view_layer, collection):
    for candidate_layer, layer_collection, owner_path in (
        engine.iter_layer_collection_paths(scene)
    ):
        if (
            candidate_layer == view_layer
            and layer_collection.collection == collection
        ):
            return (
                layer_collection,
                f"{owner_path}.exclude",
            )
    raise AssertionError(f"LayerCollection not found: {collection.name}")


def cleanup():
    for scene in tuple(bpy.data.scenes):
        if scene.name.startswith("TS_Noop"):
            bpy.data.scenes.remove(scene)
    for obj in tuple(bpy.data.objects):
        if obj.name.startswith("TS_Noop"):
            bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        if collection.name.startswith("TS_Noop"):
            bpy.data.collections.remove(collection)
    for mesh in tuple(bpy.data.meshes):
        if mesh.name.startswith("TS_Noop") and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for material in tuple(bpy.data.materials):
        if material.name.startswith("TS_Noop") and material.users == 0:
            bpy.data.materials.remove(material)


registered = False
try:
    blender_take_system.register()
    registered = True
    engine.clear_runtime_state()

    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    subject = make_object(scene, "TS_Noop_Subject")
    alternate_mesh = make_mesh("TS_Noop_AlternateMesh")

    state_collection = bpy.data.collections.new("TS_Noop_StateCollection")
    scene.collection.children.link(state_collection)
    layer_collection, exclude_path = collection_path(
        scene,
        scene.view_layers[0],
        state_collection,
    )

    child = engine.create_take(
        scene,
        "TS_Noop_Child",
        parent_uuid=main.uuid,
        make_active=True,
    )
    exact_main = float.fromhex("0x1.0000000000001p-3")
    exact_child = float.fromhex("0x1.0000000000002p-3")
    subject["ts_noop_exact_float"] = exact_main

    capture_child_value(
        scene,
        child,
        subject,
        "location",
        (1.25, -2.5, 3.75),
    )
    capture_child_value(
        scene,
        child,
        subject,
        "hide_render",
        True,
    )
    capture_child_value(
        scene,
        child,
        subject,
        '["ts_noop_exact_float"]',
        exact_child,
    )
    capture_child_value(
        scene,
        child,
        subject,
        "data",
        alternate_mesh,
    )
    capture_child_value(
        scene,
        child,
        scene,
        exclude_path,
        True,
    )

    resolved_child_count = len(engine.resolve_take(scene, child.uuid))
    require(resolved_child_count == 5, "Unexpected no-op fixture size")

    # Strict and repair application retain resolved-entry applied counts and the
    # active-take update, but no assignment means no tracker invalidation.
    scene.take_system.active_take_uuid = main.uuid
    strict_revision = engine.scene_mutation_revision(scene)
    strict_global_revision = engine.global_mutation_revision()
    original_write, calls = instrument_writes()
    try:
        strict_report = engine.apply_take(scene, child.uuid, strict=True)
    finally:
        engine.write_path_value = original_write
    require(not calls, f"Strict no-op apply wrote values: {calls!r}")
    require(
        strict_report.applied == resolved_child_count
        and strict_report.skipped == 0,
        "Strict no-op apply changed report-count semantics",
    )
    require(
        scene.take_system.active_take_uuid == child.uuid,
        "Strict no-op apply did not update the active take",
    )
    require(
        engine.scene_mutation_revision(scene) == strict_revision
        and engine.global_mutation_revision() == strict_global_revision,
        "Strict no-op apply invalidated mutation tracking",
    )

    scene.take_system.active_take_uuid = main.uuid
    repair_revision = engine.scene_mutation_revision(scene)
    repair_global_revision = engine.global_mutation_revision()
    original_write, calls = instrument_writes()
    try:
        repair_report = engine.apply_take(scene, child.uuid, strict=False)
    finally:
        engine.write_path_value = original_write
    require(not calls, f"Repair no-op apply wrote values: {calls!r}")
    require(
        repair_report.applied == resolved_child_count
        and repair_report.skipped == 0,
        "Repair no-op apply changed report-count semantics",
    )
    require(
        scene.take_system.active_take_uuid == child.uuid,
        "Repair no-op apply did not update the active take",
    )
    require(
        engine.scene_mutation_revision(scene) == repair_revision
        and engine.global_mutation_revision() == repair_global_revision,
        "Repair no-op apply invalidated mutation tracking",
    )
    require_vector(
        subject.location,
        (1.25, -2.5, 3.75),
        "Mathutils vector changed during no-op apply",
    )
    require(
        subject["ts_noop_exact_float"] == exact_child,
        "Exact custom float changed during no-op apply",
    )
    require(
        subject.data == alternate_mesh,
        "Pointer changed during no-op apply",
    )
    require(
        layer_collection.exclude is True,
        "LayerCollection.exclude changed during no-op apply",
    )

    # Equality is exact, not tolerant: a one-ULP custom-float difference writes.
    subject["ts_noop_exact_float"] = exact_main
    original_write, calls = instrument_writes()
    try:
        exact_report = engine.apply_take(scene, child.uuid, strict=True)
    finally:
        engine.write_path_value = original_write
    require(
        [
            path
            for _target, path, _value in calls
            if path == '["ts_noop_exact_float"]'
        ]
        == ['["ts_noop_exact_float"]'],
        "One-ULP custom-float difference was treated as a no-op",
    )
    require(
        exact_report.applied == resolved_child_count
        and subject["ts_noop_exact_float"] == exact_child,
        "Exact custom-float apply failed",
    )

    # A no-op entry preceding changed and failing entries must never enter the
    # rollback plan. Changed writes still roll back atomically.
    engine.apply_take(scene, main.uuid, strict=True)
    rollback_take = engine.create_take(
        scene,
        "TS_Noop_Rollback",
        parent_uuid=main.uuid,
        make_active=True,
    )
    capture_child_value(
        scene,
        rollback_take,
        subject,
        "show_name",
        True,
    )
    capture_child_value(
        scene,
        rollback_take,
        subject,
        "hide_render",
        True,
    )
    capture_child_value(
        scene,
        rollback_take,
        subject,
        "hide_viewport",
        True,
    )
    subject.show_name = True
    subject.hide_render = False
    subject.hide_viewport = False
    scene.take_system.active_take_uuid = main.uuid
    rollback_revision = engine.scene_mutation_revision(scene)
    original_write = engine.write_path_value
    calls = []

    def fail_late(target_id, data_path, value):
        calls.append((target_id, data_path, value))
        if (
            target_id == subject
            and data_path == "hide_viewport"
            and value is True
        ):
            raise engine.TakePathError("Intentional late no-op regression failure")
        return original_write(target_id, data_path, value)

    engine.write_path_value = fail_late
    try:
        try:
            engine.apply_take(scene, rollback_take.uuid, strict=True)
        except engine.TakeApplyError as exc:
            require(
                exc.report.applied == 0 and exc.report.skipped == 1,
                "Strict rollback report semantics changed",
            )
        else:
            raise AssertionError("Strict rollback fixture did not fail")
    finally:
        engine.write_path_value = original_write
    require(
        not any(path == "show_name" for _target, path, _value in calls),
        "No-op value was written or added to the rollback plan",
    )
    require(
        sum(path == "hide_render" for _target, path, _value in calls) == 2,
        "Changed value was not written and rolled back exactly once",
    )
    require(
        subject.show_name is True
        and subject.hide_render is False
        and subject.hide_viewport is False,
        "Strict failure did not restore the pre-apply state",
    )
    require(
        scene.take_system.active_take_uuid == main.uuid
        and engine.scene_mutation_revision(scene) == rollback_revision,
        "Strict failure changed active identity or mutation revision",
    )

    # Two Object-keyed Main records can alias one DATA-linked material slot.
    # Once the first record writes the shared slot, the second must be a no-op.
    engine.apply_take(scene, main.uuid, strict=True)
    shared_mesh = make_mesh("TS_Noop_SharedMesh")
    base_material = bpy.data.materials.new("TS_Noop_BaseMaterial")
    other_material = bpy.data.materials.new("TS_Noop_OtherMaterial")
    shared_mesh.materials.append(base_material)
    shared_a = make_object(scene, "TS_Noop_SharedA", shared_mesh)
    shared_b = make_object(scene, "TS_Noop_SharedB", shared_mesh)
    material_path = "material_slots[0].material"
    engine.capture_override(scene, shared_a, material_path, main.uuid)
    engine.capture_override(scene, shared_b, material_path, main.uuid)
    require(
        shared_a.material_slots[0].link == "DATA"
        and shared_b.material_slots[0].link == "DATA",
        "Shared material fixture is not DATA-linked",
    )
    shared_mesh.materials[0] = other_material
    original_write, calls = instrument_writes()
    try:
        alias_report = engine.apply_take(scene, main.uuid, strict=True)
    finally:
        engine.write_path_value = original_write
    alias_writes = [
        call
        for call in calls
        if call[0] in {shared_a, shared_b}
        and call[1] == material_path
    ]
    require(
        len(alias_writes) == 1,
        f"Shared DATA-slot aliases issued {len(alias_writes)} writes",
    )
    require(
        shared_a.active_material == base_material
        and shared_b.active_material == base_material,
        "Shared DATA-slot alias apply produced the wrong material",
    )
    require(
        alias_report.applied == len(engine.resolve_take(scene, main.uuid)),
        "Alias optimization changed applied-count semantics",
    )

    original_write, calls = instrument_writes()
    try:
        engine.apply_take(scene, main.uuid, strict=True)
    finally:
        engine.write_path_value = original_write
    require(not calls, f"Second Main apply was not fully no-op: {calls!r}")

    # Repair mode must retain structured errors while skipping valid no-op
    # writes. Strict preflight must still fail before any assignment.
    repair_take = engine.create_take(
        scene,
        "TS_Noop_Repair",
        parent_uuid=main.uuid,
        make_active=False,
    )
    malformed_path = "broken["
    malformed_ref = engine.new_uuid()
    add_corrupt_override(
        main,
        subject,
        malformed_ref,
        malformed_path,
    )
    add_corrupt_override(
        repair_take,
        subject,
        malformed_ref,
        malformed_path,
    )
    resolved_repair_count = len(engine.resolve_take(scene, repair_take.uuid))
    scene.take_system.active_take_uuid = main.uuid
    repair_revision = engine.scene_mutation_revision(scene)
    original_write, calls = instrument_writes()
    try:
        report = engine.apply_take(scene, repair_take.uuid, strict=False)
    finally:
        engine.write_path_value = original_write
    require(
        report.applied == resolved_repair_count - 1
        and report.skipped == 1
        and len(report.issues) == 1
        and "Unbalanced RNA path" in report.issues[0].message,
        "Repair no-op apply lost structured malformed-path diagnostics",
    )
    require(
        not calls,
        f"Repair no-op apply wrote before/around a broken path: {calls!r}",
    )
    require(
        scene.take_system.active_take_uuid == repair_take.uuid
        and engine.scene_mutation_revision(scene) == repair_revision,
        "Repair issue changed active/revision success semantics",
    )

    scene.take_system.active_take_uuid = main.uuid
    strict_revision = engine.scene_mutation_revision(scene)
    original_write, calls = instrument_writes()
    try:
        try:
            engine.apply_take(scene, repair_take.uuid, strict=True)
        except engine.TakeApplyError as exc:
            require(
                exc.report.applied == 0
                and exc.report.skipped == 1
                and len(exc.report.issues) == 1,
                "Strict malformed-path report semantics changed",
            )
        else:
            raise AssertionError("Strict preflight accepted malformed storage")
    finally:
        engine.write_path_value = original_write
    require(
        not calls,
        "Strict malformed-path preflight performed an assignment",
    )
    require(
        scene.take_system.active_take_uuid == main.uuid
        and engine.scene_mutation_revision(scene) == strict_revision,
        "Strict preflight failure changed active identity or revision",
    )

    # Python considers True == 1. The no-op predicate must not: a mismatched
    # stored scalar type still takes the assignment path.
    typed_scene = bpy.data.scenes.new("TS_Noop_TypedScene")
    typed_subject = make_object(typed_scene, "TS_Noop_TypedSubject")
    typed_subject["ts_noop_typed"] = True
    typed_main = engine.ensure_main_take(typed_scene)
    typed_override = engine.capture_override(
        typed_scene,
        typed_subject,
        '["ts_noop_typed"]',
        typed_main.uuid,
    ).override
    typed_override.prop_type = "INT"
    typed_override.value_int = 1
    original_write, calls = instrument_writes()
    try:
        typed_report = engine.apply_take(
            typed_scene,
            typed_main.uuid,
            strict=True,
        )
    finally:
        engine.write_path_value = original_write
    require(
        any(path == '["ts_noop_typed"]' for _target, path, _value in calls),
        "Cross-numeric bool/int equality bypassed assignment validation",
    )
    require(
        typed_report.applied == 1,
        "Typed custom-property apply changed applied-count semantics",
    )

    # Stored floating-point payloads are bit-exact. In particular, applying
    # -0.0 over +0.0 must not be skipped merely because Python says they are
    # numerically equal.
    edge_scene = bpy.data.scenes.new("TS_Noop_EdgeScene")
    edge_subject = make_object(edge_scene, "TS_Noop_EdgeSubject")
    edge_main = engine.ensure_main_take(edge_scene)
    signed_zero_path = '["ts_noop_signed_zero"]'
    edge_subject["ts_noop_signed_zero"] = -0.0
    engine.capture_override(
        edge_scene,
        edge_subject,
        signed_zero_path,
        edge_main.uuid,
    )
    edge_subject["ts_noop_signed_zero"] = 0.0
    original_write, calls = instrument_writes()
    try:
        engine.apply_take(edge_scene, edge_main.uuid, strict=True)
    finally:
        engine.write_path_value = original_write
    require(
        any(path == signed_zero_path for _target, path, _value in calls)
        and math.copysign(1.0, edge_subject["ts_noop_signed_zero"]) < 0.0
        and not engine._exact_normalized_values_equal(
            (0.0, 1.0),
            (-0.0, 1.0),
        ),
        "Signed zero was incorrectly treated as an exact no-op",
    )

    # Repair mode must attempt the valid stored assignment when the current
    # value cannot be represented by the override model. This preserves the
    # repair behavior while still skipping values it can prove are identical.
    unsupported_live_path = '["ts_noop_unsupported_live"]'
    edge_subject["ts_noop_unsupported_live"] = 3.5
    engine.capture_override(
        edge_scene,
        edge_subject,
        unsupported_live_path,
        edge_main.uuid,
    )
    edge_subject["ts_noop_unsupported_live"] = [1, 2, 3, 4, 5]
    original_write, calls = instrument_writes()
    try:
        unsupported_report = engine.apply_take(
            edge_scene,
            edge_main.uuid,
            strict=False,
        )
    finally:
        engine.write_path_value = original_write
    require(
        any(
            path == unsupported_live_path
            for _target, path, _value in calls
        )
        and unsupported_report.ok
        and edge_subject["ts_noop_unsupported_live"] == 3.5,
        "Repair mode did not overwrite an unsupported live value",
    )

    print(
        "TAKE_SYSTEM_APPLY_NOOP_TEST_OK",
        {
            "strict_noop": resolved_child_count,
            "repair_noop": resolved_child_count,
            "atomic_rollback": 1,
            "shared_alias_writes": len(alias_writes),
            "structured_issues": report.skipped,
            "signed_zero": True,
            "unsupported_live_repair": True,
        },
    )
finally:
    try:
        if registered:
            blender_take_system.unregister()
    finally:
        cleanup()
