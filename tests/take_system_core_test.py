"""Headless integration tests for Take System Phases 1 and 2."""

import math
import sys
from pathlib import Path

import bpy
from bpy.props import BoolVectorProperty, IntVectorProperty


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_close(actual, expected, message, tolerance=1e-5):
    if isinstance(expected, (tuple, list)):
        actual_values = tuple(actual)
        require(
            len(actual_values) == len(expected)
            and all(
                math.isclose(float(a), float(e), abs_tol=tolerance)
                for a, e in zip(actual_values, expected)
            ),
            f"{message}: got {actual_values!r}, expected {expected!r}",
        )
    else:
        require(
            math.isclose(float(actual), float(expected), abs_tol=tolerance),
            f"{message}: got {actual!r}, expected {expected!r}",
        )


def clear_scene():
    for datablock in tuple(bpy.data.objects):
        bpy.data.objects.remove(datablock, do_unlink=True)
    for datablock in tuple(bpy.data.meshes):
        if datablock.users == 0:
            bpy.data.meshes.remove(datablock)
    for datablock in tuple(bpy.data.materials):
        if datablock.users == 0:
            bpy.data.materials.remove(datablock)
    for datablock in tuple(bpy.data.cameras):
        if datablock.users == 0:
            bpy.data.cameras.remove(datablock)


def make_mesh_object(scene, name):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (-1.0, -1.0, -1.0),
            (1.0, -1.0, -1.0),
            (1.0, 1.0, -1.0),
            (-1.0, 1.0, -1.0),
            (-1.0, -1.0, 1.0),
            (1.0, -1.0, 1.0),
            (1.0, 1.0, 1.0),
            (-1.0, 1.0, 1.0),
        ),
        (),
        (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (4, 0, 3, 7),
        ),
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def set_and_capture(scene, take, target, path, value):
    """Arm at the inherited value, edit, then capture the take value."""

    scene.take_system.active_take_uuid = take.uuid
    canonical_target, canonical_path = engine.canonicalize_id_path(target, path)
    main = engine.ensure_main_take(scene)
    had_main_baseline = (
        engine.find_override(main, canonical_target, canonical_path) is not None
    )
    before_count = len(take.overrides)
    first = engine.capture_override(scene, target, path, take.uuid)
    require(
        first.main_seeded == (not had_main_baseline and not take.is_main),
        f"Unexpected Main-seeding result for: {path}",
    )
    require(
        engine.find_override(main, canonical_target, canonical_path) is not None,
        f"Main has no baseline after capture: {path}",
    )
    require(
        len(take.overrides) == before_count + 1,
        f"First capture did not add exactly one override: {path}",
    )

    engine.write_path_value(canonical_target, canonical_path, value)
    second = engine.capture_override(scene, target, path, take.uuid)
    require(not second.created, f"Recapture duplicated override: {path}")
    require(
        len(take.overrides) == before_count + 1,
        f"Recapture changed override count: {path}",
    )
    return second.override


