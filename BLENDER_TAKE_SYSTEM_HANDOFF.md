# Blender Take System - Phase 6 Engineering Handoff

Last updated: 2026-07-27

This is the self-contained project handoff for the Blender Take System add-on in
`C:\Codex_Playpen\blender-take-system`. It is written so a new Codex chat can continue development
without the previous conversation.

Suggested first message in a new chat:

> Continue the Blender Take System project described in
> `C:\Codex_Playpen\blender-take-system\BLENDER_TAKE_SYSTEM_HANDOFF.md`. Read that file, the
> original product prompt, and the add-on README before changing code. Preserve
> the Phase 6 invariants and run the relevant Blender background tests. Use the
> Blender MCP connection for live inspection when available; do not use desktop
> control unless I explicitly authorize it in the new chat.

## Executive summary

The project is a real installable Blender Python add-on modeled on Cinema 4D's
Take Manager. It lets one Blender Scene contain a Main take plus an arbitrary
hierarchy of child takes. A take stores property-value overrides without
duplicating the scene. Applying a take resolves Main-to-leaf inheritance and
writes the deepest value for each logical property.

Phases 1 through 6 are complete:

1. Persistent scene-local data model and Main bootstrap.
2. Generic manual property capture and apply/resolve engine.
3. Dockable Take Manager UI in Properties > Scene.
4. Arbitrary parent/child inheritance with integrity validation.
5. Inherited camera/render-setting overrides and transactional still batch
   rendering.
6. Opt-in automatic recording for supported edits on the applied non-Main
   take.

Features added along the way include:

- a runtime "Apply Most Recent Action as Overrides" workflow;
- automatic 0.45-second action grouping over that same tracker and override
  engine;
- independently inherited v0.6.2 render-profile groups edited through Blender's
  native controls;
- per-View-Layer collection enabled/disabled overrides using
  `LayerCollection.exclude`;
- a full performance pass for large LayerCollection trees;
- separate selected and applied take states;
- exact typed storage, including persistent Blender ID pointers and exact float
  payloads;
- changed-only application to avoid redundant RNA setter/depsgraph work.

The next planned milestone is the Phase 7/stretch backlog: JSON exchange,
ordering improvements, and opt-in take previews/thumbnails.

Current release:

- Add-on version: `0.6.2`
- Persistent schema: `2`
- Declared Blender support: `4.0.0+`
- Actually tested: Blender `5.1.2` and `5.2.0`
- Release ZIP:
  `C:\Codex_Playpen\blender-take-system\dist\blender_take_system_v0_6_2.zip`
- ZIP SHA-256:
  `7FB000AA3CB1E189DF5ED70F271ED9F4131D50E8ACD6089275B9C23C3FBF3DFF`

## Documentation audit

The code is well documented at the user/workflow and critical-invariant levels,
but it is not yet a complete formal developer reference.

What is strong:

- `README.md` is detailed and current through v0.6.2. It covers installation,
  UI access, hierarchy behavior, automatic recording, inherited render
  profiles, camera/render workflows, transactional batch behavior, collection
  states, recent-action capture, performance, limitations, scripting examples,
  and tests.
- Every production module has a module-level description.
- The important engine entry points and the difficult logic have docstrings or
  explanatory comments. This includes hierarchy validation, path
  canonicalization, embedded datablocks, atomic apply, render dependency
  ordering, runtime write journaling, rollback, collection occurrence paths,
  tracker invalidation, and migration.
- Blender operators and panels have descriptive labels/tooltips, and most
  operator classes have docstrings.
- Background tests are scenario-oriented and use descriptive assertion
  messages. The batch suite includes real rendering as well as injected
  failure/cancellation paths.

Remaining documentation weaknesses:

- Before this file, there was no single maintainer-oriented architecture and
  release handoff.
- Some small public helpers do not have docstrings, including lookup/value
  utilities such as `find_take`, `active_take`, `read_path_value`,
  `decoded_override_value`, and `resolved_scene_value`.
- `engine.py` and `recent.py` are large. Their internal comments are useful, but
  the modules would benefit from future subdivision and typed interface
  boundaries.
- There is no generated API reference, formal semantic-versioning/API-stability
  promise, changelog, or architecture decision record.
- Blender 4.x is declared but still lacks a real automated/manual validation
  lane.

Practical verdict: the project is documented well enough to maintain safely
when paired with this handoff and the tests. It is not yet documented like a
public SDK whose every callable is a supported external API.

## Product intent and scope

The original request is in:

`C:\Codex_Playpen\blender-take-system\blender_take_system_addon_prompt.md`

Primary production use cases:

- CMF/material variants without scene duplication;
- object transform and visibility variants;
- modifier, custom property, shader input, light, camera, and compatible RNA
  property variants;
