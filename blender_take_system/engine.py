"""Core hierarchy, capture, resolve, and apply engine.

The module intentionally has no UI dependencies. Operators and future panels call
these functions, and the same API can be exercised from Blender background tests.
"""

import ast
import numbers
import os
import re
import struct
import uuid as _uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import bpy


MAIN_NAME = "Main"
SCHEMA_VERSION = 2

# Render-profile paths are stored through the same generic Scene override
# engine as every other take value. Groups let the UI keep unrelated settings
# inherited instead of freezing the entire preset on every take.
RENDER_GROUP_ENGINE_SAMPLING = "ENGINE_SAMPLING"
RENDER_GROUP_RESOLUTION = "RESOLUTION"
RENDER_GROUP_OUTPUT = "OUTPUT"
RENDER_GROUP_TRANSPARENCY = "TRANSPARENCY"
RENDER_GROUP_COLOR_MANAGEMENT = "COLOR_MANAGEMENT"
RENDER_PROFILE_GROUPS = (
    RENDER_GROUP_ENGINE_SAMPLING,
    RENDER_GROUP_RESOLUTION,
    RENDER_GROUP_OUTPUT,
    RENDER_GROUP_TRANSPARENCY,
    RENDER_GROUP_COLOR_MANAGEMENT,
)
RENDER_PROFILE_GROUP_LABELS = {
    RENDER_GROUP_ENGINE_SAMPLING: "Engine & Sampling",
    RENDER_GROUP_RESOLUTION: "Resolution & Frame",
    RENDER_GROUP_OUTPUT: "Output & Format",
    RENDER_GROUP_TRANSPARENCY: "Film Transparency",
    RENDER_GROUP_COLOR_MANAGEMENT: "Color Management",
}

_RENDER_SETTING_GROUP_PATHS = {
    RENDER_GROUP_ENGINE_SAMPLING: (
        "render.engine",
    ),
    RENDER_GROUP_RESOLUTION: (
        "render.resolution_x",
        "render.resolution_y",
        "render.resolution_percentage",
        "render.pixel_aspect_x",
        "render.pixel_aspect_y",
        "render.fps",
        "render.fps_base",
    ),
    RENDER_GROUP_OUTPUT: (
        "render.filepath",
        "render.use_file_extension",
        "render.use_overwrite",
        "render.use_placeholder",
        "render.image_settings.file_format",
        "render.image_settings.color_mode",
        "render.image_settings.color_depth",
        "render.image_settings.compression",
        "render.image_settings.quality",
    ),
    RENDER_GROUP_TRANSPARENCY: (
        "render.film_transparent",
    ),
    RENDER_GROUP_COLOR_MANAGEMENT: (
        "view_settings.view_transform",
        "view_settings.look",
        "view_settings.exposure",
        "view_settings.gamma",
    ),
}

_CORE_RENDER_SETTING_PATHS = (
    "render.engine",
    "render.resolution_x",
    "render.resolution_y",
    "render.resolution_percentage",
    "render.pixel_aspect_x",
    "render.pixel_aspect_y",
    "render.fps",
    "render.fps_base",
    "render.filepath",
    "render.use_file_extension",
    "render.use_overwrite",
    "render.use_placeholder",
    "render.image_settings.file_format",
    "render.image_settings.color_mode",
    "render.image_settings.color_depth",
    "render.image_settings.compression",
    "render.image_settings.quality",
    "render.film_transparent",
    "view_settings.view_transform",
    "view_settings.look",
    "view_settings.exposure",
    "view_settings.gamma",
)
_ENGINE_RENDER_SETTING_PATHS = {
    "CYCLES": (
        "cycles.samples",
        "cycles.use_denoising",
        "cycles.use_adaptive_sampling",
        "cycles.adaptive_min_samples",
        "cycles.adaptive_threshold",
    ),
    "BLENDER_EEVEE": (
        "eevee.taa_render_samples",
    ),
    "BLENDER_EEVEE_NEXT": (
        "eevee.taa_render_samples",
    ),
    "BLENDER_WORKBENCH": (
        "display.shading.light",
        "display.shading.color_type",
        "display.shading.show_shadows",
        "display.shading.show_cavity",
        "display.shading.show_specular_highlight",
    ),
}
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_APPLY_DEPTH = 0
_SCENE_MUTATION_REVISIONS = {}
_GLOBAL_MUTATION_REVISION = 0


class TakeSystemError(RuntimeError):
    """Base class for user-facing Take System errors."""


class TakeHierarchyError(TakeSystemError):
    """The take tree is missing a parent, has a cycle, or is not Main-rooted."""


class TakePathError(TakeSystemError):
    """An RNA path cannot be read or assigned."""


class UnsupportedValueError(TakeSystemError):
    """The property value cannot be represented by the Phase 1/2 model."""


class MissingReferenceError(TakeSystemError):
    """A stored target or pointer-value datablock no longer exists."""


class TakeApplyError(TakeSystemError):
    """Strict application completed with one or more skipped overrides."""

    def __init__(self, report):
        self.report = report
        super().__init__(
            f"Applied {report.applied} override(s); "
            f"{report.skipped} override(s) failed"
        )


class BatchRenderError(TakeSystemError):
    """A queued take could not render or the live scene could not be restored."""

    def __init__(self, report):
        self.report = report
        if report.restoration_issues:
            message = (
                "Batch rendering stopped and one or more live scene values "
                "could not be restored"
            )
        elif report.failed_take_name:
            message = f"Batch rendering stopped at '{report.failed_take_name}'"
        else:
            message = "Batch rendering could not start"
        if report.error:
            message = f"{message}: {report.error}"
        super().__init__(message)


@dataclass(frozen=True)
class FinalPathToken:
    parent_path: str
    kind: str
    token: object


@dataclass
class CaptureResult:
    override: object
    created: bool
    main_seeded: bool


@dataclass(frozen=True)
class OverrideChange:
    """One observed property transition ready to become a take override."""

    target_id: object
    data_path: str
    baseline_value: object
    after_value: object


@dataclass(frozen=True)
class ExplicitOverrideValue:
    """One caller-supplied value to store without first writing it live."""

    target_id: object
    data_path: str
    value: object


@dataclass
class CaptureBatchReport:
    take_uuid: str
    take_name: str
    captured: int = 0
    created: int = 0
    main_seeded: int = 0
    paths: list = field(default_factory=list)


@dataclass
class RenderProfileReport:
    take_uuid: str
    take_name: str
    configured: int = 0
    created: int = 0
    main_seeded: int = 0
    removed: int = 0
    groups: tuple = ()
    paths: list = field(default_factory=list)


@dataclass
class ResolvedEntry:
    take_uuid: str
    take_name: str
    override: object


@dataclass
class ApplyIssue:
    take_uuid: str
    take_name: str
    target: str
    data_path: str
    message: str

    def summary(self):
        return f"{self.take_name}: {self.target}.{self.data_path}: {self.message}"


@dataclass
class ApplyReport:
    take_uuid: str
    take_name: str
    applied: int = 0
    skipped: int = 0
    issues: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.issues


@dataclass(frozen=True)
class BatchQueueEntry:
    take_uuid: str
    take_name: str
    explicit_output_path: str


@dataclass(frozen=True)
class BatchRenderItem:
    take_uuid: str
    take_name: str
    output_path: str
    applied_overrides: int


@dataclass
class BatchRenderReport:
    queued: int = 0
    rendered: list = field(default_factory=list)
    failed_take_uuid: str = ""
    failed_take_name: str = ""
    error: str = ""
    restored: bool = False
    restoration_issues: list = field(default_factory=list)

    @property
    def ok(self):
        return (
            not self.error
            and not self.restoration_issues
            and len(self.rendered) == self.queued
        )


@dataclass
class RuntimeWriteSnapshot:
    """One concrete RNA write location and its detached previous value."""

    parent: object
    kind: str
    token: object
    value: object
    target_name: str
    data_path: str


@dataclass(frozen=True)
class TakeHierarchyRow:
    take: object
    collection_index: int
    depth: int
    issue: str = ""


def new_uuid():
    return _uuid.uuid4().hex


def _scene_runtime_key(scene):
    try:
        session_uid = int(scene.session_uid)
    except (AttributeError, ReferenceError, TypeError, ValueError):
        session_uid = 0
    if session_uid:
        return session_uid
    try:
        return scene.as_pointer()
    except (AttributeError, ReferenceError):
        return id(scene)


def scene_mutation_revision(scene):
    """Runtime counter incremented after Take System writes live scene values."""

    return _SCENE_MUTATION_REVISIONS.get(_scene_runtime_key(scene), 0)


def global_mutation_revision():
    """Runtime counter incremented after any Take System live-value write."""

    return _GLOBAL_MUTATION_REVISION


def _mark_scene_mutated(scene):
    global _GLOBAL_MUTATION_REVISION
    key = _scene_runtime_key(scene)
    _SCENE_MUTATION_REVISIONS[key] = _SCENE_MUTATION_REVISIONS.get(key, 0) + 1
    # A take can target a Material, World, Object, or another ID shared by
    # multiple scenes. Invalidate every scene tracker after a live write so a
    # sibling scene cannot misinterpret that shared-ID change as a user action.
    _GLOBAL_MUTATION_REVISION += 1


def prune_runtime_state(scenes=None):
    """Discard per-scene revision entries for scenes that no longer exist."""

    if scenes is None:
        try:
            scenes = tuple(bpy.data.scenes)
        except AttributeError:
            scenes = ()
    live_keys = {_scene_runtime_key(scene) for scene in scenes}
    for key in tuple(_SCENE_MUTATION_REVISIONS):
        if key not in live_keys:
            _SCENE_MUTATION_REVISIONS.pop(key, None)


def clear_runtime_state():
    global _GLOBAL_MUTATION_REVISION
    _SCENE_MUTATION_REVISIONS.clear()
    _GLOBAL_MUTATION_REVISION = 0


def _safe_id_pointer(id_block):
    if id_block is None:
        return 0
    try:
        return id_block.as_pointer()
    except (ReferenceError, TypeError):
        return 0


def _id_library_path(id_block):
    try:
        library = id_block.library
    except (AttributeError, ReferenceError):
        return ""
    return library.filepath if library else ""


def _id_type_name(id_block):
    try:
        return id_block.bl_rna.identifier
    except (AttributeError, ReferenceError):
        return ""


def _id_display_name(id_block):
    try:
        return id_block.name_full
    except (AttributeError, ReferenceError):
        return "<missing datablock>"


def _safe_struct_pointer(value):
    if value is None:
        return 0
    try:
        return value.as_pointer()
    except (AttributeError, ReferenceError, TypeError):
        return 0


def _rna_collection_key(name):
    """Quote a collection key for use inside an RNA data path."""

    return f'"{bpy.utils.escape_identifier(str(name))}"'


def iter_layer_collection_paths(scene, *, include_root=False):
    """Yield ``(view_layer, layer_collection, owner_path)`` tuples.

    ``LayerCollection.path_from_id()`` is unsupported by Blender even though
    its ``id_data`` is the owning Scene. Build the Scene-relative path by
    walking each ViewLayer tree instead.
    """

    if not isinstance(scene, bpy.types.Scene):
        return
    try:
        view_layers = tuple(scene.view_layers)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return

    for view_layer in view_layers:
        try:
            root = view_layer.layer_collection
            root_path = view_layer.path_from_id("layer_collection")
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            continue
        if include_root:
            yield view_layer, root, root_path

        try:
            children = tuple(root.children)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            children = ()
        pending = [
            (
                child,
                f"{root_path}.children[{_rna_collection_key(child.name)}]",
            )
            for child in reversed(children)
        ]
        while pending:
            layer_collection, owner_path = pending.pop()
            yield view_layer, layer_collection, owner_path
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
                pending.append(
                    (
                        child,
                        (
                            f"{owner_path}.children["
                            f"{_rna_collection_key(child.name)}]"
                        ),
                    )
                )


def layer_collection_data_path(
    scene,
    layer_collection,
    property_name="exclude",
):
    """Return a writable Scene-relative path for a LayerCollection property."""

    if not isinstance(scene, bpy.types.Scene):
        raise TakePathError(
            "Layer collection overrides require an owning Scene"
        )
    if not isinstance(layer_collection, bpy.types.LayerCollection):
        raise TakePathError("The target is not a Layer Collection")
    property_name = (property_name or "").strip()
    if not property_name.isidentifier():
        raise TakePathError(
            f"Invalid Layer Collection property: {property_name!r}"
        )

    target_pointer = _safe_struct_pointer(layer_collection)
    if not target_pointer:
        raise MissingReferenceError("The Layer Collection no longer exists")
    for view_layer, candidate, owner_path in iter_layer_collection_paths(
        scene,
        include_root=True,
    ):
        if _safe_struct_pointer(candidate) != target_pointer:
            continue
        try:
            is_root = candidate == view_layer.layer_collection
        except (AttributeError, ReferenceError):
            is_root = owner_path.endswith(".layer_collection")
        if is_root:
            raise UnsupportedValueError(
                "The View Layer root collection cannot be disabled"
            )
        return f"{owner_path}.{property_name}"

    raise MissingReferenceError(
        "The Layer Collection is not part of an owning Scene View Layer"
    )


def is_layer_collection_exclude_path(target_id, data_path):
    """Return whether a live Scene path addresses LayerCollection.exclude."""

    if (
        not isinstance(target_id, bpy.types.Scene)
        or not (data_path or "").endswith(".exclude")
        or ".layer_collection" not in data_path
    ):
        return False
    try:
        final_token = split_final_path(data_path)
        parent = _resolve_parent(target_id, final_token)
    except TakeSystemError:
        return False
    return (
        final_token.kind == "ATTR"
        and final_token.token == "exclude"
        and isinstance(parent, bpy.types.LayerCollection)
    )


