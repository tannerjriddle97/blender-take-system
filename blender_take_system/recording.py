"""Phase 6 automatic recording over the recent-action tracker.

This module owns recording eligibility, message-bus wakeups, quiet-period
commits, and user-facing runtime status. It deliberately delegates property
discovery to :mod:`recent` and persistent override writes to :mod:`engine`.
"""

import time
from dataclasses import dataclass

import bpy

from . import engine, recent


TIMER_INTERVAL = 0.1
MSGBUS_FALLBACK_SECONDS = 0.075

_STATUSES = {}
_MSGBUS_OWNER = object()
_MSGBUS_REVISION = 0
_MSGBUS_SIGNAL_AT = 0.0
_MSGBUS_SUBSCRIPTION_COUNT = 0

# Message bus is a low-cost wakeup signal for common UI edits. Dependency-graph
# observation remains authoritative and handles the complete supported-property
# set. The timer performs a broad fallback scan only when no depsgraph callback
# has acknowledged a newer message-bus revision.
_MSGBUS_SPECS = (
    (
        "Object",
        (
            "location",
            "rotation_mode",
            "rotation_euler",
            "rotation_quaternion",
            "rotation_axis_angle",
            "scale",
            "delta_location",
            "delta_rotation_euler",
            "delta_rotation_quaternion",
            "delta_scale",
            "hide_viewport",
            "hide_render",
            "display_type",
            "color",
        ),
    ),
    ("LayerCollection", ("exclude",)),
    ("Scene", ("camera", "world")),
    (
        "RenderSettings",
        (
            "engine",
            "resolution_x",
            "resolution_y",
            "resolution_percentage",
            "filepath",
            "film_transparent",
        ),
    ),
    (
        "ImageFormatSettings",
        (
            "file_format",
            "color_mode",
            "color_depth",
            "compression",
            "quality",
        ),
    ),
    (
        "ColorManagedViewSettings",
        ("view_transform", "look", "exposure", "gamma"),
    ),
    ("MaterialSlot", ("link", "material")),
    ("Material", ("diffuse_color", "metallic", "roughness")),
    ("Camera", ("lens", "sensor_width", "sensor_height")),
    ("Light", ("color", "energy")),
)


@dataclass
class RecordingStatus:
    """Runtime-only status for one Scene's automatic recording session."""

    scene_uid: int
    take_uuid: str = ""
    state: str = "IDLE"
    message: str = "Automatic recording is off"
    captured_actions: int = 0
    captured_properties: int = 0
    last_summary: str = ""
    last_error: str = ""
    last_updated_at: float = 0.0
    msgbus_revision: int = 0


def _scene_uid(scene):
    return recent._scene_uid(scene)


def _safe_takes(scene):
    try:
        return tuple(scene.take_system.takes)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _set_recording_flags(scene, take_uuid=""):
    for take in _safe_takes(scene):
        try:
            take.is_recording = bool(
                take_uuid
                and take.uuid == take_uuid
                and not take.is_main
            )
        except (AttributeError, ReferenceError, RuntimeError):
            continue


def _valid_recording_take(scene):
    if scene is None or not hasattr(scene, "take_system"):
        return None
    try:
        applied_uuid = scene.take_system.active_take_uuid
    except (AttributeError, ReferenceError):
        return None
    take = engine.find_take(scene, applied_uuid)
    if take is None or take.is_main or not take.is_recording:
        return None
    return take


def active_take(scene):
    """Return the valid applied recording take, or ``None``."""

    return _valid_recording_take(scene)


def _new_status(scene, take_uuid="", state="IDLE", message=None):
    if message is None:
        message = (
            "Automatic recording is active"
            if state == "RECORDING"
            else "Automatic recording is off"
        )
    runtime = RecordingStatus(
        scene_uid=_scene_uid(scene),
        take_uuid=take_uuid,
        state=state,
        message=message,
        last_updated_at=time.monotonic(),
        msgbus_revision=_MSGBUS_REVISION,
    )
    _STATUSES[runtime.scene_uid] = runtime
    return runtime


def status_for_scene(scene):
    """Return a stable runtime status object for UI and tests."""

    scene_uid = _scene_uid(scene)
    runtime = _STATUSES.get(scene_uid)
    take = _valid_recording_take(scene)
    if runtime is None:
        runtime = _new_status(
            scene,
            take.uuid if take is not None else "",
            "RECORDING" if take is not None else "IDLE",
        )
    elif take is not None and (
        runtime.state != "RECORDING"
        or runtime.take_uuid != take.uuid
    ):
        runtime.take_uuid = take.uuid
        runtime.state = "RECORDING"
        runtime.message = f"Recording changes on '{take.name}'"
        runtime.last_error = ""
        runtime.last_updated_at = time.monotonic()
        runtime.msgbus_revision = _MSGBUS_REVISION
    return runtime


def recording_status_text(scene):
    """Return concise user-facing recording status for the Take Manager."""

    runtime = status_for_scene(scene)
    if runtime.state == "ERROR":
        return f"Stopped after error: {runtime.last_error}"
    take = _valid_recording_take(scene)
    if take is not None:
        action = recent.peek_recent_action(scene)
        if action is not None and action.changes:
            return f"Recording pending: {action.summary}"
        if runtime.last_summary:
            return f"Recording: last captured {runtime.last_summary}"
        return f"Recording changes on '{take.name}'"
    return runtime.message