- camera setups;
- render configuration variants;
- per-take still rendering.

Explicit non-goals/current deferrals:

- geometry-level edits;
- duplicating objects/materials as the override mechanism;
- background render-queue UI parity with Cinema 4D;
- animation/video batch rendering;
- JSON exchange, drag-and-drop reordering, and thumbnails until a later phase.

## Repository and artifact map

| Path | Purpose |
| --- | --- |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\__init__.py` | Add-on metadata, registration, handlers, timer, migration bootstrap, hot reload, teardown |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\model.py` | Persistent Blender `PropertyGroup` schema |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\engine.py` | Hierarchy, paths, typed storage, capture, resolve/apply, camera/render helpers, batch transaction |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\recent.py` | Runtime-only recent-action diff tracker and large-scene collection performance path |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\recording.py` | Phase 6 eligibility, message-bus wakeups, quiet-period commits, lifecycle reconciliation, runtime status |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\operators.py` | Undoable UI operators, capture menu hook, camera/render dialogs, synchronous render callback |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\ui.py` | Properties-editor UI lists and panels |
| `C:\Codex_Playpen\blender-take-system\blender_take_system\README.md` | User guide, behavior reference, limitations, test instructions |
| `C:\Codex_Playpen\blender-take-system\tests\` | Blender background integration/regression tests and package builder |
| `C:\Codex_Playpen\blender-take-system\examples\take_system_phase_1_2_demo.py` | Small Main/Red/Blue example scene script |
| `C:\Codex_Playpen\blender-take-system\dist\blender_take_system_v0_6_2.zip` | Current installable release |

The package builder deliberately includes only:

- `__init__.py`
- `model.py`
- `engine.py`
- `recent.py`
- `recording.py`
- `operators.py`
- `ui.py`
- `README.md`

## Architecture at a glance

```text
Blender Scene
  |
  +-- Scene.take_system (persistent PropertyGroup)
  |     |
  |     +-- Main take
  |     +-- child takes linked by UUID
  |            |
  |            +-- typed override records
  |
  +-- Blender RNA values/datablocks

UI panels and operators
  |
  +-- engine.py ---------------- capture / resolve / apply / batch transaction
  |
  +-- recent.py ---------------- runtime observation -> capture_change_batch()

Lifecycle handlers in __init__.py
  |
  +-- bootstrap/migrate Main and schema
  +-- rebaseline or invalidate runtime tracker state
  +-- suppress frame/undo/apply noise
```

The architectural rule to preserve is that `engine.py` owns behavior and has no
UI dependency. Operators and panels call it. Tests can therefore exercise the
same API in Blender background mode.

## Persistent data model

All core take data is stored in the `.blend` through
`Scene.take_system`. No external sidecar is required.

### `TS_PG_TakeSystem`

- `schema_version`: currently `2`.
- `main_take_uuid`: stable UUID of the canonical Main take.
- `takes`: collection of `TS_PG_Take`.
- `active_take_uuid`: take currently applied to live scene values.
- `active_take_index`: UI-selected row index. Despite its historical name, it
  is selection state, not the authoritative applied state.
- `active_override_index`: selected override in the inspector.

### `TS_PG_Take`

- Blender-provided `name`.
- `uuid`: stable identity.
- `parent_uuid`: UUID link; empty only for Main.
- `is_main`: canonical Main marker.
- `is_recording`: persisted flag identifying the currently armed take. Load,
  undo/redo, take switches, and teardown clear it fail-closed; runtime snapshots
  and status remain outside the `.blend`.
- `include_in_render`: Phase 5 batch toggle, default `True`.
- `render_output_path`: optional per-take batch filepath/directory override.
- `overrides`: direct records owned by this take.
- `use_camera_override` and `camera_override`: convenience UI metadata.
  Canonical camera state is an ordinary `Scene.camera` override.

### `TS_PG_TakeOverride`

Identity:

- `uuid`: record identity.
- `target_ref_uuid`: stable logical target/path identity shared across hierarchy
  levels.
- `target_id`: strong pointer to the owning Blender ID.
- diagnostic target type/name/library strings.
- `data_path`: RNA path relative to `target_id`.

Value storage:

- `FLOAT`, `INT`, `BOOL`, `STRING`, `ENUM`, `VECTOR`, `COLOR`, or `POINTER`.
- parallel Blender properties hold the payload selected by `prop_type`.
- float and float-array text fields hold hexadecimal payloads for exact
  double-precision round trips.
- integer and Boolean vectors have dedicated storage.
- pointer storage preserves a real Blender ID or intentional `None`, plus
  diagnostic type/name/library metadata.

The strong Blender ID references preserve identity across renames and saves.
They can also keep otherwise unused datablocks alive until the override record
is removed.

## Core invariants

Changes must preserve these invariants:

1. Every Scene has exactly one canonical Main root.
2. Main is named `Main`, has `is_main == True`, and has no parent.
3. Every non-Main take must reach Main through valid UUID parents.
4. Hierarchy cycles and missing parents are rejected.
5. Every logical property overridden below Main must have a Main baseline.
6. A shared `target_ref_uuid` cannot silently change to another target/path.
7. Duplicate logical property records on one take are rejected.
8. Resolution validates the whole chain before strict application writes.
9. Selection does not apply a take. Applied identity is
   `active_take_uuid`; selected identity is derived from the list index.
10. `Scene.camera` and render settings use the generic override engine rather
    than a second inheritance system.
11. User-facing apply operators use strict atomic apply.
12. Batch rendering restores the pre-batch live values, not merely the stored
    values of the previously active take.

`ensure_main_take(scene)` is both bootstrap and repair/migration logic. It fixes
missing/duplicate UUIDs, restores Main's canonical role, reconnects parentless
non-Main takes to Main, syncs camera metadata, and raises schema to at least
version 2 without downgrading a future schema.

## Hierarchy and resolution

Hierarchy is stored by UUID because Blender `PropertyGroup` collection elements
cannot safely self-reference as ordinary Python objects.

Important hierarchy API:

```python
from blender_take_system import engine

