# Blender Take System — Phases 1–6

This package is an installable Blender 4.0+ addon implementing the persistent
take data model, manual override capture/resolve/apply engine, and dockable Take
Manager. Version `0.6.1` adds an inherited render-profile editor directly to
each applied take. Main/current settings remain the default; a child can
independently override engine/sampling, resolution, output/format, film
transparency, or color management. Version 0.6.0's automatic recording and the
Phase 5 camera, batch controls, and transactional still rendering remain fully
supported.

Phase 5 retains v0.4.2's fast per-View-Layer collection enabled-state tracking:
dirty-datablock indexes, direct cached LayerCollection readers, event-triggered
topology discovery, and changed-only take application. It also retains Phase
4's validated Main-to-leaf inheritance, conflicting overrides at multiple
depths, sibling isolation, and atomic hierarchy resolution.

## Install

Install `blender_take_system_v0_6_1.zip` with:

1. **Edit → Preferences → Add-ons → Install from Disk**
2. Select the ZIP.
3. Enable **Scene: Take System** if Blender does not enable it automatically.

Main is bootstrapped for every existing scene when the addon is enabled,
repaired after a `.blend` is loaded, and added to newly created scenes by a
lightweight recurring lifecycle check. The recent-action tracker is built
eagerly only for scenes currently displayed in a Blender window; other scenes
initialize it lazily when displayed or when they receive a relevant
dependency-graph update.

Opening v0.4.x take data upgrades its schema in place without replacing existing
takes or overrides. Version 0.6.1 keeps persistent schema 2; render profiles
remain ordinary override records and automatic-record
snapshots and status are runtime-only, while the resulting ordinary overrides
save in the `.blend`. The batch-output field starts blank and **Include in
Batch** defaults on, including for existing takes, so review those toggles before
starting the first render queue.

## Open the Take Manager

The manager lives in Blender's existing Properties editor so it can occupy any
area in a split workspace:

1. Change an area's editor type to **Properties**.
2. Open **Scene Properties**.
3. Expand **Take Manager**.

For a shortcut, press **F3** and run **Open Take Manager in Current Area**. This
changes the area where the command was invoked to Properties and opens its Scene
context. Split or join that area using Blender's normal workspace controls to
place the manager wherever you want.

Blender's Python API does not let a regular addon register a new editor `Space`
in the editor-type picker. Embedding the manager in Properties provides the
same dockable-area behavior while remaining an installable Python addon.

The list has separate **selected** and **applied** states. Clicking a row only
selects the take for inspection and hierarchy commands; it deliberately does
not change scene values. Use the row's apply control or **Apply Selected Take**
to apply it as one undoable operation. The manager shows both states so
inspection cannot silently switch the scene.

### Phase 4 inheritance behavior

Applying a take resolves its chain in deterministic order:

```text
Main → parent → child → selected descendant
```

Each take contributes its direct overrides. When more than one level stores the
same target and RNA path, the deepest take wins; unrelated parent overrides
remain inherited. Main owns the baseline for every overridden key, so applying
Main or switching to a sibling restores values from the previous branch instead
of leaking them across takes.

The resolver rejects missing parents, cycles, non-Main overrides without a Main
baseline, duplicate logical property records, and stable override identities
that point at different datablocks. These integrity checks finish before any
scene value is written. Lifecycle bootstrap repairs missing or duplicate UUIDs,
restores the canonical Main flag/root, and reconnects parentless non-Main takes
to Main before normal resolution. Reparenting changes the ancestry used by the
next resolve/apply operation, while the override records themselves stay scoped
to their original takes. Public take creation validates that its proposed
parent already reaches Main—even for non-active scripting calls—so it cannot
extend an orphaned or cyclic branch.

### Camera and inherited render profiles

Expand **Selected Take Camera & Render Settings** below Take Manager. Camera
and profile controls are enabled for the applied take; selecting a different
row for inspection is not enough. The output icon on each applied take row opens
the same render-profile editor.

