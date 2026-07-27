"""Runtime tracking for capturing the most recent supported scene action."""

import json
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import bpy

from . import engine


ACTION_GROUP_SECONDS = 0.45
_TRACKERS = {}
_DEFERRED_UNTIL = {}
_SUSPEND_DEPTH = 0
_MISSING_VALUE = object()

_OBJECT_PATHS = (
    "location",
    "rotation_mode",
    "rotation_euler",
    "rotation_quaternion",
    "rotation_axis_angle",
    "scale",
    "delta_location",
    "delta_rotation_euler",
    "delta_rotation_quaternion",
    "delta_scale",
    "hide_viewport",
    "hide_render",
    "display_type",
    "color",
    "visible_camera",
    "visible_diffuse",
    "visible_glossy",
    "visible_transmission",
    "visible_volume_scatter",
    "visible_shadow",
)

_GENERIC_PROPERTY_EXCLUSIONS = {
    "rna_type",
    "name",
    "name_full",
    "type",
    "show_expanded",
    "is_embedded_data",
    "is_evaluated",
    "is_library_indirect",
    "is_missing",
    "is_runtime_data",
    "is_updated",
    "is_updated_data",
    "is_updated_transform",
    "original",
    "users",
    "use_fake_user",
    "tag",
}


@dataclass(frozen=True)
class FrozenValue:
    key: object
    value: object

    @property
    def is_missing(self):
        return self.value is _MISSING_VALUE


@dataclass
class TrackedProperty:
    key: tuple
    target_id: object
    data_path: str
    label: str
    watch_uids: frozenset
    baseline: FrozenValue
    current: FrozenValue
    direct_owner: object = None
    direct_attribute: str = ""
    direct_key: object = None
    source_uid: int = 0


@dataclass
class RecentChange:
    key: tuple
    target_id: object
    data_path: str
    label: str
    baseline: FrozenValue
    before: FrozenValue
    after: FrozenValue


@dataclass
class RecentAction:
    scene_uid: int
    take_uuid: str
    changes: dict = field(default_factory=dict)
    started_at: float = 0.0
    updated_at: float = 0.0
    finalized: bool = False

    @property
    def summary(self):
        count = len(self.changes)
        if count == 1:
            return next(iter(self.changes.values())).label
        target_count = len(
            {
                change.key[0]
                for change in self.changes.values()
            }
        )
        return (
            f"{count} changed properties on "
            f"{target_count} datablock{'s' if target_count != 1 else ''}"
        )


@dataclass
class TrackerState:
    scene_uid: int
    applied_take_uuid: str
    mutation_revision: int
    global_mutation_revision: int
    properties: dict = field(default_factory=dict)
    watch_index: dict = field(default_factory=dict)
    direct_index: dict = field(default_factory=dict)
    recent_action: object = None
    last_discovery_at: float = 0.0
    layer_collection_topology_signature: tuple = ()
    layer_collection_watch_uids: frozenset = frozenset()
    collection_hierarchy_guard: tuple = ()
    collection_structure_signatures: dict = field(default_factory=dict)
    structure_signatures: dict = field(default_factory=dict)
    structure_ids: dict = field(default_factory=dict)


@dataclass
class LayerCollectionEntry:
    view_layer: object
    layer_collection: object
    owner_path: str
    direct_key: tuple
    collection_uid: int
    value: bool


@dataclass
class LayerCollectionSnapshot:
    signature: tuple
    entries: tuple
    values: dict
    watch_uids: frozenset


def _id_uid(id_block):
    if id_block is None:
        return 0
    try:
        session_uid = int(id_block.session_uid)
    except (AttributeError, ReferenceError, TypeError, ValueError):
        session_uid = 0
    if session_uid:
        return session_uid
    try:
        return id_block.as_pointer()
    except (AttributeError, ReferenceError, TypeError):
        return 0


def _scene_uid(scene):
    return _id_uid(scene) or id(scene)


def _safe_target_name(target_id):
    try:
        return target_id.name_full
    except (AttributeError, ReferenceError):
        return "<missing datablock>"


def _value_key(value):
    if isinstance(value, bpy.types.ID):
        return ("ID", _id_uid(value))
    if value is None:
        return ("NONE",)
    if isinstance(value, float):
        return ("float", struct.pack("!d", value))
    if isinstance(value, tuple):
        return ("TUPLE", tuple(_value_key(component) for component in value))
    return (type(value).__name__, value)


def _freeze_supported_value(target_id, data_path):
    rna_property = engine.validate_capture_path(target_id, data_path)
    value = engine.read_path_value(target_id, data_path)
    _prop_type, _subtype, normalized = engine.classify_value(
        value,
        rna_property,
    )
    return FrozenValue(_value_key(normalized), normalized)


def _freeze_tracked_value(tracked, direct_values=None):
    """Read a tracked value, using its trusted runtime handle when available."""

    if tracked.direct_attribute:
        if (
            direct_values is not None
            and tracked.direct_key in direct_values
        ):
            value = direct_values[tracked.direct_key]
        else:
            value = getattr(
                tracked.direct_owner,
                tracked.direct_attribute,
            )
        # LayerCollection.exclude is a BOOL RNA property. Normalizing it
        # directly avoids resolving and validating a long Scene RNA path on
        # every depsgraph callback.
        normalized = bool(value)
        return FrozenValue(_value_key(normalized), normalized)
    return _freeze_supported_value(tracked.target_id, tracked.data_path)


def _missing_value():
    return FrozenValue(("MISSING",), _MISSING_VALUE)