main = engine.ensure_main_take(scene)
take = engine.create_take(scene, "Variant", parent_uuid=main.uuid)
copy = engine.duplicate_take(scene, take.uuid)
engine.reparent_take(scene, copy.uuid, take.uuid)
engine.delete_take(scene, copy.uuid)
rows = engine.take_hierarchy_rows(scene)
chain = engine.take_chain(scene, take.uuid)
```

`resolve_take()` walks:

```text
Main -> parent -> child -> requested descendant
```

It builds a dictionary keyed by stable logical override identity. Later/deeper
records replace earlier values for the same key; unrelated parent values remain
inherited. Resolution also validates Main baselines and target identity.

`delete_take()` adopts the deleted take's direct children into its parent.
`duplicate_take()` creates a sibling with new take/override identities while
copying only the original take's direct records.

## Capture workflows

### Manual right-click capture

The button context menu hook adds **Add/Update Take Override** to compatible
writable RNA controls.

For a non-Main take, the intended first-time workflow is:

1. Apply the child.
2. Capture the property before changing it.
3. Change it.
4. Capture again.

The first capture seeds Main with the current inherited value and creates/updates
the child's direct record. The second capture updates the child's value.

Blender exposes no generic historical previous value. If the user edits before
the first manual capture, the add-on cannot reconstruct the old Main value.

For controls without usable button context, run **Capture Take Override by RNA
Path** and provide target ID type/name/path.

### Recent-action capture

`recent.py` maintains runtime-only snapshots while a take is applied. Pressing
**Apply Most Recent Action as Overrides** converts the most recent supported
change group into an atomic persistent override batch.

The action window is `0.45` seconds, allowing multi-property transforms to group
as one action. Capture verifies that targets and values still match. If any
member became invalid, the whole capture is cancelled.

The tracker supports, among other compatible writable paths:

- object transforms, visibility, display type, and ray visibility;
- existing custom properties;
- modifier, material, camera, light, and world properties;
- object-linked material slots;
- material/world/light node input defaults;
- each `LayerCollection.exclude` occurrence in every View Layer;
- existing override paths.

It deliberately does not infer geometry changes, structure creation/deletion,
selection/navigation, animated/driven values, or ambiguous DATA-linked material
slot assignment as retroactive value overrides.

### Phase 6 automatic recording

`recording.py` turns the same recent-action observations into an opt-in
continuous workflow. Only the applied non-Main take may record. The record dot
or **Start Recording** captures a trusted tracker baseline; each supported
change group is committed after the existing `0.45` second quiet window.

The dependency graph remains the authoritative indexed observer. Persistent RNA
message-bus subscriptions provide low-cost wakeups for common property types.
If Blender sends a message-bus signal without a matching dependency-graph
callback, the recurring recording timer performs one forced observation after
`0.075` seconds. It then returns to the indexed path.

Every commit delegates to `recent.capture_pending()` and
`engine.capture_change_batch()`, preserving one generic override table, Main
baseline seeding, exact values, prevalidation, and all-or-nothing capture.
Repeated edits update the same logical override. Runtime status records action
and property counts plus the latest summary/error; none of that status is
persistent.

Safety/lifecycle rules:

- take-system operations force-commit a pending user group, perform guarded
  internal writes, then rebaseline;
- reapplying the same take keeps recording armed;
- applying another take clears the prior record flag and stops that session;
- frame changes defer observation, while undo/redo and file load stop every
  recorder and rebuild safe baselines;
- save-pre force-commits pending supported edits before serialization;
- any capture/validation failure writes no partial batch, disables recording,
  rebaselines, and exposes the error in Take Manager.

Timer-driven commits cannot create their own named Blender undo entries. The
explicit **Commit Pending** operator provides an operator/undo boundary when one
is required. Undo/redo always stops recording so restored scene state is never
captured back into the take.

### Collection enabled state

The Outliner's collection enabled checkbox maps to the inverse Blender value:

- enabled/checked -> `LayerCollection.exclude == False`
- disabled/unchecked -> `LayerCollection.exclude == True`

Each occurrence is stored as a Scene-relative path such as:

```text
view_layers["ViewLayer"].layer_collection.children["Parent"].children["CMF"].exclude
```

This preserves separate View Layer state and multiple links of one Collection.
The UI displays **Enabled**/**Disabled**, hiding the inverted Boolean.

View Layer names, Collection names, and branch topology are part of the path.
Renaming/relinking/reparenting can intentionally leave an auditable broken
record; strict apply fails instead of guessing a new occurrence.

## RNA path and value engine

The path engine reads through `path_resolve()` and writes by splitting the final
attribute/item token, resolving its parent, and using `setattr` or indexed
assignment. It never uses `eval` or `exec`.

`canonicalize_id_path()` anchors embedded node trees at their storable owning
Blender ID. For example, a socket value is stored against its Material with a
path beginning `node_tree...`, because Blender cannot persist an embedded node
tree in the generic ID pointer field.

Supported values:

- floats with exact IEEE-754 payload preservation;
- integers and Booleans without Python cross-numeric equality mistakes;
- strings and RNA enum identifiers;
- 2-4 component float/integer/Boolean vectors and colors;
- Blender ID pointers, including explicit `None`.

Current exclusions include matrices, arrays longer than four, nested custom
property groups, enum-flag sets, non-ID pointer values, and indexed
single-component paths.

Material-slot capture includes special logic. When a child needs a distinct
material on an object sharing Mesh data, a companion slot-link override keeps
Main on `DATA` and the child on `OBJECT`; otherwise multiple object CMFs could
collapse to the last shared Mesh material assignment.

## Apply behavior

Primary API:

```python
report = engine.apply_take(scene, take_uuid, strict=True)
resolved = engine.resolve_take(scene, take_uuid)
```

Strict mode:

- validates/decode-plans all resolved records first;
- orders dependency-changing paths before paths that depend on them;
- writes changed values only;
- records exact prior values;
- rolls back earlier writes if a later assignment fails;
- raises `TakeApplyError` with an `ApplyReport`.

Repair mode (`strict=False`):

- applies valid records;
- skips broken records;
- returns structured issues;
- is meant for diagnosis/repair scripting, not normal take switching.

Changed-only application compares stored type/subtype and exact normalized
value. It avoids redundant RNA setters and dependency-graph invalidation.
Positive/negative zero and float payload semantics are handled deliberately;
Boolean and integer equality is not conflated.

Render properties are ordered so the engine, format, color mode/depth, view
transform, and look establish their dependent RNA state before subordinate
values are assigned.

`engine.is_applying()` exposes an apply guard so runtime trackers and the Phase
6 recorder ignore programmatic take writes.

## Camera and render settings

### Camera

`engine.configure_take_camera(scene, take_uuid, camera)` stores an explicit
ordinary override for `Scene.camera`.

- A Camera object is valid.
- `None` is a valid stored value for a non-rendering variant.
- A child without a direct record inherits the nearest ancestor camera.
- `remove_take_camera()` removes only the direct record and reapplies inherited
  state.
- `resolved_camera()` returns `(camera, source_take_uuid)`.
- `camera_override` on the take exists for UI convenience only.

The camera selection operator uses a dynamic Enum instead of an Object
`PointerProperty`, because Blender operator properties cannot use the desired
generic Object pointer behavior reliably.

### Inherited render profile

Version 0.6.1 replaced the two-step preset UI with
`take_system.edit_render_profile`. The underlying
`capture_render_settings()` API remains for compatibility. Both paths store
ordinary Scene overrides; there is no parallel render inheritance system.

Main/current values are the default. Child takes start with every group
inherited and may enable only the groups they need:

- `ENGINE_SAMPLING`;
- `RESOLUTION`;
- `OUTPUT`;
- `TRANSPARENCY`;
- `COLOR_MANAGEMENT`.

`snapshot_render_profile()` detaches every available live profile value before
the dialog opens. `configure_render_profile()` receives that trusted baseline,
stores enabled groups, removes direct records for disabled groups, clears a
disabled batch-output override, and strictly reapplies the active take in one
transaction. `restore_render_profile()` provides dependency-ordered Cancel and
failure restoration. A child never replaces an existing Main baseline.

Portable core:

- render engine;
- X/Y resolution and percentage;
- pixel aspect;
- FPS and FPS base;
- output filepath;
- file extension, overwrite, and placeholder toggles;
- image format, color mode/depth, compression, and quality;
- film transparency;
- view transform, look, exposure, and gamma.

Engine-specific where available:

- Cycles: maximum/minimum samples, denoising, render-denoiser selection,
  adaptive sampling, threshold, and Transparent Glass.
- Eevee/Eevee Next: compatible render sample field.
- Workbench: lighting, color mode, shadows, cavity, specular highlight.

The v0.6.2 UI workflow is:

1. Apply/select the take.
2. Click the row's Output icon or **Edit Render Profile...**.
3. On a child, enable only the groups that should be direct.
4. Edit Blender's native render controls and Apply.
5. Disable a group to inherit it, or use **Inherit All Render Groups**.

Cancel restores the pre-dialog live settings without persistent writes.
Opening the profile editor stops automatic recording after committing any
pending action, preventing dialog staging changes from recording themselves.
A legacy non-empty `render_output_path` is treated as a direct Output group.

## Transactional batch still rendering

Public engine seam:

```python
report = engine.render_take_batch(scene, render_callback)
```

The callback receives `(scene, BatchRenderItem)`. The UI supplies a synchronous
callback that:

- creates the output parent directory if needed;
- updates the View Layer when possible;
- calls:

```python
bpy.ops.render.render(
    "EXEC_DEFAULT",
    write_still=True,
    scene=scene.name,
)
```

Queue order is displayed hierarchy order. By default, takes with
`include_in_render == True` are queued. A caller can pass an explicit UUID list.

The transaction has four stages:

1. Build and validate the queue and resolve paths.
2. Derive output paths, reject unsaved `//` paths, then strictly apply every
   queued take as a dry preflight. Validate every camera and still format before
   any render file is written.