Press **Configure Camera...**, choose a Camera object in the dialog, and
confirm. The camera is stored as a normal `Scene.camera` override, so it uses
the same Main-to-leaf resolution as every other property. A child without a
direct camera inherits its nearest ancestor's camera. **Inherit** removes the
direct record and reapplies the resolved parent value. Clearing the dialog's
Camera field stores `None`, which is useful for a non-rendering variant, but a
take without a valid Camera object cannot enter the render queue.

The panel displays both the resolved camera and which take supplies it. The
`camera_override` field on each take is convenience UI metadata; the ordinary,
auditable `Scene.camera` override record remains authoritative for apply,
inheritance, save/reload, and rendering.

Press **Edit Render Profile...**. Main displays every group as the inherited
default profile. On a child, all groups initially inherit; enable only the
groups that should be direct on that take. The dialog uses Blender's native
render controls, so its enum choices and engine-specific ranges remain valid.
Pressing Apply stores the enabled groups atomically and removes direct records
for groups switched back off. Cancelling restores the exact pre-dialog live
settings and writes no override records.

The independently inherited groups are:

- **Engine & Sampling** — render engine; Cycles maximum/minimum adaptive
  samples, threshold, and denoising; compatible Eevee sampling; or Workbench
  shading controls;
- **Resolution & Frame** — width, height, percentage, pixel aspect, frame rate,
  and frame-rate base;
- **Output & Format** — Scene output, batch-output override, extension,
  overwrite/placeholder controls, image format, color mode/depth, compression,
  and quality;
- **Film Transparency** — transparent background;
- **Color Management** — view transform, look, exposure, and gamma.

The values live when the editor opens are the trusted inherited baseline. If a
child enables a group for the first time, those values seed Main and the edited
values become the child records. Existing Main records are never replaced by a
child edit. **Inherit All Render Groups** removes every direct render-profile
record and clears the take's batch-output override. A legacy non-empty Output
Override is recognized as a direct Output group when the file is opened in
v0.6.1.

The portable core preset feature-detects and captures:

- render engine, resolution, percentage, pixel aspect, frame rate, and frame
  rate base;
- Scene output path, file-extension/overwrite/placeholder options, image format,
  color mode/depth, compression, and quality where writable;
- film transparency and view transform, look, exposure, and gamma;
- available engine-specific controls: Cycles maximum/minimum samples,
  denoising, adaptive sampling, and threshold; compatible Eevee render samples; or
  Workbench lighting, color, shadow, cavity, and specular controls.

Blender-version or engine-specific properties that do not exist or cannot be
stored are omitted. Switching engines in the editor exposes and stores the
supported settings for the newly active engine. Because these are
ordinary overrides, deeper takes win for conflicting values while all other
render settings remain inherited and visible in the override inspector.

### Transactional batch still rendering

Expand **Batch Render Takes**. For each take, select its row and set **Include
in Batch** plus the optional **Output Override**, then press **Render Included
Takes**. Included takes run synchronously in displayed hierarchy order. This is
a still-image queue: Blender's UI remains occupied until it finishes, animation
rendering is not included, and an `FFMPEG` output is rejected.

Output paths are planned before the first render:

- A non-empty Output Override is used exactly when it is a filename. If it ends
  in `/` or `\`, a portable, sanitized take name is appended.
- When Output Override is blank, the take's resolved `Scene Output` is used as
  the base. A take name is appended to a directory or extensionless base; when
  the base has an extension, `_<take name>` is inserted before it.
- Planned paths that would collide receive a stable short take-UUID suffix.
  Blender still controls whether its selected image-format extension is added
  through **File Extensions**.
- Blender-relative `//` paths are preserved. A `.blend` must be saved before
  such a path can render; otherwise use an absolute Output Override.
- Missing parent directories for a planned output are created immediately
  before that still is rendered.