def _collection_items(owner, attribute):
    try:
        return tuple(getattr(owner, attribute, ()))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _binding_slot_handle(binding):
    """Return the layered Action slot selected by AnimData/NlaStrip."""

    try:
        slot = binding.action_slot
    except (AttributeError, ReferenceError, RuntimeError):
        slot = None
    if slot is not None:
        try:
            handle = int(slot.handle)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            handle = 0
        if handle:
            return handle

    try:
        handle = int(binding.action_slot_handle)
    except (AttributeError, ReferenceError, TypeError, ValueError):
        handle = 0
    return handle or None


def _fcurves_have_path(fcurves, data_path):
    for fcurve in fcurves:
        try:
            if fcurve.data_path == data_path:
                return True
        except (AttributeError, ReferenceError):
            continue
    return False


def _action_has_path(action, data_path, slot_handle=None):
    if action is None:
        return False

    # Compatibility with legacy Actions in Blender versions/files that still
    # expose their curves directly on the Action.
    if _fcurves_have_path(
        _collection_items(action, "fcurves"),
        data_path,
    ):
        return True

    # Blender 5.x stores curves in channelbags below
    # Action -> layers -> strips. Only the bag for the slot selected by the
    # AnimData or NLA strip applies to this datablock.
    for layer in _collection_items(action, "layers"):
        for strip in _collection_items(layer, "strips"):
            for channelbag in _collection_items(strip, "channelbags"):
                if slot_handle is not None:
                    try:
                        bag_handle = int(channelbag.slot_handle)
                    except (
                        AttributeError,
                        ReferenceError,
                        TypeError,
                        ValueError,
                    ):
                        continue
                    if bag_handle != slot_handle:
                        continue
                if _fcurves_have_path(
                    _collection_items(channelbag, "fcurves"),
                    data_path,
                ):
                    return True
    return False


def _iter_nla_strips(strips):
    """Yield top-level and nested META strips without trusting their type."""

    try:
        pending = list(strips)
    except (ReferenceError, RuntimeError, TypeError):
        return
    seen = set()
    while pending:
        strip = pending.pop()
        try:
            pointer = strip.as_pointer()
        except (AttributeError, ReferenceError, TypeError):
            pointer = id(strip)
        if pointer in seen:
            continue
        seen.add(pointer)
        yield strip
        pending.extend(_collection_items(strip, "strips"))


def _is_animated_path(target_id, data_path):
    try:
        animation_data = target_id.animation_data
    except (AttributeError, ReferenceError):
        return False
    if animation_data is None:
        return False
    if _fcurves_have_path(
        _collection_items(animation_data, "drivers"),
        data_path,
    ):
        return True
    try:
        action = animation_data.action
    except (AttributeError, ReferenceError, RuntimeError):
        action = None
    if _action_has_path(
        action,
        data_path,
        _binding_slot_handle(animation_data),
    ):
        return True

    for track in _collection_items(animation_data, "nla_tracks"):
        try:
            strips = track.strips
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        for strip in _iter_nla_strips(strips):
            try:
                action = strip.action
            except (AttributeError, ReferenceError, RuntimeError):
                action = None
            if _action_has_path(
                action,
                data_path,
                _binding_slot_handle(strip),
            ):
                return True
    return False


def _target_is_editable(target_id):
    try:
        return not (
            (
                target_id.library is not None
                and target_id.override_library is None
            )
            or target_id.is_editable is False
        )
    except (AttributeError, ReferenceError):
        return False


def _candidate_from_path(
    target_id,
    data_path,
    *,
    label=None,
    watch_ids=(),
):
    if not isinstance(target_id, bpy.types.ID):
        return None
    if not _target_is_editable(target_id):
        return None
    try:
        target_id, data_path = engine.canonicalize_id_path(
            target_id,
            data_path,
        )
        if engine.is_take_system_internal_path(target_id, data_path):
            return None
        if _is_animated_path(target_id, data_path):
            return None
        frozen = _freeze_supported_value(target_id, data_path)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        TypeError,
        ValueError,
    ):
        return None
    target_uid = _id_uid(target_id)
    if not target_uid:
        return None
    watch_uids = {
        uid
        for uid in (_id_uid(watch_id) for watch_id in watch_ids)
        if uid
    }
    watch_uids.add(target_uid)
    key = (target_uid, data_path)
    if label is None:
        label = f"{_safe_target_name(target_id)} — {data_path}"
    return TrackedProperty(
        key=key,
        target_id=target_id,
        data_path=data_path,
        label=label,
        watch_uids=frozenset(watch_uids),
        baseline=frozen,
        current=frozen,
    )


def _safe_pointer(value):
    if value is None:
        return 0
    try:
        return int(value.as_pointer())
    except (AttributeError, ReferenceError, TypeError, ValueError):
        return 0