3. Plan collision-safe final paths, including collisions introduced by
   Blender's automatic file extension.
4. Strictly apply and render each take while journaling every concrete RNA
   write location. In a `finally` block, restore live values and Take Manager
   identity.

Output rules:

- a non-empty take output override is used exactly if it looks like a filename;
- if it ends in `/` or `\`, a sanitized take name is appended;
- otherwise the resolved `Scene.render.filepath` is the base;
- a take name is appended to directory/extensionless bases;
- for a base with an extension, `_<take name>` is inserted before it;
- collisions receive a stable short take-UUID suffix;
- Blender-relative `//` paths remain relative and require a saved `.blend`.

The runtime write journal includes actual prior values even for an apply no-op.
That matters because a synchronous render handler can mutate a value after the
take apply. Mandatory snapshots also cover frame/subframe, camera, and core
render settings whose setters can mutate sibling properties.

After success, cancellation, apply failure, render failure, or callback
exception, the engine attempts to restore:

- every live path written by take application;
- original frame/subframe, camera, and core render settings;
- original `Scene.render.filepath`;
- originally applied take UUID;
- originally selected take row;
- originally selected override index.

It restores unsaved live edits that existed before the batch. It does not
merely reapply the originally active take.

Limitations:

- still images only; `FFMPEG` is rejected;
- synchronous rendering occupies Blender until the queue completes;
- every included take must resolve to a valid Camera object;
- rendered files and newly created directories cannot be rolled back;
- a failure report includes completed items and restoration issues;
- unrelated values deliberately modified by third-party render handlers outside
  all resolved/mandatory paths are outside this transaction.

