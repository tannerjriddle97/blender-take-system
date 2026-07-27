"""Dockable Blender UI for browsing and editing scene-local takes."""

import os

import bpy

from . import engine, recent, recording


def _take_row(scene, take_uuid):
    for row in engine.take_hierarchy_rows(scene):
        if row.take.uuid == take_uuid:
            return row
    return None


def _override_target_text(override):
    try:
        target = override.target_id
    except ReferenceError:
        target = None
    if target is not None:
        try:
            return f"{target.bl_rna.name}: {target.name_full}", False
        except (AttributeError, ReferenceError):
            pass
    target_type = override.target_id_type or "Datablock"
    target_name = override.target_id_name or "<missing>"
    return f"{target_type}: {target_name}", True


def _override_value_text(override):
    try:
        target = override.target_id
        if engine.is_layer_collection_exclude_path(
            target,
            override.data_path,
        ):
            return (
                "Disabled"
                if bool(engine.decoded_override_value(override))
                else "Enabled"
            )
        return engine.override_value_as_text(override)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        TypeError,
        ValueError,
    ) as exc:
        return f"<unreadable: {exc}>"


class TS_UL_takes(bpy.types.UIList):
    """Depth-first hierarchy view backed by the scene's take collection."""

    bl_idname = "TS_UL_takes"

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if not items:
            return flags, []
        try:
            rows = engine.take_hierarchy_rows(context.scene)
        except (engine.TakeSystemError, AttributeError, ReferenceError):
            return flags, []
        new_order = list(range(len(items)))
        for display_index, hierarchy_row in enumerate(rows):
            if hierarchy_row.collection_index < len(new_order):
                new_order[hierarchy_row.collection_index] = display_index
        return flags, new_order

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index=0,
        flt_flag=0,
    ):
        del data, icon, active_data, active_propname, index, flt_flag
        scene = context.scene
        state = scene.take_system
        hierarchy_row = _take_row(scene, item.uuid)
        depth = hierarchy_row.depth if hierarchy_row is not None else 0
        issue = hierarchy_row.issue if hierarchy_row is not None else ""

        row = layout.row(align=True)
        for _unused in range(depth):
            row.separator()

        applied = item.uuid == state.active_take_uuid
        apply_slot = row.row(align=True)
        apply_slot.operator_context = "EXEC_DEFAULT"
        apply_operator = apply_slot.operator(
            "take_system.apply_take",
            text="",
            icon="RADIOBUT_ON" if applied else "RADIOBUT_OFF",
            emboss=False,
        )
        apply_operator.take_uuid = item.uuid

        if item.is_main:
            row.label(text=item.name, icon="HOME")
        else:
            row.prop(item, "name", text="", emboss=False)

        if issue:
            row.label(text="", icon="ERROR")

        row.label(text=str(len(item.overrides)), icon="DECORATE")

        record_indicator = row.row(align=True)
        record_indicator.enabled = not item.is_main and applied
        record_operator = record_indicator.operator(
            "take_system.toggle_recording",
            text="",
            icon="REC",
            depress=bool(item.is_recording),
        )
        record_operator.take_uuid = item.uuid
        row.label(
            text="",
            icon=(
                "CAMERA_DATA"
                if engine.direct_camera_override(scene, item) is not None
                else "BLANK1"
            ),
        )
        render_profile_slot = row.row(align=True)
        render_profile_slot.enabled = applied
        edit_render_profile = render_profile_slot.operator(
            "take_system.edit_render_profile",
            text="",
            icon="OUTPUT",
            depress=engine.take_has_render_settings(scene, item),
        )
        edit_render_profile.take_uuid = item.uuid
        row.prop(
            item,
            "include_in_render",
            text="",
            icon=(
                "RESTRICT_RENDER_OFF"
                if item.include_in_render
                else "RESTRICT_RENDER_ON"
            ),
            toggle=True,
        )


class TS_UL_overrides(bpy.types.UIList):
    """Direct override records for the selected take."""

    bl_idname = "TS_UL_overrides"

    def draw_item(
        self,
        context,
        layout,
        data,
        item,
        icon,
        active_data,
        active_propname,
        index=0,
        flt_flag=0,
    ):
        del icon, active_data, active_propname, index, flt_flag
        target_text, missing = _override_target_text(item)
        row = layout.row(align=True)
        row.label(
            text=target_text,
            icon="ERROR" if missing else "OBJECT_DATA",
        )
        row.label(text=item.data_path or "<missing path>", icon="RNA")
        row.label(text=_override_value_text(item))
        remove_operator = row.operator(
            "take_system.remove_override",
            text="",
            icon="X",
            emboss=False,
        )
        remove_operator.take_uuid = data.uuid
        remove_operator.override_uuid = item.uuid