def _layer_collection_snapshot(scene, *, build_paths=False):
    """Collect one compact LayerCollection tree snapshot.

    The hot path deliberately fingerprints occurrence and parent pointers
    without constructing Scene RNA paths. Full paths are only needed while
    (re)building discovery.
    """

    signature = []
    entries = []
    values = {}
    watch_uids = {_id_uid(scene)}
    try:
        view_layers = tuple(scene.view_layers)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        view_layers = ()

    for view_layer in view_layers:
        try:
            root = view_layer.layer_collection
            view_layer_name = view_layer.name
            view_layer_pointer = _safe_pointer(view_layer)
            root_path = (
                view_layer.path_from_id("layer_collection")
                if build_paths
                else ""
            )
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue

        pending = [(root, None, root_path, True)]
        while pending:
            layer_collection, parent, owner_path, is_root = pending.pop()
            try:
                layer_pointer = _safe_pointer(layer_collection)
                parent_pointer = _safe_pointer(parent)
                collection_id = layer_collection.collection
                collection_uid = _id_uid(collection_id)
                collection_name = layer_collection.name
                value = bool(layer_collection.exclude)
            except (
                AttributeError,
                ReferenceError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                continue
            direct_key = (view_layer_pointer, layer_pointer)
            signature.append(
                (
                    view_layer_name,
                    view_layer_pointer,
                    layer_pointer,
                    parent_pointer,
                    collection_uid,
                    collection_name,
                )
            )
            values[direct_key] = value
            if collection_uid:
                watch_uids.add(collection_uid)
            if not is_root:
                entries.append(
                    LayerCollectionEntry(
                        view_layer=view_layer,
                        layer_collection=layer_collection,
                        owner_path=owner_path,
                        direct_key=direct_key,
                        collection_uid=collection_uid,
                        value=value,
                    )
                )

            try:
                children = tuple(layer_collection.children)
            except (
                AttributeError,
                ReferenceError,
                RuntimeError,
                TypeError,
            ):
                children = ()
            for child in reversed(children):
                if build_paths:
                    child_path = (
                        f"{owner_path}.children["
                        f'"{bpy.utils.escape_identifier(str(child.name))}"]'
                    )
                else:
                    child_path = ""
                pending.append(
                    (
                        child,
                        layer_collection,
                        child_path,
                        False,
                    )
                )

    return LayerCollectionSnapshot(
        signature=tuple(signature),
        entries=tuple(entries),
        values=values,
        watch_uids=frozenset(uid for uid in watch_uids if uid),
    )


def _candidate_from_layer_collection(scene, entry):
    if not isinstance(scene, bpy.types.Scene):
        return None
    if not _target_is_editable(scene):
        return None
    target_uid = _id_uid(scene)
    if not target_uid or not entry.owner_path:
        return None
    data_path = f"{entry.owner_path}.exclude"
    frozen = FrozenValue(
        _value_key(entry.value),
        entry.value,
    )
    watch_uids = {target_uid}
    if entry.collection_uid:
        watch_uids.add(entry.collection_uid)
    try:
        view_layer_name = entry.view_layer.name
        collection_name = entry.layer_collection.name
    except (AttributeError, ReferenceError):
        return None
    return TrackedProperty(
        key=(target_uid, data_path),
        target_id=scene,
        data_path=data_path,
        label=(
            f"{view_layer_name} — {collection_name} Enabled State"
        ),
        watch_uids=frozenset(watch_uids),
        baseline=frozen,
        current=frozen,
        direct_owner=entry.layer_collection,
        direct_attribute="exclude",
        direct_key=entry.direct_key,
        source_uid=entry.collection_uid,
    )


def _reachable_collections(scene):
    try:
        root = scene.collection
    except (AttributeError, ReferenceError):
        return ()
    result = []
    seen = set()
    pending = [root]
    while pending:
        collection = pending.pop()
        uid = _id_uid(collection)
        if not uid or uid in seen:
            continue
        seen.add(uid)
        result.append(collection)
        try:
            pending.extend(tuple(collection.children))
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
        ):
            continue
    return tuple(result)


def _collection_structure_signature(collection):
    try:
        name = collection.name
        child_uids = tuple(
            _id_uid(child)
            for child in collection.children
        )
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        TypeError,
    ):
        return ("MISSING",)
    return (name, child_uids)


def _collection_hierarchy_guard(scene):
    try:
        view_layers = tuple(
            (view_layer.name, _safe_pointer(view_layer))
            for view_layer in scene.view_layers
        )
        root = scene.collection
        descendant_count = len(root.children_recursive)
        direct_child_count = len(root.children)
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        TypeError,
    ):
        return ("MISSING",)
    return (
        view_layers,
        _id_uid(root),
        descendant_count,
        direct_child_count,
    )


def _collection_hierarchy_state(scene):
    signatures = {}
    for collection in _reachable_collections(scene):
        uid = _id_uid(collection)
        if uid:
            signatures[uid] = _collection_structure_signature(collection)
    return _collection_hierarchy_guard(scene), signatures


def _topology_change_suspected(
    scene,
    state,
    dirty_ids,
    *,
    force_verify=False,
):
    if force_verify:
        guard, signatures = _collection_hierarchy_state(scene)
        return (
            guard != state.collection_hierarchy_guard
            or signatures != state.collection_structure_signatures
        )

    if not dirty_ids:
        return False
    scene_uid = _id_uid(scene)
    if (
        scene_uid in dirty_ids
        and _collection_hierarchy_guard(scene)
        != state.collection_hierarchy_guard
    ):
        return True
    try:
        root_collection_uid = _id_uid(scene.collection)
    except (AttributeError, ReferenceError, RuntimeError):
        root_collection_uid = 0
    if root_collection_uid and root_collection_uid in dirty_ids:
        # Blender may report only the Scene Collection when an excluded
        # subtree is atomically reparented. Root counts and its direct children
        # can remain unchanged, so compare the cached reachable hierarchy.
        guard, signatures = _collection_hierarchy_state(scene)
        return (
            guard != state.collection_hierarchy_guard
            or signatures != state.collection_structure_signatures
        )

    unknown_collections = []
    for uid, id_block in dirty_ids.items():
        if not isinstance(id_block, bpy.types.Collection):
            continue
        previous = state.collection_structure_signatures.get(
            uid,
            _MISSING_VALUE,
        )
        if previous is _MISSING_VALUE:
            unknown_collections.append(uid)
        elif _collection_structure_signature(id_block) != previous:
            return True
    if unknown_collections:
        live_uids = {
            _id_uid(collection)
            for collection in _reachable_collections(scene)
        }
        return any(uid in live_uids for uid in unknown_collections)
    return False


def _custom_property_path(key):
    return f"[{json.dumps(str(key), ensure_ascii=True)}]"


def _add_candidate(candidates, candidate):
    if candidate is not None:
        candidates.setdefault(candidate.key, candidate)