## UI and operator map

The add-on cannot register a new Blender editor `Space` from ordinary Python.
The Take Manager therefore lives in an existing **Properties** editor under
**Scene Properties**, which gives the requested dockable/splittable workflow.

Open it by:

- changing any area to **Properties**, choosing **Scene**, and expanding
  **Take Manager**; or
- pressing F3 and running **Open Take Manager in Current Area**.

Panels:

- `SCENE_PT_take_manager`: hierarchy and main take controls.
- `SCENE_PT_take_scene_settings`: selected take camera/render preset.
- `SCENE_PT_take_batch_render`: inclusion/output controls and render button.
- `SCENE_PT_take_overrides`: direct override inspector/removal.

Key operators:

| Blender operator | Purpose |
| --- | --- |
| `take_system.initialize` | Repair/bootstrap Scene state |
| `take_system.add_take` | Add top-level or child take |
| `take_system.apply_take` | Go to a specific take |
| `take_system.apply_active_take` | Reapply applied take |
| `take_system.apply_selected_take` | Apply inspected row |
| `take_system.duplicate_take` | Duplicate direct records as sibling |
| `take_system.delete_take` | Delete and adopt children |
| `take_system.reparent_take` | Cycle-safe parent change |
| `take_system.remove_override` | Remove one direct record |
| `take_system.open_manager` | Convert current area to Properties/Scene |
| `take_system.capture_recent_action` | Persist recent supported change group |
| `take_system.toggle_recording` | Start/stop automatic recording |
| `take_system.flush_recording` | Commit the pending recorded action immediately |
| `take_system.configure_take_camera` | Store direct `Scene.camera` value |
| `take_system.clear_take_camera` | Inherit camera |
| `take_system.edit_render_profile` | Edit independently inherited render groups |
| `take_system.capture_render_settings` | Initialize/update portable preset |
| `take_system.clear_render_settings` | Inherit render preset |
| `take_system.render_included_takes` | Synchronous transactional still queue |
| `take_system.capture_button_override` | Right-click property capture |
| `take_system.capture_path_override` | Explicit target/path capture |

