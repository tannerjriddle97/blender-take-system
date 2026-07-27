"""Save/reload persistence test for the Blender Take System."""

import math
import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, recent


BLEND_PATH = str(
    WORKSPACE / ".take_system_test" / "phase_1_2_persistence.blend"
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_vector(actual, expected, message):
    require(
        len(actual) == len(expected)
        and all(
            math.isclose(float(a), float(e), abs_tol=1e-5)
            for a, e in zip(actual, expected)
        ),
        f"{message}: got {tuple(actual)!r}, expected {expected!r}",
    )


blender_take_system.register()
try:
    Path(BLEND_PATH).parent.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    original_main_uuid = main.uuid

    mesh = bpy.data.meshes.new("TS_PersistMesh")
    mesh.from_pydata(((0, 0, 0), (1, 0, 0), (0, 1, 0)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new("TS_PersistObject", mesh)
    scene.collection.objects.link(obj)
    base_material = bpy.data.materials.new("TS_PersistBase")
    variant_material = bpy.data.materials.new("TS_PersistVariant")
    mesh.materials.append(base_material)
    obj.material_slots[0].link = "OBJECT"
    obj.material_slots[0].material = base_material
    obj.location = (0.0, 0.0, 0.0)
    obj["exact_double"] = 0.123456789012345
    obj["exact_int_array"] = [100000001, 16777217, 2147483647]
    base_camera_data = bpy.data.cameras.new("TS_PersistBaseCameraData")
    variant_camera_data = bpy.data.cameras.new("TS_PersistVariantCameraData")
    base_camera = bpy.data.objects.new(
        "TS_PersistBaseCamera",
        base_camera_data,
    )
    variant_camera = bpy.data.objects.new(
        "TS_PersistVariantCamera",
        variant_camera_data,
    )
    scene.collection.objects.link(base_camera)
    scene.collection.objects.link(variant_camera)
    scene.camera = base_camera

    take = engine.create_take(
        scene,
        "Persisted Variant",
        parent_uuid=main.uuid,
        make_active=True,
    )
    take_uuid = take.uuid

    engine.capture_override(scene, obj, "location", take.uuid)
    obj.location = (3.0, 4.0, 5.0)
    engine.capture_override(scene, obj, "location", take.uuid)

    engine.capture_override(
        scene, obj, "material_slots[0].material", take.uuid
    )

    collection = bpy.data.collections.new('TS_PersistCollection "State" Ω')
    scene.collection.children.link(collection)
    primary_view_layer = scene.view_layers[0]
    alternate_view_layer = scene.view_layers.new('TS_PersistViewLayer "Alt" É')
    primary_layer_collection = primary_view_layer.layer_collection.children[
        collection.name
    ]
    alternate_layer_collection = alternate_view_layer.layer_collection.children[
        collection.name
    ]
    primary_collection_path = engine.layer_collection_data_path(
        scene,
        primary_layer_collection,
    )
    alternate_collection_path = engine.layer_collection_data_path(
        scene,
        alternate_layer_collection,
    )
    engine.capture_override(
        scene,
        scene,
        primary_collection_path,
        take.uuid,
    )
    primary_layer_collection.exclude = True
    engine.capture_override(
        scene,
        scene,
        primary_collection_path,
        take.uuid,
    )
    engine.capture_override(
        scene,
        scene,
        alternate_collection_path,
        take.uuid,
    )
    engine.capture_override(scene, obj, '["exact_double"]', take.uuid)
    obj["exact_double"] = 0.987654321098765
    engine.capture_override(scene, obj, '["exact_double"]', take.uuid)
    engine.capture_override(scene, obj, '["exact_int_array"]', take.uuid)
    obj["exact_int_array"] = [-100000001, -16777217, -2147483648]
    engine.capture_override(scene, obj, '["exact_int_array"]', take.uuid)
    obj.material_slots[0].material = variant_material
    engine.capture_override(
        scene, obj, "material_slots[0].material", take.uuid
    )
    engine.configure_take_camera(scene, take.uuid, variant_camera)
    take = engine.find_take(scene, take.uuid)
    take.include_in_render = False
    take.render_output_path = "//renders/persisted_variant"
    engine.capture_render_settings(scene, take.uuid)
    scene.render.resolution_x = 1234
    scene.render.resolution_y = 567
    scene.render.filepath = "//renders/persisted_scene"
    engine.capture_render_settings(scene, take.uuid)

    second_scene = bpy.data.scenes.new("TS_SecondScene")
    require(
        len(second_scene.take_system.takes) == 0,
        "Low-level scene creation unexpectedly initialized before an update",
    )
    next_poll = blender_take_system._bootstrap_scenes_timer()
    require(
        len(second_scene.take_system.takes) == 1,
        "Recurring lifecycle timer did not bootstrap a new scene",
    )
    require(
        next_poll > 0,
        "Scene bootstrap timer did not remain scheduled for future scenes",
    )
    require(
        recent._scene_uid(scene) in recent._TRACKERS
        and recent._scene_uid(second_scene) not in recent._TRACKERS,
        "Lifecycle timer eagerly tracked an inactive scene",
    )
    second_main = engine.ensure_main_take(second_scene)
    second_main_uuid = second_main.uuid
    require(
        second_main_uuid != original_main_uuid,
        "Different scenes reused the same Main UUID",
    )
    recent.rebaseline_scene(second_scene)
    blender_take_system._take_system_undo_redo_post(None)
    require(
        recent._scene_uid(scene) in recent._TRACKERS
        and recent._scene_uid(second_scene) not in recent._TRACKERS,
        "Undo/redo lifecycle eagerly rebuilt an inactive scene tracker",
    )

    report = engine.apply_take(scene, take.uuid, strict=True)
    require(report.ok, "Initial take application failed")
    override_count = len(take.overrides)
    main_override_count = len(main.overrides)

    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    bpy.ops.wm.open_mainfile(filepath=BLEND_PATH)

    require(
        bpy.app.timers.is_registered(
            blender_take_system._bootstrap_scenes_timer
        ),
        "Recurring scene bootstrap timer did not survive file load",
    )
    scene = bpy.data.scenes.get("Scene")
    require(scene is not None, "Primary scene missing after reload")
    require(
        recent._scene_uid(scene) in recent._TRACKERS,
        "Load lifecycle did not rebuild the displayed scene tracker",
    )
    state = scene.take_system
    mains = [candidate for candidate in state.takes if candidate.is_main]
    require(len(mains) == 1, "Reload created duplicate Main takes")
    main = mains[0]
    require(main.uuid == original_main_uuid, "Main UUID changed after reload")
    require(
        state.main_take_uuid == original_main_uuid,
        "Stored Main identity changed after reload",
    )
    require(
        state.schema_version == 2,
        "Schema version did not persist",
    )
    take = engine.find_take(scene, take_uuid)
    require(take is not None, "Child take missing after reload")
    require(take.parent_uuid == main.uuid, "Parent UUID did not persist")
    require(take.name == "Persisted Variant", "Take name did not persist")
    require(
        take.include_in_render is False
        and take.render_output_path == "//renders/persisted_variant",
        "Phase 5 batch metadata did not persist",
    )
    require(
        state.active_take_uuid == take.uuid,
        "Active take identity did not persist",
    )
    require(
        len(take.overrides) == override_count
        and len(main.overrides) == main_override_count,
        "Override collections changed during reload",
    )

    obj = bpy.data.objects.get("TS_PersistObject")
    base_material = bpy.data.materials.get("TS_PersistBase")
    variant_material = bpy.data.materials.get("TS_PersistVariant")
    base_camera = bpy.data.objects.get("TS_PersistBaseCamera")
    variant_camera = bpy.data.objects.get("TS_PersistVariantCamera")
    require(
        obj is not None
        and base_material is not None
        and variant_material is not None
        and base_camera is not None
        and variant_camera is not None,
        "Fixture datablocks missing after reload",
    )
    location_override = engine.find_override(take, obj, "location")
    material_override = engine.find_override(
        take, obj, "material_slots[0].material"
    )
    exact_override = engine.find_override(take, obj, '["exact_double"]')
    int_array_override = engine.find_override(
        take, obj, '["exact_int_array"]'
    )
    require(location_override is not None, "Location override pointer did not persist")
    require(
        material_override is not None
        and material_override.target_id == obj
        and material_override.value_pointer == variant_material,
        "Generic target/value pointers did not persist",
    )
    require(
        material_override.target_id_name == "TS_PersistObject"
        and material_override.pointer_id_name == "TS_PersistVariant",
        "Pointer diagnostics did not persist",
    )
    require(
        exact_override is not None
        and exact_override.value_float_text
        and engine.decoded_override_value(exact_override)
        == 0.987654321098765,
        "Exact double payload did not persist",
    )
    require(
        int_array_override is not None
        and int_array_override.array_component_type == "INT"
        and engine.decoded_override_value(int_array_override)
        == (-100000001, -16777217, -2147483648),
        "Exact integer-array payload did not persist",
    )
    camera_override = engine.direct_camera_override(scene, take)
    require(
        camera_override is not None
        and engine.decoded_override_value(camera_override) == variant_camera,
        "Take Camera override did not persist",
    )
    require(
        engine.take_has_render_settings(scene, take),
        "Render-setting preset did not persist",
    )
    collection = bpy.data.collections.get('TS_PersistCollection "State" Ω')
    alternate_view_layer = scene.view_layers.get(
        'TS_PersistViewLayer "Alt" É'
    )
    require(
        collection is not None and alternate_view_layer is not None,
        "Collection-state fixture did not persist",
    )
    primary_view_layer = scene.view_layers[0]
    primary_layer_collection = primary_view_layer.layer_collection.children[
        collection.name
    ]
    alternate_layer_collection = alternate_view_layer.layer_collection.children[
        collection.name
    ]
    require(
        engine.layer_collection_data_path(
            scene,
            primary_layer_collection,
        )
        == primary_collection_path
        and engine.layer_collection_data_path(
            scene,
            alternate_layer_collection,
        )
        == alternate_collection_path,
        "Reload changed a stored Layer Collection path",
    )
    primary_collection_override = engine.find_override(
        take,
        scene,
        primary_collection_path,
    )
    alternate_collection_override = engine.find_override(
        take,
        scene,
        alternate_collection_path,
    )
    require(
        primary_collection_override is not None
        and engine.decoded_override_value(primary_collection_override) is True
        and alternate_collection_override is not None
        and engine.decoded_override_value(alternate_collection_override)
        is False,
        "Per-View-Layer collection enabled states did not persist",
    )

    second_scene = bpy.data.scenes.get("TS_SecondScene")
    require(second_scene is not None, "Second scene missing after reload")
    second_mains = [
        candidate for candidate in second_scene.take_system.takes if candidate.is_main
    ]
    require(
        len(second_mains) == 1 and second_mains[0].uuid == second_main_uuid,
        "Second scene Main was duplicated or replaced on load",
    )

    third_scene = bpy.data.scenes.new("TS_ThirdScene")
    require(
        len(third_scene.take_system.takes) == 0,
        "Third scene initialized before the recurring timer ran",
    )
    blender_take_system._bootstrap_scenes_timer()
    require(
        len(third_scene.take_system.takes) == 1
        and third_scene.take_system.takes[0].is_main,
        "Persistent timer did not bootstrap a scene created after file load",
    )

    obj.location = (-9.0, -9.0, -9.0)
    obj.material_slots[0].material = base_material
    obj["exact_double"] = 0.0
    obj["exact_int_array"] = [0, 0, 0]
    scene.camera = base_camera
    scene.render.resolution_x = 16
    scene.render.resolution_y = 16
    scene.render.filepath = "//wrong"
    primary_layer_collection.exclude = False
    alternate_layer_collection.exclude = True
    reapplied = engine.apply_take(scene, take.uuid, strict=True)
    require(reapplied.ok, "Saved take did not apply after reload")
    require_vector(obj.location, (3.0, 4.0, 5.0), "Reloaded transform")
    require(
        obj.material_slots[0].material == variant_material,
        "Reloaded material pointer did not apply",
    )
    require(
        obj["exact_double"] == 0.987654321098765,
        "Reloaded double custom property lost precision",
    )
    require(
        tuple(obj["exact_int_array"])
        == (-100000001, -16777217, -2147483648),
        "Reloaded integer custom array lost precision",
    )
    require(
        primary_layer_collection.exclude
        and not alternate_layer_collection.exclude,
        "Reloaded take did not restore per-View-Layer collection states",
    )
    require(
        scene.camera == variant_camera
        and scene.render.resolution_x == 1234
        and scene.render.resolution_y == 567
        and scene.render.filepath == "//renders/persisted_scene",
        "Reloaded Phase 5 camera/render settings did not apply",
    )
    engine.apply_take(scene, main.uuid, strict=True)
    require(
        not primary_layer_collection.exclude
        and not alternate_layer_collection.exclude,
        "Reloaded Main did not restore enabled collection baselines",
    )
    require(
        scene.camera == base_camera
        and scene.render.resolution_x == 1920
        and scene.render.resolution_y == 1080,
        "Reloaded Main did not restore Phase 5 baselines",
    )

    engine.ensure_main_take(scene)
    require(
        len([candidate for candidate in state.takes if candidate.is_main]) == 1,
        "Post-reload bootstrap duplicated Main",
    )

    print(
        "TAKE_SYSTEM_PERSISTENCE_OK",
        {
            "blend": BLEND_PATH,
            "main_uuid": main.uuid,
            "take_uuid": take.uuid,
            "overrides": len(take.overrides),
        },
    )
finally:
    blender_take_system.unregister()