def _add_custom_properties(candidates, id_block):
    try:
        keys = tuple(id_block.keys())
    except (AttributeError, ReferenceError, TypeError):
        return
    for key in keys:
        if key == "_RNA_UI":
            continue
        path = _custom_property_path(key)
        _add_candidate(
            candidates,
            _candidate_from_path(
                id_block,
                path,
                label=f"{_safe_target_name(id_block)} — {key}",
                watch_ids=(id_block,),
            ),
        )


def _add_generic_rna_properties(candidates, owner):
    try:
        id_data = owner.id_data
        properties = tuple(owner.bl_rna.properties)
    except (AttributeError, ReferenceError, TypeError):
        return
    if not isinstance(id_data, bpy.types.ID):
        return
    for rna_property in properties:
        identifier = rna_property.identifier
        if (
            identifier in _GENERIC_PROPERTY_EXCLUSIONS
            or rna_property.is_readonly
            or rna_property.type == "COLLECTION"
        ):
            continue
        try:
            data_path = owner.path_from_id(identifier)
        except (AttributeError, ReferenceError, TypeError, ValueError):
            continue
        _add_candidate(
            candidates,
            _candidate_from_path(
                id_data,
                data_path,
                watch_ids=(id_data,),
            ),
        )


def _add_node_input_candidates(candidates, owner_id):
    try:
        node_tree = owner_id.node_tree
    except (AttributeError, ReferenceError):
        return
    if node_tree is None:
        return
    try:
        nodes = tuple(node_tree.nodes)
    except (AttributeError, ReferenceError):
        return
    for node in nodes:
        try:
            sockets = tuple(node.inputs)
        except (AttributeError, ReferenceError):
            continue
        for socket in sockets:
            try:
                if "default_value" not in socket.bl_rna.properties:
                    continue
                data_path = socket.path_from_id("default_value")
            except (AttributeError, ReferenceError, TypeError, ValueError):
                continue
            _add_candidate(
                candidates,
                _candidate_from_path(
                    node_tree,
                    data_path,
                    label=(
                        f"{_safe_target_name(owner_id)} — "
                        f"{node.name}.{socket.name}"
                    ),
                    watch_ids=(owner_id, node_tree),
                ),
            )


def _discover_scene_properties(scene, layer_snapshot=None):
    candidates = {}

    try:
        takes = tuple(scene.take_system.takes)
    except (AttributeError, ReferenceError):
        takes = ()
    for take in takes:
        for override in take.overrides:
            try:
                target_id = override.target_id
                data_path = override.data_path
            except (AttributeError, ReferenceError):
                continue
            _add_candidate(
                candidates,
                _candidate_from_path(
                    target_id,
                    data_path,
                    watch_ids=(target_id,),
                ),
            )

    if layer_snapshot is None or not all(
        entry.owner_path for entry in layer_snapshot.entries
    ):
        layer_snapshot = _layer_collection_snapshot(
            scene,
            build_paths=True,
        )
    for entry in layer_snapshot.entries:
        candidate = _candidate_from_layer_collection(scene, entry)
        if candidate is not None:
            # Stored take records are discovered first. Explicit assignment
            # ensures a live LayerCollection candidate replaces the generic
            # Scene RNA reader for the same persistent path.
            candidates[candidate.key] = candidate

    materials = set()
    data_ids = set()
    try:
        objects = tuple(scene.objects)
    except (AttributeError, ReferenceError):
        objects = ()
    for obj in objects:
        for data_path in _OBJECT_PATHS:
            if not hasattr(obj, data_path):
                continue
            _add_candidate(
                candidates,
                _candidate_from_path(
                    obj,
                    data_path,
                    label=f"{_safe_target_name(obj)} — {data_path}",
                    watch_ids=(obj,),
                ),
            )
        _add_custom_properties(candidates, obj)

        try:
            modifiers = tuple(obj.modifiers)
        except (AttributeError, ReferenceError):
            modifiers = ()
        for modifier in modifiers:
            _add_generic_rna_properties(candidates, modifier)

        try:
            slots = tuple(obj.material_slots)
        except (AttributeError, ReferenceError):
            slots = ()
        for index, slot in enumerate(slots):
            link_path = f"material_slots[{index}].link"
            _add_candidate(
                candidates,
                _candidate_from_path(
                    obj,
                    link_path,
                    label=(
                        f"{_safe_target_name(obj)} — "
                        f"Material Slot {index + 1} Link"
                    ),
                    watch_ids=(obj, getattr(obj, "data", None)),
                ),
            )
            try:
                material = slot.material
                link = slot.link
            except ReferenceError:
                material = None
                link = ""
            if material is not None:
                materials.add(material)
            # A DATA-linked assignment is shared by every object using the
            # Mesh, so the tracker cannot infer which object the user intended.
            if link == "OBJECT":
                material_path = f"material_slots[{index}].material"
                _add_candidate(
                    candidates,
                    _candidate_from_path(
                        obj,
                        material_path,
                        label=(
                            f"{_safe_target_name(obj)} — "
                            f"Material Slot {index + 1}"
                        ),
                        watch_ids=(obj, getattr(obj, "data", None)),
                    ),
                )

        try:
            data_id = obj.data
        except (AttributeError, ReferenceError):
            data_id = None
        if isinstance(
            data_id,
            (bpy.types.Camera, bpy.types.Light),
        ):
            data_ids.add(data_id)

    for data_id in data_ids:
        _add_generic_rna_properties(candidates, data_id)
        _add_custom_properties(candidates, data_id)
        try:
            dof_settings = data_id.dof
        except (AttributeError, ReferenceError):
            dof_settings = None
        if dof_settings is not None:
            _add_generic_rna_properties(candidates, dof_settings)
        _add_node_input_candidates(candidates, data_id)

    for material in materials:
        _add_generic_rna_properties(candidates, material)
        _add_custom_properties(candidates, material)
        _add_node_input_candidates(candidates, material)

    try:
        world = scene.world
    except (AttributeError, ReferenceError):
        world = None
    if world is not None:
        _add_generic_rna_properties(candidates, world)
        _add_custom_properties(candidates, world)
        _add_node_input_candidates(candidates, world)

    return candidates