def canonicalize_id_path(target_id, data_path):
    """Anchor embedded datablocks at their storable owning ID.

    Blender does not permit a PointerProperty to retain an embedded ID such as a
    Material node tree. Node/socket paths are therefore prefixed with
    ``node_tree.`` and stored against the owning Material/World/Scene/Light.
    """

    try:
        original = target_id.original
    except (AttributeError, ReferenceError):
        original = None
    if isinstance(original, bpy.types.ID) and original != target_id:
        target_id = original

    if isinstance(target_id, bpy.types.Object) and data_path == "active_material":
        slot_index = target_id.active_material_index
        if slot_index < 0 or slot_index >= len(target_id.material_slots):
            raise TakePathError("Object has no active material slot to override")
        data_path = f"material_slots[{slot_index}].material"

    if not getattr(target_id, "is_embedded_data", False):
        return target_id, data_path

    owner_locations = (
        ("materials", "node_tree"),
        ("worlds", "node_tree"),
        ("lights", "node_tree"),
        ("scenes", "node_tree"),
        ("textures", "node_tree"),
        ("linestyles", "node_tree"),
        ("scenes", "collection"),
    )
    for collection_name, owner_attribute in owner_locations:
        collection = getattr(bpy.data, collection_name, ())
        for candidate in collection:
            try:
                embedded = getattr(candidate, owner_attribute, None)
            except (AttributeError, ReferenceError):
                continue
            if embedded == target_id:
                prefixed_path = (
                    f"{owner_attribute}.{data_path}"
                    if data_path
                    else owner_attribute
                )
                return candidate, prefixed_path

    raise UnsupportedValueError(
        f"Embedded {_id_type_name(target_id)} '{_id_display_name(target_id)}' "
        "has no supported owning datablock"
    )


def is_take_system_internal_path(target_id, data_path):
    """Return whether a Scene path points into this add-on's own state."""

    if not isinstance(target_id, bpy.types.Scene):
        return False
    normalized = (data_path or "").strip()
    return (
        normalized == "take_system"
        or normalized.startswith("take_system.")
        or normalized.startswith("take_system[")
    )


def _sync_active_index(state):
    for index, take in enumerate(state.takes):
        if take.uuid == state.active_take_uuid:
            state.active_take_index = index
            return
    state.active_take_index = 0


def _sync_selected_index(state, take_uuid):
    for index, take in enumerate(state.takes):
        if take.uuid == take_uuid:
            state.active_take_index = index
            return
    _sync_active_index(state)


def ensure_main_take(scene):
    """Create or repair the scene's one Main root and return it."""

    state = scene.take_system
    migrate_camera_metadata = state.schema_version < SCHEMA_VERSION
    seen_uuids = set()
    for take in state.takes:
        if not take.uuid or take.uuid in seen_uuids:
            take.uuid = new_uuid()
        seen_uuids.add(take.uuid)

    stored_main = next(
        (
            take
            for take in state.takes
            if state.main_take_uuid and take.uuid == state.main_take_uuid
        ),
        None,
    )
    if stored_main is not None:
        stored_main.is_main = True
    mains = [take for take in state.takes if take.is_main]
    if stored_main is not None:
        mains = [stored_main] + [
            take for take in mains if take.uuid != stored_main.uuid
        ]
    if not mains:
        named_root = next(
            (
                take
                for take in state.takes
                if take.name == MAIN_NAME and not take.parent_uuid
            ),
            None,
        )
        if named_root is None:
            named_root = state.takes.add()
            named_root.uuid = new_uuid()
            named_root.name = MAIN_NAME
        named_root.is_main = True
        mains = [named_root]

    main = mains[0]
    if not main.uuid:
        main.uuid = new_uuid()
    main.name = MAIN_NAME
    main.parent_uuid = ""
    main.is_main = True
    # Schema 2 adds Phase 5 per-take batch-output metadata. The new RNA field
    # has a safe empty default, so older files need no destructive rewrite.
    # Preserve a future schema number rather than silently downgrading it.
    state.schema_version = max(SCHEMA_VERSION, state.schema_version)
    state.main_take_uuid = main.uuid

    for extra_main in mains[1:]:
        extra_main.is_main = False
        if not extra_main.parent_uuid:
            extra_main.parent_uuid = main.uuid

    for take in state.takes:
        if take.uuid != main.uuid and not take.parent_uuid:
            take.parent_uuid = main.uuid

    active_was_repaired = not find_take(scene, state.active_take_uuid)
    if active_was_repaired:
        state.active_take_uuid = main.uuid
    if active_was_repaired or not (
        0 <= state.active_take_index < len(state.takes)
    ):
        _sync_active_index(state)
    if migrate_camera_metadata:
        for take in state.takes:
            _sync_camera_metadata(scene, take)
    return main


def find_take(scene, take_uuid):
    if not take_uuid:
        return None
    return next(
        (take for take in scene.take_system.takes if take.uuid == take_uuid),
        None,
    )


def active_take(scene):
    main = ensure_main_take(scene)
    return find_take(scene, scene.take_system.active_take_uuid) or main


def _unique_take_name(state, requested):
    base = (requested or "Take").strip() or "Take"
    used = {take.name for take in state.takes}
    if base not in used:
        return base
    suffix = 1
    while f"{base}.{suffix:03d}" in used:
        suffix += 1
    return f"{base}.{suffix:03d}"


def create_take(scene, name="Take", parent_uuid=None, make_active=True):
    """Create a Main-rooted take using stable UUID parent linkage."""

    main = ensure_main_take(scene)
    state = scene.take_system
    if parent_uuid is None:
        parent_uuid = state.active_take_uuid or main.uuid
    parent = find_take(scene, parent_uuid)
    if parent is None:
        raise TakeHierarchyError(f"Parent take does not exist: {parent_uuid}")
    validated_parent_uuid = parent.uuid
    # ``make_active=False`` is used by scripting and duplication paths that do
    # not apply the parent first. Validate explicitly so those callers cannot
    # extend an orphaned or cyclic branch and violate the Main-rooted contract.
    take_chain(scene, validated_parent_uuid)
    if make_active:
        # Active identity and live scene state must move together. Applying the
        # parent before creation makes the empty child start at its inherited
        # state and prevents cross-branch values from leaking into first capture.
        apply_take(scene, validated_parent_uuid, strict=True)

    # CollectionProperty growth may invalidate Python wrappers for existing
    # items, so never read ``parent`` after adding the child.
    take = state.takes.add()
    take.uuid = new_uuid()
    take.name = _unique_take_name(state, name)
    take.parent_uuid = validated_parent_uuid
    take.is_main = False
    take.include_in_render = True
    if make_active:
        state.active_take_uuid = take.uuid
        _sync_active_index(state)
    return take


def selected_take(scene):
    """Return the UI-selected take without changing the applied take."""

    state = scene.take_system
    if 0 <= state.active_take_index < len(state.takes):
        return state.takes[state.active_take_index]
    return find_take(scene, state.active_take_uuid) or (
        state.takes[0] if state.takes else None
    )


def take_hierarchy_rows(scene):
    """Return a cycle-safe, depth-first presentation of the stored take tree."""

    state = scene.take_system
    indexed_takes = list(enumerate(state.takes))
    by_uuid = {
        take.uuid: (index, take)
        for index, take in indexed_takes
        if take.uuid
    }
    main = find_take(scene, state.main_take_uuid)
    children = {}
    for index, take in indexed_takes:
        children.setdefault(take.parent_uuid, []).append((index, take))

    def lineage_issue(take):
        if take.is_main:
            if main is not None and take.uuid != main.uuid:
                return "Duplicate Main flag"
            return "" if not take.parent_uuid else "Main has a parent"
        visited = set()
        cursor = take
        while cursor is not None and not cursor.is_main:
            if not cursor.uuid:
                return "Missing take UUID"
            if cursor.uuid in visited:
                return "Parent cycle detected"
            visited.add(cursor.uuid)
            if not cursor.parent_uuid:
                return "Not connected to Main"
            parent_entry = by_uuid.get(cursor.parent_uuid)
            if parent_entry is None:
                return "Missing parent"
            cursor = parent_entry[1]
        if main is None or cursor is None or cursor.uuid != main.uuid:
            return "Not rooted at Main"
        return ""

    issues = {
        take.uuid: lineage_issue(take)
        for _index, take in indexed_takes
    }
    rows = []
    emitted = set()

    def visit(take, index, depth):
        if take.uuid in emitted:
            return
        emitted.add(take.uuid)
        rows.append(
            TakeHierarchyRow(
                take=take,
                collection_index=index,
                depth=depth,
                issue=issues.get(take.uuid, ""),
            )
        )
        for child_index, child in children.get(take.uuid, ()):
            if issues.get(child.uuid):
                continue
            visit(child, child_index, depth + 1)

    if main is not None:
        main_index = next(
            (
                index
                for index, take in indexed_takes
                if take.uuid == main.uuid
            ),
            0,
        )
        visit(main, main_index, 0)

    for index, take in indexed_takes:
        if take.uuid not in emitted:
            visit(take, index, 0)
    return rows


def take_descendant_uuids(scene, take_uuid):
    descendants = set()
    frontier = [take_uuid]
    while frontier:
        parent_uuid = frontier.pop()
        for take in scene.take_system.takes:
            if (
                take.parent_uuid == parent_uuid
                and take.uuid not in descendants
            ):
                descendants.add(take.uuid)
                frontier.append(take.uuid)
    descendants.discard(take_uuid)
    return descendants


_OVERRIDE_COPY_FIELDS = (
    "target_ref_uuid",
    "target_id",
    "target_id_type",
    "target_id_name",
    "target_library_path",
    "target_reference_set",
    "data_path",
    "prop_type",
    "rna_subtype",
    "value_float",
    "value_float_text",
    "value_int",
    "value_bool",
    "value_string",
    "value_vector",
    "value_int_vector",
    "value_bool_vector",
    "value_array_text",
    "value_color",
    "array_length",
    "array_component_type",
    "value_pointer",
    "pointer_is_none",
    "pointer_id_type",
    "pointer_id_name",
    "pointer_library_path",
)
_OVERRIDE_ARRAY_FIELDS = {
    "value_vector",
    "value_int_vector",
    "value_bool_vector",
    "value_color",
}
_OVERRIDE_POINTER_FIELDS = {"target_id", "value_pointer"}


def _snapshot_override_record(override):
    snapshot = {"uuid": override.uuid}
    for field_name in _OVERRIDE_COPY_FIELDS:
        try:
            value = getattr(override, field_name)
        except ReferenceError:
            value = None
        if field_name in _OVERRIDE_ARRAY_FIELDS:
            value = tuple(value)
        snapshot[field_name] = value
    return snapshot


def _append_override_snapshot(take, snapshot, fresh_uuid):
    override = take.overrides.add()
    override.uuid = new_uuid() if fresh_uuid else snapshot["uuid"]
    for field_name in _OVERRIDE_COPY_FIELDS:
        value = snapshot[field_name]
        if field_name in _OVERRIDE_POINTER_FIELDS and value is None:
            continue
        setattr(override, field_name, value)
    return override


def duplicate_take(scene, take_uuid, make_active=True):
    """Duplicate one take's local records as a sibling with fresh identities."""

    source = find_take(scene, take_uuid)
    if source is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    if source.is_main:
        raise TakeHierarchyError("Main cannot be duplicated")

    state = scene.take_system
    # Snapshot every source value before growing the Take CollectionProperty;
    # Blender may reallocate it and invalidate the previous item wrapper.
    source_name = source.name
    source_parent_uuid = source.parent_uuid
    source_include_in_render = source.include_in_render
    source_render_output_path = source.render_output_path
    source_use_camera_override = source.use_camera_override
    try:
        source_camera_override = source.camera_override
    except ReferenceError:
        source_camera_override = None
    source_override_snapshots = tuple(
        _snapshot_override_record(source_override)
        for source_override in source.overrides
    )
    previous_active_uuid = state.active_take_uuid
    previous_selected = selected_take(scene)
    previous_selected_uuid = (
        previous_selected.uuid if previous_selected is not None else ""
    )
    duplicate = None
    duplicate_uuid = ""
    try:
        duplicate = create_take(
            scene,
            name=f"{source_name} Copy",
            parent_uuid=source_parent_uuid,
            make_active=False,
        )
        duplicate_uuid = duplicate.uuid
        duplicate.is_recording = False
        duplicate.include_in_render = source_include_in_render
        duplicate.render_output_path = source_render_output_path
        duplicate.use_camera_override = source_use_camera_override
        if source_camera_override is not None:
            duplicate.camera_override = source_camera_override
        for source_override_snapshot in source_override_snapshots:
            _append_override_snapshot(
                duplicate,
                source_override_snapshot,
                fresh_uuid=True,
            )

        if make_active:
            apply_take(scene, duplicate.uuid, strict=True)
        else:
            _sync_selected_index(state, duplicate.uuid)
    except Exception:
        if duplicate_uuid:
            duplicate_index = next(
                (
                    index
                    for index, take in enumerate(state.takes)
                    if take.uuid == duplicate_uuid
                ),
                None,
            )
            if duplicate_index is not None:
                state.takes.remove(duplicate_index)
        state.active_take_uuid = previous_active_uuid
        _sync_selected_index(
            state,
            previous_selected_uuid or previous_active_uuid,
        )
        raise
    return duplicate