class TS_PT_take_manager(bpy.types.Panel):
    """Main Take Manager panel in the dockable Properties editor."""

    bl_idname = "SCENE_PT_take_manager"
    bl_label = "Take Manager"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = 90

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return scene is not None and hasattr(scene, "take_system")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        state = scene.take_system

        if not state.takes:
            layout.label(text="This scene has no Main take.", icon="INFO")
            layout.operator("take_system.initialize", icon="FILE_REFRESH")
            return

        selected = engine.selected_take(scene)
        applied = engine.find_take(scene, state.active_take_uuid)
        status = layout.column(align=True)
        status.label(
            text=(
                f"Selected: {selected.name}"
                if selected is not None
                else "Selected: <none>"
            ),
            icon="RESTRICT_SELECT_OFF",
        )
        status.label(
            text=(
                f"Applied: {applied.name}"
                if applied is not None
                else "Applied: <missing>"
            ),
            icon="PLAY",
        )

        layout.template_list(
            "TS_UL_takes",
            "",
            state,
            "takes",
            state,
            "active_take_index",
            rows=7,
        )

        toolbar = layout.row(align=True)
        add_top = toolbar.operator(
            "take_system.add_take",
            text="Top-Level",
            icon="ADD",
        )
        add_top.parent_mode = "MAIN"
        add_child = toolbar.operator(
            "take_system.add_take",
            text="Child",
            icon="CON_CHILDOF",
        )
        add_child.parent_mode = "SELECTED"

        editable = selected is not None and not selected.is_main
        edit_row = layout.row(align=True)
        edit_row.enabled = editable
        duplicate_operator = edit_row.operator(
            "take_system.duplicate_take",
            text="Duplicate",
            icon="DUPLICATE",
        )
        delete_operator = edit_row.operator(
            "take_system.delete_take",
            text="Delete",
            icon="TRASH",
        )
        reparent_operator = edit_row.operator(
            "take_system.reparent_take",
            text="Reparent",
            icon="LINKED",
        )
        if selected is not None:
            duplicate_operator.take_uuid = selected.uuid
            delete_operator.take_uuid = selected.uuid
            reparent_operator.take_uuid = selected.uuid

        apply_row = layout.row(align=True)
        apply_row.scale_y = 1.25
        apply_row.operator(
            "take_system.apply_selected_take",
            text="Apply Selected Take",
            icon="PLAY",
        )
        apply_row.operator(
            "take_system.apply_active_take",
            text="Reapply",
            icon="FILE_REFRESH",
        )

        recording_box = layout.box()
        recording_box.label(
            text=recording.recording_status_text(scene),
            icon="REC",
        )
        recording_row = recording_box.row(align=True)
        recording_eligible = (
            selected is not None
            and not selected.is_main
            and selected.uuid == state.active_take_uuid
        )
        recording_row.enabled = recording_eligible
        toggle_recording = recording_row.operator(
            "take_system.toggle_recording",
            text=(
                "Stop Recording"
                if selected is not None and selected.is_recording
                else "Start Recording"
            ),
            icon="REC",
            depress=bool(selected is not None and selected.is_recording),
        )
        if selected is not None:
            toggle_recording.take_uuid = selected.uuid
        flush_row = recording_row.row(align=True)
        flush_row.enabled = recording.active_take(scene) is not None
        flush_row.operator(
            "take_system.flush_recording",
            text="Commit Pending",
            icon="CHECKMARK",
        )
        if selected is not None and selected.uuid != state.active_take_uuid:
            recording_box.label(
                text="Apply the selected take before recording it.",
                icon="INFO",
            )

        recent_box = layout.box()
        recent_box.label(
            text=f"Recent: {recent.action_summary(scene)}",
            icon="RECOVER_LAST",
        )
        recent_row = recent_box.row()
        recent_row.enabled = (
            selected is not None
            and not selected.is_main
            and selected.uuid == state.active_take_uuid
            and recording.active_take(scene) is None
        )
        recent_row.scale_y = 1.15
        recent_row.operator(
            "take_system.capture_recent_action",
            text="Apply Most Recent Action as Overrides",
            icon="DECORATE_OVERRIDE",
        )
        if selected is not None and selected.uuid != state.active_take_uuid:
            recent_box.label(
                text="Apply the selected take before capturing live changes.",
                icon="INFO",
            )
        elif recording.active_take(scene) is not None:
            recent_box.label(
                text="Automatic recording will capture supported changes.",
                icon="INFO",
            )

        if selected is not None:
            details = layout.box()
            if selected.is_main:
                details.label(text="Name: Main", icon="LOCKED")
                details.label(text="Root take cannot be renamed or removed.")
            else:
                details.prop(selected, "name", text="Name")
                parent = engine.find_take(scene, selected.parent_uuid)
                details.label(
                    text=(
                        f"Parent: {parent.name}"
                        if parent is not None
                        else "Parent: <missing>"
                    ),
                    icon="CON_CHILDOF",
                )
            hierarchy_row = _take_row(scene, selected.uuid)
            if hierarchy_row is not None and hierarchy_row.issue:
                details.label(text=hierarchy_row.issue, icon="ERROR")
            details.label(
                text=f"Direct overrides: {len(selected.overrides)}",
                icon="DECORATE",
            )