def _layer_collection_topology_signature(scene):
    return _layer_collection_snapshot(scene).signature


def _custom_keys_signature(id_block):
    try:
        return tuple(
            sorted(str(key) for key in id_block.keys() if key != "_RNA_UI")
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ()


def _fcurve_collection_signature(fcurves):
    paths = []
    try:
        curves = tuple(fcurves)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        curves = ()
    for fcurve in curves:
        try:
            paths.append((fcurve.data_path, int(fcurve.array_index)))
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
    return tuple(paths)


def _action_structure_signature(action):
    if action is None:
        return ()
    result = [
        (
            "ACTION",
            _id_uid(action),
            _fcurve_collection_signature(
                getattr(action, "fcurves", ())
            ),
        )
    ]
    for layer in _collection_items(action, "layers"):
        layer_pointer = _safe_pointer(layer)
        for strip in _collection_items(layer, "strips"):
            strip_pointer = _safe_pointer(strip)
            for channelbag in _collection_items(strip, "channelbags"):
                try:
                    slot_handle = int(channelbag.slot_handle)
                except (
                    AttributeError,
                    ReferenceError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ):
                    slot_handle = 0
                result.append(
                    (
                        layer_pointer,
                        strip_pointer,
                        _safe_pointer(channelbag),
                        slot_handle,
                        _fcurve_collection_signature(
                            getattr(channelbag, "fcurves", ())
                        ),
                    )
                )
    return tuple(result)


def _animation_structure_signature(id_block):
    try:
        animation_data = id_block.animation_data
    except (AttributeError, ReferenceError, RuntimeError):
        animation_data = None
    if animation_data is None:
        return ()
    try:
        action = animation_data.action
    except (AttributeError, ReferenceError, RuntimeError):
        action = None
    result = [
        (
            "ACTIVE",
            _action_structure_signature(action),
            _binding_slot_handle(animation_data),
        ),
        (
            "DRIVERS",
            _fcurve_collection_signature(
                getattr(animation_data, "drivers", ())
            ),
        ),
    ]
    for track in _collection_items(animation_data, "nla_tracks"):
        for strip in _iter_nla_strips(
            getattr(track, "strips", ())
        ):
            try:
                action = strip.action
            except (AttributeError, ReferenceError, RuntimeError):
                action = None
            result.append(
                (
                    "NLA",
                    _safe_pointer(track),
                    _safe_pointer(strip),
                    _action_structure_signature(action),
                    _binding_slot_handle(strip),
                )
            )
    return tuple(result)


def _node_tree_structure_signature(node_tree):
    nodes_signature = []
    try:
        nodes = tuple(node_tree.nodes)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        nodes = ()
    for node in nodes:
        inputs_signature = []
        try:
            inputs = tuple(node.inputs)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            inputs = ()
        for socket in inputs:
            try:
                has_default = (
                    "default_value" in socket.bl_rna.properties
                )
                inputs_signature.append(
                    (
                        _safe_pointer(socket),
                        socket.name,
                        socket.bl_idname,
                        has_default,
                    )
                )
            except (
                AttributeError,
                ReferenceError,
                RuntimeError,
                TypeError,
            ):
                continue
        try:
            node_name = node.name
            node_type = node.bl_idname
        except (AttributeError, ReferenceError):
            continue
        nodes_signature.append(
            (
                _safe_pointer(node),
                node_name,
                node_type,
                tuple(inputs_signature),
            )
        )
    return tuple(nodes_signature)


def _object_is_in_scene(scene, obj):
    try:
        candidate = scene.objects.get(obj.name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False
    return _safe_pointer(candidate) == _safe_pointer(obj)


def _id_structure_signature(scene, id_block):
    """Return the discovery-relevant shape of one supported datablock."""

    if isinstance(id_block, bpy.types.Scene):
        try:
            return (
                "SCENE",
                len(id_block.objects),
                _id_uid(id_block.world),
                _id_uid(id_block.collection),
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return ("MISSING",)

    if isinstance(id_block, bpy.types.Object):
        modifiers = []
        for modifier in _collection_items(id_block, "modifiers"):
            try:
                modifiers.append(
                    (
                        _safe_pointer(modifier),
                        modifier.name,
                        modifier.type,
                    )
                )
            except (AttributeError, ReferenceError):
                continue
        slots = []
        for slot in _collection_items(id_block, "material_slots"):
            try:
                slots.append((slot.link, _id_uid(slot.material)))
            except (AttributeError, ReferenceError, RuntimeError):
                slots.append(("", 0))
        try:
            data_uid = _id_uid(id_block.data)
        except (AttributeError, ReferenceError):
            data_uid = 0
        return (
            "OBJECT",
            _object_is_in_scene(scene, id_block),
            data_uid,
            tuple(modifiers),
            tuple(slots),
            _custom_keys_signature(id_block),
            _animation_structure_signature(id_block),
        )

    if isinstance(id_block, bpy.types.NodeTree):
        return (
            "NODE_TREE",
            _node_tree_structure_signature(id_block),
            _animation_structure_signature(id_block),
        )

    if isinstance(id_block, bpy.types.Material):
        try:
            node_tree_uid = _id_uid(id_block.node_tree)
        except (AttributeError, ReferenceError):
            node_tree_uid = 0
        return (
            "MATERIAL",
            node_tree_uid,
            _custom_keys_signature(id_block),
            _animation_structure_signature(id_block),
        )

    if isinstance(
        id_block,
        (bpy.types.Camera, bpy.types.Light, bpy.types.World),
    ):
        try:
            node_tree_uid = _id_uid(id_block.node_tree)
        except (AttributeError, ReferenceError):
            node_tree_uid = 0
        return (
            type(id_block).__name__,
            node_tree_uid,
            _custom_keys_signature(id_block),
            _animation_structure_signature(id_block),
        )

    # Mesh-like object data can change which Materials discovery reaches.
    try:
        materials = tuple(
            _id_uid(material)
            for material in id_block.materials
        )
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        TypeError,
    ):
        materials = ()
    return (type(id_block).__name__, materials)


def _scene_structure_ids(scene):
    ids = {}

    def add(id_block):
        if not isinstance(id_block, bpy.types.ID):
            return
        uid = _id_uid(id_block)
        if uid:
            ids[uid] = id_block

    add(scene)
    try:
        objects = tuple(scene.objects)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        objects = ()
    for obj in objects:
        add(obj)
        try:
            add(obj.data)
        except (AttributeError, ReferenceError):
            pass
        for slot in _collection_items(obj, "material_slots"):
            try:
                material = slot.material
            except (AttributeError, ReferenceError, RuntimeError):
                material = None
            add(material)
            try:
                add(material.node_tree)
            except (AttributeError, ReferenceError):
                pass
        try:
            data_id = obj.data
            add(data_id.node_tree)
        except (AttributeError, ReferenceError):
            pass
    try:
        world = scene.world
    except (AttributeError, ReferenceError):
        world = None
    add(world)
    try:
        add(world.node_tree)
    except (AttributeError, ReferenceError):
        pass
    return ids


def _build_structure_state(scene):
    ids = _scene_structure_ids(scene)
    signatures = {
        uid: _id_structure_signature(scene, id_block)
        for uid, id_block in ids.items()
    }
    return signatures, ids


def _rebuild_runtime_indexes(scene, state, layer_snapshot):
    watch_index = {}
    direct_index = {}
    for key, tracked in state.properties.items():
        for uid in tracked.watch_uids:
            watch_index.setdefault(uid, []).append(key)
        if tracked.direct_key is not None:
            direct_index[tracked.direct_key] = key
    state.watch_index = {
        uid: tuple(keys)
        for uid, keys in watch_index.items()
    }
    state.direct_index = direct_index
    state.layer_collection_topology_signature = (
        layer_snapshot.signature
    )
    state.layer_collection_watch_uids = layer_snapshot.watch_uids
    (
        state.collection_hierarchy_guard,
        state.collection_structure_signatures,
    ) = _collection_hierarchy_state(scene)
    (
        state.structure_signatures,
        state.structure_ids,
    ) = _build_structure_state(scene)


def rebaseline_scene(scene):
    """Build trusted baseline/current snapshots and clear the recent action."""

    if scene is None or not hasattr(scene, "take_system"):
        return None
    _DEFERRED_UNTIL.pop(_scene_uid(scene), None)
    now = time.monotonic()
    layer_snapshot = _layer_collection_snapshot(
        scene,
        build_paths=True,
    )
    state = TrackerState(
        scene_uid=_scene_uid(scene),
        applied_take_uuid=scene.take_system.active_take_uuid,
        mutation_revision=engine.scene_mutation_revision(scene),
        global_mutation_revision=engine.global_mutation_revision(),
        properties=_discover_scene_properties(scene, layer_snapshot),
        recent_action=None,
        last_discovery_at=now,
    )
    _rebuild_runtime_indexes(scene, state, layer_snapshot)
    _TRACKERS[state.scene_uid] = state
    return state


def clear_scene(scene):
    scene_uid = _scene_uid(scene)
    _TRACKERS.pop(scene_uid, None)
    _DEFERRED_UNTIL.pop(scene_uid, None)


def clear_all():
    _TRACKERS.clear()
    _DEFERRED_UNTIL.clear()


def runtime_state_count():
    return len(_TRACKERS)


def prune_runtime_state(scenes=None):
    """Discard tracker/deferred state belonging to deleted scenes."""

    if scenes is None:
        try:
            scenes = tuple(bpy.data.scenes)
        except AttributeError:
            scenes = ()
    else:
        scenes = tuple(scenes)
    live_uids = {_scene_uid(scene) for scene in scenes}
    for scene_uid in tuple(_TRACKERS):
        if scene_uid not in live_uids:
            _TRACKERS.pop(scene_uid, None)
    for scene_uid in tuple(_DEFERRED_UNTIL):
        if scene_uid not in live_uids:
            _DEFERRED_UNTIL.pop(scene_uid, None)
    engine.prune_runtime_state(scenes)


def defer_scene(scene, seconds=0.2):
    """Pause observation around frame/evaluation-driven changes.

    Preserve the discovered property set. After evaluation settles, only its
    cached values are synchronized, avoiding full RNA discovery every frame.
    """

    scene_uid = _scene_uid(scene)
    state = _TRACKERS.get(scene_uid)
    if state is not None:
        state.recent_action = None
    _DEFERRED_UNTIL[scene_uid] = time.monotonic() + max(0.0, seconds)


def _scene_is_deferred(scene):
    scene_uid = _scene_uid(scene)
    deadline = _DEFERRED_UNTIL.get(scene_uid)
    if deadline is None:
        return False
    if time.monotonic() < deadline:
        return True
    _DEFERRED_UNTIL.pop(scene_uid, None)
    state = _TRACKERS.get(scene_uid)
    if state is not None:
        try:
            if (
                state.applied_take_uuid
                == scene.take_system.active_take_uuid
                and state.mutation_revision
                == engine.scene_mutation_revision(scene)
                and state.global_mutation_revision
                == engine.global_mutation_revision()
            ):
                _synchronize_current_values(scene, state)
            else:
                _TRACKERS.pop(scene_uid, None)
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            RuntimeError,
        ):
            _TRACKERS.pop(scene_uid, None)
    return False


def ensure_scene(scene):
    if _scene_is_deferred(scene):
        return None
    state, _was_rebaselined = _ensure_state(scene)
    return state


@contextmanager
def suspend_tracking():
    global _SUSPEND_DEPTH
    _SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _SUSPEND_DEPTH -= 1


def _ensure_state(scene):
    state = _TRACKERS.get(_scene_uid(scene))
    if state is None:
        return rebaseline_scene(scene), True
    current_take_uuid = scene.take_system.active_take_uuid
    current_revision = engine.scene_mutation_revision(scene)
    current_global_revision = engine.global_mutation_revision()
    if (
        state.applied_take_uuid != current_take_uuid
        or state.mutation_revision != current_revision
        or state.global_mutation_revision != current_global_revision
    ):
        return rebaseline_scene(scene), True
    return state, False


def _dirty_ids_from_depsgraph(depsgraph):
    if depsgraph is None:
        return None
    dirty = {}
    try:
        updates = tuple(depsgraph.updates)
    except (AttributeError, ReferenceError):
        return None
    for update in updates:
        id_block = None
        try:
            id_block = update.id
            original = id_block.original
        except (AttributeError, ReferenceError):
            original = None
        if id_block is None:
            continue
        source = original if original is not None else id_block
        uid = _id_uid(source)
        if uid:
            dirty[uid] = source
    return dirty


def _dirty_uids_from_depsgraph(depsgraph):
    dirty_ids = _dirty_ids_from_depsgraph(depsgraph)
    return None if dirty_ids is None else set(dirty_ids)


def _structure_changed(scene, state, dirty_ids):
    if not dirty_ids:
        return False
    unknown_uids = []
    for uid, id_block in dirty_ids.items():
        previous = state.structure_signatures.get(uid, _MISSING_VALUE)
        if previous is _MISSING_VALUE:
            unknown_uids.append(uid)
            continue
        if _id_structure_signature(scene, id_block) != previous:
            return True

    if not unknown_uids:
        return False
    # Unknown evaluated IDs are common and irrelevant. Only rebuild when an
    # unknown ID has become part of this scene's supported discovery graph.
    relevant_uids = _scene_structure_ids(scene)
    return any(uid in relevant_uids for uid in unknown_uids)


def _record_transition(state, tracked, after, now):
    action = state.recent_action
    new_action = (
        action is None
        or action.finalized
        or action.take_uuid != state.applied_take_uuid
        or now - action.updated_at > ACTION_GROUP_SECONDS
    )
    if new_action:
        action = RecentAction(
            scene_uid=state.scene_uid,
            take_uuid=state.applied_take_uuid,
            started_at=now,
            updated_at=now,
        )
        state.recent_action = action

    existing = action.changes.get(tracked.key)
    before = existing.before if existing is not None else tracked.current
    if after.key == before.key:
        action.changes.pop(tracked.key, None)
    else:
        action.changes[tracked.key] = RecentChange(
            key=tracked.key,
            target_id=tracked.target_id,
            data_path=tracked.data_path,
            label=tracked.label,
            baseline=tracked.baseline,
            before=before,
            after=after,
        )
    action.updated_at = now
    if not action.changes:
        state.recent_action = None


def _refresh_discovery(scene, state, now, layer_snapshot=None):
    if layer_snapshot is None or not all(
        entry.owner_path for entry in layer_snapshot.entries
    ):
        layer_snapshot = _layer_collection_snapshot(
            scene,
            build_paths=True,
        )
    previous_properties = state.properties
    previous_direct = {
        tracked.direct_key: (key, tracked)
        for key, tracked in previous_properties.items()
        if tracked.direct_key is not None
    }
    discovered = _discover_scene_properties(scene, layer_snapshot)
    preserved_keys = {}
    for key, candidate in discovered.items():
        previous_key = key
        previous = previous_properties.get(key)
        if (
            previous is None
            and candidate.direct_key is not None
            and candidate.direct_key in previous_direct
        ):
            previous_key, previous = previous_direct[candidate.direct_key]
        if (
            previous is not None
            and previous.source_uid == candidate.source_uid
        ):
            candidate.baseline = previous.baseline
            candidate.current = previous.current
            preserved_keys[previous_key] = key

    action = state.recent_action
    if action is not None:
        reconciled_changes = {}
        for previous_key, change in tuple(action.changes.items()):
            key = preserved_keys.get(previous_key)
            candidate = discovered.get(key) if key is not None else None
            if candidate is None:
                continue
            change.key = key
            change.target_id = candidate.target_id
            change.data_path = candidate.data_path
            change.label = candidate.label
            reconciled_changes[key] = change
        action.changes = reconciled_changes
        if not reconciled_changes:
            state.recent_action = None

    state.properties = discovered
    state.last_discovery_at = now
    _rebuild_runtime_indexes(scene, state, layer_snapshot)
    return layer_snapshot


def observe_scene(scene, depsgraph=None, *, now=None, force_all=False):
    """Compare current supported values against cached state."""

    if (
        _SUSPEND_DEPTH
        or engine.is_applying()
        or scene is None
        or not hasattr(scene, "take_system")
        or _scene_is_deferred(scene)
    ):
        return None
    state, was_rebaselined = _ensure_state(scene)
    if state is None or was_rebaselined:
        return None
    if now is None:
        now = time.monotonic()
    dirty_ids = (
        None
        if force_all
        else _dirty_ids_from_depsgraph(depsgraph)
    )
    empty_dirty_event = dirty_ids == {}
    dirty_uids = (
        None
        if dirty_ids is None or empty_dirty_event
        else set(dirty_ids)
    )

    layer_snapshot = None
    if _topology_change_suspected(
        scene,
        state,
        dirty_ids,
        # Blender can emit an empty update list when an excluded Collection is
        # renamed. Verify the cheap hierarchy signatures in that case so the
        # cached name-based path is rebuilt before its next checkbox change.
        force_verify=not dirty_ids,
    ):
        layer_snapshot = _layer_collection_snapshot(scene)
        if (
            layer_snapshot.signature
            != state.layer_collection_topology_signature
        ):
            layer_snapshot = _refresh_discovery(
                scene,
                state,
                now,
                layer_snapshot,
            )

    if (
        dirty_ids is not None
        and _structure_changed(scene, state, dirty_ids)
    ):
        layer_snapshot = _refresh_discovery(
            scene,
            state,
            now,
            layer_snapshot,
        )
    elif empty_dirty_event:
        # Excluded datablocks can produce a dependency-graph callback with no
        # update IDs. Treat it as an unknown-source event: verify discovery
        # structure and scan the existing indexed values once. Empty callbacks
        # are event-driven and rare, unlike the removed periodic broad scan.
        current_structure_signatures, _current_structure_ids = (
            _build_structure_state(scene)
        )
        if current_structure_signatures != state.structure_signatures:
            layer_snapshot = _refresh_discovery(
                scene,
                state,
                now,
                layer_snapshot,
            )

    if dirty_uids is None:
        tracked_keys = tuple(state.properties)
    else:
        selected = {}
        for uid in dirty_uids:
            for key in state.watch_index.get(uid, ()):
                selected.setdefault(key, None)
        tracked_keys = tuple(selected)

    direct_values = (
        layer_snapshot.values
        if layer_snapshot is not None
        else None
    )
    for key in tracked_keys:
        tracked = state.properties.get(key)
        if tracked is None:
            continue
        try:
            after = _freeze_tracked_value(
                tracked,
                direct_values,
            )
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            RuntimeError,
            SystemError,
            TypeError,
            ValueError,
        ):
            after = _missing_value()
        if after.key != tracked.current.key:
            _record_transition(state, tracked, after, now)
            tracked.current = after

    return state.recent_action