blender_take_system.register()
try:
    bpy.types.Object.ts_take_int_vector = IntVectorProperty(
        name="TS Test Int Vector",
        size=3,
    )
    bpy.types.Object.ts_take_bool_vector = BoolVectorProperty(
        name="TS Test Bool Vector",
        size=3,
    )
    clear_scene()
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    require(len(scene.take_system.takes) == 1, "Bootstrap did not create one Main")
    require(main.is_main and main.name == "Main", "Main identity is incorrect")
    require(main.uuid and not main.parent_uuid, "Main UUID/parent is invalid")
    require(
        scene.take_system.main_take_uuid == main.uuid,
        "Stored Main identity does not match Main",
    )
    original_main_uuid = main.uuid
    engine.ensure_main_take(scene)
    require(
        len(scene.take_system.takes) == 1 and main.uuid == original_main_uuid,
        "Repeated bootstrap duplicated or replaced Main",
    )

    cube = make_mesh_object(scene, "TS_Cube")
    control = make_mesh_object(scene, "TS_Control")
    base_material = bpy.data.materials.new("TS_Base")
    red_material = bpy.data.materials.new("TS_Red")
    blue_material = bpy.data.materials.new("TS_Blue")
    shader_material = bpy.data.materials.new("TS_Shader")
    if bpy.app.version < (5, 1, 0):
        shader_material.use_nodes = True

    cube.data.materials.append(base_material)
    cube.material_slots[0].link = "OBJECT"
    cube.material_slots[0].material = base_material

    principled = next(
        node
        for node in shader_material.node_tree.nodes
        if node.bl_idname == "ShaderNodeBsdfPrincipled"
    )
    principled.name = 'TS.Principled "Hero"'
    color_socket = principled.inputs["Base Color"]
    roughness_socket = principled.inputs["Roughness"]
    color_socket.default_value = (0.1, 0.2, 0.3, 1.0)
    roughness_socket.default_value = 0.25
    color_path_from_tree = color_socket.path_from_id("default_value")
    roughness_path_from_tree = roughness_socket.path_from_id("default_value")

    bevel = cube.modifiers.new("TS.Bevel", "BEVEL")
    bevel.width = 0.01
    cube["finish_code"] = "BASE"
    cube["finish_index"] = 7
    cube["precision_float"] = 0.123456789012345
    cube["int_array"] = [100000001, 16777217, 2147483647]
    cube["float_array"] = [
        0.123456789012345,
        1.000000000000003,
        -2.500000000000007,
    ]
    cube.location = (0.0, 0.0, 0.0)
    control.location = (0.0, 1.0, 0.0)
    cube.hide_viewport = False
    cube.hide_render = False
    cube.display_type = "TEXTURED"
    cube.ts_take_int_vector = (100000001, 16777217, 2147483647)
    cube.ts_take_bool_vector = (False, True, False)

    camera_data = bpy.data.cameras.new("TS_CameraData")
    camera_object = bpy.data.objects.new("TS_Camera", camera_data)
    scene.collection.objects.link(camera_object)
    camera_data.lens = 50.0
    camera_data.sensor_fit = "AUTO"

    variant = engine.create_take(
        scene, "Red Variant", parent_uuid=main.uuid, make_active=True
    )
    variant.is_main = True
    repaired_main = engine.ensure_main_take(scene)
    require(
        repaired_main.uuid == original_main_uuid
        and not variant.is_main
        and variant.parent_uuid == original_main_uuid,
        "Persisted Main identity lost to a duplicate Main flag",
    )

    for rejected_path, expected_error in (
        ("type", engine.TakePathError),
        ("bound_box[0][0]", engine.UnsupportedValueError),
    ):
        main_count = len(main.overrides)
        variant_count = len(variant.overrides)
        try:
            engine.capture_override(scene, cube, rejected_path, variant.uuid)
        except expected_error:
            pass
        else:
            raise AssertionError(f"Capture accepted unsafe path: {rejected_path}")
        require(
            len(main.overrides) == main_count
            and len(variant.overrides) == variant_count,
            f"Rejected capture left records behind: {rejected_path}",
        )

    set_and_capture(scene, variant, cube, "location", (1.25, -2.0, 3.5))
    set_and_capture(
        scene, variant, control, "location", (-4.0, 0.5, 2.0)
    )
    set_and_capture(scene, variant, cube, "hide_viewport", True)
    set_and_capture(scene, variant, cube, "hide_render", True)
    set_and_capture(scene, variant, cube, "display_type", "WIRE")
    int_vector_override = set_and_capture(
        scene,
        variant,
        cube,
        "ts_take_int_vector",
        (-100000001, -16777217, -2147483648),
    )
    bool_vector_override = set_and_capture(
        scene, variant, cube, "ts_take_bool_vector", (True, False, True)
    )
    require(
        int_vector_override.array_component_type == "INT"
        and bool_vector_override.array_component_type == "BOOL",
        "RNA array component types were not retained",
    )
    set_and_capture(scene, variant, cube, '["finish_code"]', "RED-ANODIZED")
    set_and_capture(scene, variant, cube, '["finish_index"]', 42)
    set_and_capture(
        scene,
        variant,
        cube,
        '["precision_float"]',
        0.987654321098765,
    )
    custom_int_array = set_and_capture(
        scene,
        variant,
        cube,
        '["int_array"]',
        (-100000001, -16777217, -2147483648),
    )
    custom_float_array = set_and_capture(
        scene,
        variant,
        cube,
        '["float_array"]',
        (
            0.987654321098765,
            -1.000000000000003,
            2.500000000000007,
        ),
    )
    require(
        custom_int_array.array_component_type == "INT",
        "Custom integer-array component type was not retained",
    )
    require(
        custom_float_array.value_array_text,
        "Custom double array has no exact payload",
    )
    exact_float_override = engine.find_override(
        variant, cube, '["precision_float"]'
    )
    exact_float_payload = exact_float_override.value_float_text
    exact_float_override.value_float_text = "0x1p+1000000000"
    require(
        engine.decoded_override_value(exact_float_override)
        == exact_float_override.value_float,
        "Overflowing exact-float payload did not use its safe fallback",
    )
    exact_float_override.value_float_text = exact_float_payload
    set_and_capture(
        scene,
        variant,
        cube,
        'modifiers["TS.Bevel"].width',
        0.125,
    )
    material_override = set_and_capture(
        scene,
        variant,
        cube,
        "material_slots[0].material",
        red_material,
    )
    require(
        material_override.prop_type == "POINTER"
        and material_override.value_pointer == red_material
        and not material_override.pointer_is_none,
        "Material slot did not store a real pointer",
    )
    color_override = set_and_capture(
        scene,
        variant,
        shader_material.node_tree,
        color_path_from_tree,
        (0.8, 0.05, 0.02, 1.0),
    )
    require(
        color_override.target_id == shader_material
        and color_override.data_path.startswith("node_tree."),
        "Embedded material node tree was not anchored at its Material",
    )
    set_and_capture(
        scene,
        variant,
        shader_material.node_tree,
        roughness_path_from_tree,
        0.73,
    )
    set_and_capture(scene, variant, camera_data, "lens", 85.0)
    set_and_capture(
        scene, variant, camera_data, "sensor_fit", "HORIZONTAL"
    )

    require(
        len(main.overrides) == len(variant.overrides),
        "Main does not baseline every child key",
    )
    require(
        all(override.target_ref_uuid for override in main.overrides),
        "A Main override has no stable resolved-key identity",
    )
    require(
        {override.prop_type for override in variant.overrides}
        >= {"FLOAT", "INT", "BOOL", "STRING", "ENUM", "VECTOR", "COLOR", "POINTER"},
        "The test fixture did not cover every required storage type",
    )

    # Prove capture copied values instead of retaining a mutable RNA-vector view.
    cube.location = (99.0, 99.0, 99.0)
    stored_location = engine.find_override(variant, cube, "location")
    require_close(
        stored_location.value_vector[:3],
        (1.25, -2.0, 3.5),
        "Stored transform mutated with the live vector",
    )

    data_counts = (
        len(bpy.data.objects),
        len(bpy.data.meshes),
        len(bpy.data.materials),
        len(bpy.data.cameras),
    )

    main_report = engine.apply_take(scene, main.uuid, strict=True)
    require(main_report.ok, "Main apply reported an issue")
    require_close(cube.location, (0.0, 0.0, 0.0), "Main transform")
    require_close(control.location, (0.0, 1.0, 0.0), "Main target identity")
    require(not cube.hide_viewport and not cube.hide_render, "Main visibility")
    require(
        tuple(cube.ts_take_int_vector)
        == (100000001, 16777217, 2147483647),
        "Main integer vector",
    )
    require(
        tuple(cube.ts_take_bool_vector) == (False, True, False),
        "Main Boolean vector",
    )
    require(cube.material_slots[0].material == base_material, "Main material")
    require(cube["finish_code"] == "BASE", "Main string custom property")
    require(cube["finish_index"] == 7, "Main integer custom property")
    require(
        cube["precision_float"] == 0.123456789012345,
        "Main double custom property lost precision",
    )
    require(
        tuple(cube["int_array"]) == (100000001, 16777217, 2147483647),
        "Main custom integer array lost precision",
    )
    require(
        tuple(cube["float_array"])
        == (
            0.123456789012345,
            1.000000000000003,
            -2.500000000000007,
        ),
        "Main custom double array lost precision",
    )
    require_close(bevel.width, 0.01, "Main modifier")
    require_close(color_socket.default_value, (0.1, 0.2, 0.3, 1.0), "Main color")
    require_close(roughness_socket.default_value, 0.25, "Main node float")
    require_close(camera_data.lens, 50.0, "Main camera lens")
    require(camera_data.sensor_fit == "AUTO", "Main camera enum")

    variant_report = engine.apply_take(scene, variant.uuid, strict=True)
    require(variant_report.ok, "Variant apply reported an issue")
    require_close(cube.location, (1.25, -2.0, 3.5), "Variant transform")
    require_close(control.location, (-4.0, 0.5, 2.0), "Variant target identity")
    require(cube.hide_viewport and cube.hide_render, "Variant visibility")
    require(cube.display_type == "WIRE", "Variant enum")
    require(
        tuple(cube.ts_take_int_vector)
        == (-100000001, -16777217, -2147483648),
        "Variant integer vector",
    )
    require(
        tuple(cube.ts_take_bool_vector) == (True, False, True),
        "Variant Boolean vector",
    )
    require(cube.material_slots[0].material == red_material, "Variant material")
    require(cube["finish_code"] == "RED-ANODIZED", "Variant string property")
    require(cube["finish_index"] == 42, "Variant integer property")
    require(
        cube["precision_float"] == 0.987654321098765,
        "Variant double custom property lost precision",
    )
    require(
        tuple(cube["int_array"]) == (-100000001, -16777217, -2147483648),
        "Variant custom integer array lost precision",
    )
    require(
        tuple(cube["float_array"])
        == (
            0.987654321098765,
            -1.000000000000003,
            2.500000000000007,
        ),
        "Variant custom double array lost precision",
    )
    require_close(bevel.width, 0.125, "Variant modifier")
    require_close(
        color_socket.default_value,
        (0.8, 0.05, 0.02, 1.0),
        "Variant node color",
    )
    require_close(roughness_socket.default_value, 0.73, "Variant roughness")
    require_close(camera_data.lens, 85.0, "Variant camera lens")
    require(camera_data.sensor_fit == "HORIZONTAL", "Variant camera enum")

    second_apply = engine.apply_take(scene, variant.uuid, strict=True)
    require(
        second_apply.applied == variant_report.applied,
        "Idempotent apply changed the resolved count",
    )
    require(
        data_counts
        == (
            len(bpy.data.objects),
            len(bpy.data.meshes),
            len(bpy.data.materials),
            len(bpy.data.cameras),
        ),
        "Applying a take duplicated Blender datablocks",
    )

    # Deepest-wins inheritance and an intentional None pointer.
    child = engine.create_take(
        scene, "Child Finish", parent_uuid=variant.uuid, make_active=True
    )
    set_and_capture(scene, child, cube, "location", (9.0, 8.0, 7.0))
    set_and_capture(
        scene, child, cube, "material_slots[0].material", None
    )
    none_pointer_override = engine.find_override(
        child, cube, "material_slots[0].material"
    )
    require(
        none_pointer_override.prop_type == "POINTER"
        and none_pointer_override.pointer_is_none,
        "Intentional None pointer was not distinguished",
    )
    grandchild = engine.create_take(
        scene, "Grandchild", parent_uuid=child.uuid, make_active=True
    )
    set_and_capture(scene, grandchild, cube, "hide_render", False)
    grandchild_report = engine.apply_take(scene, grandchild.uuid, strict=True)
    require(grandchild_report.ok, "Grandchild apply failed")
    require_close(cube.location, (9.0, 8.0, 7.0), "Deepest transform did not win")
    require(
        cube.material_slots[0].material is None,
        "Intentional None pointer did not apply",
    )
    require(cube.hide_viewport and not cube.hide_render, "Inherited visibility")
    require_close(
        roughness_socket.default_value,
        0.73,
        "Parent node override was not inherited",
    )

    # Sibling-switch regression: every key not mentioned by the sibling must
    # restore from Main, not leak the previously active branch.
    sibling = engine.create_take(
        scene, "Camera 35", parent_uuid=main.uuid, make_active=True
    )
    require_close(
        cube.location,
        (0.0, 0.0, 0.0),
        "Creating an active Main child did not synchronize inherited state",
    )
    require(
        cube.material_slots[0].material == base_material,
        "New sibling inherited leaked material state",
    )
    set_and_capture(scene, sibling, camera_data, "lens", 35.0)
    engine.apply_take(scene, grandchild.uuid, strict=True)
    sibling_report = engine.apply_take(scene, sibling.uuid, strict=True)
    require(sibling_report.ok, "Sibling apply failed")
    require_close(cube.location, (0.0, 0.0, 0.0), "Sibling leaked transform")
    require_close(control.location, (0.0, 1.0, 0.0), "Sibling leaked control")
    require(not cube.hide_viewport and not cube.hide_render, "Sibling leaked visibility")
    require(cube.display_type == "TEXTURED", "Sibling leaked enum")
    require(cube.material_slots[0].material == base_material, "Sibling leaked material")
    require_close(bevel.width, 0.01, "Sibling leaked modifier")
    require_close(roughness_socket.default_value, 0.25, "Sibling leaked node value")
    require_close(camera_data.lens, 35.0, "Sibling camera override")

    # Default DATA-linked material slots alias through a shared Mesh. Capturing
    # per-object CMF must create take-managed link overrides so the child can
    # resolve distinct materials without duplicating the Mesh.
    shared_mesh = bpy.data.meshes.new("TS_SharedMesh")
    shared_mesh.from_pydata(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
        (),
        ((0, 1, 2),),
    )
    shared_mesh.materials.append(base_material)
    shared_a = bpy.data.objects.new("TS_Shared_A", shared_mesh)
    shared_b = bpy.data.objects.new("TS_Shared_B", shared_mesh)
    scene.collection.objects.link(shared_a)
    scene.collection.objects.link(shared_b)
    require(
        shared_a.material_slots[0].link == "DATA"
        and shared_b.material_slots[0].link == "DATA",
        "Shared-slot fixture is not DATA-linked",
    )
    data_link_take = engine.create_take(
        scene, "Shared Mesh CMF", parent_uuid=main.uuid, make_active=True
    )
    material_path = "material_slots[0].material"
    revision_before_promotion = engine.scene_mutation_revision(scene)
    engine.capture_override(
        scene, shared_a, "active_material", data_link_take.uuid
    )
    require(
        shared_a.material_slots[0].link == "OBJECT",
        "Capture did not promote the first DATA-linked slot",
    )
    require(
        engine.scene_mutation_revision(scene) > revision_before_promotion,
        "Successful material-slot promotion did not invalidate runtime tracking",
    )
    shared_a.active_material = red_material
    active_material_result = engine.capture_override(
        scene, shared_a, "active_material", data_link_take.uuid
    )
    require(
        active_material_result.override.data_path == material_path,
        "active_material was not canonicalized to a stable slot path",
    )
    engine.capture_override(scene, shared_b, material_path, data_link_take.uuid)
    require(
        shared_b.material_slots[0].link == "OBJECT",
        "Capture did not promote the second DATA-linked slot",
    )
    shared_b.material_slots[0].material = blue_material
    engine.capture_override(scene, shared_b, material_path, data_link_take.uuid)
    require(
        engine.find_override(
            data_link_take, shared_a, "material_slots[0].link"
        )
        is not None
        and engine.find_override(
            data_link_take, shared_b, "material_slots[0].link"
        )
        is not None,
        "Implicit material-link overrides were not stored",
    )

    engine.apply_take(scene, main.uuid, strict=True)
    require(
        shared_a.material_slots[0].link == "DATA"
        and shared_b.material_slots[0].link == "DATA"
        and shared_a.material_slots[0].material == base_material
        and shared_b.material_slots[0].material == base_material,
        "Main did not restore shared DATA-linked material state",
    )
    engine.apply_take(scene, data_link_take.uuid, strict=True)
    require(
        shared_a.material_slots[0].link == "OBJECT"
        and shared_b.material_slots[0].link == "OBJECT"
        and shared_a.material_slots[0].material == red_material
        and shared_b.material_slots[0].material == blue_material,
        "Per-object CMF aliases were not resolved independently",
    )

    # Pointer values retained in a recent-action baseline can become stale
    # before capture (for example after an orphan purge). Preflight must turn
    # that RNA lifetime failure into a normal Take System error without records.
    stale_mesh = bpy.data.meshes.new("TS_StalePointerMesh")
    stale_mesh.from_pydata(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
        (),
        ((0, 1, 2),),
    )
    stale_material = bpy.data.materials.new("TS_StalePointerMaterial")
    stale_mesh.materials.append(stale_material)
    stale_object = bpy.data.objects.new("TS_StalePointerObject", stale_mesh)
    scene.collection.objects.link(stale_object)
    stale_object.material_slots[0].link = "OBJECT"
    stale_object.material_slots[0].material = stale_material
    stale_pointer_value = stale_object.material_slots[0].material
    bpy.data.materials.remove(stale_material, do_unlink=True)
    require(
        stale_object.material_slots[0].material is None,
        "Stale-pointer fixture did not clear its live material",
    )
    records_before_stale_pointer = (
        len(main.overrides),
        len(data_link_take.overrides),
    )
    try:
        engine.capture_change_batch(
            scene,
            (
                engine.OverrideChange(
                    target_id=stale_object,
                    data_path=material_path,
                    baseline_value=stale_pointer_value,
                    after_value=None,
                ),
            ),
            take_uuid=data_link_take.uuid,
        )
    except engine.MissingReferenceError as exc:
        require(
            "no longer exists" in str(exc),
            f"Unexpected stale pointer error: {exc}",
        )
    else:
        raise AssertionError("Capture accepted a removed pointer baseline")
    require(
        (
            len(main.overrides),
            len(data_link_take.overrides),
        )
        == records_before_stale_pointer,
        "Stale pointer preflight left partial override records",
    )

    engine.apply_take(scene, main.uuid, strict=True)
    link_path = "material_slots[0].link"
    child_link_override = engine.find_override(
        data_link_take, shared_a, link_path
    )
    require(
        engine.decoded_override_value(child_link_override) == "OBJECT",
        "Shared child link fixture is not OBJECT before rollback test",
    )
    main_count_before_promotion = len(main.overrides)
    child_count_before_promotion = len(data_link_take.overrides)
    revision_before_failed_promotion = engine.scene_mutation_revision(scene)
    original_write_path_value = engine.write_path_value

    def reject_object_link(target_id, path, value):
        if (
            target_id == shared_a
            and path == link_path
            and value == "OBJECT"
        ):
            raise engine.TakePathError("Intentional read-only slot failure")
        return original_write_path_value(target_id, path, value)

    engine.write_path_value = reject_object_link
    try:
        try:
            engine.capture_override(
                scene,
                shared_a,
                material_path,
                data_link_take.uuid,
            )
        except engine.TakePathError:
            pass
        else:
            raise AssertionError(
                "Material-slot promotion accepted a rejected link write"
            )
    finally:
        engine.write_path_value = original_write_path_value
    require(
        shared_a.material_slots[0].link == "DATA",
        "Failed material-slot promotion did not restore the live DATA link",
    )
    require(
        len(main.overrides) == main_count_before_promotion
        and len(data_link_take.overrides) == child_count_before_promotion,
        "Failed material-slot promotion left companion records behind",
    )
    require(
        engine.decoded_override_value(child_link_override) == "OBJECT",
        "Failed material-slot promotion changed an existing child link record",
    )
    require(
        engine.scene_mutation_revision(scene)
        == revision_before_failed_promotion,
        "Failed material-slot promotion invalidated runtime tracking",
    )

    shared_a.active_material = shader_material
    engine.capture_override(scene, shared_a, "active_material", main.uuid)
    shared_b_main_material = engine.find_override(
        main, shared_b, material_path
    )
    require(
        shared_b_main_material is not None
        and engine.decoded_override_value(shared_b_main_material)
        == shader_material,
        "Main did not synchronize shared DATA-slot aliases",
    )
    engine.apply_take(scene, main.uuid, strict=True)
    require(
        shared_a.active_material == shader_material
        and shared_b.active_material == shader_material,
        "A stale Main alias overwrote the recaptured shared material",
    )
    engine.apply_take(scene, data_link_take.uuid, strict=True)
    require(
        shared_a.active_material == red_material
        and shared_b.active_material == blue_material,
        "Child object-linked CMFs broke after Main alias synchronization",
    )

    # Dependent paths must not be capture-order dependent. This take stores the
    # nested `data.lens` before the `data` pointer but applies the pointer first.
    engine.apply_take(scene, main.uuid, strict=True)
    alternate_camera = bpy.data.cameras.new("TS_AlternateCameraData")
    alternate_camera.lens = 20.0
    dependent_take = engine.create_take(
        scene, "Dependent Camera", parent_uuid=main.uuid, make_active=True
    )
    engine.capture_override(
        scene, camera_object, "data.lens", dependent_take.uuid
    )
    engine.capture_override(scene, camera_object, "data", dependent_take.uuid)
    camera_object.data = alternate_camera
    alternate_camera.lens = 85.0
    engine.capture_override(
        scene, camera_object, "data.lens", dependent_take.uuid
    )
    engine.capture_override(scene, camera_object, "data", dependent_take.uuid)
    require(
        [
            override.data_path
            for override in list(dependent_take.overrides)[:2]
        ]
        == ["data.lens", "data"],
        "Dependent-path fixture did not preserve adverse capture order",
    )

    camera_object.data = camera_data
    camera_data.lens = 12.0
    alternate_camera.lens = 20.0
    dependent_report = engine.apply_take(
        scene, dependent_take.uuid, strict=True
    )
    require(dependent_report.ok, "Dependent-path take failed")
    require(
        camera_object.data == alternate_camera,
        "Parent pointer override did not apply",
    )
    require_close(
        alternate_camera.lens,
        85.0,
        "Nested path wrote through the old pointer",
    )
    require_close(
        camera_data.lens,
        50.0,
        "Nested path mutated the old Camera datablock",
    )

    # Force a late path failure after dependent writes and prove reverse-order,
    # just-in-time snapshots restore both the pointer and its nested target.
    engine.apply_take(scene, main.uuid, strict=True)
    camera_object.data = camera_data
    camera_data.lens = 12.0
    alternate_camera.lens = 20.0
    bad_ref = engine.new_uuid()
    bad_path = "data.no_such_property"
    for owner_take in (main, dependent_take):
        corrupt = owner_take.overrides.add()
        corrupt.uuid = engine.new_uuid()
        corrupt.target_ref_uuid = bad_ref
        corrupt.data_path = bad_path
        engine._set_target_metadata(corrupt, camera_object)
        corrupt.prop_type = "FLOAT"
        corrupt.value_float = 1.0
    scene.take_system.active_take_uuid = main.uuid
    try:
        engine.apply_take(scene, dependent_take.uuid, strict=True)
    except engine.TakeApplyError:
        pass
    else:
        raise AssertionError("Atomic apply accepted a corrupt dependent path")
    require(
        camera_object.data == camera_data,
        "Rollback did not restore the parent pointer",
    )
    require_close(camera_data.lens, 12.0, "Rollback did not restore old Camera")
    require_close(
        alternate_camera.lens,
        20.0,
        "Rollback did not restore the new Camera's nested value",
    )
    require(
        scene.take_system.active_take_uuid == main.uuid,
        "Rolled-back apply changed active identity",
    )
    dependent_take.overrides.remove(len(dependent_take.overrides) - 1)
    main.overrides.remove(len(main.overrides) - 1)

    malformed_ref = engine.new_uuid()
    for owner_take in (main, dependent_take):
        malformed = owner_take.overrides.add()
        malformed.uuid = engine.new_uuid()
        malformed.target_ref_uuid = malformed_ref
        malformed.data_path = "broken["
        engine._set_target_metadata(malformed, camera_object)
        malformed.prop_type = "FLOAT"
        malformed.value_float = 1.0
    malformed_report = engine.apply_take(
        scene, dependent_take.uuid, strict=False
    )
    require(
        malformed_report.skipped >= 1
        and any("Unbalanced RNA path" in issue.message for issue in malformed_report.issues),
        "Malformed stored path bypassed structured repair diagnostics",
    )
    dependent_take.overrides.remove(len(dependent_take.overrides) - 1)
    main.overrides.remove(len(main.overrides) - 1)

    # Broken references: strict application is atomic, while repair mode applies
    # valid entries and returns structured issues.
    engine.apply_take(scene, main.uuid, strict=True)
    broken = engine.create_take(
        scene, "Broken References", parent_uuid=main.uuid, make_active=True
    )
    set_and_capture(scene, broken, control, "hide_render", True)
    doomed = make_mesh_object(scene, "TS_Doomed")
    set_and_capture(scene, broken, doomed, "hide_viewport", True)
    bpy.data.objects.remove(doomed, do_unlink=True)
    replacement = make_mesh_object(scene, "TS_Doomed")
    replacement.hide_viewport = False

    control.hide_render = False
    cube.location = (123.0, 456.0, 789.0)
    scene.take_system.active_take_uuid = main.uuid
    previous_active = scene.take_system.active_take_uuid
    try:
        engine.apply_take(scene, broken.uuid, strict=True)
    except engine.TakeApplyError as exc:
        require(exc.report.skipped >= 1, "Strict failure had no skipped entry")
    else:
        raise AssertionError("Strict apply accepted a deleted target")
    require(
        not control.hide_render,
        "Strict apply was not atomic after preflight failure",
    )
    require_close(
        cube.location,
        (123.0, 456.0, 789.0),
        "Strict apply changed an unrelated baseline before failing",
    )
    require(
        scene.take_system.active_take_uuid == previous_active,
        "Failed strict apply changed active-take identity",
    )

    repair_report = engine.apply_take(scene, broken.uuid, strict=False)
    require(repair_report.skipped >= 1, "Repair mode did not report missing target")
    require(control.hide_render, "Repair mode did not apply the valid override")
    require(
        not replacement.hide_viewport,
        "Deleted target silently rebound to a same-name replacement",
    )
    require(
        len(broken.overrides) == 2,
        "Broken records were deleted during apply",
    )

    # Contract assertions for the Blender-facing undo boundary.
    require(
        {"REGISTER", "UNDO"}.issubset(operators.TS_OT_apply_take.bl_options),
        "Apply operator is not one undoable transaction",
    )
    require(
        {"REGISTER", "UNDO"}.issubset(
            operators.TS_OT_capture_button_override.bl_options
        ),
        "Capture operator is not undoable",
    )

    print(
        "TAKE_SYSTEM_CORE_OK",
        {
            "takes": len(scene.take_system.takes),
            "main_overrides": len(main.overrides),
            "variant_overrides": len(variant.overrides),
            "resolved_grandchild": len(
                engine.resolve_take(scene, grandchild.uuid)
            ),
            "repair_issues": len(repair_report.issues),
        },
    )
finally:
    if hasattr(bpy.types.Object, "ts_take_bool_vector"):
        del bpy.types.Object.ts_take_bool_vector
    if hasattr(bpy.types.Object, "ts_take_int_vector"):
        del bpy.types.Object.ts_take_int_vector
    blender_take_system.unregister()