class TS_PT_take_scene_settings(bpy.types.Panel):
    """Camera and render-setting controls for the selected take."""

    bl_idname = "SCENE_PT_take_scene_settings"
    bl_label = "Selected Take Camera & Render Settings"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_take_manager"

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            scene is not None
            and hasattr(scene, "take_system")
            and engine.selected_take(scene) is not None
        )

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        state = scene.take_system
        selected = engine.selected_take(scene)
        if selected is None:
            layout.label(text="No take selected", icon="INFO")
            return

        is_applied = selected.uuid == state.active_take_uuid
        direct_camera = engine.direct_camera_override(scene, selected)
        camera_box = layout.box()
        camera_box.label(text="Take Camera", icon="CAMERA_DATA")
        try:
            camera, source_uuid = engine.resolved_camera(
                scene,
                selected.uuid,
                repair=False,
            )
            source = engine.find_take(scene, source_uuid)
            camera_box.label(
                text=(
                    f"Resolved: {camera.name}"
                    if camera is not None
                    else "Resolved: None"
                ),
                icon="CAMERA_DATA" if camera is not None else "ERROR",
            )
            if direct_camera is not None:
                ownership = "Direct on this take"
            elif source is not None:
                ownership = f"Inherited from {source.name}"
            else:
                ownership = "Using the live scene camera"
            camera_box.label(text=ownership, icon="CON_CHILDOF")
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
        ) as exc:
            camera_box.label(text=f"Camera error: {exc}", icon="ERROR")

        camera_controls = camera_box.column(align=True)
        camera_controls.enabled = is_applied
        camera_row = camera_controls.row(align=True)
        set_camera = camera_row.operator(
            "take_system.configure_take_camera",
            text="Configure Camera...",
            icon="CAMERA_DATA",
        )
        set_camera.take_uuid = selected.uuid
        clear_slot = camera_row.row(align=True)
        clear_slot.enabled = direct_camera is not None
        clear_camera = clear_slot.operator(
            "take_system.clear_take_camera",
            text="Inherit",
            icon="X",
        )
        clear_camera.take_uuid = selected.uuid

        render_box = layout.box()
        render_box.label(text="Render Settings Preset", icon="OUTPUT")
        try:
            render_engine = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.engine",
                repair=False,
            )
            resolution_x = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_x",
                repair=False,
            )
            resolution_y = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_y",
                repair=False,
            )
            resolution_percentage = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_percentage",
                repair=False,
            )
            file_format = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.image_settings.file_format",
                repair=False,
            )
            render_box.label(
                text=(
                    f"{render_engine} | {resolution_x} × {resolution_y} "
                    f"@ {resolution_percentage}% | {file_format}"
                ),
                icon="SCENE_DATA",
            )
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
        ) as exc:
            render_box.label(text=f"Preset error: {exc}", icon="ERROR")

        has_render_settings = engine.take_has_render_settings(scene, selected)
        direct_groups = engine.direct_render_profile_groups(scene, selected)
        render_controls = render_box.column(align=True)
        render_controls.enabled = is_applied
        edit_profile = render_controls.operator(
            "take_system.edit_render_profile",
            text="Edit Render Profile...",
            icon="OUTPUT",
        )
        edit_profile.take_uuid = selected.uuid
        clear_render_slot = render_controls.row(align=True)
        clear_render_slot.enabled = has_render_settings and not selected.is_main
        clear_render = clear_render_slot.operator(
            "take_system.clear_render_settings",
            text="Inherit All Render Groups",
            icon="X",
        )
        clear_render.take_uuid = selected.uuid
        if selected.is_main:
            render_box.label(
                text="Main supplies the inherited default profile.",
                icon="HOME",
            )
        elif direct_groups:
            group_labels = ", ".join(
                engine.RENDER_PROFILE_GROUP_LABELS[group_identifier]
                for group_identifier in engine.RENDER_PROFILE_GROUPS
                if group_identifier in direct_groups
            )
            render_box.label(
                text=f"Direct groups: {group_labels}",
                icon="DECORATE_KEYFRAME",
            )
        else:
            render_box.label(
                text="All render groups inherit from Main/parent.",
                icon="CON_CHILDOF",
            )
        if not is_applied:
            layout.label(
                text="Apply this take before changing its camera or preset.",
                icon="INFO",
            )


