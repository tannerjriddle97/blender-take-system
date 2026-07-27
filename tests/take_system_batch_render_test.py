"""Transactional Phase 5 batch-render tests using a synchronous stub seam."""

import sys
import tempfile
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


def make_world(name, color):
    world = bpy.data.worlds.new(name)
    world.color = color
    return world


def take_index(scene, take_uuid):
    return next(
        index
        for index, take in enumerate(scene.take_system.takes)
        if take.uuid == take_uuid
    )


def configure_variant(
    scene,
    take_uuid,
    camera,
    world,
    world_color,
    obj,
    location,
    resolution_x,
):
    return engine.configure_take_overrides(
        scene,
        (
            engine.ExplicitOverrideValue(scene, "camera", camera),
            # These two paths intentionally depend on one another. Applying
            # ``world`` changes the object reached by ``world.color``.
            engine.ExplicitOverrideValue(scene, "world", world),
            engine.ExplicitOverrideValue(
                scene,
                "world.color",
                world_color,
            ),
            engine.ExplicitOverrideValue(obj, "location", location),
            engine.ExplicitOverrideValue(
                scene,
                "render.resolution_x",
                resolution_x,
            ),
        ),
        take_uuid=take_uuid,
    )


def runtime_fingerprint(scene, obj):
    selected = engine.selected_take(scene)
    return {
        "camera": scene.camera,
        "world": scene.world,
        "world_color": tuple(scene.world.color),
        "location": tuple(obj.location),
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
        "frame_current": scene.frame_current,
        "frame_subframe": scene.frame_subframe,
        "active_uuid": scene.take_system.active_take_uuid,
        "selected_uuid": selected.uuid if selected is not None else "",
        "override_index": scene.take_system.active_override_index,
    }


def take_system_fingerprint(scene):
    state = scene.take_system
    return {
        "schema_version": state.schema_version,
        "main_take_uuid": state.main_take_uuid,
        "active_take_uuid": state.active_take_uuid,
        "active_take_index": state.active_take_index,
        "active_override_index": state.active_override_index,
        "takes": tuple(
            (
                take.uuid,
                take.name,
                take.parent_uuid,
                take.is_main,
                take.include_in_render,
                take.render_output_path,
                len(take.overrides),
            )
            for take in state.takes
        ),
    }


def require_fingerprint(scene, obj, expected, label):
    actual = runtime_fingerprint(scene, obj)
    require(
        actual == expected,
        f"{label} did not restore the exact live state:\n"
        f"actual={actual!r}\nexpected={expected!r}",
    )