For example, a blank override for take `Red CMF` derives
`//renders/beauty_Red CMF.png` from `//renders/beauty.png`, while an explicit
`D:\shots\red.exr` remains exactly `D:\shots\red.exr`.

Before writing any file, the batch renderer validates every queued hierarchy
and override, strictly applies every queued take as a dry preflight, confirms a
valid camera and still-image format, and restores the starting state. The real
render pass then journals every take assignment. On success, cancellation, an
apply error, or a render error, it restores:

- every live RNA value written by take application, including camera and
  render settings;
- dependent render controls and the original `Scene.render.filepath`;
- the originally applied take, selected take row, and selected override index.

This restores unsaved live edits that existed before the batch instead of merely
reapplying the originally active take. Restoration failures are reported
explicitly. Image files successfully written before a later failure are
external side effects and cannot be rolled back or undone; the error reports
how many renders completed.

### Collection enabled-state overrides

The Outliner's **Exclude from View Layer** checkbox is supported as a take
override. Blender stores this UI state as `LayerCollection.exclude`, whose
Boolean meaning is inverted from the checkbox:

- Checked/enabled in the Outliner means `exclude == False`.
- Unchecked/disabled in the Outliner means `exclude == True`.

The Take Manager's override inspector displays **Enabled** or **Disabled**
instead of exposing that inverted raw Boolean. Each View Layer has its own
LayerCollection tree, so the same Collection can be enabled in one View Layer
and disabled in another. If a Collection is linked in multiple branches, each
occurrence is addressed independently by its full branch path.

For example, a nested state is stored against the Scene with a path shaped like:

```text
view_layers["ViewLayer"].layer_collection.children["Parent"].children["CMF"].exclude
```

To capture manually, apply a non-Main take, right-click the collection's
enabled-state checkbox before changing it, and choose **Add/Update Take
Override**. Toggle the checkbox, then repeat the same capture command. You can
also toggle one or more collection checkboxes and use **Apply Most Recent
Action as Overrides**; the recent-action tracker discovers LayerCollections in
every View Layer.

When the active LayerCollection is disabled, Blender may move the active
LayerCollection selection to its parent. Recent-action capture compares cached
LayerCollection occurrences, so that context change does not lose the toggle;
the resulting persistent override still retains the full Scene path.

Only the enabled state is overridden—not Collection linking, parenting, or
contents. The View Layer and nested collection names form the persistent RNA
path. Renaming a View Layer or Collection, or changing that branch's topology,
can invalidate a stored path. Apply then fails safely and atomically rather
than guessing another collection; inspect/remove the broken record and capture
the renamed or moved occurrence again.

### Capture the most recent action

When a non-Main take is selected and applied, the manager shows **Apply Most
Recent Action as Overrides**. Make a supported property change, then press this
button. The addon stores the value that existed when the take was applied on
Main and the new value on the child. A multi-property transform can be captured
as one atomic, undoable batch.

The tracker is runtime-only and never rewinds Blender's undo history. It watches
object transforms and visibility, existing custom properties and override paths,
writable modifier/material/camera/light/world values, object-linked material
slots, per-View-Layer collection enabled states, and material/world/light node
input defaults. If the target was deleted, the path changed again, or any member
of a batch became invalid, the whole capture is cancelled without partial
records.

Blender does not expose a generic last-action property diff. Geometry edits,
object/modifier/node creation or deletion, selection/navigation, properties
currently controlled by animation or drivers, frame-evaluated changes, and
ambiguous DATA-linked material-slot assignment are deliberately not treated as
retroactive value overrides. Use the existing right-click or RNA path capture
for an unsupported property.

### Automatic recording

Apply a non-Main take, then click its red record dot or press **Start
Recording** in Take Manager. Version 0.6.0 reuses the same supported-property
discovery and capture engine as **Apply Most Recent Action as Overrides**. It
does not introduce a second override format or inheritance path.

