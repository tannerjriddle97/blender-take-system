"""Phase 4 regression coverage for hierarchical take resolution."""

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


def require_vector(actual, expected, message, tolerance=1e-5):
    actual_values = tuple(actual)
    require(
        len(actual_values) == len(expected)
        and all(
            math.isclose(
                float(actual_component),
                float(expected_component),
                abs_tol=tolerance,
            )
            for actual_component, expected_component in zip(
                actual_values,
                expected,
            )
        ),
        f"{message}: got {actual_values!r}, expected {expected!r}",
    )


def clear_scene():
    for datablock in tuple(bpy.data.objects):
        bpy.data.objects.remove(datablock, do_unlink=True)
    for datablock in tuple(bpy.data.meshes):
        if datablock.users == 0:
            bpy.data.meshes.remove(datablock)


def make_object(scene, name):
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


def capture_value(scene, take, target, data_path, value):
    """Write and capture a local value after Main has been baselined."""

    engine.write_path_value(target, data_path, value)
    result = engine.capture_override(scene, target, data_path, take.uuid)
    require(
        not result.main_seeded,
        f"Phase 4 fixture unexpectedly seeded Main for {data_path}",
    )
    return result.override


def resolved_owner(scene, take, target, data_path):
    matches = [
        entry
        for entry in engine.resolve_take(scene, take.uuid).values()
        if entry.override.data_path == data_path
        and entry.override.target_id == target
    ]
    require(
        len(matches) == 1,
        f"Expected one resolved entry for {target.name_full}.{data_path}, "
        f"got {len(matches)}",
    )
    return matches[0].take_uuid


def apply_strict(scene, take):
    resolved = engine.resolve_take(scene, take.uuid)
    report = engine.apply_take(scene, take.uuid, strict=True)
    require(report.ok, f"Strict apply reported issues for {take.name}")
    require(report.skipped == 0, f"Strict apply skipped an entry for {take.name}")
    require(
        report.applied == len(resolved),
        f"Strict apply count did not match resolution for {take.name}",
    )
    require(
        scene.take_system.active_take_uuid == take.uuid,
        f"Strict apply did not make {take.name} active",
    )
    return report


def assert_a_branch(subject, control, expected_location, message):
    require_vector(subject.location, expected_location, f"{message}: location")
    require_vector(subject.scale, (2.0, 2.0, 2.0), f"{message}: scale")
    require_vector(
        subject.rotation_euler,
        (0.1, 0.2, 0.3),
        f"{message}: rotation",
    )
    require(subject["finish"] == "A-DETAIL", f"{message}: finish")
    require_vector(
        control.location,
        (10.0, 0.0, 0.0),
        f"{message}: control location",
    )


def assert_b_branch(subject, control, message, hero_local=False):
    require_vector(subject.location, (-1.0, 0.0, 0.0), f"{message}: location")
    require_vector(subject.scale, (0.5, 0.5, 0.5), f"{message}: scale")
    require_vector(
        subject.rotation_euler,
        (-0.4, 0.0, 0.0),
        f"{message}: rotation",
    )
    require(subject["finish"] == "B", f"{message}: finish")
    require_vector(
        control.location,
        (-10.0, 0.0, 0.0),
        f"{message}: control location",
    )
    require(
        subject.hide_viewport is hero_local,
        f"{message}: Hero visibility local was not resolved correctly",
    )
    require(
        subject.display_type == ("WIRE" if hero_local else "TEXTURED"),
        f"{message}: Hero display local was not resolved correctly",
    )


