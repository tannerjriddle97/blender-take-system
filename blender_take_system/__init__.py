"""Blender Take System - hierarchical overrides and automatic recording."""

bl_info = {
    "name": "Take System",
    "author": "OpenAI",
    "version": (0, 6, 0),
    "blender": (4, 0, 0),
    "location": "Properties > Scene > Take Manager",
    "description": (
        "Hierarchical takes with automatic override recording and batch rendering"
    ),
    "category": "Scene",
    "doc_url": "",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

if "engine" in locals():
    # Blender reloads an updated add-on's package module but can retain its
    # already-imported submodules. Refresh them explicitly so an in-place ZIP
    # update cannot mix old engine/operator classes with a new __init__.py.
    import importlib

    from . import engine as _engine
    from . import model as _model
    from . import operators as _operators
    from . import recent as _recent
    from . import recording as _recording
    from . import ui as _ui

    model = importlib.reload(_model)
    engine = importlib.reload(_engine)
    recent = importlib.reload(_recent)
    recording = importlib.reload(_recording)
    operators = importlib.reload(_operators)
    ui = importlib.reload(_ui)
else:
    from . import engine, model, operators, recent, recording, ui


CLASSES = (
    *model.CLASSES,
    *operators.CLASSES,
    *ui.CLASSES,
)
_REGISTERED_CLASSES = []
_MENU_ATTACHED = False
_BOOTSTRAP_WARNED_SCENES = set()
_BOOTSTRAP_POLL_INTERVAL = 0.5


def _context_scene():
    """Return Blender's current scene without assuming a UI context exists."""

    try:
        return bpy.context.scene
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def _scene_pointer(scene):
    if scene is None:
        return 0
    try:
        return int(scene.as_pointer())
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return 0


def _displayed_scenes():
    """Return each Scene currently shown by a Blender window."""

    scenes = []
    try:
        windows = tuple(bpy.context.window_manager.windows)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        windows = ()
    for window in windows:
        try:
            scene = window.scene
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if scene is not None:
            scenes.append(scene)
    current_scene = _context_scene()
    if current_scene is not None:
        scenes.append(current_scene)

    unique = []
    seen = set()
    for scene in scenes:
        pointer = _scene_pointer(scene)
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        unique.append(scene)
    return tuple(unique)


def _safe_bootstrap_scene(scene, force=True):
    try:
        if (
            getattr(scene, "library", None) is not None
            and getattr(scene, "override_library", None) is None
        ):
            return False
        if hasattr(scene, "take_system"):
            state = scene.take_system
            if not force and state.takes and state.main_take_uuid:
                canonical = engine.find_take(scene, state.main_take_uuid)
                if (
                    canonical is not None
                    and canonical.is_main
                    and canonical.name == engine.MAIN_NAME
                    and not canonical.parent_uuid
                    and state.schema_version >= engine.SCHEMA_VERSION
                ):
                    return True
            engine.ensure_main_take(scene)
        return True
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        engine.TakeSystemError,
    ) as exc:
        try:
            scene_key = scene.as_pointer()
            scene_name = scene.name
        except (AttributeError, ReferenceError):
            scene_key = id(scene)
            scene_name = "<unavailable>"
        if scene_key not in _BOOTSTRAP_WARNED_SCENES:
            _BOOTSTRAP_WARNED_SCENES.add(scene_key)
            print(f"Take System: skipped scene '{scene_name}': {exc}")
        return False


@persistent
def _take_system_load_post(_unused):
    recent.clear_all()
    recording.clear_runtime()
    engine.clear_runtime_state()
    ready_scene_pointers = set()
    for scene in bpy.data.scenes:
        recording.reset_after_load(scene)
        bootstrapped = _safe_bootstrap_scene(scene)
        if bootstrapped:
            ready_scene_pointers.add(_scene_pointer(scene))
    # Persisted Take data is repaired for every scene, but the comparatively
    # expensive recent-action tracker is built only for displayed scenes.
    for scene in _displayed_scenes():
        if _scene_pointer(scene) in ready_scene_pointers:
            recent.rebaseline_scene(scene)
    recording.register_message_bus()


@persistent
def _take_system_depsgraph_update_post(scene, _depsgraph):
    if _safe_bootstrap_scene(scene, force=False):
        recent.handle_depsgraph_update(scene, _depsgraph)
        recording.note_depsgraph_update(scene)


@persistent
def _take_system_undo_redo_post(_unused):
    # Undo/redo may invalidate cached RNA handles. Discard every tracker, then
    # eagerly rebuild only scenes Blender is currently presenting.
    recording.handle_undo_redo(tuple(bpy.data.scenes))
    recent.clear_all()
    for scene in _displayed_scenes():
        if _safe_bootstrap_scene(scene, force=False):
            recent.handle_internal_state_change(scene)


@persistent
def _take_system_frame_change_post(scene, _depsgraph=None):
    recording.handle_frame_change(scene)