blender_take_system.register()
try:
    clear_scene()
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    main_uuid = main.uuid
    main.include_in_render = False

    obj = make_mesh_object(scene, "TS5_Batch_Object")
    camera_main = make_camera(scene, "TS5_Batch_Camera_Main")
    camera_one = make_camera(scene, "TS5_Batch_Camera_One")
    camera_two = make_camera(scene, "TS5_Batch_Camera_Two")
    camera_live = make_camera(scene, "TS5_Batch_Camera_Live")
    world_main = make_world("TS5_Batch_World_Main", (0.01, 0.02, 0.03))
    world_one = make_world("TS5_Batch_World_One", (0.11, 0.12, 0.13))
    world_two = make_world("TS5_Batch_World_Two", (0.21, 0.22, 0.23))
    world_live = make_world("TS5_Batch_World_Live", (0.31, 0.32, 0.33))
    scene.camera = camera_main
    scene.world = world_main
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(TEST_ROOT / "batch_base.png")
    scene.render.resolution_x = 640
    scene.render.resolution_y = 360
    scene.render.resolution_percentage = 100

    take_one = engine.create_take(
        scene,
        "Hero:Red?",
        parent_uuid=main_uuid,
        make_active=True,
    )
    take_one_uuid = take_one.uuid
    configure_variant(
        scene,
        take_one_uuid,
        camera_one,
        world_one,
        (0.41, 0.42, 0.43),
        obj,
        (1.0, 2.0, 3.0),
        320,
    )

    take_two = engine.create_take(
        scene,
        "Excluded",
        parent_uuid=main_uuid,
        make_active=True,
    )
    take_two_uuid = take_two.uuid
    configure_variant(
        scene,
        take_two_uuid,
        camera_two,
        world_two,
        (0.51, 0.52, 0.53),
        obj,
        (-4.0, 5.0, -6.0),
        480,
    )

    # This distinct legal name sanitizes to the same filename component as
    # take one, which exercises collision handling without explicit outputs.
    take_three = engine.create_take(
        scene,
        "Hero<Red>",
        parent_uuid=main_uuid,
        make_active=True,
    )
    take_three_uuid = take_three.uuid
    engine.configure_take_overrides(
        scene,
        (
            engine.ExplicitOverrideValue(
                obj,
                "location",
                (-1.0, -2.0, -3.0),
            ),
        ),
        take_uuid=take_three_uuid,
    )

    engine.find_take(scene, main_uuid).include_in_render = False
    engine.find_take(scene, take_one_uuid).include_in_render = True
    engine.find_take(scene, take_two_uuid).include_in_render = False
    engine.find_take(scene, take_three_uuid).include_in_render = True
    for take_uuid in (take_one_uuid, take_two_uuid, take_three_uuid):
        engine.find_take(scene, take_uuid).render_output_path = ""

    # Keep active and selected identities deliberately different, then make
    # uncaptured live edits. A batch must restore values, not merely reapply
    # the metadata's active take.
    engine.apply_take(scene, take_one_uuid, strict=True)
    scene.take_system.active_take_index = take_index(scene, take_two_uuid)
    scene.take_system.active_override_index = 2
    scene.camera = camera_live
    scene.world = world_live
    world_live.color = (0.91, 0.82, 0.73)
    obj.location = (9.25, -8.5, 7.75)
    scene.render.resolution_x = 1777
    scene.render.resolution_y = 999
    scene.render.resolution_percentage = 63
    scene.render.filepath = str(TEST_ROOT / "unsaved.exr")
    scene.render.image_settings.file_format = "OPEN_EXR"
    original = runtime_fingerprint(scene, obj)
    original_take_system = take_system_fingerprint(scene)

    plan = engine.build_batch_plan(scene)
    require(
        [item.take_uuid for item in plan.queued_items]
        == [take_one_uuid, take_three_uuid],
        "Read-only plan did not preserve included hierarchy order",
    )
    require(
        plan.queued == 2
        and plan.ready == 2
        and plan.can_render
        and not plan.errors,
        f"Valid read-only plan was not ready: {plan!r}",
    )
    plan_by_uuid = {item.take_uuid: item for item in plan.items}
    one_plan = plan_by_uuid[take_one_uuid]
    three_plan = plan_by_uuid[take_three_uuid]
    require(
        one_plan.camera_name == camera_one.name
        and one_plan.camera_source_uuid == take_one_uuid
        and one_plan.file_format == "OPEN_EXR"
        and one_plan.resolution_x == 320
        and one_plan.resolution_y == 999
        and one_plan.resolution_percentage == 63,
        f"Take-one preview did not resolve inherited values: {one_plan!r}",
    )
    require(
        three_plan.camera_name == camera_main.name
        and three_plan.resolution_x == 640
        and any(
            issue.code == "OUTPUT_COLLISION"
            for issue in three_plan.warnings
        ),
        f"Collision/camera preview was incomplete: {three_plan!r}",
    )
    require(
        runtime_fingerprint(scene, obj) == original
        and take_system_fingerprint(scene) == original_take_system,
        "Building the batch plan mutated live or persistent scene state",
    )

    # Bulk inclusion is an explicit undoable configuration action, not an
    # implicit side effect of drawing or planning the queue.
    original_inclusion = {
        take.uuid: bool(take.include_in_render)
        for take in scene.take_system.takes
    }
    require(
        bpy.ops.take_system.set_batch_inclusion(
            "EXEC_DEFAULT",
            mode="NONE",
        )
        == {"FINISHED"}
        and engine.build_batch_plan(scene).queued == 0,
        "Include None did not clear the queue",
    )
    require(
        bpy.ops.take_system.set_batch_inclusion(
            "EXEC_DEFAULT",
            mode="ALL",
        )
        == {"FINISHED"}
        and engine.build_batch_plan(scene).queued == len(
            scene.take_system.takes
        ),
        "Include All did not populate the queue",
    )
    for take in scene.take_system.takes:
        take.include_in_render = original_inclusion[take.uuid]

    preflight_result = bpy.ops.take_system.preflight_batch("EXEC_DEFAULT")
    preflight_report = engine.last_batch_report(scene)
    require(
        preflight_result == {"FINISHED"}
        and isinstance(preflight_report, engine.BatchPreflightReport)
        and preflight_report.ok
        and preflight_report.queued == 2,
        f"User-invoked deep preflight failed: {preflight_report!r}",
    )
    require(
        runtime_fingerprint(scene, obj) == original
        and take_system_fingerprint(scene) == original_take_system,
        "Deep batch preflight did not restore the exact starting state",
    )

    # Lightweight planning reports blockers without applying any take.
    preview_scene = bpy.data.scenes.new("TS5_Batch_Preview_Errors")
    try:
        preview_main = engine.ensure_main_take(preview_scene)
        preview_main.include_in_render = True
        preview_scene.collection.objects.link(camera_main)
        preview_scene.camera = camera_main
        preview_scene.render.filepath = "//preview/"
        preview_scene.render.image_settings.file_format = "PNG"
        relative_plan = engine.build_batch_plan(preview_scene)
        require(
            any(
                issue.code == "OUTPUT"
                and "Save the .blend" in issue.message
                for issue in relative_plan.errors
            ),
            f"Unsaved relative output was not reported: {relative_plan!r}",
        )
        preview_scene.render.filepath = str(TEST_ROOT / "preview.png")
        preview_scene.camera = None
        camera_plan = engine.build_batch_plan(preview_scene)
        require(
            any(
                issue.code == "CAMERA"
                for issue in camera_plan.errors
            ),
            f"Missing camera was not reported: {camera_plan!r}",
        )
        preview_scene.camera = camera_main
        format_override = engine._add_override(
            preview_main,
            preview_scene,
            "render.image_settings.file_format",
        )
        format_override.prop_type = "ENUM"
        format_override.value_string = "FFMPEG"
        format_plan = engine.build_batch_plan(preview_scene)
        require(
            any(
                issue.code == "OUTPUT"
                and "still-image only" in issue.message
                for issue in format_plan.errors
            ),
            f"Movie output was not rejected: {format_plan!r}",
        )
    finally:
        bpy.data.scenes.remove(preview_scene)

    expected_by_uuid = {
        take_one_uuid: {
            "camera": camera_one,
            "world": world_one,
            "world_color": (0.41, 0.42, 0.43),
            "location": (1.0, 2.0, 3.0),
            "resolution_x": 320,
        },
        take_three_uuid: {
            "camera": camera_main,
            "world": world_main,
            "world_color": (0.01, 0.02, 0.03),
            "location": (-1.0, -2.0, -3.0),
            "resolution_x": 640,
        },
    }
    callback_items = []

    def successful_callback(callback_scene, item):
        expected = expected_by_uuid[item.take_uuid]
        require(
            callback_scene.camera == expected["camera"],
            f"Callback saw the wrong camera for {item.take_name}",
        )
        require(
            callback_scene.world == expected["world"],
            f"Callback saw the wrong pointer-dependent world for "
            f"{item.take_name}",
        )
        for actual_component, expected_component in zip(
            callback_scene.world.color,
            expected["world_color"],
        ):
            require(
                abs(actual_component - expected_component) <= 1e-5,
                f"Callback saw the wrong world color for {item.take_name}",
            )
        for actual_component, expected_component in zip(
            callback_scene.objects["TS5_Batch_Object"].location,
            expected["location"],
        ):
            require(
                abs(actual_component - expected_component) <= 1e-5,
                f"Callback saw the wrong object location for {item.take_name}",
            )
        require(
            callback_scene.render.resolution_x
            == expected["resolution_x"],
            f"Callback saw the wrong resolution for {item.take_name}",
        )
        require(
            callback_scene.render.filepath == item.output_path,
            "Callback output item did not match Scene.render.filepath",
        )
        callback_items.append(item)
        callback_scene.frame_set(7, subframe=0.25)
        return {"FINISHED"}

    report = engine.render_take_batch(scene, successful_callback)
    require(
        report.ok
        and report.restored
        and report.queued == 2
        and len(report.rendered) == 2,
        f"Successful batch report was not complete: {report!r}",
    )
    require(
        [item.take_uuid for item in callback_items]
        == [take_one_uuid, take_three_uuid],
        "Batch include filtering or hierarchy order was wrong",
    )
    first_output = str(TEST_ROOT / "unsaved_Hero_Red.exr")
    second_output = str(
        TEST_ROOT / f"unsaved_Hero_Red_{take_three_uuid[:8]}.exr"
    )
    require(
        callback_items[0].output_path == first_output
        and callback_items[1].output_path == second_output,
        "Sanitized output collision was not resolved deterministically: "
        f"{[item.output_path for item in callback_items]!r}",
    )
    require(
        [item.output_path for item in callback_items]
        == [
            item.output_path
            for item in plan.queued_items
        ],
        "Rendered output paths drifted from the read-only preview",
    )
    require(
        all(item.take_uuid != take_two_uuid for item in callback_items),
        "Excluded take reached the render callback",
    )
    require_fingerprint(
        scene,
        obj,
        original,
        "Successful transactional batch",
    )

    failure_calls = []

    def failing_callback(callback_scene, item):
        successful_callback(callback_scene, item)
        failure_calls.append(item.take_uuid)
        if len(failure_calls) == 2:
            raise RuntimeError("synthetic callback failure")
        return {"FINISHED"}

    try:
        engine.render_take_batch(scene, failing_callback)
    except engine.BatchRenderError as exc:
        failure_report = exc.report
    else:
        raise AssertionError("Callback exception did not stop batch rendering")
    require(
        failure_report.queued == 2
        and len(failure_report.rendered) == 1
        and failure_report.rendered[0].take_uuid == take_one_uuid
        and failure_report.failed_take_uuid == take_three_uuid
        and "synthetic callback failure" in failure_report.error
        and failure_report.restored
        and not failure_report.restoration_issues,
        f"Callback failure report was incomplete: {failure_report!r}",
    )
    require_fingerprint(
        scene,
        obj,
        original,
        "Failed transactional batch",
    )

    def cancelled_callback(_callback_scene, _item):
        return {"CANCELLED"}

    try:
        engine.render_take_batch(
            scene,
            cancelled_callback,
            take_uuids=(take_one_uuid,),
        )
    except engine.BatchRenderError as exc:
        cancelled_report = exc.report
    else:
        raise AssertionError("Cancelled callback did not cancel the batch")
    require(
        cancelled_report.queued == 1
        and not cancelled_report.rendered
        and cancelled_report.failed_take_uuid == take_one_uuid
        and "cancelled" in cancelled_report.error.lower()
        and cancelled_report.restored,
        f"Cancellation report was incomplete: {cancelled_report!r}",
    )
    require_fingerprint(
        scene,
        obj,
        original,
        "Cancelled transactional batch",
    )

    # Exercise the Blender operator through its synchronous renderer seam. No
    # files are written and the engine still owns all transaction semantics.
    operator_items = []
    original_render_still = operators._render_still

    def operator_stub(callback_scene, item):
        operator_items.append((callback_scene, item))
        return {"FINISHED"}

    operators._render_still = operator_stub
    try:
        operator_result = bpy.ops.take_system.render_included_takes(
            "EXEC_DEFAULT"
        )
    finally:
        operators._render_still = original_render_still
    require(
        operator_result == {"FINISHED"}
        and [item.take_uuid for _scene, item in operator_items]
        == [take_one_uuid, take_three_uuid],
        f"Batch-render operator seam failed: {operator_result}, "
        f"{operator_items!r}",
    )
    operator_report = engine.last_batch_report(scene)
    require(
        isinstance(operator_report, engine.BatchRenderReport)
        and operator_report.ok
        and operator_report.queued == 2,
        f"Batch operator did not preserve its runtime result: "
        f"{operator_report!r}",
    )
    require_fingerprint(
        scene,
        obj,
        original,
        "Operator transactional batch",
    )

    # Regression: an already-applied override is a no-op during batch apply,
    # but a synchronous render callback/handler can still mutate that exact
    # property. The transaction must journal the concrete no-op path so it can
    # restore the pre-batch value afterward.
    engine.apply_take(scene, take_one_uuid, strict=True)
    no_op_location = tuple(obj.location)
    require(
        no_op_location == (1.0, 2.0, 3.0),
        "No-op callback regression did not start at the stored take value",
    )

    def mutate_no_op_override(callback_scene, item):
        require(
            item.take_uuid == take_one_uuid
            and tuple(callback_scene.objects["TS5_Batch_Object"].location)
            == no_op_location,
            "Queued take override was not exactly live before the callback",
        )
        callback_scene.objects["TS5_Batch_Object"].location = (
            41.0,
            42.0,
            43.0,
        )
        return {"FINISHED"}

    no_op_report = engine.render_take_batch(
        scene,
        mutate_no_op_override,
        take_uuids=(take_one_uuid,),
    )
    require(
        no_op_report.ok
        and no_op_report.restored
        and not no_op_report.restoration_issues
        and tuple(obj.location) == no_op_location,
        "Batch did not restore a callback-mutated no-op override path: "
        f"{no_op_report!r}, location={tuple(obj.location)!r}",
    )

    # Finish with one real 8×8 synchronous still to verify Blender's render
    # operator, file extension, and output-directory path—not only the stub.
    engine.apply_take(scene, main_uuid, strict=True)
    smoke_take = engine.create_take(
        scene,
        "Actual Render Smoke",
        parent_uuid=main_uuid,
        make_active=True,
    )
    smoke_take_uuid = smoke_take.uuid
    engine.configure_take_overrides(
        scene,
        (
            engine.ExplicitOverrideValue(scene, "camera", camera_main),
            engine.ExplicitOverrideValue(
                scene,
                "render.resolution_x",
                8,
            ),
            engine.ExplicitOverrideValue(
                scene,
                "render.resolution_y",
                8,
            ),
            engine.ExplicitOverrideValue(
                scene,
                "render.resolution_percentage",
                100,
            ),
            engine.ExplicitOverrideValue(
                scene,
                "render.image_settings.file_format",
                "PNG",
            ),
            engine.ExplicitOverrideValue(
                scene,
                "render.use_file_extension",
                True,
            ),
        ),
        take_uuid=smoke_take_uuid,
    )
    for candidate in scene.take_system.takes:
        candidate.include_in_render = candidate.uuid == smoke_take_uuid
    test_root = WORKSPACE / ".take_system_test"
    test_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="phase5_actual_render_",
        dir=test_root,
    ) as temporary_directory:
        output_stem = Path(temporary_directory) / "actual_take"
        engine.find_take(
            scene,
            smoke_take_uuid,
        ).render_output_path = str(output_stem)
        actual_report = engine.render_take_batch(
            scene,
            operators._render_still,
        )
        actual_output = output_stem.with_suffix(".png")
        require(
            actual_report.ok
            and len(actual_report.rendered) == 1
            and actual_output.is_file()
            and actual_output.stat().st_size > 0,
            f"Actual 8×8 render was not written: {actual_output}",
        )

    print("TAKE_SYSTEM_BATCH_RENDER_OK")
finally:
    blender_take_system.unregister()