Camera/render configuration is intentionally enabled only when the selected row
is also the applied take, preventing edits from being associated with an
inspected-but-unapplied variant.

## Registration, lifecycle, and migration

`__init__.py` registers:

- model PropertyGroups;
- all operators and panels;
- `Scene.take_system`;
- button-context menu entry;
- load, save-pre, dependency-graph, undo, redo, and frame-change handlers;
- lightweight recurring Scene bootstrap and recording timers;
- persistent RNA message-bus subscriptions used as recording wakeups.

Important lifecycle behavior:

- load repairs persistent data for all scenes and clears every recording flag;
- the comparatively expensive recent-action baseline is built eagerly only for
  scenes displayed in a window;
- non-displayed scenes initialize tracking lazily;
- undo/redo stops recording, discards cached RNA handles, and rebaselines
  displayed scenes;
- frame changes temporarily defer tracking to avoid evaluation noise;
- save-pre commits any pending supported recording group;
- message-bus subscriptions are restored after load because Blender clears them;
- the timer catches newly created Scenes because Blender has no dedicated
  scene-added handler;
- linked read-only scenes are skipped unless locally overridden.

Hot reload logic explicitly reloads submodules when Blender retains an older
package module during an in-place ZIP update. Teardown removes every handler,
timer, menu hook, registered class, Scene property, and runtime cache.

Schema 2 added Phase 5 persistent state. Phases 6 and the v0.6.2 render-profile
updates add no persistent fields or changed interpretations, so version 0.6.2
retains schema 2. Opening v0.4.x data
migrates in place:

- existing takes and overrides remain;
- camera convenience metadata is synchronized from canonical overrides;
- `render_output_path` has a safe blank default;
- `include_in_render` uses its default enabled state;
- future schema numbers are not silently downgraded.

Any future persistent field or changed interpretation requires an explicit
schema/migration decision and save/reload tests.

## Performance design

The main large-scene risk was scanning thousands of LayerCollection occurrences
on dependency-graph updates.

The optimized path in v0.4.2, retained by Phases 5 and 6, uses:

- cached direct LayerCollection handles/readers;
- per-datablock reverse indexes;
- compact direct Boolean scans when Blender reports only the Scene as dirty;
- structural signatures and event-gated topology discovery;
- tracker revision counters invalidated by Take System writes;
- no unconditional five-second broad rediscovery.

Measured Blender 5.2 fixture with 5,000 LayerCollection occurrences:

| Operation | Old | Optimized |
| --- | ---: | ---: |
| Normal Scene-dirty observation | ~574.14 ms | ~13.13 ms |
| Forced full validation | ~558.46 ms | ~28.23 ms |
| Full rebaseline | ~754.28 ms | ~195.52 ms |
| Unrelated object update | n/a | <1 ms |

Normal Scene-dirty collection checks remain linear in occurrence count because
Blender does not report the exact toggled checkbox. The optimized work is a
direct Boolean scan, not repeated resolution of thousands of long RNA paths.
Real topology changes are intentionally more expensive because persistent paths
must be rebuilt and validated.

Do not replace this with unconditional full-tree/path discovery in every
depsgraph callback.

## Test suite

Primary runner:

```powershell
& 'C:\Codex_Playpen\blender-take-system\tests\run_take_system_tests.ps1' `
  -BlenderPath 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
