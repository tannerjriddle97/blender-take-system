"""Headless regression tests for recent-action take capture."""

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators, recent, ui


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_vector(actual, expected, message, tolerance=1e-5):
    actual_values = tuple(actual)
    require(
        len(actual_values) == len(expected)
        and all(
            math.isclose(float(a), float(e), abs_tol=tolerance)
            for a, e in zip(actual_values, expected)
        ),
        f"{message}: got {actual_values!r}, expected {expected!r}",
    )


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


def stored_value(take, target_id, data_path):
    override = engine.find_override(take, target_id, data_path)
    require(
        override is not None,
        f"Missing override for {target_id.name_full}.{data_path}",
    )
    return engine.decoded_override_value(override)


def expect_operator_cancelled(callback, message):
    try:
        result = callback()
    except RuntimeError as exc:
        require(
            "poll() failed" not in str(exc),
            f"{message}: operator unexpectedly failed its poll: {exc}",
        )
        return
    require(result == {"CANCELLED"}, f"{message}: got {result}")


registered = False
blender_take_system.register()
registered = True
try:
    handler_bindings = (
        (
            blender_take_system._take_system_load_post,
            bpy.app.handlers.load_post,
            "load_post",
        ),
        (
            blender_take_system._take_system_depsgraph_update_post,
            bpy.app.handlers.depsgraph_update_post,
            "depsgraph_update_post",
        ),
        (
            blender_take_system._take_system_undo_redo_post,
            bpy.app.handlers.undo_post,
            "undo_post",
        ),
        (
            blender_take_system._take_system_undo_redo_post,
            bpy.app.handlers.redo_post,
            "redo_post",
        ),
        (
            blender_take_system._take_system_frame_change_post,
            bpy.app.handlers.frame_change_post,
            "frame_change_post",
        ),
    )
    for callback, handler_list, label in handler_bindings:
        require(callback in handler_list, f"{label} handler is not registered")
    require(
        bpy.app.timers.is_registered(
            blender_take_system._bootstrap_scenes_timer
        ),
        "Scene bootstrap timer is not registered",
    )

    for registered_class in (
        operators.TS_OT_capture_recent_action,
        ui.TS_PT_take_capture_changes,
    ):
        require(
            registered_class.is_registered,
            f"{registered_class.__name__} is not registered",
        )
    require(
        {"REGISTER", "UNDO"}.issubset(
            operators.TS_OT_capture_recent_action.bl_options
        ),
        "Recent-action capture is not one undoable operator transaction",
    )
    require(
        "take_system.capture_recent_action"
        in ui.TS_PT_take_capture_changes.draw.__code__.co_consts
        and "action_summary"
        in ui.TS_PT_take_capture_changes.draw.__code__.co_names,
        "Capture Changes does not expose recent-action status and capture",
    )

    clear_scene()
    recent.clear_all()
    scene = bpy.context.scene
    state = scene.take_system
    main = engine.ensure_main_take(scene)
    take = engine.create_take(
        scene,
        name="Recent Action",
        parent_uuid=main.uuid,
        make_active=True,
    )
    take_uuid = take.uuid

    seed_object = make_mesh_object(scene, "TS_Recent_Seed")
    delayed_object = make_mesh_object(scene, "TS_Recent_Delayed")
    grouped_object = make_mesh_object(scene, "TS_Recent_Grouped")
    operator_object = make_mesh_object(scene, "TS_Recent_Operator")
    frame_object = make_mesh_object(scene, "TS_Recent_Frame")
    stale_a = make_mesh_object(scene, "TS_Recent_Stale_A")
    stale_b = make_mesh_object(scene, "TS_Recent_Stale_B")

    # Blender 5.x stores Action F-Curves under
    # layers -> strips -> per-datablock channelbags. A shared Action must only
    # exclude the paths in the slot actually assigned to each datablock.
    animated_slot_a = make_mesh_object(scene, "TS_Recent_Animated_Slot_A")
    animated_slot_b = make_mesh_object(scene, "TS_Recent_Animated_Slot_B")
    layered_action = bpy.data.actions.new("TS_Recent_Layered_Action")
    slot_a = layered_action.slots.new("OBJECT", animated_slot_a.name)
    slot_b = layered_action.slots.new("OBJECT", animated_slot_b.name)
    layer = layered_action.layers.new("TS_Recent_Layer")
    keyframe_strip = layer.strips.new(type="KEYFRAME")
    slot_a_bag = keyframe_strip.channelbag(slot_a, ensure=True)
    slot_b_bag = keyframe_strip.channelbag(slot_b, ensure=True)
    slot_a_bag.fcurves.new(data_path="location", index=0)
    slot_b_bag.fcurves.new(data_path="scale", index=1)

    slot_a_data = animated_slot_a.animation_data_create()
    slot_a_data.action = layered_action
    slot_a_data.action_slot = slot_a
    slot_b_data = animated_slot_b.animation_data_create()
    slot_b_data.action = layered_action
    slot_b_data.action_slot = slot_b

    require(
        recent._is_animated_path(animated_slot_a, "location")
        and not recent._is_animated_path(animated_slot_a, "scale")
        and recent._is_animated_path(animated_slot_b, "scale")
        and not recent._is_animated_path(animated_slot_b, "location"),
        "Layered Action detection did not respect assigned channelbag slots",
    )
    require(
        recent._candidate_from_path(animated_slot_a, "location") is None
        and recent._candidate_from_path(animated_slot_a, "scale") is not None,
        "Layered Action paths were not excluded precisely from discovery",
    )

    # NLA clip Actions use the slot selected on each NlaStrip, independently
    # of AnimData.action. Scan NLA even while a track is muted so a dormant
    # animation cannot later overwrite a captured take property.
    animated_nla = make_mesh_object(scene, "TS_Recent_Animated_NLA")
    nla_action = bpy.data.actions.new("TS_Recent_NLA_Action")
    nla_slot = nla_action.slots.new("OBJECT", animated_nla.name)
    nla_other_slot = nla_action.slots.new("OBJECT", "UnusedSlot")
    nla_layer = nla_action.layers.new("TS_Recent_NLA_Layer")
    nla_keyframe_strip = nla_layer.strips.new(type="KEYFRAME")
    nla_bag = nla_keyframe_strip.channelbag(nla_slot, ensure=True)
    nla_other_bag = nla_keyframe_strip.channelbag(
        nla_other_slot,
        ensure=True,
    )
    nla_bag.fcurves.new(data_path="delta_location", index=2)
    nla_other_bag.fcurves.new(data_path="hide_render", index=0)

    nla_data = animated_nla.animation_data_create()
    nla_track = nla_data.nla_tracks.new()
    nla_strip = nla_track.strips.new("TS_Recent_NLA_Clip", 1, nla_action)
    nla_strip.action_slot = nla_slot
    nla_track.mute = True

    require(
        recent._is_animated_path(animated_nla, "delta_location")
        and not recent._is_animated_path(animated_nla, "hide_render"),
        "NLA Action detection missed its selected slot or scanned another slot",
    )
    require(
        recent._candidate_from_path(animated_nla, "delta_location") is None
        and recent._candidate_from_path(animated_nla, "hide_render")
        is not None,
        "NLA-animated paths were not excluded precisely from discovery",
    )

    animated_driver = make_mesh_object(scene, "TS_Recent_Animated_Driver")
    animated_driver.driver_add("hide_viewport")
    require(
        recent._is_animated_path(animated_driver, "hide_viewport")
        and recent._candidate_from_path(
            animated_driver,
            "hide_viewport",
        )
        is None,
        "Driver-backed paths were not excluded from discovery",
    )

    # A single observed action seeds Main from the trusted pre-action state
    # while storing the post-action value directly on the applied child.
    tracker = recent.rebaseline_scene(scene)
    require(
        tracker is not None and tracker.applied_take_uuid == take_uuid,
        "Recent tracker was not baselined against the applied child",
    )
    require(
        (recent._id_uid(animated_slot_a), "location")
        not in tracker.properties
        and (recent._id_uid(animated_slot_a), "scale")
        in tracker.properties
        and (recent._id_uid(animated_nla), "delta_location")
        not in tracker.properties
        and (recent._id_uid(animated_nla), "hide_render")
        in tracker.properties
        and (recent._id_uid(animated_driver), "hide_viewport")
        not in tracker.properties,
        "Tracker discovery retained an animated path or dropped a safe path",
    )
    seed_object.location = (1.0, 2.0, 3.0)
    action = recent.observe_scene(
        scene,
        now=10.0,
        force_all=True,
    )
    require(action is not None, "The location edit was not observed")
    action = recent.finalize_pending(scene, now=10.1)
    require(
        action is not None
        and action.finalized
        and len(action.changes) == 1,
        "The single-property action did not finalize correctly",
    )
    change = next(iter(action.changes.values()))
    require(
        change.target_id == seed_object
        and change.data_path == "location"
        and change.baseline.value == (0.0, 0.0, 0.0)
        and change.after.value == (1.0, 2.0, 3.0),
        "The tracked location transition lost its baseline or final value",
    )

    report = recent.capture_pending(scene, take_uuid)
    require(
        report.captured == 1
        and report.created == 1
        and report.main_seeded == 1,
        f"Unexpected baseline-seeding report: {report}",
    )
    require_vector(
        stored_value(main, seed_object, "location"),
        (0.0, 0.0, 0.0),
        "Main did not retain the trusted pre-action location",
    )
    require_vector(
        stored_value(take, seed_object, "location"),
        (1.0, 2.0, 3.0),
        "The child did not retain the post-action location",
    )
    require(
        recent.peek_recent_action(scene) is None,
        "A successfully captured action was not consumed",
    )

    # Repeating the same property updates the existing child record and leaves
    # the original Main baseline untouched.
    main_count = len(main.overrides)
    take_count = len(take.overrides)
    recent.rebaseline_scene(scene)
    seed_object.location = (4.0, 5.0, 6.0)
    recent.observe_scene(scene, now=20.0, force_all=True)
    recent.finalize_pending(scene, now=20.1)
    report = recent.capture_pending(scene, take_uuid)
    require(
        report.captured == 1
        and report.created == 0
        and report.main_seeded == 0,
        f"Existing override was not updated in place: {report}",
    )
    require(
        len(main.overrides) == main_count
        and len(take.overrides) == take_count,
        "Updating an existing override duplicated stored records",
    )
    require_vector(
        stored_value(main, seed_object, "location"),
        (0.0, 0.0, 0.0),
        "Updating the child rewrote Main's baseline",
    )
    require_vector(
        stored_value(take, seed_object, "location"),
        (4.0, 5.0, 6.0),
        "Updating the child did not store the newest value",
    )

    # If an earlier action was not captured, a later action on the same path
    # must still seed Main from the value present when the take was applied,
    # not from the intermediate value immediately before the latest edit.
    recent.rebaseline_scene(scene)
    delayed_object.location = (1.0, 0.0, 0.0)
    recent.observe_scene(scene, now=25.0, force_all=True)
    delayed_object.location = (2.0, 0.0, 0.0)
    action = recent.observe_scene(scene, now=26.0, force_all=True)
    action = recent.finalize_pending(scene, now=26.1)
    delayed_change = next(
        change
        for change in action.changes.values()
        if change.target_id == delayed_object
        and change.data_path == "location"
    )
    require(
        delayed_change.before.value == (1.0, 0.0, 0.0)
        and delayed_change.baseline.value == (0.0, 0.0, 0.0)
        and delayed_change.after.value == (2.0, 0.0, 0.0),
        "Latest-action grouping lost the take-apply baseline",
    )
    report = recent.capture_pending(scene, take_uuid)
    require(
        report.captured == 1 and report.main_seeded == 1,
        f"Delayed recent action capture is incorrect: {report}",
    )
    require_vector(
        stored_value(main, delayed_object, "location"),
        (0.0, 0.0, 0.0),
        "Delayed action seeded Main from an intermediate value",
    )
    require_vector(
        stored_value(take, delayed_object, "location"),
        (2.0, 0.0, 0.0),
        "Delayed action did not store its final child value",
    )

    # Transitions inside the grouping window remain one action. A property
    # returned to its starting value is removed from that action as net-zero.
    recent.rebaseline_scene(scene)
    grouped_object.location = (3.0, 2.0, 1.0)
    recent.observe_scene(scene, now=30.0, force_all=True)
    grouped_object.scale = (1.5, 2.0, 2.5)
    recent.observe_scene(scene, now=30.1, force_all=True)
    grouped_object.hide_render = True
    recent.observe_scene(scene, now=30.2, force_all=True)
    grouped_object.hide_render = False
    action = recent.observe_scene(scene, now=30.3, force_all=True)
    action = recent.finalize_pending(scene, now=30.31)
    require(
        action is not None
        and action.started_at == 30.0
        and len(action.changes) == 2,
        "Grouped transform action has the wrong lifetime or property count",
    )
    require(
        {
            change.data_path
            for change in action.changes.values()
            if change.target_id == grouped_object
        }
        == {"location", "scale"},
        "Grouped action did not retain exactly location and scale",
    )
    require(
        action.summary == "2 changed properties on 1 datablock",
        f"Grouped action summary is incorrect: {action.summary!r}",
    )
    report = recent.capture_pending(scene, take_uuid)
    require(
        report.captured == 2
        and report.created == 2
        and report.main_seeded == 2,
        f"Grouped transform capture report is incorrect: {report}",
    )
    require(
        engine.find_override(main, grouped_object, "hide_render") is None
        and engine.find_override(take, grouped_object, "hide_render") is None,
        "A net-zero property became an override",
    )
    require_vector(
        stored_value(main, grouped_object, "location"),
        (0.0, 0.0, 0.0),
        "Grouped Main location baseline is incorrect",
    )
    require_vector(
        stored_value(main, grouped_object, "scale"),
        (1.0, 1.0, 1.0),
        "Grouped Main scale baseline is incorrect",
    )
    require_vector(
        stored_value(take, grouped_object, "location"),
        (3.0, 2.0, 1.0),
        "Grouped child location is incorrect",
    )
    require_vector(
        stored_value(take, grouped_object, "scale"),
        (1.5, 2.0, 2.5),
        "Grouped child scale is incorrect",
    )

    # Operator guards require the same non-Main take to be both selected and
    # applied. A valid context with no pending action reaches execute and is
    # rejected there without mutating records.
    state.active_take_index = take_index(scene, main.uuid)
    require(
        not operators.TS_OT_capture_recent_action.poll(bpy.context),
        "Capture operator accepted selected Main over an applied child",
    )
    engine.apply_take(scene, main.uuid, strict=True)
    state.active_take_index = take_index(scene, take_uuid)
    require(
        not operators.TS_OT_capture_recent_action.poll(bpy.context),
        "Capture operator accepted different selected and applied takes",
    )
    state.active_take_index = take_index(scene, main.uuid)
    require(
        not operators.TS_OT_capture_recent_action.poll(bpy.context),
        "Capture operator accepted Main as its destination",
    )
    engine.apply_take(scene, take_uuid, strict=True)
    require(
        operators.TS_OT_capture_recent_action.poll(bpy.context),
        "Capture operator rejected a selected and applied child",
    )
    before_empty_capture = (len(main.overrides), len(take.overrides))
    expect_operator_cancelled(
        lambda: bpy.ops.take_system.capture_recent_action("EXEC_DEFAULT"),
        "Capture with no recent action was not cancelled",
    )
    require(
        (len(main.overrides), len(take.overrides))
        == before_empty_capture,
        "Empty operator capture mutated override records",
    )

    readonly_scene = SimpleNamespace(
        take_system=object(),
        library=object(),
        override_library=None,
        is_editable=False,
    )
    require(
        not operators.TS_OT_capture_recent_action.poll(
            SimpleNamespace(scene=readonly_scene)
        ),
        "Capture operator accepted a linked read-only scene",
    )

    # Exercise the registered Blender operator on a real pending action.
    recent.rebaseline_scene(scene)
    operator_object.hide_viewport = True
    recent.observe_scene(scene, now=40.0, force_all=True)
    recent.finalize_pending(scene, now=40.1)
    result = bpy.ops.take_system.capture_recent_action("EXEC_DEFAULT")
    require(result == {"FINISHED"}, f"Recent-action operator failed: {result}")
    require(
        stored_value(main, operator_object, "hide_viewport") is False
        and stored_value(take, operator_object, "hide_viewport") is True,
        "Recent-action operator stored the wrong baseline or child value",
    )

    # A stale batch must fail during preflight and leave every record intact,
    # even when an earlier entry in the batch was otherwise valid.
    stale_changes = (
        engine.OverrideChange(
            target_id=stale_a,
            data_path="hide_render",
            baseline_value=False,
            after_value=True,
        ),
        engine.OverrideChange(
            target_id=stale_b,
            data_path="hide_render",
            baseline_value=False,
            after_value=True,
        ),
    )
    stale_a.hide_render = True
    stale_b.hide_render = True
    stale_b.hide_render = False
    records_before_stale = (len(main.overrides), len(take.overrides))
    try:
        engine.capture_change_batch(
            scene,
            stale_changes,
            take_uuid=take_uuid,
        )
    except engine.TakePathError as exc:
        require(
            "changed again after the tracked action" in str(exc),
            f"Unexpected stale-action error: {exc}",
        )
    else:
        raise AssertionError("Stale recent-action values were accepted")
    require(
        (len(main.overrides), len(take.overrides))
        == records_before_stale,
        "Stale batch left partial override records",
    )
    for target_id in (stale_a, stale_b):
        require(
            engine.find_override(main, target_id, "hide_render") is None
            and engine.find_override(take, target_id, "hide_render") is None,
            "Stale batch stored one of its rejected properties",
        )

    # Force an exception after the first preflighted property is written. The
    # engine must restore both collections and preserve all live post-action
    # values as one atomic failure.
    stale_b.hide_render = True
    original_capture_values = engine._capture_override_values
    capture_call_count = [0]

    def fail_second_capture(*args, **kwargs):
        capture_call_count[0] += 1
        if capture_call_count[0] == 2:
            raise engine.TakePathError("Intentional late batch failure")
        return original_capture_values(*args, **kwargs)

    engine._capture_override_values = fail_second_capture
    try:
        try:
            engine.capture_change_batch(
                scene,
                stale_changes,
                take_uuid=take_uuid,
            )
        except engine.TakePathError as exc:
            require(
                "Intentional late batch failure" in str(exc),
                f"Unexpected late batch error: {exc}",
            )
        else:
            raise AssertionError("Forced late batch failure was accepted")
    finally:
        engine._capture_override_values = original_capture_values
    require(
        capture_call_count[0] == 2,
        "Late-failure fixture did not pass the first batch write",
    )
    require(
        (len(main.overrides), len(take.overrides))
        == records_before_stale,
        "Late batch failure did not roll back override collections",
    )
    require(
        stale_a.hide_render and stale_b.hide_render,
        "Late batch failure rewound a live post-action value",
    )
    for target_id in (stale_a, stale_b):
        require(
            engine.find_override(main, target_id, "hide_render") is None
            and engine.find_override(take, target_id, "hide_render") is None,
            "Late batch rollback retained a partial property",
        )

    # Frame changes must retain the discovered property table and synchronize
    # only cached values after evaluation settles. Re-running full discovery
    # on every frame made playback cost scale with every RNA property.
    frame_tracker = recent.rebaseline_scene(scene)
    frame_key = (recent._id_uid(frame_object), "location")
    require(
        frame_key in frame_tracker.properties,
        "Frame-sync fixture location was not discovered",
    )
    frame_object.location = (7.0, 8.0, 9.0)
    original_discover = recent._discover_scene_properties
    discovery_calls = [0]

    def count_discovery(*args, **kwargs):
        discovery_calls[0] += 1
        return original_discover(*args, **kwargs)

    recent._discover_scene_properties = count_discovery
    try:
        recent.defer_scene(scene, seconds=0.0)
        synchronized_tracker = recent.ensure_scene(scene)
    finally:
        recent._discover_scene_properties = original_discover
    require(
        synchronized_tracker is frame_tracker,
        "Frame sync discarded and rebuilt the scene tracker",
    )
    require(
        discovery_calls[0] == 0,
        "Frame sync repeated full property discovery",
    )
    require(
        frame_tracker.properties[frame_key].current.value
        == (7.0, 8.0, 9.0)
        and frame_tracker.recent_action is None,
        "Frame sync did not refresh cached values and clear pending actions",
    )

    # Take application can mutate shared datablocks. A tracker owned by a
    # different scene must invalidate against the global mutation revision,
    # otherwise that scene reports the add-on's write as the user's action.
    shared_material = bpy.data.materials.new("TS_Recent_Shared_Material")
    shared_material.diffuse_color = (0.1, 0.2, 0.3, 1.0)
    shared_owner = make_mesh_object(scene, "TS_Recent_Shared_Owner")
    shared_owner.data.materials.append(shared_material)
    engine.capture_override(
        scene,
        shared_material,
        "diffuse_color",
        take_uuid,
    )
    shared_material.diffuse_color = (0.8, 0.7, 0.6, 1.0)
    engine.capture_override(
        scene,
        shared_material,
        "diffuse_color",
        take_uuid,
    )
    engine.apply_take(scene, main.uuid, strict=True)

    sibling_scene = bpy.data.scenes.new("TS_Recent_Sibling")
    sibling_main = engine.ensure_main_take(sibling_scene)
    sibling_owner = make_mesh_object(
        sibling_scene,
        "TS_Recent_Shared_Sibling",
    )
    sibling_owner.data.materials.append(shared_material)
    recent.rebaseline_scene(scene)
    sibling_tracker = recent.rebaseline_scene(sibling_scene)
    global_revision_before = engine.global_mutation_revision()
    engine.apply_take(scene, take_uuid, strict=True)
    require(
        engine.global_mutation_revision() == global_revision_before + 1,
        "Take application did not advance the global mutation revision",
    )
    sibling_action = recent.observe_scene(
        sibling_scene,
        now=50.0,
        force_all=True,
    )
    refreshed_sibling_tracker = recent._TRACKERS[
        recent._scene_uid(sibling_scene)
    ]
    require(
        sibling_action is None
        and refreshed_sibling_tracker is not sibling_tracker
        and refreshed_sibling_tracker.recent_action is None,
        "A shared-ID take write became a false action in another scene",
    )
    require_vector(
        shared_material.diffuse_color,
        (0.8, 0.7, 0.6, 1.0),
        "Shared material fixture did not receive the applied child value",
    )

    # The recurring scene bootstrap also prunes runtime dictionaries. Deleted
    # scenes must not leave tracker, deferred, or mutation-revision entries.
    sibling_owner.hide_render = True
    engine.capture_override(
        sibling_scene,
        sibling_owner,
        "hide_render",
        sibling_main.uuid,
    )
    sibling_owner.hide_render = False
    engine.apply_take(sibling_scene, sibling_main.uuid, strict=True)
    recent.rebaseline_scene(sibling_scene)
    sibling_uid = recent._scene_uid(sibling_scene)
    sibling_revision_key = engine._scene_runtime_key(sibling_scene)
    recent.defer_scene(sibling_scene, seconds=10.0)
    require(
        sibling_uid in recent._TRACKERS
        and sibling_uid in recent._DEFERRED_UNTIL
        and sibling_revision_key in engine._SCENE_MUTATION_REVISIONS,
        "Deleted-scene pruning fixture did not populate runtime state",
    )
    bpy.data.scenes.remove(sibling_scene)
    recent.prune_runtime_state()
    require(
        sibling_uid not in recent._TRACKERS
        and sibling_uid not in recent._DEFERRED_UNTIL
        and sibling_revision_key not in engine._SCENE_MUTATION_REVISIONS,
        "Deleted scene left runtime tracker/revision state behind",
    )

    recent.rebaseline_scene(scene)
    require(
        recent.runtime_state_count() >= 1,
        "Recent-action runtime state was not retained while registered",
    )

    blender_take_system.unregister()
    registered = False
    for callback, handler_list, label in handler_bindings:
        require(
            callback not in handler_list,
            f"{label} handler remained after unregister",
        )
    require(
        not bpy.app.timers.is_registered(
            blender_take_system._bootstrap_scenes_timer
        ),
        "Scene bootstrap timer remained after unregister",
    )
    require(
        recent.runtime_state_count() == 0,
        "Recent-action runtime state remained after unregister",
    )
    require(
        not hasattr(bpy.types.Scene, "take_system"),
        "Scene.take_system remained after unregister",
    )
    for unregistered_class in (
        operators.TS_OT_capture_recent_action,
        ui.TS_PT_take_manager,
    ):
        require(
            not unregistered_class.is_registered,
            f"{unregistered_class.__name__} remained registered",
        )

    print(
        "TAKE_SYSTEM_RECENT_ACTION_OK",
        {
            "seeded": 1,
            "updated": 1,
            "grouped": 2,
            "operator": 1,
            "rollback_calls": capture_call_count[0],
            "animated_guards": 4,
        },
    )
finally:
    if registered:
        blender_take_system.unregister()
