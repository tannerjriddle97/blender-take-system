"""Headless coverage for View Layer collection enabled-state overrides."""

import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine, operators, recent, ui


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def layer_collection_entry(scene, view_layer, collection):
    matches = layer_collection_entries(scene, view_layer, collection)
    if matches:
        return matches[0]
    raise AssertionError(
        f"Layer Collection not found: {view_layer.name} / {collection.name}"
    )


def layer_collection_entries(scene, view_layer, collection):
    return [
        (layer_collection, owner_path)
        for candidate_view_layer, layer_collection, owner_path in (
        engine.iter_layer_collection_paths(scene)
        )
        if candidate_view_layer == view_layer
        and layer_collection.collection == collection
    ]


def stored_value(take, scene, data_path):
    override = engine.find_override(take, scene, data_path)
    require(override is not None, f"Missing Scene override: {data_path}")
    return engine.decoded_override_value(override), override


def make_object(scene, name):
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


def require_raises(exception_type, callback, message):
    try:
        callback()
    except exception_type as exc:
        return exc
    except Exception as exc:
        raise AssertionError(
            f"{message}: raised {type(exc).__name__}, expected "
            f"{exception_type.__name__}: {exc}"
        ) from exc
    raise AssertionError(f"{message}: no exception was raised")


def cleanup_test_data():
    for obj in tuple(bpy.data.objects):
        if obj.name.startswith("TS_"):
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
    for mesh in tuple(bpy.data.meshes):
        if mesh.name.startswith("TS_") and mesh.users == 0:
            try:
                bpy.data.meshes.remove(mesh)
            except (ReferenceError, RuntimeError):
                pass
    for collection in reversed(tuple(bpy.data.collections)):
        if collection.name.startswith("TS_"):
            try:
                bpy.data.collections.remove(collection, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
    for candidate_scene in tuple(bpy.data.scenes):
        for view_layer in tuple(candidate_scene.view_layers):
            if (
                view_layer.name.startswith("TS_")
                and len(candidate_scene.view_layers) > 1
            ):
                try:
                    candidate_scene.view_layers.remove(view_layer)
                except (ReferenceError, RuntimeError):
                    pass


registered = False
blender_take_system.register()
registered = True
try:
    scene = bpy.context.scene
    primary_view_layer = bpy.context.view_layer

    outer = bpy.data.collections.new('TS_Outer "Quoted" \\ Ü')
    nested = bpy.data.collections.new('TS_Nested "State" \\ Ω')
    automatic_parent = bpy.data.collections.new("TS_Automatic_Parent")
    automatic_nested = bpy.data.collections.new("TS_Automatic_Nested")
    scene.collection.children.link(outer)
    outer.children.link(nested)
    scene.collection.children.link(automatic_parent)
    automatic_parent.children.link(automatic_nested)

    alternate_view_layer = scene.view_layers.new('TS_Alt "Layer" \\ É')
    for view_layer in scene.view_layers:
        view_layer.update()

    primary_nested, primary_owner_path = layer_collection_entry(
        scene,
        primary_view_layer,
        nested,
    )
    alternate_nested, alternate_owner_path = layer_collection_entry(
        scene,
        alternate_view_layer,
        nested,
    )
    primary_path = engine.layer_collection_data_path(
        scene,
        primary_nested,
    )
    alternate_path = engine.layer_collection_data_path(
        scene,
        alternate_nested,
    )

    require(
        primary_path == f"{primary_owner_path}.exclude"
        and alternate_path == f"{alternate_owner_path}.exclude",
        "Layer Collection helper returned the wrong nested path",
    )
    require(
        primary_path != alternate_path,
        "Two View Layers collapsed to the same collection-state path",
    )
    require(
        scene.path_resolve(primary_path) is False
        and scene.path_resolve(alternate_path) is False,
        "Escaped nested Layer Collection paths do not resolve",
    )
    require(
        engine.is_layer_collection_exclude_path(scene, primary_path),
        "Layer Collection exclude path was not recognized",
    )

    try:
        engine.layer_collection_data_path(
            scene,
            primary_view_layer.layer_collection,
        )
    except engine.UnsupportedValueError:
        pass
    else:
        raise AssertionError("The View Layer root collection was capturable")

    button_context = SimpleNamespace(
        scene=scene,
        button_pointer=primary_nested,
        button_prop=primary_nested.bl_rna.properties["exclude"],
    )
    button_target, button_path = operators._button_target_and_path(
        button_context
    )
    require(
        button_target == scene and button_path == primary_path,
        "Outliner exclude control did not map to its owning Scene path",
    )

    main = engine.ensure_main_take(scene)
    take = engine.create_take(
        scene,
        "Collection States",
        parent_uuid=main.uuid,
        make_active=True,
    )

    # The same Collection ID has a distinct exclude state in each View Layer.
    engine.capture_override(scene, scene, primary_path, take.uuid)
    primary_nested.exclude = True
    recapture_target, recapture_path = operators._button_target_and_path(
        button_context
    )
    require(
        recapture_target == scene and recapture_path == primary_path,
        "Excluded Layer Collection could not be mapped for recapture",
    )
    engine.capture_override(scene, scene, primary_path, take.uuid)

    engine.capture_override(scene, scene, alternate_path, take.uuid)
    alternate_nested.exclude = True
    engine.capture_override(scene, scene, alternate_path, take.uuid)

    require(
        stored_value(main, scene, primary_path)[0] is False
        and stored_value(take, scene, primary_path)[0] is True
        and stored_value(main, scene, alternate_path)[0] is False
        and stored_value(take, scene, alternate_path)[0] is True,
        "Manual collection capture stored the wrong enabled states",
    )
    require(
        ui._override_value_text(
            stored_value(main, scene, primary_path)[1]
        )
        == "Enabled"
        and ui._override_value_text(
            stored_value(take, scene, primary_path)[1]
        )
        == "Disabled",
        "Override inspector did not invert exclude into Enabled/Disabled text",
    )

    engine.apply_take(scene, main.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_path) is False
        and engine.read_path_value(scene, alternate_path) is False,
        "Main did not enable collections in both View Layers",
    )
    engine.apply_take(scene, take.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_path) is True
        and engine.read_path_value(scene, alternate_path) is True,
        "Child did not disable collections in both View Layers",
    )

    # An empty sibling inherits Main and must not leak exclusion state from
    # another branch when switching back and forth.
    sibling = engine.create_take(
        scene,
        "Collection States Sibling",
        parent_uuid=main.uuid,
        make_active=True,
    )
    require(
        engine.read_path_value(scene, primary_path) is False
        and engine.read_path_value(scene, alternate_path) is False,
        "Creating an empty sibling did not restore Main collection states",
    )
    engine.apply_take(scene, take.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_path) is True
        and engine.read_path_value(scene, alternate_path) is True,
        "Original child did not restore its exclusions after sibling creation",
    )
    engine.apply_take(scene, sibling.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_path) is False
        and engine.read_path_value(scene, alternate_path) is False,
        "Empty sibling leaked exclusions from the other branch",
    )
    engine.apply_take(scene, take.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_path) is True
        and engine.read_path_value(scene, alternate_path) is True,
        "Collection state did not survive a sibling round-trip",
    )

    # Recent-action discovery includes nested non-root Layer Collections even
    # before any override exists for their paths.
    primary_automatic, primary_automatic_owner = layer_collection_entry(
        scene,
        primary_view_layer,
        automatic_nested,
    )
    alternate_automatic, alternate_automatic_owner = layer_collection_entry(
        scene,
        alternate_view_layer,
        automatic_nested,
    )
    primary_automatic_path = f"{primary_automatic_owner}.exclude"
    alternate_automatic_path = f"{alternate_automatic_owner}.exclude"
    recent.clear_all()
    tracker = recent.rebaseline_scene(scene)
    scene_uid = recent._id_uid(scene)
    require(
        (scene_uid, primary_automatic_path) in tracker.properties
        and (scene_uid, alternate_automatic_path) in tracker.properties,
        "Recent tracker did not discover nested collection states",
    )

    primary_automatic.exclude = True
    alternate_automatic.exclude = True
    action = recent.observe_scene(
        scene,
        SimpleNamespace(
            updates=(SimpleNamespace(id=scene),),
        ),
    )
    require(
        action is not None
        and len(action.changes) == 2
        and all(
            "Enabled State" in change.label
            for change in action.changes.values()
        ),
        "Recent tracker did not group two View Layer collection changes",
    )
    report = recent.capture_pending(scene, take.uuid)
    require(
        report.captured == 2 and report.main_seeded == 2,
        "Recent collection-state batch was not captured atomically",
    )
    require(
        stored_value(main, scene, primary_automatic_path)[0] is False
        and stored_value(take, scene, primary_automatic_path)[0] is True
        and stored_value(main, scene, alternate_automatic_path)[0] is False
        and stored_value(take, scene, alternate_automatic_path)[0] is True,
        "Recent capture stored incorrect per-View-Layer values",
    )

    engine.apply_take(scene, main.uuid, strict=True)
    require(
        not engine.read_path_value(scene, primary_automatic_path)
        and not engine.read_path_value(scene, alternate_automatic_path),
        "Recent Main baselines did not re-enable both collections",
    )
    engine.apply_take(scene, take.uuid, strict=True)
    require(
        engine.read_path_value(scene, primary_automatic_path)
        and engine.read_path_value(scene, alternate_automatic_path),
        "Recent child values did not disable both collections",
    )

    # A topology change refreshes discovery immediately rather than waiting
    # for the periodic broad refresh and losing the first subsequent toggle.
    recent.rebaseline_scene(scene)
    dynamic = bpy.data.collections.new("TS_Dynamic_Collection")
    scene.collection.children.link(dynamic)
    for view_layer in scene.view_layers:
        view_layer.update()
    recent.observe_scene(
        scene,
        SimpleNamespace(
            updates=(SimpleNamespace(id=scene),),
        ),
    )
    dynamic_layer_collection, dynamic_owner_path = layer_collection_entry(
        scene,
        primary_view_layer,
        dynamic,
    )
    dynamic_path = f"{dynamic_owner_path}.exclude"
    tracker = recent.ensure_scene(scene)
    require(
        (scene_uid, dynamic_path) in tracker.properties,
        "Topology refresh did not discover a newly linked collection",
    )
    dynamic_layer_collection.exclude = True
    action = recent.observe_scene(scene, force_all=True)
    require(
        action is not None
        and (scene_uid, dynamic_path) in action.changes,
        "First toggle after a collection topology change was lost",
    )

    # One Collection ID can be linked under two parents in the same View
    # Layer. Those LayerCollection occurrences have independent exclude bits
    # and must receive distinct full-branch paths and override identities.
    recent.clear_all()
    dynamic_layer_collection.exclude = False
    shared_parent_a = bpy.data.collections.new("TS_Shared_Parent_A")
    shared_parent_b = bpy.data.collections.new("TS_Shared_Parent_B")
    shared_collection = bpy.data.collections.new("TS_Shared_Occurrence")
    scene.collection.children.link(shared_parent_a)
    scene.collection.children.link(shared_parent_b)
    shared_parent_a.children.link(shared_collection)
    shared_parent_b.children.link(shared_collection)
    for view_layer in scene.view_layers:
        view_layer.update()

    primary_shared_entries = layer_collection_entries(
        scene,
        primary_view_layer,
        shared_collection,
    )
    require(
        len(primary_shared_entries) == 2,
        "Shared Collection did not create two same-View-Layer occurrences",
    )
    (shared_a, shared_a_owner), (shared_b, shared_b_owner) = (
        primary_shared_entries
    )
    shared_a_path = f"{shared_a_owner}.exclude"
    shared_b_path = f"{shared_b_owner}.exclude"
    require(
        shared_a_path != shared_b_path,
        "Shared Collection occurrences collapsed to one hierarchy path",
    )
    shared_a.exclude = True
    require(
        shared_b.exclude is False,
        "One shared Collection occurrence changed its sibling occurrence",
    )
    shared_a.exclude = False

    engine.apply_take(scene, take.uuid, strict=True)
    shared_a_seed = engine.capture_override(
        scene,
        scene,
        shared_a_path,
        take.uuid,
    )
    shared_b_seed = engine.capture_override(
        scene,
        scene,
        shared_b_path,
        take.uuid,
    )
    require(
        shared_a_seed.main_seeded and shared_b_seed.main_seeded,
        "Shared occurrence captures did not seed distinct Main baselines",
    )
    shared_a.exclude = True
    engine.capture_override(scene, scene, shared_a_path, take.uuid)
    require(
        stored_value(main, scene, shared_a_path)[0] is False
        and stored_value(main, scene, shared_b_path)[0] is False
        and stored_value(take, scene, shared_a_path)[0] is True
        and stored_value(take, scene, shared_b_path)[0] is False,
        "Shared occurrence overrides did not retain independent values",
    )
    engine.apply_take(scene, main.uuid, strict=True)
    require(
        not shared_a.exclude and not shared_b.exclude,
        "Main did not enable both shared Collection occurrences",
    )
    engine.apply_take(scene, take.uuid, strict=True)
    require(
        shared_a.exclude and not shared_b.exclude,
        "Child did not restore the independent shared occurrence values",
    )
    engine.apply_take(scene, sibling.uuid, strict=True)
    require(
        not shared_a.exclude and not shared_b.exclude,
        "Sibling inherited stale shared-occurrence state",
    )

    # Collection and View Layer names are part of the stored RNA path.
    # Renaming one must cause a strict, atomic failure: valid entries applied
    # earlier in the transaction are rolled back and the active take remains.
    rename_collection = bpy.data.collections.new("TS_Rename_Target")
    scene.collection.children.link(rename_collection)
    for view_layer in scene.view_layers:
        view_layer.update()
    rename_layer_collection, rename_owner_path = layer_collection_entry(
        scene,
        primary_view_layer,
        rename_collection,
    )
    stale_rename_path = f"{rename_owner_path}.exclude"
    rename_take = engine.create_take(
        scene,
        "Collection Rename Atomicity",
        parent_uuid=main.uuid,
        make_active=True,
    )
    rollback_object = make_object(scene, "TS_Collection_Rollback")

    object_seed = engine.capture_override(
        scene,
        rollback_object,
        "location",
        rename_take.uuid,
    )
    require(
        object_seed.main_seeded,
        "Rename rollback fixture did not seed the object Main baseline",
    )
    rollback_object.location = (4.0, 5.0, 6.0)
    engine.capture_override(
        scene,
        rollback_object,
        "location",
        rename_take.uuid,
    )

    rename_seed = engine.capture_override(
        scene,
        scene,
        stale_rename_path,
        rename_take.uuid,
    )
    require(
        rename_seed.main_seeded,
        "Rename rollback fixture did not seed the collection Main baseline",
    )
    rename_layer_collection.exclude = True
    engine.capture_override(
        scene,
        scene,
        stale_rename_path,
        rename_take.uuid,
    )

    rollback_object.location = (9.0, 9.0, 9.0)
    rename_layer_collection.exclude = False
    rename_collection.name = "TS_Rename_Target_Changed"
    require_raises(
        ValueError,
        lambda: scene.path_resolve(stale_rename_path),
        "Collection rename did not invalidate its stored hierarchy path",
    )
    scene.take_system.active_take_uuid = main.uuid
    apply_error = require_raises(
        engine.TakeApplyError,
        lambda: engine.apply_take(
            scene,
            rename_take.uuid,
            strict=True,
        ),
        "Strict apply accepted a stale renamed-collection path",
    )
    require(
        apply_error.report.skipped >= 1
        and apply_error.report.applied == 0,
        "Stale collection path did not report an atomic apply failure",
    )
    require(
        all(
            abs(float(actual) - expected) <= 1e-5
            for actual, expected in zip(
                rollback_object.location,
                (9.0, 9.0, 9.0),
            )
        ),
        "Strict stale-path failure did not roll back an earlier object write",
    )
    require(
        rename_layer_collection.exclude is False,
        "Strict stale-path failure changed the renamed collection state",
    )
    require(
        scene.take_system.active_take_uuid == main.uuid,
        "Failed strict apply changed the active take",
    )

    print("TAKE_SYSTEM_COLLECTION_STATE_TEST_OK")
finally:
    recent.clear_all()
    try:
        if registered:
            blender_take_system.unregister()
    finally:
        cleanup_test_data()
