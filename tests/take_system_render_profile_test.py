"""Focused tests for inherited, granular take render profiles."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def clear_scene():
    scene = bpy.context.scene
    scene.camera = None
    scene.world = None
    for datablock in tuple(bpy.data.objects):
        bpy.data.objects.remove(datablock, do_unlink=True)
    for datablock in tuple(bpy.data.meshes):
        if datablock.users == 0:
            bpy.data.meshes.remove(datablock)


def decoded_records(take):
    return tuple(
        (
            override.data_path,
            override.target_ref_uuid,
            override.prop_type,
            engine.decoded_override_value(override),
        )
        for override in take.overrides
    )


blender_take_system.register()
try:
    clear_scene()
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    main_uuid = main.uuid

    try:
        scene.render.engine = "CYCLES"
    except TypeError:
        pass
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.filepath = "//default/"
    scene.render.film_transparent = False
    scene.view_settings.exposure = 0.0
    baseline_denoiser = ""
    child_denoiser = ""
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = 128
        baseline_denoiser = scene.cycles.denoiser
        child_denoiser = baseline_denoiser
        if hasattr(scene.cycles, "adaptive_min_samples"):
            scene.cycles.adaptive_min_samples = 0
        if hasattr(scene.cycles, "film_transparent_glass"):
            scene.cycles.film_transparent_glass = False

    child = engine.create_take(
        scene,
        "Profile Child",
        parent_uuid=main_uuid,
        make_active=True,
    )
    child_uuid = child.uuid
    engine.apply_take(scene, child_uuid, strict=True)

    baseline = engine.snapshot_render_profile(scene)
    resolution_paths = engine.render_profile_group_paths(
        scene,
        engine.RENDER_GROUP_RESOLUTION,
    )
    transparency_paths = engine.render_profile_group_paths(
        scene,
        engine.RENDER_GROUP_TRANSPARENCY,
    )
    sampling_paths = engine.render_profile_group_paths(
        scene,
        engine.RENDER_GROUP_ENGINE_SAMPLING,
    )
    require(
        "render.resolution_x" in resolution_paths
        and "render.film_transparent" in transparency_paths
        and (
            scene.render.engine != "CYCLES"
            or (
                "cycles.denoiser" in sampling_paths
                and "cycles.film_transparent_glass"
                in transparency_paths
            )
        ),
        "Expected render-profile groups were not feature-detected",
    )

    scene.render.resolution_x = 800
    scene.render.resolution_y = 600
    scene.render.resolution_percentage = 50
    scene.render.film_transparent = True
    if scene.render.engine == "CYCLES":
        for candidate in ("OPTIX", "OPENIMAGEDENOISE"):
            if candidate == baseline_denoiser:
                continue
            try:
                scene.cycles.denoiser = candidate
            except TypeError:
                continue
            child_denoiser = candidate
            break
        if hasattr(scene.cycles, "film_transparent_glass"):
            scene.cycles.film_transparent_glass = True
    first_report = engine.configure_render_profile(
        scene,
        child_uuid,
        {
            engine.RENDER_GROUP_ENGINE_SAMPLING,
            engine.RENDER_GROUP_RESOLUTION,
            engine.RENDER_GROUP_TRANSPARENCY,
        },
        baseline_values=baseline,
    )
    child = engine.find_take(scene, child_uuid)
    require(
        first_report.configured
        == (
            len(sampling_paths)
            + len(resolution_paths)
            + len(transparency_paths)
        )
        and first_report.main_seeded == first_report.configured
        and engine.direct_render_profile_groups(scene, child)
        == {
            engine.RENDER_GROUP_ENGINE_SAMPLING,
            engine.RENDER_GROUP_RESOLUTION,
            engine.RENDER_GROUP_TRANSPARENCY,
        },
        "First granular profile did not store only selected groups",
    )
    require(
        "render.filepath"
        not in engine.direct_render_setting_paths(scene, child)
        and "view_settings.exposure"
        not in engine.direct_render_setting_paths(scene, child),
        "Unselected output/color groups were frozen on the child",
    )

    engine.apply_take(scene, main_uuid, strict=True)
    require(
        scene.render.resolution_x == 1920
        and scene.render.resolution_y == 1080
        and scene.render.resolution_percentage == 100
        and not scene.render.film_transparent
        and (
            scene.render.engine != "CYCLES"
            or (
                scene.cycles.denoiser == baseline_denoiser
                and not scene.cycles.film_transparent_glass
            )
        ),
        "Main did not restore the pre-editor render defaults",
    )
    engine.apply_take(scene, child_uuid, strict=True)
    require(
        scene.render.resolution_x == 800
        and scene.render.resolution_y == 600
        and scene.render.resolution_percentage == 50
        and scene.render.film_transparent
        and (
            scene.render.engine != "CYCLES"
            or (
                scene.cycles.denoiser == child_denoiser
                and scene.cycles.film_transparent_glass
            )
        ),
        "Child profile did not restore its direct resolution/transparency",
    )

    # Switching a group off removes only that group's direct records. A new
    # color-management group is seeded from the inherited live value.
    second_baseline = engine.snapshot_render_profile(scene)
    scene.view_settings.exposure = 1.25
    second_report = engine.configure_render_profile(
        scene,
        child_uuid,
        {
            engine.RENDER_GROUP_RESOLUTION,
            engine.RENDER_GROUP_COLOR_MANAGEMENT,
        },
        baseline_values=second_baseline,
    )
    child = engine.find_take(scene, child_uuid)
    require(
        second_report.removed
        == len(sampling_paths) + len(transparency_paths)
        and engine.direct_render_profile_groups(scene, child)
        == {
            engine.RENDER_GROUP_RESOLUTION,
            engine.RENDER_GROUP_COLOR_MANAGEMENT,
        }
        and not scene.render.film_transparent
        and (
            scene.render.engine != "CYCLES"
            or not scene.cycles.film_transparent_glass
        )
        and abs(scene.view_settings.exposure - 1.25) < 1e-6,
        "Group-level inheritance did not remove transparency atomically",
    )

    # A deeper child with only an output group still inherits its parent's
    # resolution and color groups.
    grandchild = engine.create_take(
        scene,
        "Profile Grandchild",
        parent_uuid=child_uuid,
        make_active=True,
    )
    grandchild_uuid = grandchild.uuid
    engine.apply_take(scene, grandchild_uuid, strict=True)
    grandchild_baseline = engine.snapshot_render_profile(scene)
    scene.render.filepath = "//profiles/grandchild/"
    output_report = engine.configure_render_profile(
        scene,
        grandchild_uuid,
        {engine.RENDER_GROUP_OUTPUT},
        baseline_values=grandchild_baseline,
        batch_output_path="//batch/grandchild/",
        baseline_batch_output_path="",
    )
    grandchild = engine.find_take(scene, grandchild_uuid)
    require(
        output_report.configured
        == len(
            engine.render_profile_group_paths(
                scene,
                engine.RENDER_GROUP_OUTPUT,
            )
        )
        and engine.direct_render_profile_groups(scene, grandchild)
        == {engine.RENDER_GROUP_OUTPUT}
        and scene.render.resolution_x == 800
        and abs(scene.view_settings.exposure - 1.25) < 1e-6
        and scene.render.filepath == "//profiles/grandchild/"
        and grandchild.render_output_path == "//batch/grandchild/",
        "Grandchild output profile did not preserve inherited parent groups",
    )

    # Cancel restoration uses dependency ordering and must not create records.
    cancel_snapshot = engine.snapshot_render_profile(scene)
    record_count_before_cancel = sum(
        len(take.overrides) for take in scene.take_system.takes
    )
    scene.render.resolution_x = 1234
    scene.render.film_transparent = True
    if (
        scene.render.engine == "CYCLES"
        and hasattr(scene.cycles, "film_transparent_glass")
    ):
        scene.cycles.film_transparent_glass = True
    scene.view_settings.exposure = -2.0
    engine.restore_render_profile(scene, cancel_snapshot)
    require(
        scene.render.resolution_x == 800
        and not scene.render.film_transparent
        and (
            scene.render.engine != "CYCLES"
            or not scene.cycles.film_transparent_glass
        )
        and abs(scene.view_settings.exposure - 1.25) < 1e-6
        and sum(len(take.overrides) for take in scene.take_system.takes)
        == record_count_before_cancel,
        "Render-profile cancellation did not restore live values cleanly",
    )

    # Inject a mid-profile capture failure after direct-group removals. Both
    # persistent records and live values must return to the pre-dialog state.
    engine.apply_take(scene, child_uuid, strict=True)
    failure_baseline = engine.snapshot_render_profile(scene)
    main_before_failure = decoded_records(engine.find_take(scene, main_uuid))
    child_before_failure = decoded_records(
        engine.find_take(scene, child_uuid)
    )
    scene.render.resolution_x = 444
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = 32
        if hasattr(scene.cycles, "adaptive_min_samples"):
            scene.cycles.adaptive_min_samples = 4

    original_capture = engine._capture_override_values
    capture_calls = {"count": 0}

    def fail_second_capture(*args, **kwargs):
        capture_calls["count"] += 1
        if capture_calls["count"] == 2:
            raise engine.TakeSystemError("injected profile capture failure")
        return original_capture(*args, **kwargs)

    engine._capture_override_values = fail_second_capture
    try:
        try:
            engine.configure_render_profile(
                scene,
                child_uuid,
                {engine.RENDER_GROUP_ENGINE_SAMPLING},
                baseline_values=failure_baseline,
            )
        except engine.TakeSystemError as exc:
            require(
                "injected profile capture failure" in str(exc),
                f"Unexpected profile failure: {exc}",
            )
        else:
            raise AssertionError("Injected render-profile failure was ignored")
    finally:
        engine._capture_override_values = original_capture

    require(
        decoded_records(engine.find_take(scene, main_uuid))
        == main_before_failure
        and decoded_records(engine.find_take(scene, child_uuid))
        == child_before_failure
        and scene.render.resolution_x == 800
        and abs(scene.view_settings.exposure - 1.25) < 1e-6,
        "Failed profile edit left partial records or live values",
    )

    legacy_output = engine.create_take(
        scene,
        "Legacy Output Only",
        parent_uuid=main_uuid,
        make_active=False,
    )
    legacy_output_uuid = legacy_output.uuid
    legacy_output.render_output_path = "//legacy/output/"
    require(
        engine.direct_render_profile_groups(scene, legacy_output)
        == {engine.RENDER_GROUP_OUTPUT}
        and engine.take_has_render_settings(scene, legacy_output),
        "Legacy batch-only output was not recognized as an output group",
    )
    removed_legacy_output = engine.remove_render_settings(
        scene,
        legacy_output_uuid,
    )
    legacy_output = engine.find_take(scene, legacy_output_uuid)
    require(
        removed_legacy_output == 1
        and not legacy_output.render_output_path
        and not engine.take_has_render_settings(scene, legacy_output),
        "Inherit All did not clear a legacy batch-only output path",
    )

    require(
        operators.TS_OT_edit_render_profile.is_registered,
        "Render-profile editor operator is not registered",
    )
    print(
        "TAKE_SYSTEM_RENDER_PROFILE_OK",
        {
            "first_groups": 3,
            "group_removed": second_report.removed,
            "grandchild_groups": 1,
            "rollback_calls": capture_calls["count"],
        },
    )
finally:
    blender_take_system.unregister()

require(
    not operators.TS_OT_edit_render_profile.is_registered,
    "Render-profile editor remained registered after teardown",
)