def finalize_pending(scene, *, now=None):
    action = observe_scene(scene, now=now, force_all=True)
    state = _TRACKERS.get(_scene_uid(scene))
    if state is None:
        return None
    action = state.recent_action if action is None else action
    if action is not None:
        action.finalized = True
    return action


def peek_recent_action(scene):
    state = _TRACKERS.get(_scene_uid(scene))
    return state.recent_action if state is not None else None


def action_summary(scene):
    action = peek_recent_action(scene)
    if action is None or not action.changes:
        return "No supported recent action detected"
    return action.summary


def _synchronize_current_values(scene, state):
    layer_snapshot = None
    if _topology_change_suspected(
        scene,
        state,
        None,
        force_verify=True,
    ):
        layer_snapshot = _layer_collection_snapshot(scene)
        if (
            layer_snapshot.signature
            != state.layer_collection_topology_signature
        ):
            layer_snapshot = _refresh_discovery(
                scene,
                state,
                time.monotonic(),
                layer_snapshot,
            )
    current_structure_signatures, _current_structure_ids = (
        _build_structure_state(scene)
    )
    if current_structure_signatures != state.structure_signatures:
        layer_snapshot = _refresh_discovery(
            scene,
            state,
            time.monotonic(),
            layer_snapshot,
        )
    direct_values = (
        layer_snapshot.values
        if layer_snapshot is not None
        else None
    )
    for tracked in tuple(state.properties.values()):
        try:
            tracked.current = _freeze_tracked_value(
                tracked,
                direct_values,
            )
        except (
            engine.TakeSystemError,
            AttributeError,
            ReferenceError,
            RuntimeError,
            SystemError,
            TypeError,
            ValueError,
        ):
            tracked.current = _missing_value()
    state.applied_take_uuid = scene.take_system.active_take_uuid
    state.mutation_revision = engine.scene_mutation_revision(scene)
    state.global_mutation_revision = engine.global_mutation_revision()
    state.recent_action = None