registered = False
blender_take_system.register()
registered = True
try:
    clear_scene()
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    subject = make_object(scene, "TS_Phase4_Subject")
    control = make_object(scene, "TS_Phase4_Control")

    subject.location = (0.0, 0.0, 0.0)
    subject.scale = (1.0, 1.0, 1.0)
    subject.rotation_euler = (0.0, 0.0, 0.0)
    subject.hide_viewport = False
    subject.display_type = "TEXTURED"
    subject["finish"] = "MAIN"
    control.location = (0.0, 0.0, 0.0)

    baseline_paths = (
        (subject, "location"),
        (subject, "scale"),
        (subject, "rotation_euler"),
        (subject, "hide_viewport"),
        (subject, "display_type"),
        (subject, '["finish"]'),
        (control, "location"),
    )
    for target, data_path in baseline_paths:
        result = engine.capture_override(scene, target, data_path, main.uuid)
        require(
            result.created and not result.main_seeded,
            f"Main baseline was not created exactly once for {data_path}",
        )

    # Main -> Branch A -> Detail A -> Hero A is the required 3+ level
    # inheritance chain. Location deliberately conflicts at every child depth.
    branch_a = engine.create_take(
        scene,
        "Branch A",
        parent_uuid=main.uuid,
        make_active=True,
    )
    capture_value(scene, branch_a, subject, "location", (1.0, 0.0, 0.0))
    capture_value(
        scene,
        branch_a,
        subject,
        "rotation_euler",
        (0.1, 0.2, 0.3),
    )
    capture_value(scene, branch_a, subject, '["finish"]', "A")
    capture_value(
        scene,
        branch_a,
        control,
        "location",
        (10.0, 0.0, 0.0),
    )

    detail_a = engine.create_take(
        scene,
        "Detail A",
        parent_uuid=branch_a.uuid,
        make_active=True,
    )
    capture_value(scene, detail_a, subject, "location", (2.0, 0.0, 0.0))
    capture_value(scene, detail_a, subject, "scale", (2.0, 2.0, 2.0))
    capture_value(scene, detail_a, subject, '["finish"]', "A-DETAIL")

    hero_a = engine.create_take(
        scene,
        "Hero A",
        parent_uuid=detail_a.uuid,
        make_active=True,
    )
    hero_location = capture_value(
        scene,
        hero_a,
        subject,
        "location",
        (3.0, 0.0, 0.0),
    )
    capture_value(scene, hero_a, subject, "hide_viewport", True)
    capture_value(
        scene,
        hero_a,
        subject,
        "display_type",
        "WIRE",
    )

    # A separate two-level branch proves that values from the first branch do
    # not survive merely because the destination branch omits those paths.
    branch_b = engine.create_take(
        scene,
        "Branch B",
        parent_uuid=main.uuid,
        make_active=True,
    )
    capture_value(scene, branch_b, subject, "location", (-1.0, 0.0, 0.0))
    capture_value(
        scene,
        branch_b,
        subject,
        "rotation_euler",
        (-0.4, 0.0, 0.0),
    )
    capture_value(scene, branch_b, subject, '["finish"]', "B")

    detail_b = engine.create_take(
        scene,
        "Detail B",
        parent_uuid=branch_b.uuid,
        make_active=True,
    )
    capture_value(scene, detail_b, subject, "scale", (0.5, 0.5, 0.5))
    capture_value(
        scene,
        detail_b,
        control,
        "location",
        (-10.0, 0.0, 0.0),
    )

    hero_report = apply_strict(scene, hero_a)
    assert_a_branch(
        subject,
        control,
        (3.0, 0.0, 0.0),
        "Four-level deepest-wins apply",
    )
    require(subject.hide_viewport, "Hero local visibility did not apply")
    require(subject.display_type == "WIRE", "Hero local enum did not apply")
    require(
        resolved_owner(scene, hero_a, subject, "location") == hero_a.uuid,
        "Deepest conflicting location was not owned by Hero",
    )
    require(
        resolved_owner(scene, hero_a, subject, "scale") == detail_a.uuid,
        "Detail scale was not inherited by Hero",
    )
    require(
        resolved_owner(scene, hero_a, subject, "rotation_euler")
        == branch_a.uuid,
        "Branch rotation was not inherited by Hero",
    )
    require(
        resolved_owner(scene, hero_a, subject, '["finish"]')
        == detail_a.uuid,
        "Deepest finish override did not win",
    )

    # Removing the deepest conflict must expose its nearest ancestor, including
    # immediately in the live scene because the removed record affects active.
    engine.remove_override(scene, hero_a.uuid, hero_location.uuid)
    require(
        engine.find_override(hero_a, subject, "location") is None,
        "Hero location record survived removal",
    )
    require_vector(
        subject.location,
        (2.0, 0.0, 0.0),
        "Removing active Hero override did not fall back immediately",
    )
    require(
        resolved_owner(scene, hero_a, subject, "location") == detail_a.uuid,
        "Removed Hero location did not resolve from Detail A",
    )
    apply_strict(scene, hero_a)
    assert_a_branch(
        subject,
        control,
        (2.0, 0.0, 0.0),
        "Fallback strict apply",
    )

    # Repeated branch switches check both directions for stale live values.
    apply_strict(scene, detail_b)
    assert_b_branch(subject, control, "A-to-B switch")
    apply_strict(scene, hero_a)
    assert_a_branch(
        subject,
        control,
        (2.0, 0.0, 0.0),
        "B-to-A switch",
    )
    require(subject.hide_viewport, "B-to-A switch lost Hero visibility")
    require(subject.display_type == "WIRE", "B-to-A switch lost Hero display")

    # Reparenting an active leaf must immediately rebuild its ancestry while
    # preserving its own local records.
    engine.reparent_take(scene, hero_a.uuid, detail_b.uuid)
    require(
        hero_a.parent_uuid == detail_b.uuid,
        "Active Hero was not reparented to Detail B",
    )
    require(
        [take.uuid for take in engine.take_chain(scene, hero_a.uuid)]
        == [main.uuid, branch_b.uuid, detail_b.uuid, hero_a.uuid],
        "Reparented Hero chain is incorrect",
    )
    assert_b_branch(
        subject,
        control,
        "Active reparent to B",
        hero_local=True,
    )

    previous_branch_b_parent = branch_b.parent_uuid
    try:
        engine.reparent_take(scene, branch_b.uuid, hero_a.uuid)
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError("Reparent accepted a parent-to-descendant cycle")
    require(
        branch_b.parent_uuid == previous_branch_b_parent,
        "Rejected cycle changed Branch B parent",
    )
    assert_b_branch(
        subject,
        control,
        "Rejected cycle",
        hero_local=True,
    )

    # The public non-active creation path must not extend hierarchy corruption.
    # UI commands cannot create this state, but a damaged file or script can.
    orphan = scene.take_system.takes.add()
    orphan.uuid = engine.new_uuid()
    orphan.name = "Orphan Fixture"
    orphan.parent_uuid = engine.new_uuid()
    orphan.is_main = False
    take_count_with_orphan = len(scene.take_system.takes)
    try:
        engine.create_take(
            scene,
            "Rejected Orphan Child",
            parent_uuid=orphan.uuid,
            make_active=False,
        )
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError("Non-active creation extended an orphan branch")
    require(
        len(scene.take_system.takes) == take_count_with_orphan,
        "Rejected orphan child left a take record behind",
    )
    orphan_index = next(
        index
        for index, candidate in enumerate(scene.take_system.takes)
        if candidate.uuid == orphan.uuid
    )
    scene.take_system.takes.remove(orphan_index)

    engine.reparent_take(scene, hero_a.uuid, detail_a.uuid)
    require(
        [take.uuid for take in engine.take_chain(scene, hero_a.uuid)]
        == [main.uuid, branch_a.uuid, detail_a.uuid, hero_a.uuid],
        "Hero chain was not restored after reparenting back",
    )
    assert_a_branch(
        subject,
        control,
        (2.0, 0.0, 0.0),
        "Active reparent back to A",
    )
    require(subject.hide_viewport, "Reparent back lost Hero visibility")
    require(subject.display_type == "WIRE", "Reparent back lost Hero display")

    # A strict assignment failure after valid writes must roll every write back
    # and retain the previously active branch.
    apply_strict(scene, detail_b)
    original_write_path_value = engine.write_path_value

    def fail_hero_display(target_id, data_path, value):
        if (
            target_id == subject
            and data_path == "display_type"
            and value == "WIRE"
        ):
            raise engine.TakePathError("Forced Phase 4 strict-apply failure")
        return original_write_path_value(target_id, data_path, value)

    engine.write_path_value = fail_hero_display
    try:
        try:
            engine.apply_take(scene, hero_a.uuid, strict=True)
        except engine.TakeApplyError as exc:
            require(exc.report.applied == 0, "Strict failure reported partial apply")
            require(exc.report.skipped == 1, "Strict failure count is incorrect")
            require(
                len(exc.report.issues) == 1,
                "Strict failure did not return one structured issue",
            )
        else:
            raise AssertionError("Strict apply accepted the forced write failure")
    finally:
        engine.write_path_value = original_write_path_value
    require(
        scene.take_system.active_take_uuid == detail_b.uuid,
        "Failed strict apply changed active-take identity",
    )
    assert_b_branch(subject, control, "Strict rollback")

    restored_report = apply_strict(scene, hero_a)
    assert_a_branch(
        subject,
        control,
        (2.0, 0.0, 0.0),
        "Strict apply after repair",
    )
    require(subject.hide_viewport, "Repaired strict apply lost Hero visibility")
    require(subject.display_type == "WIRE", "Repaired strict apply lost Hero display")

    require(
        hero_report.applied == restored_report.applied,
        "Removing one deepest conflict changed total resolved-key count",
    )
    require(
        len(engine.take_chain(scene, hero_a.uuid)) == 4,
        "Final hierarchy is not four levels deep",
    )

    # Malformed identity tables must fail during resolution, before any live
    # property can be written. These states are not produced by the UI, but can
    # occur in damaged files, scripts, or future import/migration paths.
    hero_a_uuid = scene.take_system.active_take_uuid
    hero_a = engine.find_take(scene, hero_a_uuid)
    hero_visibility = engine.find_override(
        hero_a,
        subject,
        "hide_viewport",
    )
    hero_visibility_snapshot = engine._snapshot_override_record(
        hero_visibility
    )

    engine._append_override_snapshot(
        hero_a,
        hero_visibility_snapshot,
        fresh_uuid=True,
    )
    try:
        engine.resolve_take(scene, hero_a_uuid)
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError("Resolution accepted a duplicate logical key")
    hero_a.overrides.remove(len(hero_a.overrides) - 1)

    duplicate_live_path = engine._append_override_snapshot(
        hero_a,
        hero_visibility_snapshot,
        fresh_uuid=True,
    )
    duplicate_live_path.target_ref_uuid = engine.new_uuid()
    try:
        engine.resolve_take(scene, hero_a_uuid)
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError(
            "Resolution accepted divergent refs for one live target/path"
        )
    hero_a.overrides.remove(len(hero_a.overrides) - 1)

    subject.location = (77.0, 78.0, 79.0)
    control.hide_viewport = False
    active_before_corruption = scene.take_system.active_take_uuid
    location_before_corruption = tuple(subject.location)
    control_visibility_before_corruption = control.hide_viewport
    engine._set_target_metadata(hero_visibility, control)
    try:
        try:
            engine.apply_take(scene, hero_a_uuid, strict=True)
        except engine.TakeHierarchyError:
            pass
        else:
            raise AssertionError(
                "Apply accepted one stable ref/path targeting a different ID"
            )
        require(
            scene.take_system.active_take_uuid == active_before_corruption,
            "Identity validation failure changed active-take identity",
        )
        require_vector(
            subject.location,
            location_before_corruption,
            "Identity validation failure wrote an earlier property",
        )
        require(
            control.hide_viewport == control_visibility_before_corruption,
            "Identity validation failure wrote the mismatched target",
        )
    finally:
        engine._set_target_metadata(hero_visibility, subject)

    main = engine.ensure_main_take(scene)
    main_visibility = engine.find_override(
        main,
        subject,
        "hide_viewport",
    )
    main_visibility.target_id = None
    try:
        engine.resolve_take(scene, hero_a_uuid)
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError(
            "Resolution accepted a live child target over a missing Main target"
        )
    finally:
        engine._set_target_metadata(main_visibility, subject)

    hero_visibility.target_id = None
    try:
        engine.resolve_take(scene, hero_a_uuid)
    except engine.TakeHierarchyError:
        pass
    else:
        raise AssertionError(
            "Resolution accepted a missing child target over a live Main target"
        )
    finally:
        engine._set_target_metadata(hero_visibility, subject)

    print(
        "TAKE_SYSTEM_HIERARCHY_OK",
        {
            "depth": len(engine.take_chain(scene, hero_a.uuid)),
            "resolved": restored_report.applied,
            "branch_switches": 4,
            "reparents": 2,
            "strict_rollback": 1,
            "orphan_creation_rejected": 1,
            "identity_corruption_rejected": 5,
        },
    )
finally:
    if registered:
        blender_take_system.unregister()