While recording is active:

- supported edits are grouped for `0.45` seconds and committed as one atomic
  override batch;
- the value present when recording began is stored as the trusted Main baseline
  when that logical property is first captured;
- repeated changes to an existing take override update that record rather than
  creating duplicates;
- the status box reports a pending group, the most recently captured group, or
  a failure;
- **Commit Pending** immediately closes the current group, and stopping
  recording or saving the `.blend` force-commits any pending supported edit.

Dependency-graph observation remains the authoritative indexed change source.
Blender's RNA message bus provides low-cost wakeups for common properties; if a
message-bus signal is not followed by a dependency-graph callback, the timer
performs one fallback observation. This preserves the existing changed-only and
large-collection performance paths instead of polling every property
continuously.

Recording deliberately stops when another take is applied, after undo/redo, and
after a file is loaded. Reapplying the same take keeps recording active and
refreshes the observation baseline. Frame changes temporarily defer observation
so animation evaluation is not mistaken for a user edit. Add-on operations
commit any pending user group first, run under the existing internal-write
guards, and then rebaseline, so take application, rendering, bootstrap, and
other programmatic writes do not record themselves.

If any member of a group is invalid at commit time, no member is written.
Recording stops, the tracker rebaselines, and Take Manager displays the error.
The supported and deliberately ignored property categories are identical to the
manual recent-action workflow above. Automatic recording is a convenience over
the generic override engine, not a universal Blender undo or geometry-diff
system.

### Performance behavior in v0.4.2

Persistent collection overrides still use full, auditable Scene RNA paths.
During one Blender session, recent-action tracking additionally caches the
corresponding LayerCollection occurrences and reads their Boolean `exclude`
values directly. Reverse indexes restrict ordinary dependency-graph callbacks
to properties associated with the dirty datablocks. Collection-tree paths and
broad property discovery are rebuilt during steady-state observation only
after a relevant structural event, such as link/unlink, rename, reparent, View
Layer changes, or newly supported scene content. Initial tracking,
load/undo/redo, take-system writes, and explicit capture synchronization also
perform the required validation or rebaseline.

In the Blender 5.2 benchmark, a Scene-dirty observation with 5,000
LayerCollection occurrences dropped from about `574.14 ms` in v0.4.1 to
`13.13 ms` in v0.4.2 (43.7× faster). Forced full validation dropped from
`558.46 ms` to `28.23 ms`, and full rebaseline from `754.28 ms` to
`195.52 ms`. Unrelated object updates remained below 1 ms in that fixture.
There is no longer an unconditional five-second discovery pass.

The normal Scene-dirty collection route is still linear in the number of
LayerCollection occurrences because Blender identifies the Scene as dirty
rather than reporting the exact checkbox. It now performs a compact direct
Boolean scan, not thousands of long RNA path resolutions. Genuine topology
events remain more expensive because persistent paths must be rebuilt and
validated.

## Manual workflow

1. In **Take Manager**, add a top-level take or add a child below the selected
   take. The new take is selected and applied.
2. Before changing a property for the first time, right-click its UI control and
   choose **Add/Update Take Override**. This stores the inherited value on the
   child and creates the required Main baseline.
3. Change the property.
4. Right-click it again and choose **Add/Update Take Override** to store the
   child value.
5. Select another row in the manager and use its apply control, or press **F3**
   and run **Go to Take**.
6. Expand the selected take's override inspector to audit or remove its direct
   overrides.
7. Use the manager toolbar to duplicate, delete, or reparent non-Main takes.
   Deleting a take adopts its direct children into the deleted take's parent.
8. For a quicker workflow, make a supported change on the applied child and
   press **Apply Most Recent Action as Overrides** instead of manually arming
   and recapturing each changed property.
9. For collection variants, expose the Outliner's enabled-state restriction
   column, then use the same right-click workflow on its checkbox. Remember that
   this state belongs to the current View Layer, not globally to the Collection.
