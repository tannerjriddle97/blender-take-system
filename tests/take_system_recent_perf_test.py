"""Focused regressions for the indexed recent-action tracker hot path."""

from pathlib import Path
from types import SimpleNamespace
import sys

import bpy


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import blender_take_system
from blender_take_system import engine, recent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fake_depsgraph(*id_blocks):
    return SimpleNamespace(
        updates=tuple(
            SimpleNamespace(id=id_block)
            for id_block in id_blocks
        )
    )


def layer_entry(scene, view_layer, collection):
    collection_uid = recent._id_uid(collection)
    for candidate_view_layer, layer_collection, owner_path in (
        engine.iter_layer_collection_paths(scene)
    ):
        if (
            recent._safe_pointer(candidate_view_layer)
            == recent._safe_pointer(view_layer)
            and recent._id_uid(layer_collection.collection)
            == collection_uid
        ):
            return layer_collection, f"{owner_path}.exclude"
    raise AssertionError("LayerCollection occurrence was not found")


registered = False
try:
    try:
        blender_take_system.unregister()
    except Exception:
        pass
    blender_take_system.register()
    registered = True

    scene = bpy.context.scene
    main = engine.ensure_main_take(scene)
    take = engine.create_take(
        scene,
        "Recent Perf",
        parent_uuid=main.uuid,
        make_active=True,
    )
    test_object = bpy.data.objects.new("TS_Recent_Perf_Object", None)
    scene.collection.objects.link(test_object)

    collections = []
    for index in range(24):
        collection = bpy.data.collections.new(
            f"TS_Recent_Perf_{index:02d}"
        )
        scene.collection.children.link(collection)
        collections.append(collection)
    alternate = scene.view_layers.new("TS_Recent_Perf_Alternate")
    for view_layer in scene.view_layers:
        view_layer.update()

    primary = scene.view_layers[0]
    primary_layer, primary_path = layer_entry(
        scene,
        primary,
        collections[0],
    )
    alternate_layer, alternate_path = layer_entry(
        scene,
        alternate,
        collections[0],
    )

    # Existing stored collection paths must still be replaced by the direct
    # runtime candidate during discovery.
    engine.capture_override(
        scene,
        scene,
        primary_path,
        take.uuid,
    )
    tracker = recent.rebaseline_scene(scene)
    primary_key = (recent._id_uid(scene), primary_path)
    alternate_key = (recent._id_uid(scene), alternate_path)
    require(
        tracker.properties[primary_key].direct_attribute == "exclude"
        and recent._safe_pointer(
            tracker.properties[primary_key].direct_owner
        )
        == recent._safe_pointer(primary_layer),
        "Stored collection path retained the generic Scene RNA reader",
    )
    require(
        primary_key in tracker.watch_index[recent._id_uid(scene)]
        and primary_key
        in tracker.watch_index[recent._id_uid(collections[0])],
        "Collection candidate is missing from the reverse watch index",
    )

    original_freeze = recent._freeze_supported_value
    original_snapshot = recent._layer_collection_snapshot
    original_discover = recent._discover_scene_properties
    generic_reads = []
    snapshot_calls = [0]
    discovery_calls = [0]

    def count_freeze(target_id, data_path):
        generic_reads.append((recent._id_uid(target_id), data_path))
        return original_freeze(target_id, data_path)

    def count_snapshot(*args, **kwargs):
        snapshot_calls[0] += 1
        return original_snapshot(*args, **kwargs)

    def count_discovery(*args, **kwargs):
        discovery_calls[0] += 1
        return original_discover(*args, **kwargs)

    recent._freeze_supported_value = count_freeze
    recent._layer_collection_snapshot = count_snapshot
    recent._discover_scene_properties = count_discovery
    try:
        # A plain LayerCollection toggle should use cached occurrence handles:
        # no full tree walk, discovery pass, or long Scene path resolution.
        primary_layer.exclude = True
        action = recent.observe_scene(
            scene,
            fake_depsgraph(scene),
            now=10.0,
        )
        require(
            action is not None and primary_key in action.changes,
            "Direct collection toggle was not observed",
        )
        require(
            snapshot_calls[0] == 0
            and discovery_calls[0] == 0
            and not generic_reads,
            "Common collection toggle entered a broad/path-resolving route",
        )

        # Reverse indexing must keep an unrelated object update away from all
        # Scene-anchored collection candidates, even far past the old timer.
        recent.rebaseline_scene(scene)
        generic_reads.clear()
        snapshot_calls[0] = 0
        discovery_calls[0] = 0
        test_object.location = (1.0, 2.0, 3.0)
        action = recent.observe_scene(
            scene,
            fake_depsgraph(test_object),
            now=tracker.last_discovery_at + 1000.0,
        )
        require(
            action is not None
            and (
                recent._id_uid(test_object),
                "location",
            )
            in action.changes,
            "Indexed object update was not observed",
        )
        require(
            generic_reads
            and all(
                target_uid == recent._id_uid(test_object)
                for target_uid, _path in generic_reads
            )
            and snapshot_calls[0] == 0
            and discovery_calls[0] == 0,
            "Unrelated update scanned collection candidates or timed refresh",
        )
    finally:
        recent._freeze_supported_value = original_freeze
        recent._layer_collection_snapshot = original_snapshot
        recent._discover_scene_properties = original_discover

    # Dynamic custom properties trigger event-driven discovery. Their initial
    # value becomes the baseline; the first later edit is captured.
    recent.rebaseline_scene(scene)
    test_object["TS_New_Supported"] = 1.0
    recent.observe_scene(
        scene,
        fake_depsgraph(test_object),
        now=20.0,
    )
    custom_path = '["TS_New_Supported"]'
    custom_key = (recent._id_uid(test_object), custom_path)
    tracker = recent.ensure_scene(scene)
    require(
        custom_key in tracker.properties
        and tracker.properties[custom_key].current.value == 1.0,
        "Event-driven discovery missed a new supported property",
    )
    test_object["TS_New_Supported"] = 2.0
    action = recent.observe_scene(
        scene,
        fake_depsgraph(test_object),
        now=21.0,
    )
    require(
        action is not None
        and custom_key in action.changes
        and action.changes[custom_key].after.value == 2.0,
        "First edit after property discovery was lost",
    )

    # Exact tracking must retain IEEE-754 sign bits; Python's ordinary numeric
    # equality would otherwise hide +0.0 -> -0.0 scalar and vector changes.
    signed_zero_path = '["TS_Recent_Signed_Zero"]'
    test_object["TS_Recent_Signed_Zero"] = 0.0
    recent.rebaseline_scene(scene)
    test_object["TS_Recent_Signed_Zero"] = -0.0
    signed_zero_key = (recent._id_uid(test_object), signed_zero_path)
    action = recent.observe_scene(
        scene,
        fake_depsgraph(test_object),
        now=22.0,
    )
    require(
        action is not None
        and signed_zero_key in action.changes
        and recent._value_key((0.0, 1.0))
        != recent._value_key((-0.0, 1.0)),
        "Signed-zero property transition was treated as unchanged",
    )

    # Objects inside excluded Collections can produce callbacks with no dirty
    # IDs. Such an event must discover new supported structure and scan known
    # values so the first subsequent edit is not lost.
    recent.rebaseline_scene(scene)
    empty_event_path = '["TS_Recent_Empty_Event"]'
    empty_event_key = (recent._id_uid(test_object), empty_event_path)
    test_object["TS_Recent_Empty_Event"] = 1.0
    recent.observe_scene(scene, fake_depsgraph(), now=23.0)
    tracker = recent.ensure_scene(scene)
    require(
        empty_event_key in tracker.properties,
        "Empty dirty-ID event did not discover new supported structure",
    )
    test_object["TS_Recent_Empty_Event"] = 2.0
    action = recent.observe_scene(scene, fake_depsgraph(), now=24.0)
    require(
        action is not None
        and empty_event_key in action.changes
        and action.changes[empty_event_key].after.value == 2.0,
        "Empty dirty-ID event did not scan an existing tracked value",
    )

    # Linking and renaming collections rebuilds the persistent paths before
    # their first subsequent exclude edit, while retaining direct readers.
    recent.rebaseline_scene(scene)
    dynamic = bpy.data.collections.new("TS_Recent_Perf_Dynamic")
    scene.collection.children.link(dynamic)
    for view_layer in scene.view_layers:
        view_layer.update()
    recent.observe_scene(scene, fake_depsgraph(scene), now=30.0)
    dynamic_layer, dynamic_path = layer_entry(scene, primary, dynamic)
    dynamic_key = (recent._id_uid(scene), dynamic_path)
    tracker = recent.ensure_scene(scene)
    require(
        dynamic_key in tracker.properties
        and recent._safe_pointer(
            tracker.properties[dynamic_key].direct_owner
        )
        == recent._safe_pointer(dynamic_layer),
        "Topology event did not install a direct dynamic candidate",
    )
    dynamic_layer.exclude = True
    action = recent.observe_scene(
        scene,
        fake_depsgraph(scene),
        now=31.0,
    )
    require(
        action is not None and dynamic_key in action.changes,
        "First dynamic collection toggle was lost",
    )

    old_dynamic_key = dynamic_key
    dynamic.name = "TS_Recent_Perf_Renamed"
    for view_layer in scene.view_layers:
        view_layer.update()
    # Blender can report an excluded Collection rename with no dirty IDs.
    # The empty callback must still verify topology and move the cached
    # persistent path before the next enabled-state edit.
    recent.observe_scene(
        scene,
        fake_depsgraph(),
        now=40.0,
    )
    renamed_layer, renamed_path = layer_entry(scene, primary, dynamic)
    renamed_key = (recent._id_uid(scene), renamed_path)
    tracker = recent.ensure_scene(scene)
    require(
        old_dynamic_key not in tracker.properties
        and renamed_key in tracker.properties
        and recent._safe_pointer(
            tracker.properties[renamed_key].direct_owner
        )
        == recent._safe_pointer(renamed_layer)
        and tracker.recent_action is not None
        and old_dynamic_key not in tracker.recent_action.changes
        and renamed_key in tracker.recent_action.changes,
        "Collection rename did not rebuild its path and migrate the action",
    )
    renamed_report = recent.capture_pending(scene, take.uuid)
    require(
        renamed_report.captured == 1
        and engine.find_override(take, scene, renamed_path) is not None,
        "Migrated collection action did not capture at its renamed path",
    )
    renamed_layer.exclude = False
    action = recent.observe_scene(
        scene,
        fake_depsgraph(scene),
        now=41.0,
    )
    require(
        action is not None
        and renamed_key in action.changes
        and action.changes[renamed_key].after.value is False,
        "First toggle after an empty-update rename was absorbed as baseline",
    )

    # Deferred evaluation ignores dependency-graph callbacks by design. Its
    # one settling synchronization must nevertheless refresh topology and
    # newly supported structure before accepting the next user edit.
    renamed_layer.exclude = True
    recent.rebaseline_scene(scene)
    pre_defer_key = renamed_key
    recent.defer_scene(scene, seconds=0.0)
    dynamic.name = "TS_Recent_Perf_Deferred_Renamed"
    test_object["TS_Recent_Perf_Deferred_Custom"] = 1.0
    for view_layer in scene.view_layers:
        view_layer.update()
    synchronized = recent.ensure_scene(scene)
    deferred_layer, deferred_path = layer_entry(scene, primary, dynamic)
    deferred_key = (recent._id_uid(scene), deferred_path)
    deferred_custom_path = '["TS_Recent_Perf_Deferred_Custom"]'
    deferred_custom_key = (
        recent._id_uid(test_object),
        deferred_custom_path,
    )
    require(
        synchronized is not None
        and pre_defer_key not in synchronized.properties
        and deferred_key in synchronized.properties
        and deferred_custom_key in synchronized.properties,
        "Deferred settling sync retained stale or incomplete discovery",
    )
    test_object["TS_Recent_Perf_Deferred_Custom"] = 2.0
    action = recent.observe_scene(
        scene,
        fake_depsgraph(test_object),
        now=41.5,
    )
    require(
        action is not None
        and deferred_custom_key in action.changes
        and action.changes[deferred_custom_key].after.value == 2.0,
        "First post-defer edit was absorbed into discovery",
    )

    # Blender may emit only the root Scene Collection for an atomic reparent
    # wholly inside excluded branches. Counts remain unchanged, so the root
    # event must verify the cached per-Collection hierarchy signatures.
    parent_a = bpy.data.collections.new("TS_Recent_Perf_Parent_A")
    parent_b = bpy.data.collections.new("TS_Recent_Perf_Parent_B")
    nested = bpy.data.collections.new("TS_Recent_Perf_Nested")
    scene.collection.children.link(parent_a)
    scene.collection.children.link(parent_b)
    parent_a.children.link(nested)
    for view_layer in scene.view_layers:
        view_layer.update()
    parent_a_layer, _parent_a_path = layer_entry(
        scene,
        primary,
        parent_a,
    )
    parent_b_layer, _parent_b_path = layer_entry(
        scene,
        primary,
        parent_b,
    )
    parent_a_layer.exclude = True
    parent_b_layer.exclude = True
    recent.rebaseline_scene(scene)
    _old_nested_layer, old_nested_path = layer_entry(
        scene,
        primary,
        nested,
    )
    old_nested_key = (recent._id_uid(scene), old_nested_path)
    parent_a.children.unlink(nested)
    parent_b.children.link(nested)
    for view_layer in scene.view_layers:
        view_layer.update()
    recent.observe_scene(
        scene,
        fake_depsgraph(scene.collection),
        now=42.0,
    )
    _new_nested_layer, new_nested_path = layer_entry(
        scene,
        primary,
        nested,
    )
    new_nested_key = (recent._id_uid(scene), new_nested_path)
    tracker = recent.ensure_scene(scene)
    require(
        old_nested_key not in tracker.properties
        and new_nested_key in tracker.properties
        and tracker.recent_action is None,
        "Root-only excluded-subtree reparent retained a stale path",
    )

    # Deferred frame synchronization must also use direct handles and clear
    # the resulting value difference without manufacturing a recent action.
    recent.rebaseline_scene(scene)
    deferred_layer.exclude = False
    recent.defer_scene(scene, seconds=0.0)
    synchronized = recent.ensure_scene(scene)
    require(
        synchronized.properties[deferred_key].current.value is False
        and synchronized.recent_action is None,
        "Deferred synchronization did not refresh the direct cached value",
    )

    # Integration check with Blender's registered dependency-graph handler.
    # In Blender 5.2 this excluded rename arrives with depsgraph.updates empty;
    # no direct observe_scene call should be needed to migrate the path.
    actual = bpy.data.collections.new("TS_Recent_Perf_Actual_Event")
    scene.collection.children.link(actual)
    for view_layer in scene.view_layers:
        view_layer.update()
    actual_layer, actual_old_path = layer_entry(scene, primary, actual)
    actual_layer.exclude = True
    primary.update()
    recent.rebaseline_scene(scene)
    actual_old_key = (recent._id_uid(scene), actual_old_path)
    actual.name = "TS_Recent_Perf_Actual_Renamed"
    primary.update()
    actual_layer, actual_new_path = layer_entry(scene, primary, actual)
    actual_new_key = (recent._id_uid(scene), actual_new_path)
    tracker = recent.ensure_scene(scene)
    require(
        actual_old_key not in tracker.properties
        and actual_new_key in tracker.properties,
        "Registered handler missed an excluded Collection rename",
    )
    actual_layer.exclude = False
    primary.update()
    action = recent.peek_recent_action(scene)
    require(
        action is not None and actual_new_key in action.changes,
        "Registered handler lost the first post-rename collection toggle",
    )

    print("TAKE_SYSTEM_RECENT_PERF_TEST_OK")
finally:
    if registered:
        blender_take_system.unregister()