class TS_PT_take_batch_render(bpy.types.Panel):
    """Read-only queue preview and deliberate synchronous render actions."""

    bl_idname = "SCENE_PT_take_batch_render"
    bl_label = "Batch Render Takes"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_take_manager"

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            scene is not None
            and hasattr(scene, "take_system")
            and bool(scene.take_system.takes)
        )

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        takes = scene.take_system.takes
        is_narrow = getattr(context.region, "width", 0) < 420
        try:
            plan = engine.build_batch_plan(scene)
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            TypeError,
            ValueError,
        ) as exc:
            layout.label(text=f"Queue error: {exc}", icon="ERROR")
            return

        layout.label(
            text=f"Queue: {plan.queued} of {len(takes)} takes",
            icon="RENDER_STILL",
        )
        bulk = layout.row(align=True)
        include_all = bulk.operator(
            "take_system.set_batch_inclusion",
            text="Include All",
        )
        include_all.mode = "ALL"
        include_none = bulk.operator(
            "take_system.set_batch_inclusion",
            text="Include None",
        )
        include_none.mode = "NONE"

        for item in plan.items:
            take = engine.find_take(scene, item.take_uuid)
            if take is None:
                continue
            box = layout.box()
            header = box.row(align=True)
            header.prop(take, "include_in_render", text="")
            header.label(
                text=f"{'    ' * item.depth}{item.take_name}",
                icon=(
                    "ERROR"
                    if item.included and item.errors
                    else "CHECKMARK"
                    if item.ready
                    else "CHECKBOX_DEHLT"
                ),
            )
            header.label(
                text=(
                    "Needs attention"
                    if item.included and item.errors
                    else "Ready"
                    if item.ready
                    else "Excluded"
                ),
            )

            details = box.column(align=True)
            camera_text = (
                f"Camera: {item.camera_name}"
                if item.camera_name
                else "Camera: <invalid>"
            )
            if item.camera_name and item.camera_source_name:
                camera_text += f" (from {item.camera_source_name})"
            details.label(text=camera_text, icon="CAMERA_DATA")
            if item.file_format:
                details.label(
                    text=(
                        f"{item.file_format} | {item.resolution_x} x "
                        f"{item.resolution_y} @ "
                        f"{item.resolution_percentage}%"
                    ),
                    icon="IMAGE_DATA",
                )
            if item.file_path:
                folder, filename = os.path.split(item.file_path)
                details.label(
                    text=f"File: {filename or item.file_path}",
                    icon="IMAGE_DATA",
                )
                if folder:
                    details.label(
                        text=f"Folder: {folder}",
                        icon="FILE_FOLDER",
                    )
            else:
                details.label(text="File: <unresolved>", icon="ERROR")
            if is_narrow:
                details.label(text="Output Override")
                details.prop(take, "render_output_path", text="")
            else:
                details.prop(
                    take,
                    "render_output_path",
                    text="Output Override",
                )
            for issue in item.issues:
                issue_row = box.row()
                issue_row.alert = (
                    item.included and issue.severity == "ERROR"
                )
                issue_row.label(
                    text=issue.message,
                    icon=(
                        "ERROR"
                        if issue.severity == "ERROR"
                        else "INFO"
                    ),
                )

        actions = layout.column(align=True)
        actions_enabled = plan.can_render
        preflight_row = actions.row()
        preflight_row.enabled = actions_enabled
        preflight_label = f"Preflight {plan.queued} Take"
        if plan.queued != 1:
            preflight_label += "s"
        preflight_row.operator(
            "take_system.preflight_batch",
            text=preflight_label,
            icon="CHECKMARK",
        )
        render_row = actions.row()
        render_row.enabled = actions_enabled
        render_row.scale_y = 1.4
        render_label = f"Review & Render {plan.queued} Take"
        if plan.queued != 1:
            render_label += "s"
        render_label += "..."
        render_row.operator(
            "take_system.render_included_takes",
            text=render_label,
            icon="RENDER_STILL",
        )
        if not plan.queued:
            layout.label(
                text="Include at least one take to build the queue.",
                icon="INFO",
            )
        elif plan.errors:
            layout.label(
                text=(
                    f"Resolve {len(plan.errors)} queue error(s) before "
                    "preflight or rendering."
                ),
                icon="ERROR",
            )
        else:
            layout.label(
                text="Preflight writes no files.",
                icon="INFO",
            )
            layout.label(
                text="Rendering runs synchronously.",
                icon="INFO",
            )

        report = engine.last_batch_report(scene)
        if report is not None:
            result = layout.box()
            result.label(text="Last Batch Result", icon="INFO")
            if isinstance(report, engine.BatchPreflightReport):
                if report.ok:
                    result.label(
                        text=(
                            f"Preflight passed for {report.queued} take(s); "
                            "scene restored."
                        ),
                        icon="CHECKMARK",
                    )
                else:
                    error_row = result.row()
                    error_row.alert = True
                    error_row.label(
                        text=f"Preflight failed: {report.error}",
                        icon="ERROR",
                    )
                    if report.restored:
                        result.label(
                            text="Live scene state restored.",
                            icon="FILE_REFRESH",
                        )
            elif isinstance(report, engine.BatchRenderReport):
                if report.ok:
                    result.label(
                        text=(
                            f"Rendered {len(report.rendered)} of "
                            f"{report.queued}; scene restored."
                        ),
                        icon="CHECKMARK",
                    )
                else:
                    error_row = result.row()
                    error_row.alert = True
                    error_row.label(
                        text=(
                            f"Render stopped"
                            f"{f' at {report.failed_take_name}' if report.failed_take_name else ''}: "
                            f"{report.error or 'Unknown error'}"
                        ),
                        icon="ERROR",
                    )
                if report.rendered:
                    result.label(
                        text=f"Files written: {len(report.rendered)}",
                        icon="FILE_FOLDER",
                    )
                    plan_by_uuid = {
                        item.take_uuid: item
                        for item in (
                            report.plan.items
                            if report.plan is not None
                            else ()
                        )
                    }
                    for rendered in report.rendered[:3]:
                        planned = plan_by_uuid.get(rendered.take_uuid)
                        result.label(
                            text=(
                                planned.file_path
                                if planned is not None
                                else rendered.output_path
                            ),
                        )
                    if len(report.rendered) > 3:
                        result.label(
                            text=f"...and {len(report.rendered) - 3} more",
                        )
                if report.restored and not report.ok:
                    result.label(
                        text="Live scene state restored.",
                        icon="FILE_REFRESH",
                    )
            if getattr(report, "restoration_issues", None):
                restoration_row = result.row()
                restoration_row.alert = True
                restoration_row.label(
                    text="Scene restoration needs attention.",
                    icon="ERROR",
                )