10. For a take-specific camera, keep that take applied, press **Configure
    Camera...** in **Selected Take Camera & Render Settings**, and choose the
    Camera object in the dialog.
11. Press the applied row's output icon or **Edit Render Profile...**, enable
    only the render groups that differ on that take, edit them, and Apply.
12. Set **Include in Batch** for each desired take. Set **Batch Output
    Override** in the profile's Output group or the Batch Render panel when a
    take should not derive its destination from Scene Output.
13. Save the `.blend` when using `//` paths, then press **Render Included
    Takes**. The batch returns to the exact pre-render live scene and manager
    state after all included stills finish.

For the Phase 6 workflow, replace steps 2–4 or step 8 with **Start Recording**,
make one or more supported edits, wait for the status to report the capture, and
then stop recording. Use **Commit Pending** when an action must be stored
immediately. Selecting a row does not redirect recording; only the currently
applied non-Main take is eligible.

Blender does not expose a generic previous-value history. If step 2 is skipped
and the first capture happens only after editing, the addon cannot reconstruct
the old Main value. In that case, switch to Main, restore/capture the intended
base value, then recapture the child.

Quick interactive smoke check:

1. Create `Parent → Child → Grandchild` below Main.
2. Store conflicting `Location` values on Parent, Child, and Grandchild; store
   visibility only on Child and a material only on Parent.
3. Apply Grandchild. Confirm its location wins while Child visibility and the
   Parent material are inherited.
4. Apply Parent, Child, and Main in turn and confirm each level resolves to its
   own value without retaining values from the previously applied descendant.
5. Create and apply a sibling below Main and confirm the prior branch does not
   leak into it.
6. Undo once after a switch and confirm the entire take application undoes as
   one operation. This button-context/undo check requires interactive Blender;
   the background suite covers the underlying operators and engine.

For controls that do not expose button context, use **F3 → Capture Take
Override by RNA Path**. Examples:

```text
Object / Cube / location
Object / Cube / hide_render
Object / Cube / material_slots[0].material
Object / Cube / modifiers["Bevel"].width
Camera / Camera / lens
Material / Paint / node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value
Object / Cube / ["finish_code"]
```

Right-click capture generates Blender-safe paths automatically, including
escaped node and modifier names.

## Supported through v0.6.1

- Main-rooted, UUID-linked arbitrary take hierarchies
- Stable UUID snapshots across Blender CollectionProperty growth and
  take duplication/deletion
- Dockable Properties → Scene → Take Manager hierarchy
- Separate selected and applied take states
- Main/current render settings as the inherited default profile
- Per-take render-profile dialog using Blender's native controls
- Independently inheritable engine/sampling, resolution, output/format,
  transparency, and color-management groups
- Cycles maximum/minimum adaptive samples, threshold, and denoising where
  supported
- Transactional profile Apply/Cancel with legacy batch-output compatibility
- Opt-in automatic recording on the applied non-Main take
- Atomic 0.45-second action grouping with automatic Main-baseline seeding
- Message-bus wakeups plus indexed dependency-graph observation and a guarded
  fallback scan
- Fail-closed recording lifecycle across take switches, undo/redo, frame
  evaluation, load, save, and add-on-internal writes
- Hierarchical per-take `Scene.camera` overrides with direct/inherited source
  display and explicit `None` support
- Feature-detected, portable render-setting presets stored through ordinary
  deepest-child-wins Scene overrides
- Per-take batch inclusion and explicit-or-derived collision-safe output paths
- Synchronous still rendering with whole-queue preflight, strict take apply,
  valid-camera/format checks, and missing output-directory creation
- Transactional restoration of take-written live values, dependent render
  settings, output path, and applied/selected Take Manager identity on success
  or failure
