"""Focused headless tests for Phase 5 camera and render-setting features."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
TEST_ROOT = WORKSPACE / ".take_system_test"
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_close(actual, expected, message, tolerance=1e-5):
    actual_values = tuple(actual)
    expected_values = tuple(expected)
    require(
        len(actual_values) == len(expected_values)
        and all(
            abs(float(component) - float(expected_component)) <= tolerance
            for component, expected_component in zip(
                actual_values,
                expected_values,
            )
        ),
        f"{message}: got {actual_values!r}, expected {expected_values!r}",
    )


def clear_scene():
    scene = bpy.context.scene
    scene.camera = None
    scene.world = None
    for datablock in tuple(bpy.data.objects):
        bpy.data.objects.remove(datablock, do_unlink=True)
    for datablock in tuple(bpy.data.meshes):
        if datablock.users == 0:
            bpy.data.meshes.remove(datablock)
    for datablock in tuple(bpy.data.cameras):
        if datablock.users == 0:
            bpy.data.cameras.remove(datablock)
    for datablock in tuple(bpy.data.worlds):
        if datablock.users == 0:
            bpy.data.worlds.remove(datablock)


def make_camera(scene, name):
    data = bpy.data.cameras.new(f"{name}_Data")
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    return camera


def take_index(scene, take_uuid):
    return next(
        index
        for index, take in enumerate(scene.take_system.takes)
        if take.uuid == take_uuid
    )


def resolved_source(scene, take_uuid, data_path):
    for entry in engine.resolve_take(scene, take_uuid).values():
        override = entry.override
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        if target == scene and override.data_path == data_path:
            return entry.take_uuid, engine.decoded_override_value(override)
    return "", engine.read_path_value(scene, data_path)


blender_take_system.register()
try:
    clear_scene()
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    main_uuid = main.uuid
    require(
        scene.take_system.schema_version == engine.SCHEMA_VERSION == 2,
        "Phase 5 schema version was not initialized",
    )

    camera_main = make_camera(scene, "TS5_Camera_Main")
    camera_parent = make_camera(scene, "TS5_Camera_Parent")
    camera_child = make_camera(scene, "TS5_Camera_Child")
    scene.camera = camera_main

    parent = engine.create_take(
        scene,
        "Phase 5 Parent",
        parent_uuid=main_uuid,
        make_active=True,
    )
    parent_uuid = parent.uuid
    camera_report = engine.configure_take_camera(
        scene,
        parent_uuid,
        camera_parent,
    )
    require(
        camera_report.captured == 1
        and camera_report.created == 1
        and camera_report.main_seeded == 1,
        "First child camera configuration did not seed Main atomically",
    )
    main_camera_record = engine.direct_camera_override(
        scene,
        engine.find_take(scene, main_uuid),
    )
    parent_camera_record = engine.direct_camera_override(
        scene,
        engine.find_take(scene, parent_uuid),
    )
    require(
        main_camera_record is not None
        and engine.decoded_override_value(main_camera_record) == camera_main,
        "Main camera baseline was not stored",
    )
    require(
        parent_camera_record is not None
        and engine.decoded_override_value(parent_camera_record)
        == camera_parent,
        "Parent camera override was not stored",
    )
    require(
        scene.camera == camera_parent,
        "Configured parent camera was not applied",
    )

    child = engine.create_take(
        scene,
        "Phase 5 Child",
        parent_uuid=parent_uuid,
        make_active=True,
    )
    child_uuid = child.uuid
    resolved_camera, camera_source = engine.resolved_camera(
        scene,
        child_uuid,
    )
    require(
        resolved_camera == camera_parent and camera_source == parent_uuid,
        "Child did not inherit its parent's camera",
    )

    engine.configure_take_camera(scene, child_uuid, camera_child)
    resolved_camera, camera_source = engine.resolved_camera(
        scene,
        child_uuid,
    )
    require(
        resolved_camera == camera_child and camera_source == child_uuid,
        "Deepest camera override did not win",
    )
    for take_uuid, expected_camera in (
        (main_uuid, camera_main),
        (parent_uuid, camera_parent),
        (child_uuid, camera_child),
    ):
        engine.apply_take(scene, take_uuid, strict=True)
        require(
            scene.camera == expected_camera,
            f"Applying {take_uuid} selected the wrong camera",
        )

    engine.apply_take(scene, child_uuid, strict=True)
    engine.remove_take_camera(scene, child_uuid)
    require(
        scene.camera == camera_parent
        and engine.direct_camera_override(
            scene,
            engine.find_take(scene, child_uuid),
        )
        is None,
        "Clearing the child camera did not restore parent inheritance",
    )
    resolved_camera, camera_source = engine.resolved_camera(
        scene,
        child_uuid,
    )
    require(
        resolved_camera == camera_parent and camera_source == parent_uuid,
        "Resolved camera source was wrong after clearing the child record",
    )

    # A deliberate None is different from no direct record.
    engine.configure_take_camera(scene, child_uuid, None)
    child_camera_record = engine.direct_camera_override(
        scene,
        engine.find_take(scene, child_uuid),
    )
    require(
        child_camera_record is not None
        and engine.decoded_override_value(child_camera_record) is None
        and scene.camera is None,
        "A deliberate no-camera override was not preserved",
    )
    engine.remove_take_camera(scene, child_uuid)
    require(
        scene.camera == camera_parent,
        "Clearing a deliberate no-camera override did not inherit parent",
    )

    # Capture the full portable render preset twice: first to seed Main, then
    # to update the parent values after an edit.
    engine.apply_take(scene, parent_uuid, strict=True)
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100
    scene.render.filepath = str(TEST_ROOT / "phase5_main.png")
    scene.render.image_settings.file_format = "PNG"
    first_render_report = engine.capture_render_settings(
        scene,
        parent_uuid,
    )
    supported_paths = engine.render_setting_paths(scene)
    require(
        first_render_report.captured == len(supported_paths)
        and first_render_report.main_seeded == len(supported_paths)
        and len(supported_paths) >= 10,
        "Portable render preset was not captured/Main-seeded completely",
    )

    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.filepath = str(TEST_ROOT / "phase5_parent.png")
    second_render_report = engine.capture_render_settings(
        scene,
        parent_uuid,
    )
    require(
        second_render_report.captured == len(supported_paths)
        and second_render_report.main_seeded == 0,
        "Render preset update unexpectedly reseeded Main",
    )

    engine.apply_take(scene, child_uuid, strict=True)
    require(
        scene.render.resolution_x == 1280
        and scene.render.resolution_y == 720
        and scene.render.filepath.endswith("phase5_parent.png"),
        "Child did not inherit the parent's render preset",
    )
    x_source, inherited_x = resolved_source(
        scene,
        child_uuid,
        "render.resolution_x",
    )
    require(
        x_source == parent_uuid and inherited_x == 1280,
        "Inherited render-setting source was not the parent",
    )

    child_x_report = engine.configure_take_overrides(
        scene,
        (
            engine.ExplicitOverrideValue(
                target_id=scene,
                data_path="render.resolution_x",
                value=2048,
            ),
        ),
        take_uuid=child_uuid,
    )
    require(
        child_x_report.captured == 1
        and scene.render.resolution_x == 2048
        and scene.render.resolution_y == 720,
        "A child render-setting override did not preserve parent inheritance",
    )
    x_source, inherited_x = resolved_source(
        scene,
        child_uuid,
        "render.resolution_x",
    )
    y_source, inherited_y = resolved_source(
        scene,
        child_uuid,
        "render.resolution_y",
    )
    require(
        x_source == child_uuid
        and inherited_x == 2048
        and y_source == parent_uuid
        and inherited_y == 720,
        "Deepest-wins render resolution was resolved incorrectly",
    )

    removed = engine.remove_render_settings(scene, child_uuid)
    require(
        removed == 1
        and scene.render.resolution_x == 1280
        and not engine.take_has_render_settings(
            scene,
            engine.find_take(scene, child_uuid),
        ),
        "Clearing child render settings did not restore parent inheritance",
    )
    removed = engine.remove_render_settings(scene, parent_uuid)
    require(
        removed == len(supported_paths)
        and scene.render.resolution_x == 640
        and scene.render.resolution_y == 360
        and scene.render.filepath.endswith("phase5_main.png"),
        "Clearing parent render settings did not restore Main",
    )

    # Per-take Phase 5 metadata must survive duplication independently.
    parent = engine.find_take(scene, parent_uuid)
    parent.include_in_render = False
    parent.render_output_path = r"C:\renders\parent.png"
    parent.camera_override = camera_parent
    parent.use_camera_override = True
    duplicate = engine.duplicate_take(scene, parent_uuid, make_active=False)
    duplicate_uuid = duplicate.uuid
    duplicate = engine.find_take(scene, duplicate_uuid)
    require(
        not duplicate.include_in_render
        and duplicate.render_output_path == r"C:\renders\parent.png"
        and duplicate.camera_override == camera_parent
        and duplicate.use_camera_override,
        "Duplicate did not copy Phase 5 metadata",
    )

    # Output helpers must be portable, deterministic, and preserve Blender's
    # relative path syntax without touching the filesystem.
    require(
        engine.safe_take_output_name(
            '  Hero<>:"/\\|?*   Final.. ',
            "1234567890",
        )
        == "Hero_ Final",
        "Illegal filename characters were not sanitized deterministically",
    )
    require(
        engine.safe_take_output_name("CON", "1234567890") == "_CON",
        "Windows reserved filename was not made portable",
    )
    require(
        engine.safe_take_output_name(" ._ ", "abcdef123456")
        == "Take_abcdef12",
        "Blank sanitized take name did not receive a stable fallback",
    )
    require(
        engine.derive_batch_output_path(
            "//renders/beauty.png",
            "",
            "Hero: Final?",
            "abcd",
        )
        == "//renders/beauty_Hero_ Final.png",
        "Derived path did not sanitize and suffix the take name",
    )
    require(
        engine.derive_batch_output_path(
            "//renders/",
            "",
            "Hero",
            "abcd",
        )
        == "//renders/Hero",
        "Directory-style base path was not handled",
    )
    require(
        engine.derive_batch_output_path(
            "//ignored.png",
            r"C:\renders\\",
            "Hero",
            "abcd",
        )
        == r"C:\renders\\Hero",
        "Explicit directory-style take output was not handled",
    )
    require(
        engine.derive_batch_output_path(
            "//ignored.png",
            r"C:\renders\exact.png",
            "Hero",
            "abcd",
        )
        == r"C:\renders\exact.png",
        "Explicit file output was unexpectedly rewritten",
    )
    extension_collision_paths = set()
    extensionless_output = engine._unique_batch_output_path(
        r"C:\renders\same",
        "11111111aaaaaaaa",
        extension_collision_paths,
        file_extension=".png",
        use_file_extension=True,
    )
    explicit_extension_output = engine._unique_batch_output_path(
        r"C:\renders\same.png",
        "22222222bbbbbbbb",
        extension_collision_paths,
        file_extension=".png",
        use_file_extension=True,
    )
    require(
        extensionless_output == r"C:\renders\same"
        and explicit_extension_output
        == r"C:\renders\same_22222222.png",
        "Automatic image extensions were not included in collision planning",
    )
    normalized_collision_paths = set()
    first_normalized_output = engine._unique_batch_output_path(
        r"C:\renders\sub\..\normalized",
        "33333333cccccccc",
        normalized_collision_paths,
        file_extension=".png",
        use_file_extension=True,
    )
    second_normalized_output = engine._unique_batch_output_path(
        r"C:\renders\normalized",
        "44444444dddddddd",
        normalized_collision_paths,
        file_extension=".png",
        use_file_extension=True,
    )
    require(
        first_normalized_output == r"C:\renders\sub\..\normalized"
        and second_normalized_output == r"C:\renders\normalized_44444444",
        "Normalized path aliases were not disambiguated",
    )

    # Exercise the camera operator seam with a staged pointer.
    engine.apply_take(scene, child_uuid, strict=True)
    scene.take_system.active_take_index = take_index(scene, child_uuid)
    child = engine.find_take(scene, child_uuid)
    child.camera_override = camera_child
    operator_result = bpy.ops.take_system.configure_take_camera(
        "EXEC_DEFAULT",
        take_uuid=child_uuid,
        camera_choice=operators._camera_enum_identifier(camera_child),
    )
    require(
        operator_result == {"FINISHED"} and scene.camera == camera_child,
        f"Camera operator seam failed: {operator_result}",
    )
    child = engine.find_take(scene, child_uuid)
    require(
        child.use_camera_override
        and child.camera_override == camera_child,
        "Canonical camera record did not synchronize UI metadata",
    )
    inherit_result = bpy.ops.take_system.configure_take_camera(
        "EXEC_DEFAULT",
        take_uuid=child_uuid,
        camera_choice="INHERIT",
    )
    child = engine.find_take(scene, child_uuid)
    require(
        inherit_result == {"FINISHED"}
        and engine.direct_camera_override(scene, child) is None
        and not child.use_camera_override
        and child.camera_override is None
        and scene.camera == camera_parent,
        f"Camera inheritance choice failed: {inherit_result}",
    )
    operator_result = bpy.ops.take_system.configure_take_camera(
        "EXEC_DEFAULT",
        take_uuid=child_uuid,
        camera_choice=operators._camera_enum_identifier(camera_child),
    )
    require(
        operator_result == {"FINISHED"} and scene.camera == camera_child,
        f"Camera operator did not restore a direct choice: {operator_result}",
    )
    child = engine.find_take(scene, child_uuid)
    generic_camera_record = engine.direct_camera_override(scene, child)
    engine.remove_override(
        scene,
        child_uuid,
        generic_camera_record.uuid,
    )
    child = engine.find_take(scene, child_uuid)
    require(
        engine.direct_camera_override(scene, child) is None
        and not child.use_camera_override
        and child.camera_override is None
        and scene.camera == camera_parent,
        "Generic camera removal left stale UI camera metadata",
    )

    print("TAKE_SYSTEM_PHASE5_OK")
finally:
    blender_take_system.unregister()