class TS_PT_take_overrides(bpy.types.Panel):
    """Inspector for the selected take's direct override records."""

    bl_idname = "SCENE_PT_take_overrides"
    bl_label = "Selected Take Overrides"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_parent_id = "SCENE_PT_take_manager"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return (
            scene is not None
            and hasattr(scene, "take_system")
            and engine.selected_take(scene) is not None
        )

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        state = scene.take_system
        selected = engine.selected_take(scene)
        if selected is None:
            layout.label(text="No take selected", icon="INFO")
            return

        if selected.overrides:
            layout.template_list(
                "TS_UL_overrides",
                "",
                selected,
                "overrides",
                state,
                "active_override_index",
                rows=5,
            )
        else:
            layout.label(
                text=f"'{selected.name}' has no direct overrides.",
                icon="INFO",
            )

        capture_column = layout.column()
        capture_column.enabled = selected.uuid == state.active_take_uuid
        capture_column.operator(
            "take_system.capture_path_override",
            text="Capture by RNA Path",
            icon="DECORATE_KEYFRAME",
        )
        if selected.uuid != state.active_take_uuid:
            layout.label(
                text="Apply this take before capturing live property values.",
                icon="INFO",
            )
        else:
            layout.label(
                text="Tip: right-click most property controls to capture them.",
                icon="QUESTION",
            )


CLASSES = (
    TS_UL_takes,
    TS_UL_overrides,
    TS_PT_take_manager,
    TS_PT_take_scene_settings,
    TS_PT_take_batch_render,
    TS_PT_take_overrides,
)
