"""Thin Blender operators for the Take System engine and manager UI."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import engine, recent, recording


CAPTURE_ID_TYPES = (
    ("Object", "Object", "Object transforms, visibility, slots, and modifiers"),
    ("Material", "Material", "Material and shader-node properties"),
    ("Camera", "Camera Data", "Camera datablock properties"),
    ("Scene", "Scene", "Scene-level properties"),
    ("World", "World", "World and world-node properties"),
    ("Mesh", "Mesh", "Mesh datablock properties"),
    ("Light", "Light", "Light datablock properties"),
    ("Collection", "Collection", "Collection properties"),
    ("NodeTree", "Node Group", "Standalone node-group properties"),
)

_TAKE_ENUM_ITEMS_CACHE = {}
_PARENT_ENUM_ITEMS_CACHE = {}
_CAMERA_ENUM_ITEMS_CACHE = {}
_LAST_SETTINGS_LAUNCH = {}


def _scene_editability_error(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        return "No active scene"
    if not hasattr(scene, "take_system"):
        return "Take System is not registered on the active scene"
    if (
        getattr(scene, "library", None) is not None
        and getattr(scene, "override_library", None) is None
    ):
        return "The active scene is linked and read-only"
    if getattr(scene, "is_editable", True) is False:
        return "The active scene is read-only"
    return None


def _poll_editable_scene(operator_class, context):
    error = _scene_editability_error(context)
    if error:
        operator_class.poll_message_set(error)
        return False
    return True


def _prepare_recording_for_internal_change(operator, scene):
    """Commit pending auto-record data before an add-on operation."""

    try:
        recording.prepare_internal_change(scene)
    except engine.TakeSystemError as exc:
        operator.report(
            {"ERROR"},
            f"Pending automatic recording could not be committed: {exc}",
        )
        return False
    return True


def _take_enum_items(_self, context):
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "take_system"):
        return []
    try:
        hierarchy_rows = engine.take_hierarchy_rows(scene)
        scene_pointer = scene.as_pointer()
        signature = tuple(
            (
                row.take.uuid,
                row.take.name,
                row.take.parent_uuid,
                row.depth,
                row.issue,
            )
            for row in hierarchy_rows
        )
        cached = _TAKE_ENUM_ITEMS_CACHE.get(scene_pointer)
        if cached is not None and cached[0] == signature:
            return cached[1]
        items = [
            (
                row.take.uuid,
                f"{'    ' * row.depth}{row.take.name}",
                (
                    row.issue
                    if row.issue
                    else f"Apply {row.take.name}"
                ),
                index,
            )
            for index, row in enumerate(hierarchy_rows)
        ]
        # Blender retains callback-returned enum string pointers. Keep the
        # current item tuples alive without accumulating every old tree edit.
        _TAKE_ENUM_ITEMS_CACHE[scene_pointer] = (signature, items)
        return items
    except (engine.TakeSystemError, AttributeError):
        return []


def _parent_enum_items(self, context):
    """List valid new parents for one take in depth-first UI order."""

    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "take_system"):
        return []
    take_uuid = getattr(self, "take_uuid", "")
    try:
        excluded = {take_uuid}
        if take_uuid:
            excluded.update(engine.take_descendant_uuids(scene, take_uuid))
        hierarchy_rows = engine.take_hierarchy_rows(scene)
        signature = (
            take_uuid,
            tuple(
                (
                    row.take.uuid,
                    row.take.name,
                    row.take.parent_uuid,
                    row.depth,
                    row.issue,
                )
                for row in hierarchy_rows
            ),
        )
        scene_pointer = scene.as_pointer()
        cache_key = (scene_pointer, take_uuid)
        cached = _PARENT_ENUM_ITEMS_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        items = [
            (
                row.take.uuid,
                f"{'    ' * row.depth}{row.take.name}",
                f"Move under {row.take.name}",
                index,
            )
            for index, row in enumerate(hierarchy_rows)
            if row.take.uuid not in excluded and not row.issue
        ]
        _PARENT_ENUM_ITEMS_CACHE[cache_key] = (signature, items)
        return items
    except (engine.TakeSystemError, AttributeError):
        return []


def _button_target_and_path(context):
    pointer = getattr(context, "button_pointer", None)
    rna_property = getattr(context, "button_prop", None)
    if pointer is None or rna_property is None:
        raise engine.TakeSystemError(
            "The clicked UI control does not expose an RNA property"
        )
    if getattr(rna_property, "is_readonly", False):
        raise engine.TakeSystemError("The clicked property is read-only")

    try:
        target_id = pointer.id_data
    except (AttributeError, ReferenceError) as exc:
        raise engine.TakeSystemError(
            "The clicked property has no storable Blender datablock owner"
        ) from exc
    if not isinstance(target_id, bpy.types.ID):
        raise engine.TakeSystemError(
            "The clicked property has no storable Blender datablock owner"
        )

    identifier = getattr(rna_property, "identifier", "")
    if not identifier:
        raise engine.TakeSystemError(
            "The clicked UI control has no RNA property identifier"
        )

    if (
        isinstance(pointer, bpy.types.LayerCollection)
        and identifier == "exclude"
    ):
        data_path = engine.layer_collection_data_path(
            target_id,
            pointer,
            identifier,
        )
    else:
        try:
            data_path = pointer.path_from_id(identifier)
        except (TypeError, ValueError, AttributeError):
            try:
                owner_path = pointer.path_from_id()
            except (TypeError, ValueError, AttributeError) as exc:
                raise engine.TakeSystemError(
                    "Blender could not generate an RNA path for this control"
                ) from exc
            if identifier.startswith("["):
                data_path = f"{owner_path}{identifier}"
            elif owner_path:
                data_path = f"{owner_path}.{identifier}"
            else:
                data_path = identifier

    target_id, data_path = engine.canonicalize_id_path(target_id, data_path)
    if engine.is_take_system_internal_path(target_id, data_path):
        raise engine.TakePathError(
            "Take System settings cannot themselves be stored as take overrides"
        )
    return target_id, data_path


class TS_OT_initialize(bpy.types.Operator):
    """Repair or create Main for the current scene."""

    bl_idname = "take_system.initialize"
    bl_label = "Initialize Take System"
    bl_description = "Create or repair the scene's Main take"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def execute(self, context):
        try:
            main = engine.ensure_main_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Take System ready; Main ID: {main.uuid}")
        return {"FINISHED"}


class TS_OT_add_take(bpy.types.Operator):
    """Add a child beneath the take highlighted in Take Manager."""

    bl_idname = "take_system.add_take"
    bl_label = "Add Take"
    bl_description = (
        "Add a child beneath the highlighted take and make the new take active"
    )
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="Take")
    parent_mode: EnumProperty(
        name="Parent",
        items=(
            ("ACTIVE", "Active Take", "Create as a child of the active take"),
            (
                "SELECTED",
                "Selected Take",
                "Create as a child of the take selected in Take Manager",
            ),
            ("MAIN", "Main", "Create as a top-level child of Main"),
        ),
        default="SELECTED",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        try:
            engine.ensure_main_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        selected = engine.selected_take(context.scene)
        layout.label(
            text=(
                f"Parent: {selected.name}"
                if selected is not None
                else "Parent: <none>"
            ),
            icon="CON_CHILDOF",
        )

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            main = engine.ensure_main_take(context.scene)
            if self.parent_mode == "ACTIVE":
                parent_uuid = context.scene.take_system.active_take_uuid
            elif self.parent_mode == "SELECTED":
                selected = engine.selected_take(context.scene)
                if selected is None:
                    raise engine.TakeHierarchyError("No take is selected")
                parent_uuid = selected.uuid
            else:
                parent_uuid = main.uuid
            take = engine.create_take(
                context.scene,
                name=self.name,
                parent_uuid=parent_uuid,
                make_active=True,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Parent state could not be applied: {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Created take '{take.name}'. Add overrides before editing a property.",
        )
        return {"FINISHED"}


class TS_OT_apply_take(bpy.types.Operator):
    """Resolve and atomically apply one selected take."""

    bl_idname = "take_system.apply_take"
    bl_label = "Go to Take"
    bl_description = "Resolve Main-to-child overrides and apply the selected take"
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: EnumProperty(name="Take", items=_take_enum_items)

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        try:
            main = engine.ensure_main_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not self.take_uuid:
            self.take_uuid = (
                context.scene.take_system.active_take_uuid or main.uuid
            )
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        requested = self.take_uuid or context.scene.take_system.active_take_uuid
        try:
            report = engine.apply_take(context.scene, requested, strict=True)
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Take not fully applied ({exc.report.skipped} issue(s)): {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Applied '{report.take_name}' ({report.applied} override(s))",
        )
        return {"FINISHED"}


class TS_OT_apply_active_take(bpy.types.Operator):
    """Reapply the currently active take."""

    bl_idname = "take_system.apply_active_take"
    bl_label = "Reapply Active Take"
    bl_description = "Reapply the active take's fully resolved state"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            report = engine.apply_take(
                context.scene,
                context.scene.take_system.active_take_uuid,
                strict=True,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Take not fully applied ({exc.report.skipped} issue(s)): {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Applied '{report.take_name}' ({report.applied} override(s))",
        )
        return {"FINISHED"}


class TS_OT_apply_selected_take(bpy.types.Operator):
    """Apply the take selected in the Take Manager."""

    bl_idname = "take_system.apply_selected_take"
    bl_label = "Apply Selected Take"
    bl_description = (
        "Apply the take selected in Take Manager as one undoable operation"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        selected = engine.selected_take(context.scene)
        if selected is None:
            cls.poll_message_set("No take is selected")
            return False
        return True

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        if selected is None:
            self.report({"ERROR"}, "No take is selected")
            return {"CANCELLED"}
        try:
            report = engine.apply_take(
                context.scene,
                selected.uuid,
                strict=True,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Take not fully applied ({exc.report.skipped} issue(s)): {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Applied '{report.take_name}' ({report.applied} override(s))",
        )
        return {"FINISHED"}


class TS_OT_duplicate_take(bpy.types.Operator):
    """Duplicate one take and its descendants as a sibling subtree."""

    bl_idname = "take_system.duplicate_take"
    bl_label = "Duplicate Take"
    bl_description = (
        "Duplicate the selected take, all descendants, and their local "
        "settings as a sibling subtree"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        copied_count = 1 + len(
            engine.take_descendant_uuids(context.scene, requested)
        )
        try:
            duplicate = engine.duplicate_take(
                context.scene,
                requested,
                make_active=True,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Duplicate could not be applied: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Duplicated {copied_count} take(s) as '{duplicate.name}'",
        )
        return {"FINISHED"}


class TS_OT_delete_take(bpy.types.Operator):
    """Delete one take and adopt its children into its parent."""

    bl_idname = "take_system.delete_take"
    bl_label = "Delete Take"
    bl_description = (
        "Delete the selected take; its children are moved to the deleted "
        "take's parent"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        selected = engine.selected_take(context.scene)
        if not self.take_uuid and selected is not None:
            self.take_uuid = selected.uuid
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        try:
            engine.delete_take(context.scene, requested)
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Delete could not update the scene: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report({"INFO"}, "Take deleted; children were preserved")
        return {"FINISHED"}


class TS_OT_reparent_take(bpy.types.Operator):
    """Move one take under another valid parent."""

    bl_idname = "take_system.reparent_take"
    bl_label = "Reparent Take"
    bl_description = "Move the selected take under another take"
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})
    parent_uuid: EnumProperty(name="New Parent", items=_parent_enum_items)

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        selected = engine.selected_take(context.scene)
        if not self.take_uuid and selected is not None:
            self.take_uuid = selected.uuid
        take = engine.find_take(context.scene, self.take_uuid)
        if take is None:
            self.report({"ERROR"}, "No take is selected")
            return {"CANCELLED"}
        if take.is_main:
            self.report({"ERROR"}, "Main cannot be reparented")
            return {"CANCELLED"}
        valid_parent_ids = {
            identifier
            for identifier, _name, _description, _number
            in _parent_enum_items(self, context)
        }
        if take.parent_uuid in valid_parent_ids:
            self.parent_uuid = take.parent_uuid
        elif valid_parent_ids:
            self.parent_uuid = next(iter(valid_parent_ids))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            take = engine.reparent_take(
                context.scene,
                self.take_uuid,
                self.parent_uuid,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Reparent could not update the scene: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        parent = engine.find_take(context.scene, take.parent_uuid)
        parent_name = parent.name if parent is not None else "<missing>"
        self.report({"INFO"}, f"Moved '{take.name}' under '{parent_name}'")
        return {"FINISHED"}


class TS_OT_remove_override(bpy.types.Operator):
    """Remove one direct override from a take."""

    bl_idname = "take_system.remove_override"
    bl_label = "Remove Take Override"
    bl_description = (
        "Remove this direct override and reapply the active take if needed"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})
    override_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            engine.remove_override(
                context.scene,
                self.take_uuid,
                self.override_uuid,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Override could not be removed: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report({"INFO"}, "Take override removed")
        return {"FINISHED"}


class TS_OT_open_manager(bpy.types.Operator):
    """Open Take Manager in the area that invoked this command."""

    bl_idname = "take_system.open_manager"
    bl_label = "Open Take Manager in Current Area"
    bl_description = (
        "Change the current area to Properties and show Scene > Take Manager"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        area = getattr(context, "area", None)
        if area is None:
            cls.poll_message_set("This command needs an interactive editor area")
            return False
        return True

    def execute(self, context):
        try:
            context.area.type = "PROPERTIES"
            context.area.spaces.active.context = "SCENE"
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, f"Could not open Take Manager: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class TS_OT_capture_recent_action(bpy.types.Operator):
    """Capture the most recent supported scene change onto the applied take."""

    bl_idname = "take_system.capture_recent_action"
    bl_label = "Capture Most Recent Action as Overrides"
    bl_description = (
        "Store the most recent tracked property action on the applied child "
        "take, automatically seeding missing Main baselines"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        scene = context.scene
        selected = engine.selected_take(scene)
        applied = engine.find_take(
            scene,
            scene.take_system.active_take_uuid,
        )
        if selected is None or applied is None:
            cls.poll_message_set("No valid take is selected and applied")
            return False
        if selected.uuid != applied.uuid:
            cls.poll_message_set(
                "Apply the selected take before capturing live changes"
            )
            return False
        if applied.is_main:
            cls.poll_message_set(
                "Apply a non-Main take before capturing a recent action"
            )
            return False
        return True

    def execute(self, context):
        scene = context.scene
        selected = engine.selected_take(scene)
        applied = engine.find_take(
            scene,
            scene.take_system.active_take_uuid,
        )
        if (
            selected is None
            or applied is None
            or selected.uuid != applied.uuid
            or applied.is_main
        ):
            self.report(
                {"ERROR"},
                "A non-Main take must be both selected and applied",
            )
            return {"CANCELLED"}
        try:
            if recording.active_take(scene) is not None:
                report = recording.flush(scene, force=True)
                if report is None:
                    raise engine.TakeSystemError(
                        "No supported recording action is pending"
                    )
            else:
                report = recent.capture_pending(scene, applied.uuid)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            (
                f"Captured {report.captured} recent "
                f"propert{'y' if report.captured == 1 else 'ies'} "
                f"on '{report.take_name}'"
            ),
        )
        return {"FINISHED"}


class TS_OT_toggle_recording(bpy.types.Operator):
    """Start or stop automatic recording on the applied child take."""

    bl_idname = "take_system.toggle_recording"
    bl_label = "Toggle Automatic Recording"
    bl_description = (
        "Automatically store supported property actions on the applied "
        "non-Main take"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def execute(self, context):
        scene = context.scene
        selected = engine.selected_take(scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        take = engine.find_take(scene, requested)
        if take is None:
            self.report({"ERROR"}, "No valid take was chosen for recording")
            return {"CANCELLED"}
        if take.is_main:
            self.report({"ERROR"}, "Main cannot record automatic overrides")
            return {"CANCELLED"}
        if scene.take_system.active_take_uuid != take.uuid:
            self.report(
                {"ERROR"},
                "Apply the take before enabling automatic recording",
            )
            return {"CANCELLED"}
        try:
            if take.is_recording:
                runtime = recording.stop(
                    scene,
                    commit_pending=True,
                    reason=f"Automatic recording stopped on '{take.name}'",
                )
                message = runtime.message
            else:
                recording.start(scene, take.uuid)
                message = f"Automatic recording started on '{take.name}'"
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, message)
        return {"FINISHED"}


class TS_OT_flush_recording(bpy.types.Operator):
    """Commit the current automatic-record action before its timer fires."""

    bl_idname = "take_system.flush_recording"
    bl_label = "Commit Pending Recording"
    bl_description = "Commit the pending grouped automatic-record action now"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        if recording.active_take(context.scene) is None:
            cls.poll_message_set("Automatic recording is not active")
            return False
        return True

    def execute(self, context):
        try:
            report = recording.flush(context.scene, force=True)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if report is None:
            self.report({"INFO"}, "No supported recording action is pending")
        else:
            self.report(
                {"INFO"},
                (
                    f"Recorded {report.captured} "
                    f"propert{'y' if report.captured == 1 else 'ies'} "
                    f"on '{report.take_name}'"
                ),
            )
        return {"FINISHED"}


def _selected_applied_take(operator_class, context):
    if not _poll_editable_scene(operator_class, context):
        return None
    scene = context.scene
    selected = engine.selected_take(scene)
    if selected is None:
        operator_class.poll_message_set("No take is selected")
        return None
    if selected.uuid != scene.take_system.active_take_uuid:
        operator_class.poll_message_set(
            "Apply the selected take before configuring it"
        )
        return None
    return selected


def _camera_enum_identifier(camera):
    try:
        return f"CAMERA_{int(camera.session_uid)}"
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return ""


def _camera_enum_items(_self, _context):
    cameras = [
        camera
        for camera in bpy.data.objects
        if camera.type == "CAMERA"
    ]
    signature = tuple(
        (_camera_enum_identifier(camera), camera.name_full)
        for camera in cameras
    )
    cached = _CAMERA_ENUM_ITEMS_CACHE.get("CURRENT")
    if cached is not None and cached[0] == signature:
        return cached[1]
    items = [
        (
            "INHERIT",
            "Inherit from Parent",
            "Remove this take's direct camera and use its parent's camera",
            "CON_CHILDOF",
            0,
        ),
        ("NONE", "None", "Store no active camera", "X", 1),
        *[
            (
                identifier,
                name,
                f"Use Camera object {name}",
                "CAMERA_DATA",
                index + 1,
            )
            for index, (identifier, name) in enumerate(signature, start=1)
            if identifier
        ],
    ]
    _CAMERA_ENUM_ITEMS_CACHE["CURRENT"] = (signature, items)
    return items


def _camera_from_enum(identifier):
    if not identifier or identifier in {"INHERIT", "NONE"}:
        return None
    return next(
        (
            camera
            for camera in bpy.data.objects
            if camera.type == "CAMERA"
            and _camera_enum_identifier(camera) == identifier
        ),
        None,
    )


class TS_OT_open_take_settings(bpy.types.Operator):
    """Open one row-targeted settings dialog with explicit take application."""

    bl_idname = "take_system.open_take_settings"
    bl_label = "Open Take Settings"
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})
    settings_kind: EnumProperty(
        name="Settings",
        items=(
            ("CAMERA", "Camera", "Choose the camera used by this take"),
            (
                "RENDER",
                "Render",
                "Edit this take's inherited render-setting groups",
            ),
        ),
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    @classmethod
    def description(cls, _context, properties):
        if properties.settings_kind == "CAMERA":
            return (
                "Choose this take's camera; applying the take is confirmed "
                "first when it does not control the live scene"
            )
        return (
            "Edit this take's render overrides; applying the take is "
            "confirmed first when it does not control the live scene"
        )

    def _take(self, scene):
        return engine.find_take(scene, self.take_uuid)

    def _schedule_editor(self, context):
        take_uuid = self.take_uuid
        settings_kind = self.settings_kind
        window = context.window
        area = context.area
        region = context.region
        if window is not None:
            for candidate in window.screen.areas:
                candidate.tag_redraw()

        def open_editor():
            try:
                windows = tuple(bpy.context.window_manager.windows)
                if window not in windows:
                    return None
                override = {"window": window}
                if any(candidate == area for candidate in window.screen.areas):
                    override["area"] = area
                    if any(
                        candidate == region for candidate in area.regions
                    ):
                        override["region"] = region
                with bpy.context.temp_override(**override):
                    if settings_kind == "CAMERA":
                        result = bpy.ops.take_system.configure_take_camera(
                            "INVOKE_DEFAULT",
                            take_uuid=take_uuid,
                        )
                    else:
                        result = bpy.ops.take_system.edit_render_profile(
                            "INVOKE_DEFAULT",
                            take_uuid=take_uuid,
                        )
                _LAST_SETTINGS_LAUNCH.clear()
                _LAST_SETTINGS_LAUNCH.update(
                    {
                        "take_uuid": take_uuid,
                        "settings_kind": settings_kind,
                        "result": frozenset(result),
                    }
                )
            except (
                engine.TakeSystemError,
                AttributeError,
                ReferenceError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                _LAST_SETTINGS_LAUNCH.clear()
                _LAST_SETTINGS_LAUNCH["error"] = str(exc)
                print(f"Take System: settings dialog could not open: {exc}")
            return None

        bpy.app.timers.register(open_editor, first_interval=0.01)
        return {"FINISHED"}

    def invoke(self, context, event):
        take = self._take(context.scene)
        if take is None:
            self.report({"ERROR"}, "The take no longer exists")
            return {"CANCELLED"}
        if take.uuid == context.scene.take_system.active_take_uuid:
            return self._schedule_editor(context)
        return context.window_manager.invoke_confirm(self, event)

    def draw(self, context):
        take = self._take(context.scene)
        if take is None:
            self.layout.label(text="The take no longer exists.", icon="ERROR")
            return
        settings_label = (
            "camera" if self.settings_kind == "CAMERA" else "render settings"
        )
        self.layout.label(
            text=f"'{take.name}' is not applied.",
            icon="INFO",
        )
        self.layout.label(
            text=f"Apply it and edit its {settings_label}?",
            icon="PLAY",
        )
        self.layout.label(
            text="Applying changes the live scene state.",
            icon="ERROR",
        )

    def execute(self, context):
        take = self._take(context.scene)
        if take is None:
            self.report({"ERROR"}, "The take no longer exists")
            return {"CANCELLED"}
        if take.uuid != context.scene.take_system.active_take_uuid:
            if not _prepare_recording_for_internal_change(self, context.scene):
                return {"CANCELLED"}
            try:
                engine.apply_take(context.scene, take.uuid, strict=True)
            except engine.TakeApplyError as exc:
                first = exc.report.issues[0].summary()
                self.report({"ERROR"}, f"Take could not be applied: {first}")
                return {"CANCELLED"}
            except engine.TakeSystemError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            recording.handle_internal_state_change(context.scene)
        return self._schedule_editor(context)


class TS_OT_configure_take_camera(bpy.types.Operator):
    """Commit the selected take's staged Camera pointer."""

    bl_idname = "take_system.configure_take_camera"
    bl_label = "Set Take Camera"
    bl_description = (
        "Store the Camera shown in Take Manager as this take's canonical "
        "Scene camera override"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})
    camera_choice: EnumProperty(
        name="Camera",
        description="Camera object to store, or None",
        items=_camera_enum_items,
    )

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        take = engine.find_take(context.scene, requested)
        if take is None:
            self.report({"ERROR"}, "No take is selected")
            return {"CANCELLED"}
        if take.uuid != context.scene.take_system.active_take_uuid:
            self.report({"ERROR"}, "Apply the take before configuring it")
            return {"CANCELLED"}
        self.take_uuid = take.uuid
        direct_camera = engine.direct_camera_override(context.scene, take)
        if not take.is_main and direct_camera is None:
            self.camera_choice = "INHERIT"
            return context.window_manager.invoke_props_dialog(self)
        try:
            camera, _source_uuid = engine.resolved_camera(
                context.scene,
                self.take_uuid,
            )
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if isinstance(camera, bpy.types.Object) and camera.type == "CAMERA":
            self.camera_choice = _camera_enum_identifier(camera)
        else:
            self.camera_choice = "NONE"
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, _context):
        self.layout.prop(self, "camera_choice")

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        take = engine.find_take(context.scene, requested)
        if take is None:
            self.report({"ERROR"}, "The selected take no longer exists")
            return {"CANCELLED"}
        if take.uuid != context.scene.take_system.active_take_uuid:
            self.report({"ERROR"}, "Apply the selected take first")
            return {"CANCELLED"}
        if self.camera_choice == "INHERIT":
            if take.is_main:
                self.report({"ERROR"}, "Main cannot inherit a camera")
                return {"CANCELLED"}
            try:
                if engine.direct_camera_override(context.scene, take) is None:
                    self.report(
                        {"INFO"},
                        f"'{take.name}' already inherits its camera",
                    )
                    return {"FINISHED"}
                engine.remove_take_camera(context.scene, take.uuid)
            except engine.TakeApplyError as exc:
                first = exc.report.issues[0].summary()
                self.report(
                    {"ERROR"},
                    f"Camera inheritance could not be applied: {first}",
                )
                return {"CANCELLED"}
            except engine.TakeSystemError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            recording.handle_internal_state_change(context.scene)
            self.report(
                {"INFO"},
                f"'{take.name}' now inherits its parent camera",
            )
            return {"FINISHED"}
        if self.camera_choice:
            chosen_camera = _camera_from_enum(self.camera_choice)
            if (
                self.camera_choice != "NONE"
                and chosen_camera is None
            ):
                self.report(
                    {"ERROR"},
                    "The selected Camera object no longer exists",
                )
                return {"CANCELLED"}
        else:
            # Scripting compatibility for callers that execute directly
            # without the interactive camera dialog.
            chosen_camera = take.camera_override
        try:
            report = engine.configure_take_camera(
                context.scene,
                take.uuid,
                chosen_camera,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Camera could not be applied: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        camera_name = chosen_camera.name if chosen_camera is not None else "None"
        self.report(
            {"INFO"},
            f"Set '{take.name}' camera to {camera_name} "
            f"({report.captured} override)",
        )
        return {"FINISHED"}


class TS_OT_clear_take_camera(bpy.types.Operator):
    """Remove the selected take's direct camera override."""

    bl_idname = "take_system.clear_take_camera"
    bl_label = "Clear Take Camera"
    bl_description = (
        "Remove this take's direct camera record and inherit its parent camera"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        selected = _selected_applied_take(cls, context)
        return (
            selected is not None
            and engine.direct_camera_override(context.scene, selected)
            is not None
        )

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        try:
            engine.remove_take_camera(context.scene, requested)
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report({"ERROR"}, f"Camera could not be cleared: {first}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report({"INFO"}, "Direct take camera removed; parent is inherited")
        return {"FINISHED"}


class TS_OT_edit_render_profile(bpy.types.Operator):
    """Edit independently inherited render-setting groups in one dialog."""

    bl_idname = "take_system.edit_render_profile"
    bl_label = "Apply Render Profile"
    bl_description = (
        "Edit this applied take's sampling, resolution, output, transparency, "
        "and color-management overrides"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})
    use_engine_sampling: BoolProperty(
        name="Override Engine & Sampling",
        description=(
            "Store engine and sampling settings directly on this take; "
            "disable to inherit them"
        ),
    )
    use_resolution: BoolProperty(
        name="Override Resolution & Frame",
        description=(
            "Store resolution, aspect, and frame-rate settings directly on "
            "this take; disable to inherit them"
        ),
    )
    use_output: BoolProperty(
        name="Override Output & Format",
        description=(
            "Store output path and image-format settings directly on this "
            "take; disable to inherit them"
        ),
    )
    use_transparency: BoolProperty(
        name="Override Film Transparency",
        description=(
            "Store film transparency directly on this take; disable to "
            "inherit it"
        ),
    )
    use_color_management: BoolProperty(
        name="Override Color Management",
        description=(
            "Store view transform, look, exposure, and gamma directly on this "
            "take; disable to inherit them"
        ),
    )

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        try:
            if bpy.app.is_job_running("RENDER"):
                cls.poll_message_set(
                    "Render profiles cannot be edited while Blender is rendering"
                )
                return False
        except (AttributeError, TypeError):
            pass
        return True

    def _take(self, scene):
        selected = engine.selected_take(scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        return engine.find_take(scene, requested)

    def _set_group_flags(self, scene, take):
        direct_groups = engine.direct_render_profile_groups(scene, take)
        use_all = take.is_main
        self.use_engine_sampling = (
            use_all
            or engine.RENDER_GROUP_ENGINE_SAMPLING in direct_groups
        )
        self.use_resolution = (
            use_all
            or engine.RENDER_GROUP_RESOLUTION in direct_groups
        )
        self.use_output = (
            use_all
            or engine.RENDER_GROUP_OUTPUT in direct_groups
        )
        self.use_transparency = (
            use_all
            or engine.RENDER_GROUP_TRANSPARENCY in direct_groups
        )
        self.use_color_management = (
            use_all
            or engine.RENDER_GROUP_COLOR_MANAGEMENT in direct_groups
        )

    def _enabled_groups(self, take):
        if take.is_main:
            return set(engine.RENDER_PROFILE_GROUPS)
        enabled = set()
        if self.use_engine_sampling:
            enabled.add(engine.RENDER_GROUP_ENGINE_SAMPLING)
        if self.use_resolution:
            enabled.add(engine.RENDER_GROUP_RESOLUTION)
        if self.use_output:
            enabled.add(engine.RENDER_GROUP_OUTPUT)
        if self.use_transparency:
            enabled.add(engine.RENDER_GROUP_TRANSPARENCY)
        if self.use_color_management:
            enabled.add(engine.RENDER_GROUP_COLOR_MANAGEMENT)
        return enabled

    def invoke(self, context, _event):
        scene = context.scene
        take = self._take(scene)
        if take is None:
            self.report({"ERROR"}, "No valid take was chosen")
            return {"CANCELLED"}
        if scene.take_system.active_take_uuid != take.uuid:
            self.report(
                {"ERROR"},
                "Apply the take before editing its render profile",
            )
            return {"CANCELLED"}
        if not _prepare_recording_for_internal_change(self, scene):
            return {"CANCELLED"}
        if recording.active_take(scene) is not None:
            recording.stop(
                scene,
                commit_pending=False,
                reason="Recording stopped while the render profile is edited",
            )
        try:
            self._render_profile_baseline = engine.snapshot_render_profile(
                scene
            )
            self._render_output_baseline = take.render_output_path
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.take_uuid = take.uuid
        self._set_group_flags(scene, take)
        return context.window_manager.invoke_props_dialog(self, width=560)

    def _group_box(
        self,
        layout,
        take,
        *,
        property_name,
        label,
        icon,
    ):
        box = layout.box()
        header = box.row(align=True)
        if take.is_main:
            header.label(text=f"{label} — Default", icon=icon)
            enabled = True
        else:
            header.prop(
                self,
                property_name,
                text=label,
                icon=icon,
                toggle=True,
            )
            enabled = bool(getattr(self, property_name))
        controls = box.column(align=True)
        controls.enabled = enabled
        return box, controls, enabled

    def _draw_engine_sampling(self, layout, scene, take):
        _box, controls, _enabled = self._group_box(
            layout,
            take,
            property_name="use_engine_sampling",
            label="Engine & Sampling",
            icon="RENDER_STILL",
        )
        controls.prop(scene.render, "engine", text="Render Engine")
        engine_identifier = scene.render.engine
        if engine_identifier == "CYCLES" and hasattr(scene, "cycles"):
            cycles = scene.cycles
            controls.prop(cycles, "samples", text="Max Samples")
            if hasattr(cycles, "use_adaptive_sampling"):
                controls.prop(
                    cycles,
                    "use_adaptive_sampling",
                    text="Adaptive Sampling",
                )
            adaptive = controls.column(align=True)
            adaptive.enabled = bool(
                getattr(cycles, "use_adaptive_sampling", False)
            )
            if hasattr(cycles, "adaptive_min_samples"):
                adaptive.prop(
                    cycles,
                    "adaptive_min_samples",
                    text="Min Samples",
                )
            if hasattr(cycles, "adaptive_threshold"):
                adaptive.prop(
                    cycles,
                    "adaptive_threshold",
                    text="Noise Threshold",
                )
            if hasattr(cycles, "use_denoising"):
                controls.prop(cycles, "use_denoising", text="Denoising")
                denoiser = controls.column(align=True)
                denoiser.enabled = bool(cycles.use_denoising)
                if hasattr(cycles, "denoiser"):
                    denoiser.prop(cycles, "denoiser", text="Denoiser")
        elif engine_identifier in {
            "BLENDER_EEVEE",
            "BLENDER_EEVEE_NEXT",
        } and hasattr(scene, "eevee"):
            if hasattr(scene.eevee, "taa_render_samples"):
                controls.prop(
                    scene.eevee,
                    "taa_render_samples",
                    text="Render Samples",
                )
        elif engine_identifier == "BLENDER_WORKBENCH":
            shading = scene.display.shading
            for property_name, label in (
                ("light", "Lighting"),
                ("color_type", "Color"),
                ("show_shadows", "Shadows"),
                ("show_cavity", "Cavity"),
                ("show_specular_highlight", "Specular Highlights"),
            ):
                if hasattr(shading, property_name):
                    controls.prop(shading, property_name, text=label)

    def _draw_resolution(self, layout, scene, take):
        _box, controls, _enabled = self._group_box(
            layout,
            take,
            property_name="use_resolution",
            label="Resolution & Frame",
            icon="FULLSCREEN_ENTER",
        )
        row = controls.row(align=True)
        row.prop(scene.render, "resolution_x", text="Width")
        row.prop(scene.render, "resolution_y", text="Height")
        controls.prop(scene.render, "resolution_percentage", text="Scale")
        row = controls.row(align=True)
        row.prop(scene.render, "pixel_aspect_x", text="Aspect X")
        row.prop(scene.render, "pixel_aspect_y", text="Y")
        row = controls.row(align=True)
        row.prop(scene.render, "fps", text="Frame Rate")
        row.prop(scene.render, "fps_base", text="Base")

    def _draw_output(self, layout, scene, take):
        box, controls, _enabled = self._group_box(
            layout,
            take,
            property_name="use_output",
            label="Output & Format",
            icon="OUTPUT",
        )
        controls.prop(scene.render, "filepath", text="Scene Output")
        controls.prop(
            take,
            "render_output_path",
            text="Batch Output Override",
        )
        controls.label(
            text="Blank batch output derives a unique take name.",
            icon="INFO",
        )
        controls.prop(
            scene.render.image_settings,
            "file_format",
            text="File Format",
        )
        image_settings = scene.render.image_settings
        row = controls.row(align=True)
        if hasattr(image_settings, "color_mode"):
            row.prop(image_settings, "color_mode", text="Color")
        if hasattr(image_settings, "color_depth"):
            row.prop(image_settings, "color_depth", text="Depth")
        if image_settings.file_format == "PNG" and hasattr(
            image_settings,
            "compression",
        ):
            controls.prop(image_settings, "compression", text="Compression")
        elif hasattr(image_settings, "quality"):
            controls.prop(image_settings, "quality", text="Quality")
        row = controls.row(align=True)
        row.prop(scene.render, "use_file_extension", text="File Extension")
        row.prop(scene.render, "use_overwrite", text="Overwrite")
        controls.prop(scene.render, "use_placeholder", text="Placeholders")
        if image_settings.file_format == "FFMPEG":
            box.label(
                text="Batch rendering accepts still-image formats only.",
                icon="ERROR",
            )

    def _draw_transparency(self, layout, scene, take):
        _box, controls, _enabled = self._group_box(
            layout,
            take,
            property_name="use_transparency",
            label="Film Transparency",
            icon="IMAGE_ALPHA",
        )
        controls.prop(
            scene.render,
            "film_transparent",
            text="Transparent Background",
        )
        if (
            scene.render.engine == "CYCLES"
            and hasattr(scene, "cycles")
            and hasattr(scene.cycles, "film_transparent_glass")
        ):
            glass = controls.column(align=True)
            glass.enabled = bool(scene.render.film_transparent)
            glass.prop(
                scene.cycles,
                "film_transparent_glass",
                text="Transparent Glass",
            )

    def _draw_color_management(self, layout, scene, take):
        _box, controls, _enabled = self._group_box(
            layout,
            take,
            property_name="use_color_management",
            label="Color Management",
            icon="COLOR",
        )
        view_settings = scene.view_settings
        controls.prop(view_settings, "view_transform", text="View Transform")
        controls.prop(view_settings, "look", text="Look")
        controls.prop(view_settings, "exposure", text="Exposure")
        controls.prop(view_settings, "gamma", text="Gamma")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        take = self._take(scene)
        if take is None:
            layout.label(text="The selected take no longer exists", icon="ERROR")
            return
        if take.is_main:
            layout.label(
                text="Main defines the inherited render defaults.",
                icon="HOME",
            )
        else:
            layout.label(
                text=(
                    "Enable only the groups this take should override; "
                    "disabled groups inherit."
                ),
                icon="CON_CHILDOF",
            )
        self._draw_engine_sampling(layout, scene, take)
        self._draw_resolution(layout, scene, take)
        self._draw_output(layout, scene, take)
        self._draw_transparency(layout, scene, take)
        self._draw_color_management(layout, scene, take)

    def execute(self, context):
        scene = context.scene
        take = self._take(scene)
        if take is None:
            self.report({"ERROR"}, "No valid take was chosen")
            return {"CANCELLED"}
        baseline = getattr(self, "_render_profile_baseline", None)
        try:
            if baseline is None:
                baseline = engine.snapshot_render_profile(scene)
            report = engine.configure_render_profile(
                scene,
                take.uuid,
                self._enabled_groups(take),
                baseline_values=baseline,
                batch_output_path=(
                    take.render_output_path
                    if take.is_main or self.use_output
                    else ""
                ),
                baseline_batch_output_path=getattr(
                    self,
                    "_render_output_baseline",
                    take.render_output_path,
                ),
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            recording.handle_internal_state_change(scene)
            self.report(
                {"ERROR"},
                f"Render profile could not be applied: {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            recording.handle_internal_state_change(scene)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(scene)
        self.report(
            {"INFO"},
            (
                f"Render profile updated on '{report.take_name}': "
                f"{report.configured} stored, {report.removed} inherited"
            ),
        )
        return {"FINISHED"}

    def cancel(self, context):
        scene = getattr(context, "scene", None)
        if scene is None or not hasattr(scene, "take_system"):
            return
        baseline = getattr(self, "_render_profile_baseline", None)
        if baseline is None:
            return
        take = self._take(scene)
        if take is not None:
            take.render_output_path = getattr(
                self,
                "_render_output_baseline",
                take.render_output_path,
            )
        try:
            engine.restore_render_profile(scene, baseline)
        except engine.TakeSystemError as exc:
            self.report(
                {"ERROR"},
                f"Render-profile cancellation could not fully restore: {exc}",
            )
        recording.handle_internal_state_change(scene)


class TS_OT_capture_render_settings(bpy.types.Operator):
    """Initialize or update the selected take's current render preset."""

    bl_idname = "take_system.capture_render_settings"
    bl_label = "Capture Current Render Settings"
    bl_description = (
        "Capture the current portable render preset; run before editing to "
        "seed Main, then again afterward to update this take"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _selected_applied_take(cls, context) is not None

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        try:
            report = engine.capture_render_settings(
                context.scene,
                requested,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Render settings could not be applied: {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report(
            {"INFO"},
            f"Captured {report.captured} render settings on "
            f"'{report.take_name}'",
        )
        return {"FINISHED"}


class TS_OT_clear_render_settings(bpy.types.Operator):
    """Remove the selected take's direct Phase 5 render preset."""

    bl_idname = "take_system.clear_render_settings"
    bl_label = "Clear Render Settings"
    bl_description = (
        "Remove this take's direct render-setting records and inherit its parent"
    )
    bl_options = {"REGISTER", "UNDO"}

    take_uuid: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        selected = _selected_applied_take(cls, context)
        return (
            selected is not None
            and engine.take_has_render_settings(context.scene, selected)
        )

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        selected = engine.selected_take(context.scene)
        requested = self.take_uuid or (
            selected.uuid if selected is not None else ""
        )
        try:
            removed = engine.remove_render_settings(
                context.scene,
                requested,
            )
        except engine.TakeApplyError as exc:
            first = exc.report.issues[0].summary()
            self.report(
                {"ERROR"},
                f"Render settings could not be cleared: {first}",
            )
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        recording.handle_internal_state_change(context.scene)
        self.report({"INFO"}, f"Removed {removed} direct render settings")
        return {"FINISHED"}


def _render_still(scene, _item):
    """Synchronous renderer seam used by the batch operator and tests."""

    absolute_output = bpy.path.abspath(scene.render.filepath)
    output_directory = os.path.dirname(absolute_output)
    if output_directory:
        try:
            os.makedirs(output_directory, exist_ok=True)
        except OSError as exc:
            raise engine.TakeSystemError(
                f"Could not create render output directory "
                f"'{output_directory}': {exc}"
            ) from exc
    try:
        view_layer = bpy.context.view_layer
        if view_layer is not None:
            view_layer.update()
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    return bpy.ops.render.render(
        "EXEC_DEFAULT",
        write_still=True,
        scene=scene.name,
    )


class TS_OT_set_batch_inclusion(bpy.types.Operator):
    """Include or exclude every take from the still-render queue."""

    bl_idname = "take_system.set_batch_inclusion"
    bl_label = "Set Batch Inclusion"
    bl_description = "Include or exclude every take in the batch render queue"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Mode",
        items=(
            ("ALL", "Include All", "Include every take in the queue"),
            ("NONE", "Include None", "Exclude every take from the queue"),
        ),
        default="ALL",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    @classmethod
    def description(cls, _context, properties):
        if properties.mode == "NONE":
            return "Exclude every take from the batch render queue"
        return "Include every take in the batch render queue"

    def execute(self, context):
        include = self.mode == "ALL"
        changed = 0
        for take in context.scene.take_system.takes:
            if bool(take.include_in_render) != include:
                take.include_in_render = include
                changed += 1
        self.report(
            {"INFO"},
            (
                f"{'Included' if include else 'Excluded'} "
                f"{changed} take(s)"
            ),
        )
        return {"FINISHED"}


class TS_OT_preflight_batch(bpy.types.Operator):
    """Apply and restore the complete queue without writing render files."""

    bl_idname = "take_system.preflight_batch"
    bl_label = "Preflight Batch"
    bl_description = (
        "Deeply validate every included take by applying the complete queue, "
        "then restore the exact live scene state without rendering files"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        if not any(
            take.include_in_render
            for take in context.scene.take_system.takes
        ):
            cls.poll_message_set("No takes are included in batch rendering")
            return False
        try:
            if bpy.app.is_job_running("RENDER"):
                cls.poll_message_set("Blender is already rendering")
                return False
        except (AttributeError, TypeError):
            pass
        return True

    def execute(self, context):
        scene = context.scene
        if not _prepare_recording_for_internal_change(self, scene):
            return {"CANCELLED"}
        with recent.suspend_tracking():
            report = engine.preflight_take_batch(scene)
        engine.remember_batch_report(scene, report)
        recording.handle_internal_state_change(scene)
        if not report.ok:
            suffix = (
                "; scene restoration needs attention"
                if report.restoration_issues
                else ""
            )
            self.report(
                {"ERROR"},
                f"Batch preflight failed: {report.error or 'Unknown error'}"
                f"{suffix}",
            )
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Preflight passed for {report.queued} take(s); scene restored",
        )
        return {"FINISHED"}


class TS_OT_render_included_takes(bpy.types.Operator):
    """Synchronously render every take whose include toggle is enabled."""

    bl_idname = "take_system.render_included_takes"
    bl_label = "Review Batch Render"
    bl_description = (
        "Review final still-image destinations, then render every included "
        "take and restore the exact live scene state"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        if not _poll_editable_scene(cls, context):
            return False
        if not any(
            take.include_in_render
            for take in context.scene.take_system.takes
        ):
            cls.poll_message_set("No takes are included in batch rendering")
            return False
        try:
            if bpy.app.is_job_running("RENDER"):
                cls.poll_message_set("Blender is already rendering")
                return False
        except (AttributeError, TypeError):
            pass
        return True

    def invoke(self, context, _event):
        try:
            plan = engine.build_batch_plan(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not plan.queued:
            self.report({"ERROR"}, "No takes are included in batch rendering")
            return {"CANCELLED"}
        if plan.errors:
            self.report(
                {"ERROR"},
                f"Resolve queue errors before rendering: "
                f"{plan.errors[0].message}",
            )
            return {"CANCELLED"}
        confirm_text = f"Render {plan.queued} Take"
        if plan.queued != 1:
            confirm_text += "s"
        try:
            return context.window_manager.invoke_props_dialog(
                self,
                width=680,
                title="Review Batch Render",
                confirm_text=confirm_text,
            )
        except TypeError:
            return context.window_manager.invoke_props_dialog(
                self,
                width=680,
            )

    def draw(self, context):
        layout = self.layout
        try:
            plan = engine.build_batch_plan(context.scene)
        except engine.TakeSystemError as exc:
            layout.label(text=f"Queue error: {exc}", icon="ERROR")
            return
        layout.label(
            text=f"{plan.queued} still render(s), in hierarchy order",
            icon="RENDER_STILL",
        )
        for item in plan.queued_items:
            box = layout.box()
            header = box.row(align=True)
            header.label(text=item.take_name, icon="CHECKMARK")
            header.label(
                text=(
                    f"{item.file_format} | {item.resolution_x} x "
                    f"{item.resolution_y} @ {item.resolution_percentage}%"
                ),
            )
            box.label(
                text=f"Camera: {item.camera_name}",
                icon="CAMERA_DATA",
            )
            folder, filename = os.path.split(item.file_path)
            box.label(
                text=f"File: {filename or item.file_path}",
                icon="IMAGE_DATA",
            )
            if folder:
                box.label(
                    text=f"Folder: {folder}",
                    icon="FILE_FOLDER",
                )
            for issue in item.warnings:
                box.label(text=issue.message, icon="INFO")
        warning = layout.box()
        warning.label(
            text="Completed files cannot be undone if a later take fails.",
            icon="INFO",
        )
        warning.label(
            text="The applied/selected take and live scene values are restored.",
            icon="FILE_REFRESH",
        )

    def execute(self, context):
        scene = context.scene
        if not _prepare_recording_for_internal_change(self, scene):
            return {"CANCELLED"}
        try:
            with recent.suspend_tracking():
                report = engine.render_take_batch(scene, _render_still)
        except engine.BatchRenderError as exc:
            report = exc.report
            engine.remember_batch_report(scene, report)
            recording.handle_internal_state_change(scene)
            suffix = (
                f"; {len(report.rendered)} file(s) were already written"
                if report.rendered
                else ""
            )
            if report.restoration_issues:
                suffix += "; scene restoration needs attention"
            self.report({"ERROR"}, f"{exc}{suffix}")
            return {"CANCELLED"}
        except engine.TakeSystemError as exc:
            engine.remember_batch_report(
                scene,
                engine.BatchRenderReport(
                    error=str(exc),
                    restored=False,
                ),
            )
            recording.handle_internal_state_change(scene)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        engine.remember_batch_report(scene, report)
        recording.handle_internal_state_change(scene)
        self.report(
            {"INFO"},
            f"Rendered {len(report.rendered)} included take(s); scene restored",
        )
        return {"FINISHED"}


class TS_OT_capture_button_override(bpy.types.Operator):
    """Capture the RNA property behind the clicked UI control."""

    bl_idname = "take_system.capture_button_override"
    bl_label = "Add/Update Take Override"
    bl_description = (
        "Capture this property on the active take; run once before editing to "
        "seed Main, then again after editing to update the take"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            _poll_editable_scene(cls, context)
            and getattr(context, "button_pointer", None) is not None
            and getattr(context, "button_prop", None) is not None
        )

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            target_id, data_path = _button_target_and_path(context)
            result = engine.capture_override(
                context.scene,
                target_id,
                data_path,
            )
            take = engine.active_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        recording.handle_internal_state_change(context.scene)
        action = "Added" if result.created else "Updated"
        suffix = "; Main baseline stored" if result.main_seeded else ""
        self.report(
            {"INFO"},
            f"{action} '{data_path}' on '{take.name}'{suffix}",
        )
        return {"FINISHED"}


class TS_OT_capture_path_override(bpy.types.Operator):
    """Capture an explicitly entered target datablock and RNA path."""

    bl_idname = "take_system.capture_path_override"
    bl_label = "Capture Take Override by RNA Path"
    bl_description = "Capture a property when its UI control has no context entry"
    bl_options = {"REGISTER", "UNDO"}

    target_id_type: EnumProperty(name="Datablock Type", items=CAPTURE_ID_TYPES)
    target_id_name: StringProperty(name="Datablock Name")
    target_library_path: StringProperty(
        name="Library Path",
        description="Leave empty for local datablocks",
        subtype="FILE_PATH",
    )
    data_path: StringProperty(
        name="RNA Data Path",
        description='For example: modifiers["Bevel"].width',
    )

    @classmethod
    def poll(cls, context):
        return _poll_editable_scene(cls, context)

    def invoke(self, context, _event):
        try:
            engine.ensure_main_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        active_object = context.active_object
        if active_object is not None and not self.target_id_name:
            self.target_id_type = "Object"
            self.target_id_name = active_object.name
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "target_id_type")
        layout.prop(self, "target_id_name")
        layout.prop(self, "target_library_path")
        layout.prop(self, "data_path")

    def execute(self, context):
        if not _prepare_recording_for_internal_change(self, context.scene):
            return {"CANCELLED"}
        try:
            target_id = engine.find_id_by_name(
                self.target_id_type,
                self.target_id_name,
                self.target_library_path,
            )
            result = engine.capture_override(
                context.scene,
                target_id,
                self.data_path,
            )
            take = engine.active_take(context.scene)
        except engine.TakeSystemError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        recording.handle_internal_state_change(context.scene)
        action = "Added" if result.created else "Updated"
        suffix = "; Main baseline stored" if result.main_seeded else ""
        self.report(
            {"INFO"},
            f"{action} '{self.data_path}' on '{take.name}'{suffix}",
        )
        return {"FINISHED"}


def draw_button_context_menu(self, context):
    if not TS_OT_capture_button_override.poll(context):
        return
    try:
        _button_target_and_path(context)
    except engine.TakeSystemError:
        return
    self.layout.separator()
    self.layout.operator(
        TS_OT_capture_button_override.bl_idname,
        icon="DECORATE_KEYFRAME",
    )


def clear_runtime_caches():
    _TAKE_ENUM_ITEMS_CACHE.clear()
    _PARENT_ENUM_ITEMS_CACHE.clear()
    _CAMERA_ENUM_ITEMS_CACHE.clear()
    _LAST_SETTINGS_LAUNCH.clear()


def last_settings_launch():
    """Return diagnostic state for the most recent deferred row editor."""

    return dict(_LAST_SETTINGS_LAUNCH)


CLASSES = (
    TS_OT_initialize,
    TS_OT_add_take,
    TS_OT_apply_take,
    TS_OT_apply_active_take,
    TS_OT_apply_selected_take,
    TS_OT_duplicate_take,
    TS_OT_delete_take,
    TS_OT_reparent_take,
    TS_OT_remove_override,
    TS_OT_open_manager,
    TS_OT_capture_recent_action,
    TS_OT_toggle_recording,
    TS_OT_flush_recording,
    TS_OT_open_take_settings,
    TS_OT_configure_take_camera,
    TS_OT_clear_take_camera,
    TS_OT_edit_render_profile,
    TS_OT_capture_render_settings,
    TS_OT_clear_render_settings,
    TS_OT_set_batch_inclusion,
    TS_OT_preflight_batch,
    TS_OT_render_included_takes,
    TS_OT_capture_button_override,
    TS_OT_capture_path_override,
)