@persistent
def _take_system_save_pre(_unused):
    """Commit settled or pending recording actions before serialization."""

    for scene in tuple(bpy.data.scenes):
        try:
            recording.prepare_internal_change(scene)
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            RuntimeError,
        ) as exc:
            # ``recording.flush`` has already failed closed and exposed the
            # error in Take Manager. Saving unrelated scene data may continue.
            try:
                scene_name = scene.name_full
            except (AttributeError, ReferenceError):
                scene_name = "<unavailable>"
            print(
                f"Take System: recording stopped before saving "
                f"'{scene_name}': {exc}"
            )


def _bootstrap_scenes_timer():
    """Bootstrap existing and newly created scenes outside restricted contexts."""

    try:
        scenes = tuple(bpy.data.scenes)
    except AttributeError:
        return 0.1
    recent.prune_runtime_state(scenes)
    recording.prune_runtime_state(scenes)
    ready_scene_pointers = set()
    for scene in scenes:
        bootstrapped = _safe_bootstrap_scene(scene, force=False)
        if bootstrapped:
            ready_scene_pointers.add(_scene_pointer(scene))
    for scene in _displayed_scenes():
        if _scene_pointer(scene) in ready_scene_pointers:
            recent.ensure_scene(scene)
    # Blender has no dedicated scene-added handler. Keep a cheap recurring
    # check so scenes created without a depsgraph update are still Main-rooted.
    # Their recent-action trackers remain lazy until the scene becomes active
    # or receives an actual dependency-graph update.
    return _BOOTSTRAP_POLL_INTERVAL


def _take_system_recording_timer():
    """Commit settled Phase 6 actions outside depsgraph handler restrictions."""

    try:
        scenes = tuple(bpy.data.scenes)
    except AttributeError:
        return recording.TIMER_INTERVAL
    recording.tick(scenes)
    return recording.TIMER_INTERVAL


def _teardown_registration():
    global _MENU_ATTACHED
    try:
        if _MENU_ATTACHED:
            bpy.types.UI_MT_button_context_menu.remove(
                operators.draw_button_context_menu
            )
    except (ValueError, RuntimeError):
        pass
    _MENU_ATTACHED = False

    if _take_system_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_take_system_load_post)
    if _take_system_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(
            _take_system_depsgraph_update_post
        )
    if _take_system_undo_redo_post in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(_take_system_undo_redo_post)
    if _take_system_undo_redo_post in bpy.app.handlers.redo_post:
        bpy.app.handlers.redo_post.remove(_take_system_undo_redo_post)
    if _take_system_frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(
            _take_system_frame_change_post
        )
    if _take_system_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_take_system_save_pre)
    if bpy.app.timers.is_registered(_bootstrap_scenes_timer):
        bpy.app.timers.unregister(_bootstrap_scenes_timer)
    if bpy.app.timers.is_registered(_take_system_recording_timer):
        bpy.app.timers.unregister(_take_system_recording_timer)
    recording.unregister_message_bus()

    if hasattr(bpy.types.Scene, "take_system"):
        del bpy.types.Scene.take_system

    for cls in reversed(_REGISTERED_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
    _REGISTERED_CLASSES.clear()
    _BOOTSTRAP_WARNED_SCENES.clear()
    operators.clear_runtime_caches()
    recent.clear_all()
    recording.clear_runtime()
    engine.clear_runtime_state()


def register():
    global _MENU_ATTACHED
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _REGISTERED_CLASSES.append(cls)

        bpy.types.Scene.take_system = PointerProperty(type=model.TS_PG_TakeSystem)

        if _take_system_load_post not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_take_system_load_post)
        if (
            _take_system_depsgraph_update_post
            not in bpy.app.handlers.depsgraph_update_post
        ):
            bpy.app.handlers.depsgraph_update_post.append(
                _take_system_depsgraph_update_post
            )
        if _take_system_undo_redo_post not in bpy.app.handlers.undo_post:
            bpy.app.handlers.undo_post.append(_take_system_undo_redo_post)
        if _take_system_undo_redo_post not in bpy.app.handlers.redo_post:
            bpy.app.handlers.redo_post.append(_take_system_undo_redo_post)
        if _take_system_frame_change_post not in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.append(
                _take_system_frame_change_post
            )
        if _take_system_save_pre not in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.append(_take_system_save_pre)
        # Dynamic menu callbacks do not expose a stable public membership API
        # on every 4.x version.
        bpy.types.UI_MT_button_context_menu.append(
            operators.draw_button_context_menu
        )
        _MENU_ATTACHED = True
        recording.register_message_bus()
        if not bpy.app.timers.is_registered(_bootstrap_scenes_timer):
            bpy.app.timers.register(
                _bootstrap_scenes_timer,
                first_interval=0.0,
                persistent=True,
            )
        if not bpy.app.timers.is_registered(_take_system_recording_timer):
            bpy.app.timers.register(
                _take_system_recording_timer,
                first_interval=recording.TIMER_INTERVAL,
                persistent=True,
            )
    except Exception:
        _teardown_registration()
        raise


def unregister():
    _teardown_registration()


if __name__ == "__main__":
    register()
