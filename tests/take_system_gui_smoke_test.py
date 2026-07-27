"""Interactive Blender draw smoke test for Take Manager panels and dialogs."""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators, ui


SCREENSHOT = Path(
    os.environ.get(
        "TAKE_SYSTEM_GUI_SCREENSHOT",
        WORKSPACE / ".take_system_test" / "take_system_gui_smoke.png",
    )
)
RESULT = {
    "ok": False,
    "screenshot": str(SCREENSHOT),
}
TARGET_AREA = None


def fail(exc):
    RESULT["error"] = f"{type(exc).__name__}: {exc}"
    traceback.print_exc()
    print(f"TAKE_SYSTEM_GUI_SMOKE_FAILED {json.dumps(RESULT)}", flush=True)
    bpy.ops.wm.quit_blender()
    return None


def setup():
    global TARGET_AREA
    try:
        blender_take_system.register()
        if hasattr(bpy.context.preferences.view, "show_splash"):
            bpy.context.preferences.view.show_splash = False
        try:
            bpy.context.window.event_simulate(type="ESC", value="PRESS")
            bpy.context.window.event_simulate(type="ESC", value="RELEASE")
        except (AttributeError, RuntimeError, TypeError):
            pass
        scene = bpy.context.scene
        main = engine.ensure_main_take(scene)
        camera_data = bpy.data.cameras.new("TS_GUI_Camera_Data")
        camera = bpy.data.objects.new("TS_GUI_Camera", camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera
        scene.render.filepath = str(
            WORKSPACE / ".take_system_test" / "gui_render.png"
        )
        scene.render.image_settings.file_format = "PNG"
        child = engine.create_take(
            scene,
            "UI Preview",
            parent_uuid=main.uuid,
            make_active=False,
        )
        child.render_output_path = str(
            WORKSPACE / ".take_system_test" / "ui_preview.png"
        )

        plan = engine.build_batch_plan(scene)
        if not plan.can_render or plan.queued != 2:
            raise AssertionError(f"GUI fixture plan is not ready: {plan!r}")
        if not all(
            panel.is_registered
            for panel in (
                ui.TS_PT_take_manager,
                ui.TS_PT_take_scene_settings,
                ui.TS_PT_take_batch_render,
                ui.TS_PT_take_overrides,
            )
        ):
            raise AssertionError("One or more Take Manager panels are missing")
        if not all(
            operator.is_registered
            for operator in (
                operators.TS_OT_set_batch_inclusion,
                operators.TS_OT_preflight_batch,
                operators.TS_OT_render_included_takes,
            )
        ):
            raise AssertionError("One or more batch operators are missing")

        existing_properties = [
            area
            for area in bpy.context.screen.areas
            if area.type == "PROPERTIES"
        ]
        if (
            os.environ.get("TAKE_SYSTEM_GUI_USE_EXISTING") == "1"
            and existing_properties
        ):
            target_area = max(
                existing_properties,
                key=lambda candidate: candidate.width * candidate.height,
            )
        else:
            target_area = max(
                bpy.context.screen.areas,
                key=lambda candidate: candidate.width * candidate.height,
            )
            target_area.type = "PROPERTIES"
        TARGET_AREA = target_area
        target_area.spaces.active.context = "SCENE"
        for area in bpy.context.screen.areas:
            if area.type == "PROPERTIES":
                area.tag_redraw()
        RESULT.update(
            {
                "area_width": target_area.width,
                "region_width": next(
                    (
                        region.width
                        for region in target_area.regions
                        if region.type == "WINDOW"
                    ),
                    0,
                ),
                "queued": plan.queued,
            }
        )
        bpy.app.timers.register(scroll_to_batch, first_interval=0.45)
        if os.environ.get("TAKE_SYSTEM_GUI_REVIEW_DIALOG") == "1":
            bpy.app.timers.register(open_review, first_interval=0.8)
            bpy.app.timers.register(capture, first_interval=1.25)
        else:
            bpy.app.timers.register(capture, first_interval=1.0)
    except Exception as exc:
        return fail(exc)
    return None


def scroll_to_batch():
    try:
        region = next(
            region
            for region in TARGET_AREA.regions
            if region.type == "WINDOW"
        )
        with bpy.context.temp_override(
            window=bpy.context.window,
            area=TARGET_AREA,
            region=region,
        ):
            for _unused in range(3):
                bpy.ops.view2d.scroll_down(page=True)
        TARGET_AREA.tag_redraw()
    except Exception as exc:
        return fail(exc)
    return None


def open_review():
    try:
        region = next(
            region
            for region in TARGET_AREA.regions
            if region.type == "WINDOW"
        )
        with bpy.context.temp_override(
            window=bpy.context.window,
            area=TARGET_AREA,
            region=region,
        ):
            result = bpy.ops.take_system.render_included_takes(
                "INVOKE_DEFAULT"
            )
        if "RUNNING_MODAL" not in result:
            raise AssertionError(f"Review dialog did not open: {result}")
        RESULT["review_dialog"] = True
    except Exception as exc:
        return fail(exc)
    return None


def capture():
    try:
        SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        result = bpy.ops.screen.screenshot(
            filepath=str(SCREENSHOT),
            check_existing=False,
        )
        if "FINISHED" not in result or not SCREENSHOT.is_file():
            raise AssertionError(f"Screenshot failed: {result}")
        RESULT["bytes"] = SCREENSHOT.stat().st_size
        RESULT["ok"] = RESULT["bytes"] > 0
        print(f"TAKE_SYSTEM_GUI_SMOKE_OK {json.dumps(RESULT)}", flush=True)
        bpy.app.timers.register(finish, first_interval=0.1)
    except Exception as exc:
        return fail(exc)
    return None


def finish():
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(setup, first_interval=0.1)