def reparent_take(scene, take_uuid, parent_uuid):
    """Move one take under a new parent while preserving an acyclic tree."""

    take = find_take(scene, take_uuid)
    parent = find_take(scene, parent_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    if parent is None:
        raise TakeHierarchyError(f"Parent take does not exist: {parent_uuid}")
    if take.is_main:
        raise TakeHierarchyError("Main cannot be reparented")
    if take.uuid == parent.uuid:
        raise TakeHierarchyError("A take cannot be its own parent")
    if parent.uuid in take_descendant_uuids(scene, take.uuid):
        raise TakeHierarchyError("A take cannot be parented to its descendant")
    if take.parent_uuid == parent.uuid:
        return take

    state = scene.take_system
    selected = selected_take(scene)
    selected_uuid = selected.uuid if selected is not None else ""
    active_uuid = state.active_take_uuid
    active_chain_uuids = {
        member.uuid for member in take_chain(scene, active_uuid)
    }
    affects_active = take.uuid in active_chain_uuids
    previous_parent_uuid = take.parent_uuid
    take.parent_uuid = parent.uuid
    try:
        take_chain(scene, take.uuid)
        if affects_active:
            apply_take(scene, active_uuid, strict=True)
    except TakeSystemError:
        take.parent_uuid = previous_parent_uuid
        _sync_selected_index(state, selected_uuid)
        raise
    _sync_selected_index(state, selected_uuid or take.uuid)
    return take


def delete_take(scene, take_uuid):
    """Delete one take, adopting its direct children into its parent."""

    take = find_take(scene, take_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    if take.is_main:
        raise TakeHierarchyError("Main cannot be deleted")

    state = scene.take_system
    parent = find_take(scene, take.parent_uuid)
    if parent is None:
        raise TakeHierarchyError("The take has no valid surviving parent")
    selected = selected_take(scene)
    selected_uuid = selected.uuid if selected is not None else ""
    active_uuid = state.active_take_uuid
    active_chain_uuids = {
        member.uuid for member in take_chain(scene, active_uuid)
    }
    affects_active = take.uuid in active_chain_uuids
    fallback_uuid = parent.uuid if active_uuid == take.uuid else active_uuid
    children = [
        child
        for child in state.takes
        if child.parent_uuid == take.uuid
    ]
    for child in children:
        child.parent_uuid = parent.uuid

    try:
        if affects_active:
            apply_take(scene, fallback_uuid, strict=True)
    except TakeSystemError:
        for child in children:
            child.parent_uuid = take.uuid
        _sync_selected_index(state, selected_uuid)
        raise

    delete_index = next(
        index
        for index, candidate in enumerate(state.takes)
        if candidate.uuid == take.uuid
    )
    state.takes.remove(delete_index)
    if selected_uuid == take_uuid:
        selected_uuid = fallback_uuid
    _sync_selected_index(state, selected_uuid or fallback_uuid)
    return find_take(scene, fallback_uuid)


def remove_override(scene, take_uuid, override_uuid):
    """Remove one local override while preserving Main-baseline invariants."""

    take = find_take(scene, take_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    override_index = next(
        (
            index
            for index, candidate in enumerate(take.overrides)
            if candidate.uuid == override_uuid
        ),
        None,
    )
    if override_index is None:
        raise TakeHierarchyError(f"Override does not exist: {override_uuid}")
    override = take.overrides[override_index]
    if take.is_main:
        for other_take in scene.take_system.takes:
            if other_take.uuid == take.uuid:
                continue
            if any(
                candidate.target_ref_uuid == override.target_ref_uuid
                for candidate in other_take.overrides
            ):
                raise TakeHierarchyError(
                    "Remove descendant overrides before removing their "
                    "Main baseline"
                )

    active_uuid = scene.take_system.active_take_uuid
    active_chain_uuids = {
        member.uuid for member in take_chain(scene, active_uuid)
    }
    affects_active = take.uuid in active_chain_uuids
    snapshot = _snapshot_override_record(override)
    take.overrides.remove(override_index)
    try:
        if affects_active:
            apply_take(scene, active_uuid, strict=True)
    except TakeSystemError:
        restored = _append_override_snapshot(
            take,
            snapshot,
            fresh_uuid=False,
        )
        restored_index = len(take.overrides) - 1
        if restored_index != override_index:
            take.overrides.move(restored_index, override_index)
        raise

    state = scene.take_system
    state.active_override_index = min(
        state.active_override_index,
        max(0, len(take.overrides) - 1),
    )
    take = find_take(scene, take_uuid)
    if take is not None:
        _sync_camera_metadata(scene, take)
    return snapshot


def take_chain(scene, take_uuid=None):
    """Return the requested ancestry from Main through the requested take."""

    main = ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid or main.uuid
    take = find_take(scene, requested)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {requested}")

    reversed_chain = []
    visited = set()
    cursor = take
    while cursor is not None:
        if cursor.uuid in visited:
            raise TakeHierarchyError(
                f"Cycle detected at take '{cursor.name}' ({cursor.uuid})"
            )
        visited.add(cursor.uuid)
        reversed_chain.append(cursor)
        if cursor.is_main:
            break
        if not cursor.parent_uuid:
            raise TakeHierarchyError(
                f"Take '{cursor.name}' is not connected to Main"
            )
        cursor = find_take(scene, cursor.parent_uuid)
        if cursor is None:
            raise TakeHierarchyError(
                f"Take '{reversed_chain[-1].name}' has a missing parent"
            )

    chain = list(reversed(reversed_chain))
    if not chain or chain[0].uuid != main.uuid:
        raise TakeHierarchyError(
            f"Take '{take.name}' is not rooted at this scene's Main take"
        )
    return chain


def _scan_path(path):
    """Yield (index, character, square-bracket depth) outside quoted strings."""

    depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(path):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "[":
            depth += 1
            yield index, char, depth
        elif char == "]":
            yield index, char, depth
            depth -= 1
            if depth < 0:
                raise TakePathError(f"Unbalanced RNA path: {path}")
        else:
            yield index, char, depth
    if quote is not None or depth != 0:
        raise TakePathError(f"Unbalanced RNA path: {path}")


def split_final_path(path):
    """Split an RNA path into a resolvable parent and final attr/item token."""

    path = (path or "").strip()
    if not path:
        raise TakePathError("RNA data path is empty")

    bracket_stack = []
    final_bracket_start = None
    final_top_level_dot = None
    for index, char, depth in _scan_path(path):
        if char == "[":
            bracket_stack.append(index)
        elif char == "]":
            if not bracket_stack:
                raise TakePathError(f"Unbalanced RNA path: {path}")
            start = bracket_stack.pop()
            if index == len(path) - 1:
                final_bracket_start = start
        elif char == "." and depth == 0:
            final_top_level_dot = index

    if bracket_stack:
        raise TakePathError(f"Unbalanced RNA path: {path}")

    if final_bracket_start is not None:
        raw_token = path[final_bracket_start + 1 : -1]
        try:
            token = ast.literal_eval(raw_token)
        except (SyntaxError, ValueError) as exc:
            raise TakePathError(
                f"Unsupported final bracket token in RNA path: {path}"
            ) from exc
        if not isinstance(token, (str, int)):
            raise TakePathError(
                f"Final RNA item key must be a string or integer: {path}"
            )
        return FinalPathToken(
            parent_path=path[:final_bracket_start],
            kind="ITEM",
            token=token,
        )

    if final_top_level_dot is None:
        if not path.isidentifier():
            raise TakePathError(f"Invalid final RNA attribute: {path}")
        return FinalPathToken(parent_path="", kind="ATTR", token=path)

    parent_path = path[:final_top_level_dot]
    token = path[final_top_level_dot + 1 :]
    if not token.isidentifier():
        raise TakePathError(f"Invalid final RNA attribute: {path}")
    return FinalPathToken(parent_path=parent_path, kind="ATTR", token=token)


def _resolve_parent(target_id, final_token):
    if not final_token.parent_path:
        return target_id
    try:
        return target_id.path_resolve(final_token.parent_path)
    except (
        ValueError,
        AttributeError,
        ReferenceError,
        RuntimeError,
        SystemError,
        TypeError,
    ) as exc:
        raise TakePathError(
            f"Cannot resolve parent path '{final_token.parent_path}'"
        ) from exc


def _rna_property_for_path(target_id, path):
    final_token = split_final_path(path)
    parent = _resolve_parent(target_id, final_token)
    if final_token.kind != "ATTR":
        return None
    try:
        return parent.bl_rna.properties.get(final_token.token)
    except (
        AttributeError,
        ReferenceError,
        RuntimeError,
        SystemError,
        TypeError,
    ):
        return None


def validate_capture_path(target_id, path):
    """Reject paths known to be non-writable before creating records."""

    final_token = split_final_path(path)
    if final_token.kind == "ITEM" and isinstance(final_token.token, int):
        raise UnsupportedValueError(
            "Indexed array-component overrides are not supported; "
            "capture the owning vector property"
        )
    rna_property = _rna_property_for_path(target_id, path)
    if getattr(rna_property, "is_readonly", False):
        raise TakePathError(f"RNA property is read-only: {path}")
    if getattr(rna_property, "type", "") == "COLLECTION":
        raise UnsupportedValueError(
            "RNA collection structure cannot be overridden"
        )
    return rna_property


def read_path_value(target_id, path):
    final_token = split_final_path(path)
    try:
        parent = _resolve_parent(target_id, final_token)
        if final_token.kind == "ATTR":
            return getattr(parent, final_token.token)
        return parent[final_token.token]
    except (
        ValueError,
        AttributeError,
        KeyError,
        IndexError,
        ReferenceError,
        RuntimeError,
        SystemError,
        OverflowError,
        TypeError,
    ) as exc:
        raise TakePathError(
            f"Cannot resolve '{path}' on {_id_display_name(target_id)}"
        ) from exc


def _numeric_sequence(value, component_type="FLOAT"):
    if isinstance(value, (str, bytes, set, dict)):
        return None
    try:
        values = tuple(value)
    except TypeError:
        return None
    if not 2 <= len(values) <= 4:
        return None
    if not all(isinstance(component, numbers.Real) for component in values):
        return None
    if component_type == "INT":
        return tuple(int(component) for component in values)
    if component_type == "BOOL":
        return tuple(bool(component) for component in values)
    return tuple(float(component) for component in values)


def classify_value(value, rna_property=None):
    """Return (storage type, RNA subtype, normalized value)."""

    rna_type = getattr(rna_property, "type", "")
    subtype = getattr(rna_property, "subtype", "") or ""
    is_array = bool(getattr(rna_property, "is_array", False))

    if rna_type == "ENUM":
        if not isinstance(value, str):
            raise UnsupportedValueError(
                "Enum-flag/set properties are not supported in Phase 1/2"
            )
        return "ENUM", subtype, value

    if rna_type == "POINTER":
        if value is not None and not isinstance(value, bpy.types.ID):
            raise UnsupportedValueError(
                "Only pointers to Blender ID datablocks are supported"
            )
        if value is not None:
            if _safe_id_pointer(value) == 0:
                raise MissingReferenceError(
                    "Pointer value datablock no longer exists"
                )
            try:
                is_embedded = value.is_embedded_data
            except ReferenceError as exc:
                raise MissingReferenceError(
                    "Pointer value datablock no longer exists"
                ) from exc
            if is_embedded:
                raise UnsupportedValueError(
                    "Embedded datablocks cannot be stored as pointer values"
                )
        return "POINTER", subtype, value

    if is_array:
        component_type = rna_type if rna_type in {"INT", "BOOLEAN"} else "FLOAT"
        if component_type == "BOOLEAN":
            component_type = "BOOL"
        sequence = _numeric_sequence(value, component_type)
        if sequence is None:
            raise UnsupportedValueError(
                "Only numeric RNA arrays with 2-4 components are supported"
            )
        prop_type = "COLOR" if subtype in {"COLOR", "COLOR_GAMMA"} else "VECTOR"
        return prop_type, subtype, sequence

    if isinstance(value, bpy.types.ID):
        if _safe_id_pointer(value) == 0:
            raise MissingReferenceError(
                "Pointer value datablock no longer exists"
            )
        try:
            is_embedded = value.is_embedded_data
        except ReferenceError as exc:
            raise MissingReferenceError(
                "Pointer value datablock no longer exists"
            ) from exc
        if is_embedded:
            raise UnsupportedValueError(
                "Embedded datablocks cannot be stored as pointer values"
            )
        return "POINTER", subtype, value
    if value is None:
        raise UnsupportedValueError(
            "None is supported only for RNA datablock-pointer properties"
        )
    if isinstance(value, bool):
        return "BOOL", subtype, bool(value)
    if isinstance(value, int):
        return "INT", subtype, int(value)
    if isinstance(value, float):
        return "FLOAT", subtype, float(value)
    if isinstance(value, str):
        return "STRING", subtype, value

    sequence = _numeric_sequence(value)
    try:
        raw_sequence = tuple(value)
    except TypeError:
        raw_sequence = ()
    if raw_sequence and all(type(component) is bool for component in raw_sequence):
        sequence = _numeric_sequence(value, "BOOL")
    elif raw_sequence and all(
        isinstance(component, int) and not isinstance(component, bool)
        for component in raw_sequence
    ):
        sequence = _numeric_sequence(value, "INT")
    if sequence is not None:
        prop_type = "COLOR" if subtype in {"COLOR", "COLOR_GAMMA"} else "VECTOR"
        return prop_type, subtype, sequence

    raise UnsupportedValueError(
        f"Unsupported property value type: {type(value).__name__}"
    )


def _set_target_metadata(override, target_id):
    override.target_id = target_id
    override.target_reference_set = True
    override.target_id_type = _id_type_name(target_id)
    override.target_id_name = _id_display_name(target_id)
    override.target_library_path = _id_library_path(target_id)


def _clear_pointer_metadata(override):
    override.value_pointer = None
    override.pointer_is_none = False
    override.pointer_id_type = ""
    override.pointer_id_name = ""
    override.pointer_library_path = ""


def store_override_value(override, value, rna_property=None):
    """Copy a live Blender value into the override's parallel storage fields."""

    prop_type, subtype, normalized = classify_value(value, rna_property)
    override.prop_type = prop_type
    override.rna_subtype = subtype
    override.array_length = 0
    override.array_component_type = "FLOAT"
    override.value_float_text = ""
    override.value_array_text = ""
    _clear_pointer_metadata(override)

    if prop_type == "FLOAT":
        override.value_float = normalized
        override.value_float_text = float(normalized).hex()
    elif prop_type == "INT":
        override.value_int = normalized
    elif prop_type == "BOOL":
        override.value_bool = normalized
    elif prop_type in {"STRING", "ENUM"}:
        override.value_string = normalized
    elif prop_type in {"VECTOR", "COLOR"}:
        override.array_length = len(normalized)
        rna_type = getattr(rna_property, "type", "")
        if rna_type == "INT":
            override.array_component_type = "INT"
        elif rna_type == "BOOLEAN":
            override.array_component_type = "BOOL"
        elif normalized and all(
            isinstance(component, int) and not isinstance(component, bool)
            for component in normalized
        ):
            override.array_component_type = "INT"
        elif normalized and all(
            type(component) is bool for component in normalized
        ):
            override.array_component_type = "BOOL"
        if override.array_component_type == "INT":
            override.value_int_vector = normalized + (0,) * (4 - len(normalized))
        elif override.array_component_type == "BOOL":
            override.value_bool_vector = normalized + (False,) * (
                4 - len(normalized)
            )
        elif prop_type == "COLOR":
            padded = normalized + (0.0,) * (4 - len(normalized))
            override.value_color = padded
            override.value_array_text = ";".join(
                float(component).hex() for component in normalized
            )
        else:
            padded = normalized + (0.0,) * (4 - len(normalized))
            override.value_vector = padded
            override.value_array_text = ";".join(
                float(component).hex() for component in normalized
            )
    elif prop_type == "POINTER":
        override.pointer_is_none = normalized is None
        if normalized is not None:
            override.value_pointer = normalized
            override.pointer_id_type = _id_type_name(normalized)
            override.pointer_id_name = _id_display_name(normalized)
            override.pointer_library_path = _id_library_path(normalized)
    else:
        raise UnsupportedValueError(f"Unknown storage type: {prop_type}")


def decoded_override_value(override):
    prop_type = override.prop_type
    if prop_type == "FLOAT":
        if override.value_float_text:
            try:
                return float.fromhex(override.value_float_text)
            except (ValueError, OverflowError):
                pass
        return override.value_float
    if prop_type == "INT":
        return override.value_int
    if prop_type == "BOOL":
        return override.value_bool
    if prop_type in {"STRING", "ENUM"}:
        return override.value_string
    if prop_type == "VECTOR":
        if override.array_component_type == "INT":
            return tuple(
                override.value_int_vector[: override.array_length]
            )
        if override.array_component_type == "BOOL":
            return tuple(
                override.value_bool_vector[: override.array_length]
            )
        if override.value_array_text:
            try:
                return tuple(
                    float.fromhex(component)
                    for component in override.value_array_text.split(";")
                )[: override.array_length]
            except (ValueError, OverflowError):
                pass
        return tuple(override.value_vector[: override.array_length])
    if prop_type == "COLOR":
        if override.value_array_text:
            try:
                return tuple(
                    float.fromhex(component)
                    for component in override.value_array_text.split(";")
                )[: override.array_length]
            except (ValueError, OverflowError):
                pass
        return tuple(override.value_color[: override.array_length])
    if prop_type == "POINTER":
        if override.pointer_is_none:
            return None
        try:
            pointer = override.value_pointer
        except ReferenceError:
            pointer = None
        if pointer is None:
            label = (
                f"{override.pointer_id_type} "
                f"'{override.pointer_id_name or '<unnamed>'}'"
            )
            raise MissingReferenceError(
                f"Stored pointer value no longer exists: {label}"
            )
        return pointer
    raise UnsupportedValueError(f"Unknown stored type: {prop_type}")


def _runtime_write_snapshot(target_id, path, normalized_value=None):
    """Capture the concrete owner/token reached by a path right now."""

    final_token = split_final_path(path)
    parent = _resolve_parent(target_id, final_token)
    if normalized_value is None:
        rna_property = _rna_property_for_path(target_id, path)
        current = read_path_value(target_id, path)
        _prop_type, _subtype, normalized_value = classify_value(
            current,
            rna_property,
        )
    return RuntimeWriteSnapshot(
        parent=parent,
        kind=final_token.kind,
        token=final_token.token,
        value=normalized_value,
        target_name=_id_display_name(target_id),
        data_path=path,
    )


def _restore_runtime_write(snapshot):
    """Restore a concrete write location without resolving through new pointers."""

    try:
        if snapshot.kind == "ATTR":
            setattr(snapshot.parent, snapshot.token, snapshot.value)
        else:
            snapshot.parent[snapshot.token] = snapshot.value
    except (
        AttributeError,
        KeyError,
        IndexError,
        OverflowError,
        SystemError,
        TypeError,
        ValueError,
        ReferenceError,
    ) as exc:
        raise TakePathError(
            f"Cannot restore '{snapshot.data_path}' on "
            f"{snapshot.target_name}: {exc}"
        ) from exc


def _journaled_write_path(target_id, path, value, write_journal):
    """Write one path and append its concrete previous state on success."""

    rna_property = _rna_property_for_path(target_id, path)
    current = read_path_value(target_id, path)
    runtime_type, runtime_subtype, normalized_current = classify_value(
        current,
        rna_property,
    )
    value_type, value_subtype, normalized_value = classify_value(
        value,
        rna_property,
    )
    if (
        runtime_type == value_type
        and runtime_subtype == value_subtype
        and _exact_normalized_values_equal(
            normalized_current,
            normalized_value,
        )
    ):
        return False
    snapshot = _runtime_write_snapshot(
        target_id,
        path,
        normalized_current,
    )
    write_path_value(target_id, path, normalized_value)
    write_journal.append(snapshot)
    return True


def write_path_value(target_id, path, value):
    """Assign one decoded value without eval/exec."""

    final_token = split_final_path(path)
    parent = _resolve_parent(target_id, final_token)
    try:
        if final_token.kind == "ATTR":
            setattr(parent, final_token.token, value)
        else:
            parent[final_token.token] = value
    except (
        AttributeError,
        KeyError,
        IndexError,
        OverflowError,
        SystemError,
        TypeError,
        ValueError,
        ReferenceError,
    ) as exc:
        raise TakePathError(
            f"Cannot assign '{path}' on {_id_display_name(target_id)}: {exc}"
        ) from exc


def _same_live_target(override, target_id):
    stored_pointer = _safe_id_pointer(getattr(override, "target_id", None))
    requested_pointer = _safe_id_pointer(target_id)
    return bool(stored_pointer and stored_pointer == requested_pointer)


def find_override(take, target_id, data_path):
    return next(
        (
            override
            for override in take.overrides
            if override.data_path == data_path
            and _same_live_target(override, target_id)
        ),
        None,
    )


def is_render_setting_path(data_path):
    """Return whether a Scene path belongs to the render profile."""

    return data_path in _CORE_RENDER_SETTING_PATHS or any(
        data_path in engine_paths
        for engine_paths in _ENGINE_RENDER_SETTING_PATHS.values()
    )


def render_setting_group_for_path(data_path):
    """Return the render-profile group owning ``data_path``, if any."""

    for group_identifier, paths in _RENDER_SETTING_GROUP_PATHS.items():
        if data_path in paths:
            return group_identifier
    if any(
        data_path in paths
        for paths in _ENGINE_RENDER_SETTING_PATHS.values()
    ):
        return RENDER_GROUP_ENGINE_SAMPLING
    return None


def _supported_render_setting_paths(scene, paths):
    supported = []
    seen = set()
    for data_path in paths:
        if data_path in seen:
            continue
        seen.add(data_path)
        try:
            validate_capture_path(scene, data_path)
            value = read_path_value(scene, data_path)
            rna_property = _rna_property_for_path(scene, data_path)
            classify_value(value, rna_property)
        except (
            MissingReferenceError,
            TakePathError,
            UnsupportedValueError,
            ReferenceError,
        ):
            continue
        supported.append(data_path)
    return tuple(supported)


def render_profile_group_paths(scene, group_identifier, *, all_engines=False):
    """Return feature-detected paths for one independently inherited group."""

    if group_identifier not in RENDER_PROFILE_GROUPS:
        raise TakeSystemError(
            f"Unknown render-profile group: {group_identifier}"
        )
    paths = list(_RENDER_SETTING_GROUP_PATHS[group_identifier])
    if group_identifier == RENDER_GROUP_ENGINE_SAMPLING:
        if all_engines:
            for engine_paths in _ENGINE_RENDER_SETTING_PATHS.values():
                paths.extend(engine_paths)
        else:
            try:
                engine_identifier = str(scene.render.engine)
            except (AttributeError, ReferenceError):
                engine_identifier = ""
            paths.extend(
                _ENGINE_RENDER_SETTING_PATHS.get(engine_identifier, ())
            )
    return _supported_render_setting_paths(scene, paths)


def render_profile_paths(scene, *, all_engines=False):
    """Return every supported path displayed by the render-profile editor."""

    paths = []
    for group_identifier in RENDER_PROFILE_GROUPS:
        paths.extend(
            render_profile_group_paths(
                scene,
                group_identifier,
                all_engines=all_engines,
            )
        )
    return tuple(dict.fromkeys(paths))


def render_setting_paths(scene):
    """Return the feature-detected full preset for the current engine."""

    paths = list(_CORE_RENDER_SETTING_PATHS)
    try:
        engine_identifier = str(scene.render.engine)
    except (AttributeError, ReferenceError):
        engine_identifier = ""
    paths.extend(_ENGINE_RENDER_SETTING_PATHS.get(engine_identifier, ()))
    return _supported_render_setting_paths(scene, paths)


def _render_setting_sort_key(data_path):
    priority = {
        "render.engine": -40,
        "render.image_settings.file_format": -30,
        "render.image_settings.color_mode": -20,
        "render.image_settings.color_depth": -10,
        "view_settings.view_transform": -5,
        "view_settings.look": -4,
    }
    ranked = priority.get(data_path)
    if ranked is not None:
        return (ranked, 0)
    try:
        return (_path_depth(data_path), len(data_path))
    except TakePathError:
        return (1_000_000, len(data_path))


def direct_camera_override(scene, take):
    """Return a take's direct canonical Scene.camera record, if present."""

    if take is None:
        return None
    return find_override(take, scene, "camera")


def _sync_camera_metadata(scene, take):
    """Mirror canonical camera records into the UI staging compatibility fields."""

    override = direct_camera_override(scene, take)
    uses_override = override is not None
    camera = None
    if override is not None:
        try:
            decoded = decoded_override_value(override)
        except TakeSystemError:
            decoded = None
        if isinstance(decoded, bpy.types.Object) and decoded.type == "CAMERA":
            camera = decoded

    if bool(take.use_camera_override) != uses_override:
        take.use_camera_override = uses_override
    try:
        staged = take.camera_override
    except ReferenceError:
        staged = None
    if _safe_id_pointer(staged) != _safe_id_pointer(camera):
        take.camera_override = camera


def take_has_render_settings(scene, take):
    """Return whether a take directly owns any render-setting record."""

    if take is None:
        return False
    if str(take.render_output_path or "").strip():
        return True
    for override in take.overrides:
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        if (
            _safe_id_pointer(target) == _safe_id_pointer(scene)
            and is_render_setting_path(override.data_path)
        ):
            return True
    return False


def direct_render_setting_paths(scene, take, group_identifier=None):
    """Return direct Scene render paths owned by ``take``."""

    if take is None:
        return ()
    if (
        group_identifier is not None
        and group_identifier not in RENDER_PROFILE_GROUPS
    ):
        raise TakeSystemError(
            f"Unknown render-profile group: {group_identifier}"
        )
    scene_pointer = _safe_id_pointer(scene)
    paths = []
    for override in take.overrides:
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        data_path = override.data_path
        if (
            _safe_id_pointer(target) == scene_pointer
            and is_render_setting_path(data_path)
            and (
                group_identifier is None
                or render_setting_group_for_path(data_path)
                == group_identifier
            )
        ):
            paths.append(data_path)
    return tuple(paths)


def direct_render_profile_groups(scene, take):
    """Return render groups with at least one direct record on ``take``."""

    groups = {
        group_identifier
        for group_identifier in RENDER_PROFILE_GROUPS
        if direct_render_setting_paths(scene, take, group_identifier)
    }
    if take is not None and str(take.render_output_path or "").strip():
        groups.add(RENDER_GROUP_OUTPUT)
    return frozenset(groups)


def resolved_camera(scene, take_uuid=None):
    """Return ``(camera, source_take_uuid)`` for a resolved take camera."""

    requested = take_uuid or scene.take_system.active_take_uuid
    resolved = resolve_take(scene, requested)
    for entry in resolved.values():
        override = entry.override
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        if (
            _safe_id_pointer(target) == _safe_id_pointer(scene)
            and override.data_path == "camera"
        ):
            return decoded_override_value(override), entry.take_uuid
    try:
        return scene.camera, ""
    except (AttributeError, ReferenceError) as exc:
        raise MissingReferenceError("The scene camera no longer exists") from exc


def _add_override(take, target_id, data_path, target_ref_uuid=None):
    override = take.overrides.add()
    override.uuid = new_uuid()
    override.target_ref_uuid = target_ref_uuid or new_uuid()
    override.data_path = data_path
    _set_target_metadata(override, target_id)
    return override


def _material_slot_info(target_id, data_path):
    if not isinstance(target_id, bpy.types.Object):
        return None
    try:
        final_token = split_final_path(data_path)
    except TakePathError:
        return None
    if final_token.kind != "ATTR" or final_token.token != "material":
        return None
    try:
        slot = _resolve_parent(target_id, final_token)
    except TakePathError:
        return None
    if not isinstance(slot, bpy.types.MaterialSlot):
        return None
    slot_pointer = _safe_id_pointer(slot)
    for index, candidate in enumerate(target_id.material_slots):
        try:
            if candidate == slot or (
                slot_pointer and candidate.as_pointer() == slot_pointer
            ):
                return final_token, slot, index
        except ReferenceError:
            continue
    return None


def _main_slot_uses_data(scene, main, target_id, slot_index):
    link_path = f"material_slots[{slot_index}].link"
    link_override = find_override(main, target_id, link_path)
    if link_override is not None:
        try:
            return decoded_override_value(link_override) == "DATA"
        except TakeSystemError:
            return False
    try:
        return target_id.material_slots[slot_index].link == "DATA"
    except (IndexError, ReferenceError):
        return False


def _synchronize_main_data_slot_aliases(
    scene,
    target_id,
    data_path,
    value,
    rollback_snapshots=None,
):
    """Keep Object-keyed Main aliases consistent for one shared DATA slot."""

    if rollback_snapshots is None:
        rollback_snapshots = []
    source_info = _material_slot_info(target_id, data_path)
    if source_info is None:
        return rollback_snapshots
    _source_token, source_slot, slot_index = source_info
    if source_slot.link != "DATA":
        return rollback_snapshots
    source_data = target_id.data
    main = ensure_main_take(scene)

    for candidate in main.overrides:
        try:
            other_target = candidate.target_id
        except ReferenceError:
            continue
        if (
            not isinstance(other_target, bpy.types.Object)
            or other_target.data != source_data
        ):
            continue
        other_info = _material_slot_info(other_target, candidate.data_path)
        if other_info is None or other_info[2] != slot_index:
            continue
        if not _main_slot_uses_data(scene, main, other_target, slot_index):
            continue
        other_rna_property = _rna_property_for_path(
            other_target,
            candidate.data_path,
        )
        try:
            previous_value = decoded_override_value(candidate)
        except TakeSystemError:
            continue
        rollback_snapshots.append(
            (candidate, previous_value, other_rna_property)
        )
        store_override_value(candidate, value, other_rna_property)
    return rollback_snapshots


def _prepare_object_material_slot(scene, take, target_id, data_path):
    """Promote a DATA-linked material slot to a take-managed OBJECT link."""

    if take.is_main:
        return
    slot_info = _material_slot_info(target_id, data_path)
    if slot_info is None:
        return
    final_token, slot, _slot_index = slot_info
    if slot.link != "DATA":
        return

    inherited_material = slot.material
    link_path = f"{final_token.parent_path}.link"
    link_rna_property = _rna_property_for_path(target_id, link_path)
    main = ensure_main_take(scene)
    main_count = len(main.overrides)
    take_count = len(take.overrides)
    rollback_snapshots = []
    for owner_take in (main, take):
        existing = find_override(owner_take, target_id, link_path)
        if existing is None:
            continue
        rollback_snapshots.append(
            (
                existing,
                decoded_override_value(existing),
                link_rna_property,
            )
        )

    try:
        _synchronize_main_data_slot_aliases(
            scene,
            target_id,
            data_path,
            inherited_material,
            rollback_snapshots,
        )
        # Store DATA on Main and the child before changing anything, then update
        # the child to OBJECT. This makes per-object CMF deterministic without
        # cloning the shared Mesh or Material.
        capture_override(scene, target_id, link_path, take.uuid)
        write_path_value(target_id, link_path, "OBJECT")
        promoted_slot = _resolve_parent(target_id, final_token)
        if promoted_slot.material is None and inherited_material is not None:
            promoted_slot.material = inherited_material
        capture_override(scene, target_id, link_path, take.uuid)
        _mark_scene_mutated(scene)
    except Exception:
        try:
            write_path_value(target_id, link_path, "DATA")
        except TakeSystemError:
            pass
        for override, previous_value, rna_property in reversed(
            rollback_snapshots
        ):
            try:
                store_override_value(
                    override,
                    previous_value,
                    rna_property,
                )
            except (TakeSystemError, ReferenceError, RuntimeError):
                pass
        while len(take.overrides) > take_count:
            take.overrides.remove(len(take.overrides) - 1)
        while len(main.overrides) > main_count:
            main.overrides.remove(len(main.overrides) - 1)
        raise


def capture_override(scene, target_id, data_path, take_uuid=None):
    """Capture the current value and upsert it on one take.

    On the first non-Main capture for a target/path, the current value is also
    stored on Main. The intended manual workflow is therefore:

    1. Add the override before changing the property (seeds Main).
    2. Change the property.
    3. Capture again to update the active take's value.
    """

    if not isinstance(target_id, bpy.types.ID):
        raise TakeSystemError("Override targets must be Blender ID datablocks")
    target_id, data_path = canonicalize_id_path(target_id, data_path)
    if is_take_system_internal_path(target_id, data_path):
        raise TakePathError(
            "Take System settings cannot themselves be stored as take overrides"
        )

    main = ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid or main.uuid
    take = find_take(scene, requested)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {requested}")
    # Validate the hierarchy now rather than allowing orphan data to accumulate.
    take_chain(scene, take.uuid)

    validate_capture_path(target_id, data_path)
    _prepare_object_material_slot(scene, take, target_id, data_path)
    value = read_path_value(target_id, data_path)
    rna_property = validate_capture_path(target_id, data_path)
    # Classify before mutating the collection, so unsupported values leave no
    # half-created records behind.
    classify_value(value, rna_property)

    existing = find_override(take, target_id, data_path)
    main_override = find_override(main, target_id, data_path)
    main_seeded = False

    if take.uuid != main.uuid and main_override is None:
        inherited_ref_uuid = (
            existing.target_ref_uuid if existing is not None else None
        )
        main_override = _add_override(
            main,
            target_id,
            data_path,
            target_ref_uuid=inherited_ref_uuid,
        )
        store_override_value(main_override, value, rna_property)
        main_seeded = True

    created = existing is None
    if existing is None:
        shared_ref_uuid = (
            main_override.target_ref_uuid if main_override is not None else None
        )
        existing = _add_override(
            take,
            target_id,
            data_path,
            target_ref_uuid=shared_ref_uuid,
        )
    elif main_override is not None:
        existing.target_ref_uuid = main_override.target_ref_uuid

    _set_target_metadata(existing, target_id)
    store_override_value(existing, value, rna_property)
    if take.is_main:
        _synchronize_main_data_slot_aliases(
            scene,
            target_id,
            data_path,
            value,
        )
    if target_id == scene and data_path == "camera":
        _sync_camera_metadata(scene, main)
        _sync_camera_metadata(scene, take)
    return CaptureResult(
        override=existing,
        created=created,
        main_seeded=main_seeded,
    )


def normalized_values_equal(left, right):
    """Compare detached supported values, including Blender ID pointers."""

    if isinstance(left, bpy.types.ID) or isinstance(right, bpy.types.ID):
        if not isinstance(left, bpy.types.ID) or not isinstance(
            right,
            bpy.types.ID,
        ):
            return False
        left_pointer = _safe_id_pointer(left)
        right_pointer = _safe_id_pointer(right)
        return bool(left_pointer and left_pointer == right_pointer)
    return left == right


def _replace_override_records(take, snapshots):
    while take.overrides:
        take.overrides.remove(len(take.overrides) - 1)
    for snapshot in snapshots:
        _append_override_snapshot(take, snapshot, fresh_uuid=False)


def _capture_override_values(
    scene,
    main,
    take,
    target_id,
    data_path,
    baseline_value,
    after_value,
    rna_property,
):
    """Store one preflighted transition without rewinding the live property."""

    _prepare_object_material_slot(scene, take, target_id, data_path)
    rna_property = validate_capture_path(target_id, data_path)
    existing = find_override(take, target_id, data_path)
    main_override = find_override(main, target_id, data_path)
    main_seeded = False

    if main_override is None:
        inherited_ref_uuid = (
            existing.target_ref_uuid if existing is not None else None
        )
        main_override = _add_override(
            main,
            target_id,
            data_path,
            target_ref_uuid=inherited_ref_uuid,
        )
        store_override_value(main_override, baseline_value, rna_property)
        _synchronize_main_data_slot_aliases(
            scene,
            target_id,
            data_path,
            baseline_value,
        )
        main_seeded = True

    created = existing is None
    if existing is None:
        existing = _add_override(
            take,
            target_id,
            data_path,
            target_ref_uuid=main_override.target_ref_uuid,
        )
    else:
        existing.target_ref_uuid = main_override.target_ref_uuid
        _set_target_metadata(existing, target_id)
    store_override_value(existing, after_value, rna_property)
    return CaptureResult(
        override=existing,
        created=created,
        main_seeded=main_seeded,
    )


def capture_change_batch(scene, changes, take_uuid=None):
    """Atomically turn observed value transitions into direct take overrides.

    Missing Main records are seeded from each transition's trusted baseline,
    while child records receive the live post-action value. The live property
    is never rewound to discover its prior state.
    """

    main = ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid or main.uuid
    take = find_take(scene, requested)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {requested}")
    if take.is_main:
        raise TakeHierarchyError(
            "Apply a non-Main take before capturing a recent action"
        )
    if scene.take_system.active_take_uuid != take.uuid:
        raise TakeHierarchyError(
            "The destination take must be applied before capturing live changes"
        )
    take_chain(scene, take.uuid)

    prepared = []
    seen_keys = set()
    for change in changes:
        target_id = change.target_id
        data_path = change.data_path
        if not isinstance(target_id, bpy.types.ID):
            raise MissingReferenceError(
                "A recent-action target no longer exists"
            )
        if _safe_id_pointer(target_id) == 0:
            raise MissingReferenceError(
                "A recent-action target no longer exists"
            )
        if (
            getattr(target_id, "library", None) is not None
            and getattr(target_id, "override_library", None) is None
        ) or getattr(target_id, "is_editable", True) is False:
            raise TakePathError(
                f"Recent-action target is read-only: "
                f"{_id_display_name(target_id)}"
            )

        target_id, data_path = canonicalize_id_path(target_id, data_path)
        if is_take_system_internal_path(target_id, data_path):
            raise TakePathError(
                "Take System settings cannot themselves be stored as "
                "take overrides"
            )
        key = (_safe_id_pointer(target_id), data_path)
        if key in seen_keys:
            raise TakePathError(
                f"Recent action contains the same property twice: {data_path}"
            )
        seen_keys.add(key)

        rna_property = validate_capture_path(target_id, data_path)
        _before_type, _before_subtype, baseline_value = classify_value(
            change.baseline_value,
            rna_property,
        )
        _after_type, _after_subtype, after_value = classify_value(
            change.after_value,
            rna_property,
        )
        current_value = read_path_value(target_id, data_path)
        _current_type, _current_subtype, current_value = classify_value(
            current_value,
            rna_property,
        )
        if not normalized_values_equal(current_value, after_value):
            raise TakePathError(
                f"'{_id_display_name(target_id)}.{data_path}' changed again "
                "after the tracked action"
            )
        prepared.append(
            (
                target_id,
                data_path,
                baseline_value,
                after_value,
                rna_property,
            )
        )

    if not prepared:
        raise TakeSystemError("No supported recent property changes were found")

    main_snapshot = [
        _snapshot_override_record(override)
        for override in main.overrides
    ]
    take_snapshot = [
        _snapshot_override_record(override)
        for override in take.overrides
    ]
    live_snapshots = {}
    for target_id, data_path, _baseline, _after, _rna in prepared:
        live_snapshots[(_safe_id_pointer(target_id), data_path)] = (
            target_id,
            data_path,
            read_path_value(target_id, data_path),
        )
        slot_info = _material_slot_info(target_id, data_path)
        if slot_info is not None:
            final_token, _slot, _slot_index = slot_info
            link_path = f"{final_token.parent_path}.link"
            live_snapshots[(_safe_id_pointer(target_id), link_path)] = (
                target_id,
                link_path,
                read_path_value(target_id, link_path),
            )

    report = CaptureBatchReport(
        take_uuid=take.uuid,
        take_name=take.name,
    )
    try:
        for (
            target_id,
            data_path,
            baseline_value,
            after_value,
            rna_property,
        ) in prepared:
            result = _capture_override_values(
                scene,
                main,
                take,
                target_id,
                data_path,
                baseline_value,
                after_value,
                rna_property,
            )
            report.captured += 1
            report.created += int(result.created)
            report.main_seeded += int(result.main_seeded)
            report.paths.append(
                f"{_id_display_name(target_id)}.{data_path}"
            )
    except Exception:
        _replace_override_records(main, main_snapshot)
        _replace_override_records(take, take_snapshot)
        with _applying_guard():
            for target_id, data_path, value in reversed(
                tuple(live_snapshots.values())
            ):
                try:
                    write_path_value(target_id, data_path, value)
                except TakeSystemError:
                    pass
        raise
    _sync_camera_metadata(scene, main)
    _sync_camera_metadata(scene, take)
    return report


def configure_take_overrides(scene, values, take_uuid=None):
    """Atomically store explicit values on Main or the applied child take.

    This is the Phase 5 dialog path: callers can stage a camera or a group of
    render settings without first changing the live scene. A non-Main
    destination must be applied so any missing Main records are seeded from
    its actual inherited live state.
    """

    main = ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid or main.uuid
    take = find_take(scene, requested)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {requested}")
    take_chain(scene, take.uuid)
    if (
        not take.is_main
        and scene.take_system.active_take_uuid != take.uuid
    ):
        raise TakeHierarchyError(
            "Apply the destination take before configuring camera or render "
            "settings"
        )

    prepared = []
    seen_keys = set()
    for explicit in values:
        target_id = explicit.target_id
        if not isinstance(target_id, bpy.types.ID):
            raise MissingReferenceError("An override target no longer exists")
        if _safe_id_pointer(target_id) == 0:
            raise MissingReferenceError("An override target no longer exists")
        if (
            getattr(target_id, "library", None) is not None
            and getattr(target_id, "override_library", None) is None
        ) or getattr(target_id, "is_editable", True) is False:
            raise TakePathError(
                f"Override target is read-only: {_id_display_name(target_id)}"
            )

        target_id, data_path = canonicalize_id_path(
            target_id,
            explicit.data_path,
        )
        if is_take_system_internal_path(target_id, data_path):
            raise TakePathError(
                "Take System settings cannot themselves be stored as take "
                "overrides"
            )
        key = (_safe_id_pointer(target_id), data_path)
        if key in seen_keys:
            raise TakePathError(
                f"The same property was configured twice: {data_path}"
            )
        seen_keys.add(key)

        rna_property = validate_capture_path(target_id, data_path)
        current_value = read_path_value(target_id, data_path)
        _current_type, _current_subtype, baseline_value = classify_value(
            current_value,
            rna_property,
        )
        _value_type, _value_subtype, desired_value = classify_value(
            explicit.value,
            rna_property,
        )
        prepared.append(
            (
                target_id,
                data_path,
                baseline_value,
                desired_value,
                rna_property,
            )
        )

    if not prepared:
        raise TakeSystemError("No supported values were selected")

    main_uuid = main.uuid
    take_uuid_snapshot = take.uuid
    main_snapshot = [
        _snapshot_override_record(override)
        for override in main.overrides
    ]
    take_snapshot = (
        main_snapshot
        if take_uuid_snapshot == main_uuid
        else [
            _snapshot_override_record(override)
            for override in take.overrides
        ]
    )
    report = CaptureBatchReport(
        take_uuid=take_uuid_snapshot,
        take_name=take.name,
    )

    try:
        for (
            target_id,
            data_path,
            baseline_value,
            desired_value,
            rna_property,
        ) in prepared:
            main = find_take(scene, main_uuid)
            take = find_take(scene, take_uuid_snapshot)
            if main is None or take is None:
                raise TakeHierarchyError(
                    "The configured take changed while values were stored"
                )

            if take.is_main:
                existing = find_override(take, target_id, data_path)
                created = existing is None
                if existing is None:
                    existing = _add_override(take, target_id, data_path)
                _set_target_metadata(existing, target_id)
                store_override_value(
                    existing,
                    desired_value,
                    rna_property,
                )
                result = CaptureResult(
                    override=existing,
                    created=created,
                    main_seeded=False,
                )
            else:
                result = _capture_override_values(
                    scene,
                    main,
                    take,
                    target_id,
                    data_path,
                    baseline_value,
                    desired_value,
                    rna_property,
                )

            report.captured += 1
            report.created += int(result.created)
            report.main_seeded += int(result.main_seeded)
            report.paths.append(
                f"{_id_display_name(target_id)}.{data_path}"
            )

        if scene.take_system.active_take_uuid == take_uuid_snapshot:
            apply_take(scene, take_uuid_snapshot, strict=True)
    except Exception:
        main = find_take(scene, main_uuid)
        take = find_take(scene, take_uuid_snapshot)
        if main is not None:
            _replace_override_records(main, main_snapshot)
        if take is not None and take_uuid_snapshot != main_uuid:
            _replace_override_records(take, take_snapshot)
        raise
    main = find_take(scene, main_uuid)
    take = find_take(scene, take_uuid_snapshot)
    if main is not None:
        _sync_camera_metadata(scene, main)
    if take is not None:
        _sync_camera_metadata(scene, take)
    return report


def configure_take_camera(scene, take_uuid, camera):
    """Store a canonical Scene.camera override for one take."""

    if camera is not None and (
        not isinstance(camera, bpy.types.Object) or camera.type != "CAMERA"
    ):
        raise TakeSystemError("Take Camera must be a Camera object or None")
    report = configure_take_overrides(
        scene,
        (
            ExplicitOverrideValue(
                target_id=scene,
                data_path="camera",
                value=camera,
            ),
        ),
        take_uuid=take_uuid,
    )
    main = find_take(scene, scene.take_system.main_take_uuid)
    take = find_take(scene, take_uuid)
    if main is not None:
        _sync_camera_metadata(scene, main)
    if take is not None:
        _sync_camera_metadata(scene, take)
    return report


def capture_render_settings(scene, take_uuid=None):
    """Capture the current portable render preset as ordinary Scene overrides."""

    paths = render_setting_paths(scene)
    values = tuple(
        ExplicitOverrideValue(
            target_id=scene,
            data_path=data_path,
            value=read_path_value(scene, data_path),
        )
        for data_path in paths
    )
    return configure_take_overrides(
        scene,
        values,
        take_uuid=take_uuid,
    )


def snapshot_render_profile(scene):
    """Return detached live values for every available profile control."""

    snapshot = {}
    for data_path in render_profile_paths(scene, all_engines=True):
        rna_property = validate_capture_path(scene, data_path)
        value = read_path_value(scene, data_path)
        _prop_type, _subtype, normalized = classify_value(
            value,
            rna_property,
        )
        snapshot[data_path] = normalized
    return snapshot


def restore_render_profile(scene, values):
    """Restore a render-profile snapshot under the internal-write guard."""

    failures = []
    wrote_value = False
    with _applying_guard():
        for data_path in sorted(values, key=_render_setting_sort_key):
            try:
                rna_property = validate_capture_path(scene, data_path)
                _value_type, _value_subtype, desired_value = classify_value(
                    values[data_path],
                    rna_property,
                )
                current_value = read_path_value(scene, data_path)
                (
                    _current_type,
                    _current_subtype,
                    current_value,
                ) = classify_value(current_value, rna_property)
                if _exact_normalized_values_equal(
                    current_value,
                    desired_value,
                ):
                    continue
                write_path_value(scene, data_path, desired_value)
                wrote_value = True
            except (
                TakeSystemError,
                AttributeError,
                ReferenceError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                failures.append(f"{data_path}: {exc}")
    if wrote_value:
        _mark_scene_mutated(scene)
    if failures:
        raise TakeSystemError(
            "Render-profile restoration failed: " + "; ".join(failures)
        )
    return wrote_value


def configure_render_profile(
    scene,
    take_uuid,
    enabled_groups,
    *,
    baseline_values=None,
    batch_output_path=None,
    baseline_batch_output_path=None,
):
    """Atomically store selected profile groups and inherit all others.

    The live Scene contains the values staged by the editor. ``baseline_values``
    is its pre-dialog snapshot, allowing a child to seed missing Main records
    from the inherited default rather than from the newly edited value.
    """

    main = ensure_main_take(scene)
    take = find_take(scene, take_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    take_chain(scene, take.uuid)
    if scene.take_system.active_take_uuid != take.uuid:
        raise TakeHierarchyError(
            "Apply the destination take before editing its render profile"
        )

    unknown_groups = set(enabled_groups) - set(RENDER_PROFILE_GROUPS)
    if unknown_groups:
        raise TakeSystemError(
            "Unknown render-profile group(s): "
            + ", ".join(sorted(unknown_groups))
        )
    enabled = tuple(
        group_identifier
        for group_identifier in RENDER_PROFILE_GROUPS
        if group_identifier in enabled_groups
    )
    baselines = (
        snapshot_render_profile(scene)
        if baseline_values is None
        else dict(baseline_values)
    )

    desired_paths = []
    for group_identifier in enabled:
        desired_paths.extend(
            render_profile_group_paths(scene, group_identifier)
        )
    desired_paths = tuple(dict.fromkeys(desired_paths))
    desired_path_set = set(desired_paths)

    prepared = []
    for data_path in desired_paths:
        rna_property = validate_capture_path(scene, data_path)
        current_value = read_path_value(scene, data_path)
        _value_type, _value_subtype, desired_value = classify_value(
            current_value,
            rna_property,
        )
        main_override = find_override(main, scene, data_path)
        if (
            not take.is_main
            and main_override is None
            and data_path not in baselines
        ):
            raise TakeSystemError(
                f"The inherited baseline is unavailable for '{data_path}'"
            )
        baseline_value = baselines.get(data_path, desired_value)
        _base_type, _base_subtype, baseline_value = classify_value(
            baseline_value,
            rna_property,
        )
        prepared.append(
            (
                data_path,
                baseline_value,
                desired_value,
                rna_property,
            )
        )

    remove_indices = []
    if not take.is_main:
        scene_pointer = _safe_id_pointer(scene)
        enabled_set = set(enabled)
        for index, override in enumerate(take.overrides):
            try:
                target = override.target_id
            except ReferenceError:
                target = None
            data_path = override.data_path
            group_identifier = render_setting_group_for_path(data_path)
            if (
                _safe_id_pointer(target) == scene_pointer
                and group_identifier is not None
                and (
                    group_identifier not in enabled_set
                    or data_path not in desired_path_set
                )
            ):
                remove_indices.append(index)

    main_uuid = main.uuid
    take_uuid_snapshot = take.uuid
    main_snapshot = [
        _snapshot_override_record(override)
        for override in main.overrides
    ]
    take_snapshot = (
        main_snapshot
        if take.is_main
        else [
            _snapshot_override_record(override)
            for override in take.overrides
        ]
    )
    original_batch_output_path = (
        take.render_output_path
        if baseline_batch_output_path is None
        else str(baseline_batch_output_path)
    )
    report = RenderProfileReport(
        take_uuid=take_uuid_snapshot,
        take_name=take.name,
        groups=enabled,
    )

    try:
        take = find_take(scene, take_uuid_snapshot)
        if take is None:
            raise TakeHierarchyError(
                "The render-profile take changed before it was stored"
            )
        for index in reversed(remove_indices):
            take.overrides.remove(index)
            report.removed += 1

        for (
            data_path,
            baseline_value,
            desired_value,
            rna_property,
        ) in prepared:
            main = find_take(scene, main_uuid)
            take = find_take(scene, take_uuid_snapshot)
            if main is None or take is None:
                raise TakeHierarchyError(
                    "The render-profile take changed while values were stored"
                )
            if take.is_main:
                existing = find_override(take, scene, data_path)
                created = existing is None
                if existing is None:
                    existing = _add_override(take, scene, data_path)
                _set_target_metadata(existing, scene)
                store_override_value(
                    existing,
                    desired_value,
                    rna_property,
                )
                result = CaptureResult(
                    override=existing,
                    created=created,
                    main_seeded=False,
                )
            else:
                result = _capture_override_values(
                    scene,
                    main,
                    take,
                    scene,
                    data_path,
                    baseline_value,
                    desired_value,
                    rna_property,
                )
            report.configured += 1
            report.created += int(result.created)
            report.main_seeded += int(result.main_seeded)
            report.paths.append(data_path)

        take = find_take(scene, take_uuid_snapshot)
        if take is None:
            raise TakeHierarchyError(
                "The render-profile take changed before output was stored"
            )
        if batch_output_path is not None:
            take.render_output_path = str(batch_output_path)
        apply_take(scene, take_uuid_snapshot, strict=True)
    except Exception:
        main = find_take(scene, main_uuid)
        take = find_take(scene, take_uuid_snapshot)
        if main is not None:
            _replace_override_records(main, main_snapshot)
        if take is not None and take_uuid_snapshot != main_uuid:
            _replace_override_records(take, take_snapshot)
        if take is not None:
            take.render_output_path = original_batch_output_path
        try:
            restore_render_profile(scene, baselines)
        except TakeSystemError:
            pass
        raise
    return report


def _direct_render_override_indices(scene, take):
    scene_pointer = _safe_id_pointer(scene)
    indices = []
    for index, override in enumerate(take.overrides):
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        if (
            _safe_id_pointer(target) == scene_pointer
            and is_render_setting_path(override.data_path)
        ):
            indices.append(index)
    return indices


def remove_render_settings(scene, take_uuid):
    """Atomically remove all direct Phase 5 render records from one take."""

    take = find_take(scene, take_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    indices = _direct_render_override_indices(scene, take)
    original_output_path = take.render_output_path
    had_output_path = bool(str(original_output_path or "").strip())
    if not indices and not had_output_path:
        raise TakeSystemError("The selected take has no direct render settings")

    removed_ref_uuids = {
        take.overrides[index].target_ref_uuid
        for index in indices
    }
    if take.is_main:
        for other_take in scene.take_system.takes:
            if other_take.uuid == take.uuid:
                continue
            if any(
                override.target_ref_uuid in removed_ref_uuids
                for override in other_take.overrides
            ):
                raise TakeHierarchyError(
                    "Remove descendant render-setting overrides before "
                    "removing their Main baselines"
                )

    active_uuid = scene.take_system.active_take_uuid
    active_chain_uuids = {
        member.uuid for member in take_chain(scene, active_uuid)
    }
    affects_active = take.uuid in active_chain_uuids
    snapshots = [
        _snapshot_override_record(override)
        for override in take.overrides
    ]
    try:
        for index in reversed(indices):
            take.overrides.remove(index)
        take.render_output_path = ""
        if affects_active:
            apply_take(scene, active_uuid, strict=True)
    except Exception:
        take = find_take(scene, take_uuid)
        if take is not None:
            _replace_override_records(take, snapshots)
            take.render_output_path = original_output_path
        raise
    return len(indices) + int(had_output_path)


def remove_take_camera(scene, take_uuid):
    """Remove one direct canonical Scene.camera override."""

    take = find_take(scene, take_uuid)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {take_uuid}")
    override = direct_camera_override(scene, take)
    if override is None:
        raise TakeSystemError("The selected take has no direct camera override")
    removed = remove_override(scene, take_uuid, override.uuid)
    take = find_take(scene, take_uuid)
    if take is not None:
        _sync_camera_metadata(scene, take)
    return removed


def _resolved_key(override):
    if override.target_ref_uuid:
        return ("REF", override.target_ref_uuid, override.data_path)
    try:
        target = override.target_id
    except ReferenceError:
        target = None
    pointer = _safe_id_pointer(target)
    if pointer:
        return ("PTR", pointer, override.data_path)
    # Corrupt/legacy missing records must not collide merely because their old
    # human-readable names happen to match.
    return ("MISSING", override.uuid, override.data_path)


def _live_target_path_key(override):
    """Return the concrete live property identity when its target still exists."""

    try:
        target = override.target_id
    except ReferenceError:
        target = None
    pointer = _safe_id_pointer(target)
    if not pointer:
        return None
    return (pointer, override.data_path)


def _validated_take_override_keys(take):
    """Reject ambiguous direct records before hierarchy resolution mutates data."""

    by_resolved_key = {}
    by_live_target_path = {}
    for override in take.overrides:
        resolved_key = _resolved_key(override)
        if resolved_key in by_resolved_key:
            previous = by_resolved_key[resolved_key]
            raise TakeHierarchyError(
                f"Take '{take.name}' has duplicate overrides for "
                f"'{override.target_id_name or previous.target_id_name}."
                f"{override.data_path}'"
            )
        by_resolved_key[resolved_key] = override

        live_key = _live_target_path_key(override)
        if live_key is None:
            continue
        if live_key in by_live_target_path:
            raise TakeHierarchyError(
                f"Take '{take.name}' has more than one override for "
                f"'{override.target_id_name}.{override.data_path}'"
            )
        by_live_target_path[live_key] = override
    return by_resolved_key


def _validate_main_target_identity(main_override, take, override):
    """Ensure one stable ref/path does not silently switch live datablocks."""

    try:
        main_target = main_override.target_id
    except ReferenceError:
        main_target = None
    try:
        take_target = override.target_id
    except ReferenceError:
        take_target = None
    main_pointer = _safe_id_pointer(main_target)
    take_pointer = _safe_id_pointer(take_target)
    # Two missing pointers remain apply-time MissingReferenceError diagnostics.
    # If only one side is live, allowing the child to replace the missing Main
    # entry would bypass the broken baseline and write an unverified datablock.
    if (
        (main_pointer or take_pointer)
        and main_pointer != take_pointer
    ):
        raise TakeHierarchyError(
            f"Take '{take.name}' override "
            f"'{override.target_id_name}.{override.data_path}' reuses a Main "
            f"identity belonging to "
            f"'{main_override.target_id_name}.{main_override.data_path}'"
        )


def resolve_take(scene, take_uuid=None):
    """Build the deepest-wins resolved override table for one take."""

    resolved = {}
    chain = take_chain(scene, take_uuid)
    main = chain[0]
    main_entries = _validated_take_override_keys(main)
    for depth, take in enumerate(chain):
        direct_entries = (
            main_entries if depth == 0 else _validated_take_override_keys(take)
        )
        for key, override in direct_entries.items():
            main_override = main_entries.get(key)
            if depth and main_override is None:
                raise TakeHierarchyError(
                    f"Take '{take.name}' override "
                    f"'{override.target_id_name}.{override.data_path}' "
                    "has no Main baseline"
                )
            if depth:
                _validate_main_target_identity(
                    main_override,
                    take,
                    override,
                )
            resolved[key] = ResolvedEntry(
                take_uuid=take.uuid,
                take_name=take.name,
                override=override,
            )
    return resolved


def _path_depth(path):
    depth = 1
    for _index, char, bracket_depth in _scan_path(path):
        if char == "[":
            depth += 1
        elif char == "." and bracket_depth == 0:
            depth += 1
    return depth


def _ordered_resolved_entries(resolved):
    """Apply parent pointer paths before paths that traverse those pointers."""

    entries = list(resolved.values())

    def sort_key(entry):
        path = entry.override.data_path
        render_group = render_setting_group_for_path(path)
        if render_group is not None:
            try:
                target = entry.override.target_id
            except ReferenceError:
                target = None
            if isinstance(target, bpy.types.Scene):
                return _render_setting_sort_key(path)
        try:
            return (_path_depth(path), len(path))
        except TakePathError:
            # Let the per-entry strict/repair logic turn malformed storage into a
            # structured issue instead of failing before an ApplyReport exists.
            return (1_000_000, len(path))

    return sorted(
        entries,
        key=sort_key,
    )


@contextmanager
def _applying_guard():
    global _APPLY_DEPTH
    _APPLY_DEPTH += 1
    try:
        yield
    finally:
        _APPLY_DEPTH -= 1


def is_applying():
    """Future recording handlers use this to ignore programmatic take applies."""

    return _APPLY_DEPTH > 0


def _target_for_override(override):
    try:
        target = override.target_id
    except ReferenceError:
        target = None
    if target is None:
        label = (
            f"{override.target_id_type} "
            f"'{override.target_id_name or '<unnamed>'}'"
        )
        raise MissingReferenceError(f"Stored target no longer exists: {label}")
    return target


def _issue_for(entry, override, exc):
    return ApplyIssue(
        take_uuid=entry.take_uuid,
        take_name=entry.take_name,
        target=(
            override.target_id_name
            or override.target_id_type
            or "<missing target>"
        ),
        data_path=override.data_path,
        message=str(exc),
    )


def _exact_normalized_values_equal(left, right):
    """Compare normalized values without Python's cross-numeric coercion."""

    if isinstance(left, bpy.types.ID) or isinstance(right, bpy.types.ID):
        return normalized_values_equal(left, right)
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        # Python treats positive and negative zero as equal, but the override
        # payload preserves the exact IEEE-754 value. Bit comparison also
        # avoids treating an arbitrary NaN payload as a proven no-op.
        return struct.pack("!d", left) == struct.pack("!d", right)
    if isinstance(left, tuple):
        return len(left) == len(right) and all(
            _exact_normalized_values_equal(
                left_component,
                right_component,
            )
            for left_component, right_component in zip(left, right)
        )
    return left == right


def _normalized_runtime_matches_override(
    override,
    runtime_prop_type,
    runtime_subtype,
    normalized_runtime_value,
    decoded_value,
):
    """Return whether one live value exactly matches its stored override."""

    if (
        runtime_prop_type != override.prop_type
        or runtime_subtype != override.rna_subtype
    ):
        return False
    return _exact_normalized_values_equal(
        normalized_runtime_value,
        decoded_value,
    )


def _apply_take_atomic(scene, take, resolved, write_journal=None):
    """Preflight, apply, and roll back on failure."""

    report = ApplyReport(take_uuid=take.uuid, take_name=take.name)
    plans = []

    for entry in _ordered_resolved_entries(resolved):
        override = entry.override
        try:
            target = _target_for_override(override)
            value = decoded_override_value(override)
            # Syntax can be validated without resolving through a pointer that
            # an earlier, shallower override is about to replace.
            split_final_path(override.data_path)
            plans.append((entry, target, value))
        except (
            MissingReferenceError,
            TakePathError,
            UnsupportedValueError,
            ReferenceError,
        ) as exc:
            report.skipped += 1
            report.issues.append(_issue_for(entry, override, exc))

    if report.issues:
        raise TakeApplyError(report)

    render_dependency_snapshots = []
    if any(
        isinstance(target, bpy.types.Scene)
        and is_render_setting_path(entry.override.data_path)
        for entry, target, _value in plans
    ):
        render_dependency_snapshots = _mandatory_batch_snapshots(scene)

    applied_plans = []
    wrote_live_value = False
    with _applying_guard():
        for entry, target, value in plans:
            override = entry.override
            try:
                rna_property = _rna_property_for_path(target, override.data_path)
                if getattr(rna_property, "is_readonly", False):
                    raise TakePathError(
                        f"RNA property is read-only: {override.data_path}"
                    )
                current = read_path_value(target, override.data_path)
                runtime_prop_type, runtime_subtype, snapshot = classify_value(
                    current,
                    rna_property,
                )
                if not _normalized_runtime_matches_override(
                    override,
                    runtime_prop_type,
                    runtime_subtype,
                    snapshot,
                    value,
                ):
                    runtime_snapshot = _runtime_write_snapshot(
                        target,
                        override.data_path,
                        snapshot,
                    )
                    write_path_value(target, override.data_path, value)
                    applied_plans.append(
                        (entry, target, snapshot, runtime_snapshot)
                    )
                    if write_journal is not None:
                        write_journal.append(runtime_snapshot)
                    wrote_live_value = True
                elif write_journal is not None:
                    # A synchronous render handler may mutate a property even
                    # when applying this take was a no-op. Record the concrete
                    # current location so the outer batch transaction can
                    # restore that post-apply mutation as well.
                    write_journal.append(
                        _runtime_write_snapshot(
                            target,
                            override.data_path,
                            snapshot,
                        )
                    )
            except (
                MissingReferenceError,
                TakePathError,
                UnsupportedValueError,
                ReferenceError,
            ) as exc:
                report.skipped += 1
                report.issues.append(_issue_for(entry, override, exc))
                for (
                    rollback_entry,
                    rollback_target,
                    rollback_value,
                    _runtime_snapshot,
                ) in reversed(applied_plans):
                    rollback_override = rollback_entry.override
                    try:
                        write_path_value(
                            rollback_target,
                            rollback_override.data_path,
                            rollback_value,
                        )
                    except (TakePathError, ReferenceError) as rollback_exc:
                        report.issues.append(
                            _issue_for(
                                rollback_entry,
                                rollback_override,
                                TakePathError(
                                    f"Rollback failed: {rollback_exc}"
                                ),
                            )
                        )
                for dependency_snapshot in render_dependency_snapshots:
                    try:
                        _restore_runtime_write(dependency_snapshot)
                    except (TakePathError, ReferenceError) as rollback_exc:
                        report.issues.append(
                            _issue_for(
                                entry,
                                override,
                                TakePathError(
                                    "Render-setting rollback failed for "
                                    f"'{dependency_snapshot.data_path}': "
                                    f"{rollback_exc}"
                                ),
                            )
                        )
                report.applied = 0
                raise TakeApplyError(report) from exc

    report.applied = len(plans)
    scene.take_system.active_take_uuid = take.uuid
    _sync_active_index(scene.take_system)
    if wrote_live_value:
        _mark_scene_mutated(scene)
    return report


def apply_take(
    scene,
    take_uuid=None,
    strict=False,
    *,
    _write_journal=None,
):
    """Resolve and apply one take.

    ``strict=True`` is atomic: every entry is preflighted, and an unexpected
    assignment failure rolls earlier writes back. ``strict=False`` is a repair
    mode that applies valid entries and reports broken ones.
    """

    main = ensure_main_take(scene)
    requested = take_uuid or scene.take_system.active_take_uuid or main.uuid
    take = find_take(scene, requested)
    if take is None:
        raise TakeHierarchyError(f"Take does not exist: {requested}")

    resolved = resolve_take(scene, take.uuid)
    if strict:
        return _apply_take_atomic(
            scene,
            take,
            resolved,
            write_journal=_write_journal,
        )
    if _write_journal is not None:
        raise TakeSystemError(
            "Runtime write journaling requires strict take application"
        )

    report = ApplyReport(take_uuid=take.uuid, take_name=take.name)
    wrote_live_value = False
    with _applying_guard():
        for entry in _ordered_resolved_entries(resolved):
            override = entry.override
            try:
                target = _target_for_override(override)
                value = decoded_override_value(override)
                rna_property = _rna_property_for_path(
                    target,
                    override.data_path,
                )
                if getattr(rna_property, "is_readonly", False):
                    raise TakePathError(
                        f"RNA property is read-only: {override.data_path}"
                    )
                current_matches = False
                try:
                    current = read_path_value(target, override.data_path)
                    (
                        runtime_prop_type,
                        runtime_subtype,
                        normalized_current,
                    ) = classify_value(current, rna_property)
                    current_matches = _normalized_runtime_matches_override(
                        override,
                        runtime_prop_type,
                        runtime_subtype,
                        normalized_current,
                        value,
                    )
                except (
                    MissingReferenceError,
                    TakePathError,
                    UnsupportedValueError,
                    ReferenceError,
                    RuntimeError,
                    SystemError,
                    OverflowError,
                    TypeError,
                    ValueError,
                ):
                    # Repair mode should not lose its ability to overwrite a
                    # currently malformed/unsupported value. A failed read or
                    # normalization merely means that no-op status is unknown;
                    # let the normal setter provide the structured result.
                    current_matches = False
                if not current_matches:
                    write_path_value(target, override.data_path, value)
                    wrote_live_value = True
                report.applied += 1
            except (
                MissingReferenceError,
                TakePathError,
                UnsupportedValueError,
                ReferenceError,
            ) as exc:
                report.skipped += 1
                report.issues.append(_issue_for(entry, override, exc))

    scene.take_system.active_take_uuid = take.uuid
    _sync_active_index(scene.take_system)
    if wrote_live_value:
        _mark_scene_mutated(scene)
    return report


def safe_take_output_name(name, take_uuid=""):
    """Return a portable filename component derived from a take name."""

    cleaned = _INVALID_FILENAME_CHARACTERS.sub("_", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if cleaned in {"", ".", ".."}:
        suffix = (take_uuid or new_uuid())[:8]
        cleaned = f"Take_{suffix}"
    cleaned = cleaned[:80].rstrip(" .")
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_FILENAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def derive_batch_output_path(
    base_path,
    explicit_path,
    take_name,
    take_uuid="",
):
    """Derive one still-render filepath without expanding Blender ``//``."""

    safe_name = safe_take_output_name(take_name, take_uuid)
    explicit = str(explicit_path or "").strip()
    if explicit:
        if explicit.endswith(("/", "\\")):
            return f"{explicit}{safe_name}"
        return explicit

    base = str(base_path or "").strip() or "//"
    if base.endswith(("/", "\\")):
        return f"{base}{safe_name}"
    root, extension = os.path.splitext(base)
    if extension:
        return f"{root}_{safe_name}{extension}"
    return f"{base}_{safe_name}"


def _output_collision_key(path):
    raw_path = str(path)
    try:
        expanded = bpy.path.abspath(raw_path)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        expanded = raw_path
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    normalized = os.path.normpath(expanded)
    return os.path.normcase(normalized).replace("\\", "/").casefold()


def _rendered_output_collision_key(path, file_extension, use_file_extension):
    rendered_path = str(path)
    extension = str(file_extension or "")
    if (
        use_file_extension
        and extension
        and not rendered_path.casefold().endswith(extension.casefold())
    ):
        rendered_path = f"{rendered_path}{extension}"
    return _output_collision_key(rendered_path)


def _append_output_suffix(path, suffix):
    root, extension = os.path.splitext(path)
    if extension:
        return f"{root}_{suffix}{extension}"
    return f"{path}_{suffix}"


def _unique_batch_output_path(
    path,
    take_uuid,
    used_paths,
    *,
    file_extension="",
    use_file_extension=False,
):
    candidate = path
    key = _rendered_output_collision_key(
        candidate,
        file_extension,
        use_file_extension,
    )
    if key not in used_paths:
        used_paths.add(key)
        return candidate

    suffix = (take_uuid or new_uuid())[:8]
    candidate = _append_output_suffix(path, suffix)
    counter = 2
    key = _rendered_output_collision_key(
        candidate,
        file_extension,
        use_file_extension,
    )
    while key in used_paths:
        candidate = _append_output_suffix(path, f"{suffix}_{counter}")
        counter += 1
        key = _rendered_output_collision_key(
            candidate,
            file_extension,
            use_file_extension,
        )
    used_paths.add(key)
    return candidate


def resolved_scene_value(scene, take_uuid, data_path):
    resolved = resolve_take(scene, take_uuid)
    scene_pointer = _safe_id_pointer(scene)
    for entry in resolved.values():
        override = entry.override
        try:
            target = override.target_id
        except ReferenceError:
            target = None
        if (
            _safe_id_pointer(target) == scene_pointer
            and override.data_path == data_path
        ):
            return decoded_override_value(override)
    return read_path_value(scene, data_path)


def _build_batch_queue(scene, take_uuids=None):
    rows = take_hierarchy_rows(scene)
    by_uuid = {row.take.uuid: row for row in rows}
    if take_uuids is None:
        requested_uuids = [
            row.take.uuid
            for row in rows
            if row.take.include_in_render
        ]
    else:
        requested_uuids = list(take_uuids)
        if len(requested_uuids) != len(set(requested_uuids)):
            raise TakeHierarchyError(
                "The batch queue contains the same take more than once"
            )

    queue = []
    for take_uuid in requested_uuids:
        row = by_uuid.get(take_uuid)
        if row is None:
            raise TakeHierarchyError(
                f"Batch take does not exist: {take_uuid}"
            )
        if row.issue:
            raise TakeHierarchyError(
                f"Take '{row.take.name}' cannot render: {row.issue}"
            )
        queue.append(
            BatchQueueEntry(
                take_uuid=row.take.uuid,
                take_name=row.take.name,
                explicit_output_path=row.take.render_output_path,
            )
        )
    if not queue:
        raise TakeSystemError("No takes are included in batch rendering")
    return tuple(queue)


def _preflight_resolved_take(scene, entry):
    resolved = resolve_take(scene, entry.take_uuid)
    for resolved_entry in _ordered_resolved_entries(resolved):
        override = resolved_entry.override
        _target_for_override(override)
        decoded_override_value(override)
        split_final_path(override.data_path)


def _validate_renderable_camera(scene, take_name):
    try:
        camera = scene.camera
    except (AttributeError, ReferenceError):
        camera = None
    if not isinstance(camera, bpy.types.Object) or camera.type != "CAMERA":
        raise TakeSystemError(
            f"Take '{take_name}' has no valid Camera object"
        )
    return camera


def _validate_still_format(scene, take_name):
    try:
        file_format = scene.render.image_settings.file_format
    except (AttributeError, ReferenceError) as exc:
        raise TakeSystemError(
            f"Take '{take_name}' has no readable image format"
        ) from exc
    if file_format == "FFMPEG":
        raise TakeSystemError(
            f"Take '{take_name}' uses FFMPEG; Phase 5 batch rendering is "
            "still-image only"
        )
    return file_format


def _mandatory_batch_snapshots(scene):
    """Snapshot values whose setters can mutate sibling render properties."""

    paths = (
        "frame_current",
        "frame_subframe",
        "camera",
        *_CORE_RENDER_SETTING_PATHS,
    )
    snapshots = []
    seen = set()
    for data_path in paths:
        if data_path in seen:
            continue
        seen.add(data_path)
        try:
            snapshots.append(_runtime_write_snapshot(scene, data_path))
        except (
            MissingReferenceError,
            TakePathError,
            UnsupportedValueError,
            ReferenceError,
        ):
            # Feature detection already narrows normal preset capture. A
            # build-specific optional restore path must not prevent batching.
            continue
    return snapshots


def _restore_batch_runtime(scene, write_journal, mandatory_snapshots):
    issues = []
    with _applying_guard():
        # Restore frame evaluation before replaying concrete take writes; a
        # frame change can otherwise immediately overwrite animated values
        # that the journal just restored.
        for snapshot in mandatory_snapshots:
            if snapshot.data_path not in {"frame_current", "frame_subframe"}:
                continue
            try:
                _restore_runtime_write(snapshot)
            except (TakeSystemError, ReferenceError) as exc:
                issues.append(
                    f"{snapshot.target_name}.{snapshot.data_path}: {exc}"
                )
        for snapshot in reversed(write_journal):
            try:
                _restore_runtime_write(snapshot)
            except (TakeSystemError, ReferenceError) as exc:
                issues.append(
                    f"{snapshot.target_name}.{snapshot.data_path}: {exc}"
                )
        # Restore dependency-changing render controls in their capture order:
        # engine, format, color mode/depth, then the remaining settings.
        for snapshot in mandatory_snapshots:
            if snapshot.data_path in {"frame_current", "frame_subframe"}:
                continue
            try:
                _restore_runtime_write(snapshot)
            except (TakeSystemError, ReferenceError) as exc:
                issues.append(
                    f"{snapshot.target_name}.{snapshot.data_path}: {exc}"
                )
    if write_journal or mandatory_snapshots:
        _mark_scene_mutated(scene)
    return issues


def _restore_batch_identity(
    scene,
    original_active_uuid,
    original_selected_uuid,
    original_override_index,
):
    issues = []
    state = scene.take_system
    if find_take(scene, original_active_uuid) is None:
        issues.append("The originally applied take no longer exists")
        fallback = find_take(scene, state.main_take_uuid)
        state.active_take_uuid = fallback.uuid if fallback is not None else ""
    else:
        state.active_take_uuid = original_active_uuid

    if find_take(scene, original_selected_uuid) is None:
        if original_selected_uuid:
            issues.append("The originally selected take no longer exists")
        _sync_selected_index(state, state.active_take_uuid)
    else:
        _sync_selected_index(state, original_selected_uuid)

    selected = selected_take(scene)
    maximum = max(0, len(selected.overrides) - 1) if selected else 0
    restored_index = min(max(0, original_override_index), maximum)
    if restored_index != original_override_index:
        issues.append("The originally selected override no longer exists")
    state.active_override_index = restored_index
    return issues


def _batch_exception_text(exc):
    if isinstance(exc, TakeApplyError) and exc.report.issues:
        return exc.report.issues[0].summary()
    return str(exc) or type(exc).__name__


def _preflight_batch_apply(
    scene,
    queue,
    original_active_uuid,
    original_selected_uuid,
    original_override_index,
):
    journal = []
    mandatory = _mandatory_batch_snapshots(scene)
    failure = None
    output_metadata = {}
    try:
        for queue_entry in queue:
            apply_take(
                scene,
                queue_entry.take_uuid,
                strict=True,
                _write_journal=journal,
            )
            _validate_renderable_camera(scene, queue_entry.take_name)
            _validate_still_format(scene, queue_entry.take_name)
            output_metadata[queue_entry.take_uuid] = (
                scene.render.file_extension,
                bool(scene.render.use_file_extension),
            )
    except Exception as exc:
        failure = exc
    restore_issues = _restore_batch_runtime(scene, journal, mandatory)
    restore_issues.extend(
        _restore_batch_identity(
            scene,
            original_active_uuid,
            original_selected_uuid,
            original_override_index,
        )
    )
    return failure, restore_issues, output_metadata


def render_take_batch(scene, render_callback, take_uuids=None):
    """Synchronously render queued takes and restore the exact live state.

    ``render_callback`` receives ``(scene, BatchRenderItem)``. It may return
    Blender's operator result set; any result without ``FINISHED`` is treated
    as cancellation. Files written before a failure are intentionally reported
    but cannot be rolled back.
    """

    if not callable(render_callback):
        raise TakeSystemError("A synchronous render callback is required")
    ensure_main_take(scene)
    queue = _build_batch_queue(scene, take_uuids)
    report = BatchRenderReport(queued=len(queue))

    try:
        for entry in queue:
            _preflight_resolved_take(scene, entry)
    except Exception as exc:
        report.error = _batch_exception_text(exc)
        raise BatchRenderError(report) from exc

    raw_outputs = {}
    try:
        for entry in queue:
            base_path = resolved_scene_value(
                scene,
                entry.take_uuid,
                "render.filepath",
            )
            output_path = derive_batch_output_path(
                base_path,
                entry.explicit_output_path,
                entry.take_name,
                entry.take_uuid,
            )
            if output_path.startswith("//") and not bpy.data.filepath:
                raise TakeSystemError(
                    "Save the .blend file or use an absolute batch output "
                    "path before rendering"
                )
            raw_outputs[entry.take_uuid] = output_path
    except Exception as exc:
        report.error = _batch_exception_text(exc)
        raise BatchRenderError(report) from exc

    state = scene.take_system
    original_active_uuid = state.active_take_uuid
    selected = selected_take(scene)
    original_selected_uuid = selected.uuid if selected is not None else ""
    original_override_index = state.active_override_index

    (
        failure,
        preflight_restore_issues,
        output_metadata,
    ) = _preflight_batch_apply(
        scene,
        queue,
        original_active_uuid,
        original_selected_uuid,
        original_override_index,
    )
    if failure is not None or preflight_restore_issues:
        if failure is not None:
            report.error = _batch_exception_text(failure)
        report.restoration_issues.extend(preflight_restore_issues)
        report.restored = not report.restoration_issues
        raise BatchRenderError(report) from failure

    used_outputs = set()
    planned_outputs = {}
    for entry in queue:
        file_extension, use_file_extension = output_metadata.get(
            entry.take_uuid,
            ("", False),
        )
        planned_outputs[entry.take_uuid] = _unique_batch_output_path(
            raw_outputs[entry.take_uuid],
            entry.take_uuid,
            used_outputs,
            file_extension=file_extension,
            use_file_extension=use_file_extension,
        )

    journal = []
    mandatory = _mandatory_batch_snapshots(scene)
    failure = None
    current_entry = None
    try:
        for current_entry in queue:
            apply_report = apply_take(
                scene,
                current_entry.take_uuid,
                strict=True,
                _write_journal=journal,
            )
            _validate_renderable_camera(scene, current_entry.take_name)
            _validate_still_format(scene, current_entry.take_name)
            output_path = planned_outputs[current_entry.take_uuid]
            with _applying_guard():
                if _journaled_write_path(
                    scene,
                    "render.filepath",
                    output_path,
                    journal,
                ):
                    _mark_scene_mutated(scene)
            item = BatchRenderItem(
                take_uuid=current_entry.take_uuid,
                take_name=current_entry.take_name,
                output_path=output_path,
                applied_overrides=apply_report.applied,
            )
            callback_result = render_callback(scene, item)
            if callback_result is False or (
                isinstance(callback_result, set)
                and "FINISHED" not in callback_result
            ):
                raise TakeSystemError(
                    f"Render was cancelled for '{current_entry.take_name}'"
                )
            report.rendered.append(item)
    except Exception as exc:
        failure = exc
        report.error = _batch_exception_text(exc)
        if current_entry is not None:
            report.failed_take_uuid = current_entry.take_uuid
            report.failed_take_name = current_entry.take_name
    finally:
        report.restoration_issues.extend(
            _restore_batch_runtime(scene, journal, mandatory)
        )
        report.restoration_issues.extend(
            _restore_batch_identity(
                scene,
                original_active_uuid,
                original_selected_uuid,
                original_override_index,
            )
        )
        report.restored = not report.restoration_issues

    if failure is not None or report.restoration_issues:
        raise BatchRenderError(report) from failure
    return report


def override_value_as_text(override):
    """Compact value formatting for diagnostics and the Take Manager UI."""

    try:
        value = decoded_override_value(override)
    except MissingReferenceError:
        return "<missing datablock>"
    if isinstance(value, bpy.types.ID):
        return f"{_id_type_name(value)}: {_id_display_name(value)}"
    if isinstance(value, tuple):
        return "(" + ", ".join(f"{component:.6g}" for component in value) + ")"
    return repr(value)


_ID_COLLECTIONS = {
    "ACTION": "actions",
    "ARMATURE": "armatures",
    "BRUSH": "brushes",
    "CACHEFILE": "cache_files",
    "CAMERA": "cameras",
    "COLLECTION": "collections",
    "CURVE": "curves",
    "FONT": "fonts",
    "GREASEPENCIL": "grease_pencils",
    "IMAGE": "images",
    "KEY": "shape_keys",
    "LATTICE": "lattices",
    "LIBRARY": "libraries",
    "LIGHT": "lights",
    "LIGHTPROBE": "lightprobes",
    "MASK": "masks",
    "MATERIAL": "materials",
    "MESH": "meshes",
    "META": "metaballs",
    "MOVIECLIP": "movieclips",
    "NODETREE": "node_groups",
    "OBJECT": "objects",
    "PALETTE": "palettes",
    "PAINTCURVE": "paint_curves",
    "PARTICLESETTINGS": "particles",
    "SCENE": "scenes",
    "SOUND": "sounds",
    "SPEAKER": "speakers",
    "TEXT": "texts",
    "TEXTURE": "textures",
    "VOLUME": "volumes",
    "WORLD": "worlds",
    "WORKSPACE": "workspaces",
}


def find_id_by_name(id_type, name, library_path=""):
    """Resolve an explicit type/name chosen by the user-facing path operator."""

    normalized = (id_type or "").replace("_", "").replace(" ", "").upper()
    collection_name = _ID_COLLECTIONS.get(normalized)
    collection = getattr(bpy.data, collection_name, None) if collection_name else None
    if collection is None:
        raise TakeSystemError(f"Unsupported Blender ID type: {id_type}")

    candidates = [candidate for candidate in collection if candidate.name == name]
    if library_path:
        normalized_library = bpy.path.abspath(library_path)
        candidates = [
            candidate
            for candidate in candidates
            if _id_library_path(candidate)
            and bpy.path.abspath(_id_library_path(candidate)) == normalized_library
        ]
    else:
        local_candidates = [
            candidate for candidate in candidates if not _id_library_path(candidate)
        ]
        if local_candidates:
            candidates = local_candidates

    if not candidates:
        raise TakeSystemError(f"{id_type} datablock not found: {name}")
    if len(candidates) > 1:
        raise TakeSystemError(
            f"More than one {id_type} is named '{name}'; specify a library path"
        )
    return candidates[0]
