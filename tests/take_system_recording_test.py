"""Headless regression tests for Phase 6 automatic take recording."""

import math
import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators, recent, recording, ui


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_vector(actual, expected, message, tolerance=1e-5):
    actual_values = tuple(actual)
    require(
        len(actual_values) == len(expected)
        and all(
            math.isclose(float(actual), float(wanted), abs_tol=tolerance)
            for actual, wanted in zip(actual_values, expected)
        ),
        f"{message}: got {actual_values!r}, expected {expected!r}",
    )


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


def direct_override_count(take, target_id, data_path):
    return sum(
        1
        for override in take.overrides
        if (
            override.target_id == target_id
            and override.data_path == data_path
        )
    )


registered = False
blender_take_system.register()
registered = True
try:
    require(
        bpy.app.timers.is_registered(
            blender_take_system._take_system_recording_timer
        ),
        "Automatic-recording timer is not registered",
    )
    require(
        recording.message_bus_subscription_count() > 0,
        "Automatic recording did not register message-bus subscriptions",
    )
    require(
        blender_take_system._take_system_save_pre
        in bpy.app.handlers.save_pre,
        "Automatic-recording save handler is not registered",
    )
    for registered_class in (
        operators.TS_OT_toggle_recording,
        operators.TS_OT_flush_recording,
    ):
        require(
            registered_class.is_registered,
            f"{registered_class.__name__} is not registered",
        )
        require(
            {"REGISTER", "UNDO"}.issubset(registered_class.bl_options),
            f"{registered_class.__name__} is not undoable",
        )
    require(
        "take_system.toggle_recording"
        in ui._draw_capture_controls.__code__.co_consts
        and "recording_status_text"
        in ui._draw_capture_controls.__code__.co_names,
        "Take Manager does not expose recording controls/status",
    )

    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    main_uuid = main.uuid
    take = engine.create_take(
        scene,
        "Automatic Recording",
        parent_uuid=main_uuid,
        make_active=True,
    )
    take_uuid = take.uuid
    inactive = engine.create_take(
        scene,
        "Inactive Recording Target",
        parent_uuid=main_uuid,
        make_active=False,
    )
    inactive_uuid = inactive.uuid
    state = scene.take_system
    state.active_take_index = take_index(scene, take_uuid)

    location_object = make_mesh_object(scene, "TS_Record_Location")
    grouped_object = make_mesh_object(scene, "TS_Record_Grouped")
    fallback_object = make_mesh_object(scene, "TS_Record_Msgbus")
    frame_object = make_mesh_object(scene, "TS_Record_Frame")
    failure_object = make_mesh_object(scene, "TS_Record_Failure")

    for invalid_uuid, expected_fragment in (
        (main_uuid, "non-Main"),
        (inactive_uuid, "applied"),
    ):
        try:
            recording.start(scene, invalid_uuid)
        except engine.TakeHierarchyError as exc:
            require(
                expected_fragment in str(exc),
                f"Unexpected recording eligibility error: {exc}",
            )
        else:
            raise AssertionError(
                f"Automatic recording accepted invalid take {invalid_uuid}"
            )

    started = recording.start(scene, take_uuid)
    require(
        started.take_uuid == take_uuid
        and started.state == "RECORDING"
        and engine.find_take(scene, take_uuid).is_recording,
        "Automatic recording did not start on the applied child",
    )
    require(
        not engine.find_take(scene, main_uuid).is_recording
        and not engine.find_take(scene, inactive_uuid).is_recording,
        "Starting one recorder left another take recording",
    )

    # One observed edit remains pending until the 0.45-second action-group
    # window closes, then seeds Main and writes the child without rewinding.
    location_object.location = (1.0, 2.0, 3.0)
    action = recent.observe_scene(scene, now=10.0, force_all=True)
    require(action is not None, "Location edit was not observed")
    require(
        recording.flush(scene, now=10.2) is None,
        "Automatic recording committed before the grouping window closed",
    )
    require(
        engine.find_override(
            engine.find_take(scene, take_uuid),
            location_object,
            "location",
        )
        is None,
        "Early recording flush created an override",
    )
    report = recording.flush(scene, now=10.6)
    require(
        report is not None
        and report.captured == 1
        and report.main_seeded == 1,
        f"Automatic recording did not commit the location edit: {report!r}",
    )
    main = engine.find_take(scene, main_uuid)
    take = engine.find_take(scene, take_uuid)
    require_vector(
        stored_value(main, location_object, "location"),
        (0.0, 0.0, 0.0),
        "Automatic recording stored the wrong Main location baseline",
    )
    require_vector(
        stored_value(take, location_object, "location"),
        (1.0, 2.0, 3.0),
        "Automatic recording stored the wrong child location",
    )
    require(
        recent.peek_recent_action(scene) is None,
        "Committed automatic action remained pending",
    )

    # Multiple properties inside one action window commit as one atomic batch.
    grouped_object.location = (3.0, 2.0, 1.0)
    recent.observe_scene(scene, now=20.0, force_all=True)
    grouped_object.scale = (1.5, 2.0, 2.5)
    recent.observe_scene(scene, now=20.1, force_all=True)
    require(
        recording.flush(scene, now=20.4) is None,
        "Grouped action committed before its last change settled",
    )
    grouped_report = recording.flush(scene, now=20.6)
    require(
        grouped_report is not None
        and grouped_report.captured == 2
        and grouped_report.main_seeded == 2,
        f"Grouped recording was not captured atomically: {grouped_report!r}",
    )
    main = engine.find_take(scene, main_uuid)
    take = engine.find_take(scene, take_uuid)
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

    # A later user action updates the same direct child record and preserves
    # the original Main baseline rather than duplicating either record.
    location_object.location = (-4.0, 5.0, -6.0)
    recent.observe_scene(scene, now=30.0, force_all=True)
    repeated_report = recording.flush(scene, now=30.6)
    require(
        repeated_report is not None
        and repeated_report.captured == 1
        and repeated_report.created == 0
        and repeated_report.main_seeded == 0,
        "Repeated automatic edit did not update the existing child record",
    )
    main = engine.find_take(scene, main_uuid)
    take = engine.find_take(scene, take_uuid)
    require(
        direct_override_count(main, location_object, "location") == 1
        and direct_override_count(take, location_object, "location") == 1,
        "Repeated automatic edit duplicated stored records",
    )
    require_vector(
        stored_value(main, location_object, "location"),
        (0.0, 0.0, 0.0),
        "Repeated edit changed the Main baseline",
    )
    require_vector(
        stored_value(take, location_object, "location"),
        (-4.0, 5.0, -6.0),
        "Repeated edit did not update the child value",
    )

    # Message bus is a low-cost signal. If Blender supplies no useful
    # depsgraph update, the timer performs one fallback observation and then
    # commits after the normal quiet window.
    fallback_object.hide_render = True
    recording._message_bus_notify(now=40.0)
    recording.tick((scene,), now=40.1)
    require(
        recent.peek_recent_action(scene) is not None,
        "Message-bus fallback did not observe a supported property edit",
    )
    recording.tick((scene,), now=40.7)
    require(
        stored_value(
            engine.find_take(scene, main_uuid),
            fallback_object,
            "hide_render",
        )
        is False
        and stored_value(
            engine.find_take(scene, take_uuid),
            fallback_object,
            "hide_render",
        )
        is True,
        "Message-bus fallback stored the wrong baseline or child value",
    )

    status = recording.status_for_scene(scene)
    require(
        status.state == "RECORDING"
        and status.captured_actions == 4
        and status.captured_properties == 5
        and not status.last_error,
        f"Recording status counters are wrong: {status!r}",
    )

    # Frame/evaluation changes are synchronized as new runtime current values,
    # not recorded as user actions.
    recording.handle_frame_change(scene, seconds=0.0)
    frame_object.location = (7.0, 8.0, 9.0)
    recent.ensure_scene(scene)
    require(
        recording.flush(scene, force=True) is None
        and recent.peek_recent_action(scene) is None
        and engine.find_take(scene, take_uuid).is_recording,
        "Frame evaluation became an automatic override or stopped recording",
    )

    # A capture failure is atomic and fails closed: no partial records, no
    # endless timer retries, and the record dot turns off with an error status.
    failure_object.hide_viewport = True
    recent.observe_scene(scene, now=50.0, force_all=True)
    original_capture_pending = recent.capture_pending

    def fail_capture(*_args, **_kwargs):
        raise engine.TakePathError("Intentional automatic-record failure")

    recent.capture_pending = fail_capture
    try:
        try:
            recording.flush(scene, now=50.6)
        except engine.TakePathError as exc:
            require(
                "Intentional automatic-record failure" in str(exc),
                f"Unexpected recording failure: {exc}",
            )
        else:
            raise AssertionError("Automatic recording accepted a failed batch")
    finally:
        recent.capture_pending = original_capture_pending
    require(
        not engine.find_take(scene, take_uuid).is_recording
        and engine.find_override(
            engine.find_take(scene, take_uuid),
            failure_object,
            "hide_viewport",
        )
        is None,
        "Failed automatic capture left recording active or stored a record",
    )
    failed_status = recording.status_for_scene(scene)
    require(
        failed_status.state == "ERROR"
        and "Intentional automatic-record failure" in failed_status.last_error,
        f"Automatic recording did not expose its failure: {failed_status!r}",
    )

    # The registered toggle operator starts/stops the applied take. Stopping
    # forces any pending action to commit before the record dot turns off.
    state.active_take_index = take_index(scene, take_uuid)
    toggle_result = bpy.ops.take_system.toggle_recording(
        "EXEC_DEFAULT",
        take_uuid=take_uuid,
    )
    require(
        toggle_result == {"FINISHED"}
        and engine.find_take(scene, take_uuid).is_recording,
        f"Record toggle operator did not start: {toggle_result}",
    )
    location_object.hide_render = True
    recent.observe_scene(scene, now=60.0, force_all=True)
    toggle_result = bpy.ops.take_system.toggle_recording(
        "EXEC_DEFAULT",
        take_uuid=take_uuid,
    )
    require(
        toggle_result == {"FINISHED"}
        and not engine.find_take(scene, take_uuid).is_recording
        and stored_value(
            engine.find_take(scene, take_uuid),
            location_object,
            "hide_render",
        )
        is True,
        "Stopping the recorder discarded its pending action",
    )

    # Saving is a hard serialization boundary. A pending action is forced into
    # persistent overrides before Blender writes the file.
    recording.start(scene, take_uuid)
    frame_object.hide_render = True
    recent.observe_scene(scene, now=65.0, force_all=True)
    blender_take_system._take_system_save_pre(None)
    require(
        stored_value(
            engine.find_take(scene, take_uuid),
            frame_object,
            "hide_render",
        )
        is True
        and recent.peek_recent_action(scene) is None,
        "Save-pre lifecycle did not commit the pending recording action",
    )

    # Reapplying the same take rebaselines and keeps recording; switching to a
    # different take clears the old record flag instead of leaving stale UI.
    recording.start(scene, take_uuid)
    engine.apply_take(scene, take_uuid, strict=True)
    recording.handle_internal_state_change(scene)
    require(
        engine.find_take(scene, take_uuid).is_recording
        and recent.peek_recent_action(scene) is None,
        "Same-take apply did not safely preserve/rebaseline recording",
    )
    engine.apply_take(scene, main_uuid, strict=True)
    recording.handle_internal_state_change(scene)
    require(
        not engine.find_take(scene, take_uuid).is_recording
        and recording.active_take(scene) is None,
        "Take switch left the previous take recording",
    )

    # Undo/redo and load are hard recording boundaries. They stop every record
    # flag and rebuild displayed-scene baselines without preserving snapshots.
    engine.apply_take(scene, take_uuid, strict=True)
    state.active_take_index = take_index(scene, take_uuid)
    recording.start(scene, take_uuid)
    blender_take_system._take_system_undo_redo_post(None)
    require(
        not engine.find_take(scene, take_uuid).is_recording,
        "Undo/redo lifecycle left automatic recording enabled",
    )
    recording.start(scene, take_uuid)
    blender_take_system._take_system_load_post(None)
    require(
        not engine.find_take(scene, take_uuid).is_recording
        and recording.message_bus_subscription_count() > 0,
        "Load lifecycle retained recording or lost message-bus subscriptions",
    )

    blender_take_system.unregister()
    registered = False
    require(
        not bpy.app.timers.is_registered(
            blender_take_system._take_system_recording_timer
        ),
        "Automatic-recording timer remained after unregister",
    )
    require(
        blender_take_system._take_system_save_pre
        not in bpy.app.handlers.save_pre,
        "Automatic-recording save handler remained after unregister",
    )
    require(
        recording.message_bus_subscription_count() == 0
        and recording.runtime_state_count() == 0,
        "Automatic-recording runtime state remained after unregister",
    )
    for unregistered_class in (
        operators.TS_OT_toggle_recording,
        operators.TS_OT_flush_recording,
    ):
        require(
            not unregistered_class.is_registered,
            f"{unregistered_class.__name__} remained registered",
        )

    print(
        "TAKE_SYSTEM_RECORDING_OK",
        {
            "single": 1,
            "grouped": 2,
            "updated": 1,
            "msgbus_fallback": 1,
            "failure_stop": 1,
            "lifecycle_stops": 2,
        },
    )
finally:
    if registered:
        blender_take_system.unregister()