def start(scene, take_uuid=None):
    """Enable automatic recording for the applied non-Main take."""

    main = engine.ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid
    take = engine.find_take(scene, requested)
    if take is None:
        raise engine.TakeHierarchyError(f"Take does not exist: {requested}")
    if take.uuid == main.uuid or take.is_main:
        raise engine.TakeHierarchyError(
            "Automatic recording requires a non-Main take"
        )
    if scene.take_system.active_take_uuid != take.uuid:
        raise engine.TakeHierarchyError(
            "The recording take must be applied before recording starts"
        )
    engine.take_chain(scene, take.uuid)

    _set_recording_flags(scene, take.uuid)
    recent.rebaseline_scene(scene)
    runtime = _new_status(
        scene,
        take.uuid,
        "RECORDING",
        f"Recording changes on '{take.name}'",
    )
    return runtime


def _stop_with_error(scene, exc):
    runtime = status_for_scene(scene)
    take_uuid = runtime.take_uuid
    _set_recording_flags(scene)
    try:
        recent.rebaseline_scene(scene)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        RuntimeError,
    ):
        recent.clear_scene(scene)
    runtime.take_uuid = take_uuid
    runtime.state = "ERROR"
    runtime.last_error = str(exc)
    runtime.message = f"Automatic recording stopped: {exc}"
    runtime.last_updated_at = time.monotonic()
    runtime.msgbus_revision = _MSGBUS_REVISION


def flush(scene, *, now=None, force=False):
    """Commit one settled pending action, returning its capture report."""

    take = _valid_recording_take(scene)
    if take is None:
        return None
    runtime = status_for_scene(scene)
    action = recent.peek_recent_action(scene)
    if action is None or not action.changes:
        return None
    if action.take_uuid != take.uuid:
        exc = engine.TakeHierarchyError(
            "Pending recording belongs to a different applied take"
        )
        _stop_with_error(scene, exc)
        raise exc

    if now is None:
        now = time.monotonic()
    if (
        not force
        and not action.finalized
        and now - action.updated_at < recent.ACTION_GROUP_SECONDS
    ):
        return None

    summary = action.summary
    try:
        report = recent.capture_pending(scene, take.uuid)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        RuntimeError,
    ) as exc:
        _stop_with_error(scene, exc)
        raise

    runtime.state = "RECORDING"
    runtime.message = (
        f"Captured {report.captured} "
        f"propert{'y' if report.captured == 1 else 'ies'}"
    )
    runtime.captured_actions += 1
    runtime.captured_properties += report.captured
    runtime.last_summary = summary
    runtime.last_error = ""
    runtime.last_updated_at = now
    runtime.msgbus_revision = _MSGBUS_REVISION
    return report


def stop(
    scene,
    *,
    commit_pending=True,
    reason="Automatic recording stopped",
):
    """Stop recording, optionally committing the pending grouped action."""

    take = _valid_recording_take(scene)
    runtime = status_for_scene(scene)
    if take is not None and commit_pending:
        flush(scene, force=True)
    _set_recording_flags(scene)
    try:
        recent.rebaseline_scene(scene)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        RuntimeError,
    ):
        recent.clear_scene(scene)
    runtime.state = "STOPPED"
    runtime.message = reason
    runtime.last_error = ""
    runtime.last_updated_at = time.monotonic()
    runtime.msgbus_revision = _MSGBUS_REVISION
    return runtime


def prepare_internal_change(scene):
    """Commit pending user edits before a Take System operation mutates state."""

    if _valid_recording_take(scene) is None:
        return None
    return flush(scene, force=True)


def _normalize_flags_for_applied_take(scene):
    try:
        applied_uuid = scene.take_system.active_take_uuid
    except (AttributeError, ReferenceError):
        applied_uuid = ""
    valid_uuid = ""
    for take in _safe_takes(scene):
        try:
            if (
                take.uuid == applied_uuid
                and not take.is_main
                and take.is_recording
            ):
                valid_uuid = take.uuid
                break
        except (AttributeError, ReferenceError):
            continue
    _set_recording_flags(scene, valid_uuid)
    return engine.find_take(scene, valid_uuid) if valid_uuid else None


def handle_internal_state_change(scene):
    """Rebaseline after add-on writes and reconcile the recording flag."""

    previous = _STATUSES.get(_scene_uid(scene))
    take = _normalize_flags_for_applied_take(scene)
    recent.handle_internal_state_change(scene)
    if take is not None:
        runtime = status_for_scene(scene)
        runtime.state = "RECORDING"
        runtime.take_uuid = take.uuid
        runtime.message = f"Recording changes on '{take.name}'"
        runtime.last_error = ""
        runtime.msgbus_revision = _MSGBUS_REVISION
    elif previous is not None and previous.state == "RECORDING":
        previous.state = "STOPPED"
        previous.message = "Recording stopped because the applied take changed"
        previous.last_updated_at = time.monotonic()
        previous.msgbus_revision = _MSGBUS_REVISION