```

If no path is passed, the runner selects the newest installed Blender. It first
rebuilds/verifies the ZIP, then runs factory-startup background lanes:

1. `take_system_core_test.py`
2. `take_system_apply_noop_test.py`
3. `take_system_propertygroup_stability_test.py`
4. `take_system_hierarchy_test.py`
5. `take_system_operator_test.py`
6. `take_system_ui_test.py`
7. `take_system_collection_state_test.py`
8. `take_system_recent_action_test.py`
9. `take_system_recording_test.py`
10. `take_system_recent_perf_test.py`
11. `take_system_phase5_test.py`
12. `take_system_render_profile_test.py`
13. `take_system_batch_render_test.py`
14. `take_system_persistence_test.py`
15. isolated `take_system_install_test.py` against the built ZIP

The suite covers:

- generic path/value behavior and edge cases;
- exact changed-only/no-op semantics;
- UUID/CollectionProperty stability;
- arbitrary hierarchy inheritance and integrity failures;
- operators and Properties UI;
- nested and multiply-linked LayerCollection state;
- recent-action grouping/validation;
- automatic-record eligibility, delayed/grouped/forced commits, repeated
  updates, message-bus fallback, failure shutdown, and lifecycle stops;
- large-scene tracker behavior/performance;
- camera inheritance and render preset capture/removal;
- granular render-profile groups, Main baseline seeding, parent/child
  inheritance, minimum/maximum Cycles samples, render-denoiser selection,
  Transparent Glass, legacy output metadata, Cancel, and injected transactional
  rollback;
- output derivation and collision handling;
- whole-queue dry preflight;
- batch success, injected apply/render/cancellation failure, partial reports,
  and exact state restoration;
- a real 8x8 still PNG render;
- save/reload persistence;
- clean packaged install and functional use.

All lanes passed in Blender 5.1.2 during earlier releases and in Blender 5.2.0
for Phase 6 on 2026-07-27. The focused v0.6.2 render-profile lane also passed in
Blender 5.1.2 and 5.2.0. The package hash listed in this handoff was rechecked
on disk after the final v0.6.2 build. Blender 4.x remains a
declared-but-unverified compatibility target.

An isolated non-background Blender 5.2 GUI-context smoke test also verified the
real record-toggle operator, Undo shutdown/rebaseline, restart after Undo,
explicit **Commit Pending**, correct one-record Main/child capture, and Stop.
It used factory-startup data in a hidden process and did not open or mutate the
user's live `.blend`.

An isolated non-background Blender 5.2 smoke test also opened and drew the real
render-profile dialog, then verified Main default restoration and child-only
resolution/transparency application in a factory-startup scene.

Build the release ZIP independently with:

```powershell
& 'C:\Codex_Playpen\blender-take-system\tests\build_take_system_package.ps1'
```

The builder creates a temporary archive, verifies the exact entry list, hashes
every archived entry against its source file, and only then replaces the final
ZIP.

## Last verified live Blender state

During the Phase 5 rollout, a connected Blender 5.2 instance was updated through
Blender MCP from add-on v0.4.2 to v0.5.0 without desktop control.

At that time:

- schema migrated to `2`;
- there were 4 takes and 12 total overrides;
- an exact logical scene fingerprint was unchanged before/after update:
  `c72eda3138be672a68f08a47458038b3cc1b8e15c3368d861074fdbb69e4a839`;
- the test Scene contained one Cube and no Camera object;
- therefore Phase 5 batch rendering would correctly fail preflight until a
  valid Camera object was assigned/resolved.

This is historical verification, not a guarantee about the user's current open
file. A read-only MCP reconnection attempt made while preparing this handoff on
2026-07-26 could not connect. Reinspect the current Scene before any live
mutation.

When updating a live user Scene:

1. Prefer Blender MCP and execute small, inspectable Python steps.
2. Record add-on version, schema, take count, override count, take UUID/parent
   structure, active/selected identity, and a logical data fingerprint.
3. Install/reload the package.
4. Reinspect and compare before making feature-specific changes.
5. Do not treat a passing isolated test Scene as authorization to modify user
   content.

## Known Blender/API limitations

- Blender add-ons cannot add a true custom editor type to the editor picker, so
  the manager is a panel inside a dockable Properties area.
- Linked/read-only datablocks can reject writes.
- Stored names/indexes within RNA paths are not magical identities. Renamed
  modifiers/nodes/View Layers/Collections or reordered material slots can break
  a record safely and visibly.
- Material node values are shared by every object using that Material.
- Animated/driven values may overwrite take-applied values during evaluation.
- Strong pointer fields can retain orphan datablocks until override removal.
- Some virtual/operator-backed controls have no writable button RNA context.
- Recent-action capture is necessarily curated because Blender exposes no
  universal "last action property diff."
- Root LayerCollection exclusion is not supported.
- Batch output file side effects are outside Blender undo and transaction
  rollback.

## Phase 6 completion notes

Phase 6 implements the original automatic-recording request through
`recording.py`, `recent.py`, and the existing generic override engine. The key
design decision is immediate commit after the existing `0.45` second action
quiet period, with an explicit **Commit Pending** control for users who want to
close the group sooner.

The implementation preserves all reserved invariants:

- recording is opt-in and only valid for the applied non-Main take;
- programmatic apply/render/bootstrap writes, frame evaluation, undo/redo, and
  load cannot self-record;
- supported-property discovery, dirty indexes, detached values, validation, and
  `capture_change_batch()` are reused rather than duplicated;
- a trusted baseline is captured at start/reapply;
- every action group is prevalidated and persisted atomically with a Main
  baseline;
- take switches, undo/redo, load, save, and commit errors reconcile fail-closed;
- normal large-collection observation retains the optimized indexed/direct
  reader path;
- Take Manager reports pending, captured, stopped, and failure states.

The Phase 6 regression lane exercises applied/selected eligibility,
single-property and grouped transform recording, duplicate suppression,
message-bus fallback, Main immutability, failure atomicity, operator stop/flush,
save-pre commit, same-take reapply, take switching, frame suppression,
undo/redo, load, registration, and clean teardown. The existing recent-action
performance lane still passes unchanged.

Known undo boundary: timer callbacks cannot safely add an independent named
Blender undo step. **Commit Pending**, start/stop, and the other direct UI
commands remain Blender operators with `UNDO`; any undo/redo event stops the
recorder and rebaselines before observation resumes.

## Phase 7/stretch backlog

- JSON import/export with ID/path reconciliation diagnostics.
- Operator-based ordering improvements and later drag-and-drop if Blender's UI
  APIs permit a robust implementation.
- Take thumbnails/previews.

Thumbnail guidance:

- keep previews opt-in or explicitly refreshed at first;
- do not run hidden renders on every depsgraph update;
- store a cache key tied to take mutation/render-relevant revision;
- avoid persistent raw image bloat in the `.blend` until storage behavior is
  measured;
- isolate preview application/rendering with the same restoration discipline as
  batch render;
- decide whether previews represent viewport, Workbench, or final render output.

## Rules for future changes

1. Keep the generic override table as the single source of truth.
2. Do not create parallel inheritance engines for cameras, render settings,
   collection states, or recording.
3. Preserve Main baseline and stable target identity validation.
4. Preserve separate selected/applied states.
5. Use strict atomic apply for user-facing take switches and rendering.
6. Preserve exact type/value semantics and changed-only assignment.
7. Never weaken batch restoration to "reapply the old take."
8. Treat live `.blend` content as user data; fingerprint before/after add-on
   updates.
9. Use isolated factory-startup Blender tests before live-scene tests.
10. Add migration logic and save/reload coverage for persistent schema changes.
11. Rebuild and verify the installable ZIP after source changes.
12. Add a Blender 4.x validation lane before claiming verified 4.x support.
13. Prefer MCP for live Blender work. Desktop control needs fresh explicit
    authorization in the chat doing that work.

## Minimal scripting examples

Create, configure, and apply a take:

```python
import bpy
from blender_take_system import engine

