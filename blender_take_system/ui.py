"""Dockable Blender UI for browsing and editing scene-local takes."""

import bpy

from . import engine, recent


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
        record_indicator.enabled = False
        record_indicator.prop(
            item,
            "is_recording",
            text="",
            icon="REC",
            toggle=True,
        )
        row.label(
            text="",
            icon=(
                "CAMERA_DATA"
                if engine.direct_camera_override(scene, item) is not None
                else "BLANK1"
            ),
        )
        row.label(
            text="",
            icon=(
                "OUTPUT"
                if engine.take_has_render_settings(scene, item)
                else "BLANK1"
            ),
        )
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

        note = layout.row()
        note.enabled = False
        note.label(
            text="Automatic record mode is reserved for Phase 6.",
            icon="INFO",
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
            )
            resolution_x = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_x",
            )
            resolution_y = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_y",
            )
            resolution_percentage = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.resolution_percentage",
            )
            file_format = engine.resolved_scene_value(
                scene,
                selected.uuid,
                "render.image_settings.file_format",
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
        render_controls = render_box.column(align=True)
        render_controls.enabled = is_applied
        render_row = render_controls.row(align=True)
        capture_render = render_row.operator(
            "take_system.capture_render_settings",
            text=(
                "Update Current Settings"
                if has_render_settings
                else "Initialize Current Settings"
            ),
            icon="DECORATE_KEYFRAME",
        )
        capture_render.take_uuid = selected.uuid
        clear_render_slot = render_row.row(align=True)
        clear_render_slot.enabled = has_render_settings
        clear_render = clear_render_slot.operator(
            "take_system.clear_render_settings",
            text="Inherit",
            icon="X",
        )
        clear_render.take_uuid = selected.uuid
        render_box.label(
            text="Initialize before editing; update after making changes.",
            icon="INFO",
        )
        if not is_applied:
            layout.label(
                text="Apply this take before changing its camera or preset.",
                icon="INFO",
            )


class TS_PT_take_batch_render(bpy.types.Panel):
    """Functional synchronous still-render queue for included takes."""

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
        selected = engine.selected_take(scene)
        included = sum(take.include_in_render for take in takes)
        layout.label(
            text=f"Included: {included} of {len(takes)}",
            icon="RENDER_STILL",
        )
        if selected is not None:
            selected_box = layout.box()
            selected_box.label(
                text=f"Selected: {selected.name}",
                icon="RESTRICT_SELECT_OFF",
            )
            selected_box.prop(
                selected,
                "include_in_render",
                text="Include in Batch",
            )
            selected_box.prop(
                selected,
                "render_output_path",
                text="Output Override",
            )
            selected_box.label(
                text="Blank output derives a unique name from Scene Output.",
                icon="INFO",
            )

        render_row = layout.row()
        render_row.scale_y = 1.5
        render_row.operator(
            "take_system.render_included_takes",
            text="Render Included Takes",
            icon="RENDER_STILL",
        )
        layout.label(
            text="Synchronous still renders; live scene state is restored.",
            icon="FILE_REFRESH",
        )
        layout.label(
            text="Files written before an error cannot be undone.",
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