- Add-top-level, add-child, duplicate, delete, and cycle-safe reparent commands
- Direct per-take override inspection and removal
- Atomic capture of the most recent supported property action, with automatic
  Main-baseline seeding
- Deepest-child-wins inheritance resolution
- Persistent target and pointer values using real Blender ID references
- Float, integer, Boolean, string, enum, 2–4 component vector/color, and ID
  pointer values, including intentional `None`
- Exact payloads for double-precision custom floats/arrays and dedicated
  integer/Boolean vector storage
- Object transforms, visibility, custom properties, material slots, modifier
  parameters, material node inputs, camera data, and other compatible RNA paths
- Per-View-Layer collection enabled states, including nested and multiply-linked
  LayerCollection occurrences
- Indexed, event-gated recent-action tracking with direct runtime collection
  readers and no periodic broad discovery scan
- Changed-only take application that preserves exact value/type semantics and
  avoids redundant RNA setters and dependency-graph invalidation
- Main-baseline seeding so sibling switching restores unrelated properties
- Atomic apply from UI operators; the Python API also offers non-strict repair
  mode that applies valid records and reports broken ones
- Save/reload persistence and target identity across datablock renames
- Missing target/path diagnostics without silently rebinding by name

The core scripting API is available as:

```python
import bpy
from blender_take_system import engine

scene = bpy.context.scene
take = engine.create_take(scene, "Red CMF")
engine.capture_override(scene, bpy.context.object, "location", take.uuid)
engine.configure_take_camera(scene, take.uuid, bpy.data.objects["Camera"])
baseline = engine.snapshot_render_profile(scene)
scene.render.resolution_x = 1080
scene.render.resolution_y = 1080
engine.configure_render_profile(
    scene,
    take.uuid,
    {engine.RENDER_GROUP_RESOLUTION},
    baseline_values=baseline,
)
take.include_in_render = True
take.render_output_path = "//renders/"
report = engine.apply_take(scene, take.uuid, strict=True)
resolved = engine.resolve_take(scene, take.uuid)

# The UI operator supplies Blender's synchronous write-still callback.
bpy.ops.take_system.render_included_takes()
```

## Blender limitations and deliberate deferrals

- Capturing `active_material` or `material_slots[i].material` on a non-Main
  take automatically stores a companion slot-link override: Main preserves
  `DATA`, while the child uses `OBJECT`. This prevents distinct object CMFs on
  a shared Mesh from collapsing to the last material written.
- A material node value is shared by every object using that Material. True
  per-object shader parameters require separate materials or shader indirection,
  which would violate the no-duplication premise.
- Material slots are located by index; modifiers/nodes are commonly located by
  name. Reordering slots or renaming/removing path elements leaves an auditable
  broken override rather than guessing a replacement.
- LayerCollection enabled state is stored as the inverse `exclude` property on
  a Scene-relative, name-based View Layer/collection-tree path. Renaming a View
  Layer or Collection, or reparenting/relinking that occurrence, leaves an
  auditable broken override. The root LayerCollection cannot be disabled.
- Animated or driven values may be overwritten on evaluation or frame changes.
- Applying a take skips RNA assignments whose exact stored value and type
  already match the live property. Scripts that intentionally rely on a
  same-value setter callback should request an explicit View Layer/dependency
  update instead of using take reapplication as an evaluation trigger.
- Linked/read-only datablocks may reject assignment.
- Nested custom-property groups, enum-flag sets, matrices, arrays longer than
  four components, indexed single-component paths, Collection linking/parenting
  structure edits, geometry edits, and non-ID pointer values are outside the
  current release.
- The generic pointer fields are strong Blender ID references. This preserves
  identity across renames and saves, but an ordinarily deleted/unlinked object
  can remain as an orphan datablock while overrides target it, and orphan purge
  cannot remove it until those override records are removed. Use the Take
  Manager's override inspector to audit and remove those records.
- Some operator-backed or virtual UI controls expose no writable RNA button
  context; use the explicit path operator for those.