scene = bpy.context.scene
main = engine.ensure_main_take(scene)
take = engine.create_take(scene, "Red CMF", parent_uuid=main.uuid)

engine.capture_override(
    scene,
    bpy.context.object,
    "location",
    take.uuid,
)

camera = bpy.data.objects.get("Camera")
if camera is not None:
    engine.configure_take_camera(scene, take.uuid, camera)

engine.capture_render_settings(scene, take.uuid)
take.include_in_render = True
take.render_output_path = "//renders/"

report = engine.apply_take(scene, take.uuid, strict=True)
print(report.take_name, report.applied, report.ok)
```

Inspect resolved inheritance without applying:

```python
resolved = engine.resolve_take(scene, take.uuid)
for entry in resolved.values():
    override = entry.override
    print(
        entry.take_name,
        override.target_id_name,
        override.data_path,
        engine.override_value_as_text(override),
    )
```

Use the UI batch operator:

```python
bpy.ops.take_system.render_included_takes()
```

Use the engine with a test/stub renderer:

```python
def render_callback(scene, item):
    print(item.take_name, item.output_path)
    return {"FINISHED"}

report = engine.render_take_batch(scene, render_callback)
```

## Definition of done for the next release

A future phase is not complete until:

- behavior is implemented through the established engine boundaries;
- all old regression lanes still pass;
- new failure and persistence cases have tests;
- a real interactive/manual smoke check covers UI-only behavior;
- README and this handoff reflect the new behavior and limitations;
- schema/version changes are deliberate and migrated;
- the ZIP is rebuilt, entry-verified, install-tested, and hashed;
- any connected user Scene is compared before/after without content loss.