def capture_pending(scene, take_uuid=None):
    """Capture and consume the most recent supported action atomically."""

    action = finalize_pending(scene)
    if action is None or not action.changes:
        raise engine.TakeSystemError(
            "No supported recent property changes were found"
        )
    requested = take_uuid or scene.take_system.active_take_uuid
    if action.take_uuid != requested:
        raise engine.TakeHierarchyError(
            "The recent action belongs to a different applied take"
        )

    changes = []
    for change in action.changes.values():
        if change.after.is_missing:
            raise engine.MissingReferenceError(
                f"Recent-action target or path is missing: {change.label}"
            )
        if change.baseline.is_missing:
            raise engine.MissingReferenceError(
                f"Recent-action baseline is missing: {change.label}"
            )
        changes.append(
            engine.OverrideChange(
                target_id=change.target_id,
                data_path=change.data_path,
                baseline_value=change.baseline.value,
                after_value=change.after.value,
            )
        )

    state = _TRACKERS.get(_scene_uid(scene))
    with suspend_tracking():
        report = engine.capture_change_batch(
            scene,
            changes,
            take_uuid=requested,
        )
        if state is not None:
            _refresh_discovery(scene, state, time.monotonic())
            _synchronize_current_values(scene, state)
    return report


def handle_depsgraph_update(scene, depsgraph):
    try:
        observe_scene(scene, depsgraph)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        RuntimeError,
    ):
        # Tracking is advisory. It must never break Blender's dependency graph.
        clear_scene(scene)


def handle_internal_state_change(scene):
    """Clear pending observations after undo/redo/frame/apply evaluation."""

    try:
        rebaseline_scene(scene)
    except (
        engine.TakeSystemError,
        AttributeError,
        ReferenceError,
        RuntimeError,
    ):
        clear_scene(scene)