def handle_frame_change(scene, *, seconds=0.2):
    """Ignore frame/evaluation writes while keeping a valid recorder armed."""

    recent.defer_scene(scene, seconds=seconds)
    runtime = _STATUSES.get(_scene_uid(scene))
    if runtime is not None and _valid_recording_take(scene) is not None:
        runtime.message = "Frame evaluation ignored; recording remains active"
        runtime.last_updated_at = time.monotonic()


def reset_after_load(scene):
    """Fail closed on file load so recording never resumes unexpectedly."""

    _set_recording_flags(scene)
    runtime = _STATUSES.get(_scene_uid(scene))
    if runtime is not None:
        runtime.state = "STOPPED"
        runtime.message = "Recording stopped after file load"
        runtime.last_updated_at = time.monotonic()


def handle_undo_redo(scenes):
    """Stop every recorder before undo/redo tracker rebaselining."""

    for scene in tuple(scenes):
        _set_recording_flags(scene)
        runtime = _STATUSES.get(_scene_uid(scene))
        if runtime is not None:
            runtime.state = "STOPPED"
            runtime.message = "Recording stopped after undo/redo"
            runtime.last_error = ""
            runtime.last_updated_at = time.monotonic()


def clear_runtime():
    global _MSGBUS_REVISION, _MSGBUS_SIGNAL_AT
    _STATUSES.clear()
    _MSGBUS_REVISION = 0
    _MSGBUS_SIGNAL_AT = 0.0


def runtime_state_count():
    return len(_STATUSES)


def prune_runtime_state(scenes=None):
    if scenes is None:
        try:
            scenes = tuple(bpy.data.scenes)
        except AttributeError:
            scenes = ()
    else:
        scenes = tuple(scenes)
    live_uids = {_scene_uid(scene) for scene in scenes}
    for scene_uid in tuple(_STATUSES):
        if scene_uid not in live_uids:
            _STATUSES.pop(scene_uid, None)


def _message_bus_notify(*, now=None):
    """Record a cheap type-level RNA signal for the timer fallback."""

    global _MSGBUS_REVISION, _MSGBUS_SIGNAL_AT
    _MSGBUS_REVISION += 1
    _MSGBUS_SIGNAL_AT = time.monotonic() if now is None else now


def note_depsgraph_update(scene):
    """Acknowledge message-bus signals handled by normal indexed observation."""

    runtime = _STATUSES.get(_scene_uid(scene))
    if runtime is not None:
        runtime.msgbus_revision = _MSGBUS_REVISION


def register_message_bus():
    """Subscribe to common writable RNA types as low-cost wakeup signals."""

    global _MSGBUS_SUBSCRIPTION_COUNT
    unregister_message_bus()
    count = 0
    for type_name, property_names in _MSGBUS_SPECS:
        rna_type = getattr(bpy.types, type_name, None)
        if rna_type is None:
            continue
        try:
            properties = rna_type.bl_rna.properties
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        for property_name in property_names:
            try:
                if properties.get(property_name) is None:
                    continue
                bpy.msgbus.subscribe_rna(
                    key=(rna_type, property_name),
                    owner=_MSGBUS_OWNER,
                    args=(),
                    notify=_message_bus_notify,
                    options={"PERSISTENT"},
                )
            except (AttributeError, ReferenceError, RuntimeError, TypeError):
                continue
            count += 1
    _MSGBUS_SUBSCRIPTION_COUNT = count
    return count


def unregister_message_bus():
    global _MSGBUS_SUBSCRIPTION_COUNT
    try:
        bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    except (AttributeError, RuntimeError):
        pass
    _MSGBUS_SUBSCRIPTION_COUNT = 0


def message_bus_subscription_count():
    return _MSGBUS_SUBSCRIPTION_COUNT


def tick(scenes=None, *, now=None):
    """Observe message-bus fallbacks and commit settled recording actions."""

    if now is None:
        now = time.monotonic()
    if scenes is None:
        try:
            scenes = tuple(bpy.data.scenes)
        except AttributeError:
            scenes = ()
    else:
        scenes = tuple(scenes)
    prune_runtime_state(scenes)

    for scene in scenes:
        take = _valid_recording_take(scene)
        if take is None:
            _normalize_flags_for_applied_take(scene)
            continue
        runtime = status_for_scene(scene)
        if (
            runtime.msgbus_revision != _MSGBUS_REVISION
            and now - _MSGBUS_SIGNAL_AT >= MSGBUS_FALLBACK_SECONDS
        ):
            try:
                recent.observe_scene(scene, now=now, force_all=True)
            except (
                engine.TakeSystemError,
                AttributeError,
                ReferenceError,
                RuntimeError,
            ) as exc:
                _stop_with_error(scene, exc)
                continue
            runtime.msgbus_revision = _MSGBUS_REVISION
        try:
            flush(scene, now=now)
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            RuntimeError,
        ):
            # ``flush`` records the error and disables recording. Timers must
            # never propagate an exception into Blender's event loop.
            continue
    return TIMER_INTERVAL