- The render-profile dialog deliberately covers the portable, commonly varied
  render controls listed above. Engine-specific settings outside that curated
  set can still be captured through automatic recording, right-click capture,
  or an explicit RNA path when their value type is supported.
- Blender does not expose a universal property-diff event. Automatic recording
  therefore has the same curated property coverage as recent-action capture;
  geometry/structure edits, selection/navigation, and animated or driven
  evaluation are ignored.
- A quiet-period timer commit does not create its own named Blender undo entry.
  Undo/redo stops recording and rebaselines to prevent accidental recapture.
  Use **Commit Pending** when an explicit operator/undo boundary is important.
- Phase 5 batch rendering is synchronous and still-image only. It does not queue
  animation/video renders in the background, and `FFMPEG` is rejected.
- Rendered files and newly created output directories are outside Blender's
  undo system. The add-on restores scene state after failure but cannot delete
  or roll back files that were already written; Blender's overwrite setting
  still governs existing outputs.
- Transactional restoration covers every resolved take-override path plus the
  active camera, frame, and captured render-setting group. A third-party render
  handler that deliberately edits an unrelated property outside those paths is
  outside the transaction and remains that handler's responsibility.
- Every included take must resolve to a valid Camera object when batch rendering,
  even though `None` remains a valid stored camera override for variants that
  are not included.

JSON exchange and drag-and-drop are not included. Take thumbnails/previews are
deliberately deferred as a stretch goal; version 0.6.1 performs no preview
generation or background thumbnail maintenance.

The example script
`C:\Codex_Playpen\blender-take-system\examples\take_system_phase_1_2_demo.py` creates three cubes
and Main/Red/Blue takes. Main is white with base transforms and visibility; Red
lifts Cube 1 and hides Cube 3 at render; Blue lowers Cube 1 and hides Cube 2 in
the viewport.

## Automated verification

From PowerShell, the runner rebuilds the ZIP and uses the newest installed
Blender automatically:

```powershell
& 'C:\Codex_Playpen\blender-take-system\tests\run_take_system_tests.ps1'

# Or select a particular Blender executable:
& 'C:\Codex_Playpen\blender-take-system\tests\run_take_system_tests.ps1' `
  -BlenderPath 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'

# Or run individual lanes:
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_core_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_collection_state_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_recording_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_phase5_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_render_profile_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_batch_render_test.py'

& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' `
  --background --factory-startup --python-exit-code 1 `
  --python 'C:\Codex_Playpen\blender-take-system\tests\take_system_persistence_test.py'
```

The addon declares Blender 4.0+ compatibility. Automated runs have passed in
Blender 5.1.2 and Blender 5.2.0 LTS; the declared minimum still needs a real
Blender 4.x CI/manual lane. The all-tests script runs core, changed-only apply,
CollectionProperty/UUID stability, hierarchy, camera/render/batch, granular
render profiles, operator, Take Manager, collection-state, recent-action,
automatic-recording, tracker performance/behavior, save/reload, and isolated
packaged-install functional lanes. Render-profile coverage exercises Main
defaults, partial groups, parent/child inheritance, minimum/maximum sampling,
legacy output metadata, cancellation, and injected rollback failure. Phase 6
coverage exercises eligibility, delayed and forced commits,
grouped transforms, repeated updates, message-bus fallback, frame suppression,
failure shutdown, save handling, same-take reapply, take switching, undo/redo,
load, and teardown. Phase 5 coverage exercises camera inheritance, portable
preset capture, output derivation/collision handling, whole-queue preflight,
partial-render reporting, and exact restoration after success and injected
failures. The
packaged-install lane independently builds a multi-level hierarchy, verifies
deepest-wins resolution, round-trips per-View-Layer collection and Phase 5
state, verifies redundant-write suppression, and checks broken paths fail
atomically from the installed ZIP.
