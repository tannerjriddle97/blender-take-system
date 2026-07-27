# Blender Take System - UI Overhaul Handoff

Last updated: 2026-07-27

This document hands the next Codex chat a focused product and engineering brief
for a complete Take Manager UI cleanup, with special attention to the confusing
batch-render workflow. It supplements, rather than replaces,
`BLENDER_TAKE_SYSTEM_HANDOFF.md`.

## Suggested first message in the next chat

> Continue the Blender Take System project in
> `C:\Codex_Playpen\blender-take-system`. Start by reading
> `UI_OVERHAUL_HANDOFF.md`, `BLENDER_TAKE_SYSTEM_HANDOFF.md`, and
> `blender_take_system\README.md`. The next task is a complete Take Manager UI
> cleanup, especially the batch-render workflow. Begin with a read-only audit of
> the current UI in code and in the connected Blender scene, propose a concrete
> information architecture and low-risk implementation sequence, then implement
> it on a feature branch. You may freely rearrange panels and controls, rename
> actions, and consolidate or remove redundant UI entry points when that is more
> intuitive. Preserve all engine, inheritance, recording, render transaction,
> and scene-safety invariants. Use Blender MCP for live inspection and
> validation; do not use desktop control unless I explicitly authorize it. Do
> not mutate my live scene merely to inspect or sketch the UI.

## User intent

The user's current assessment is:

- batch-render functionality and UI are messy and confusing;
- most of the add-on UI now needs a complete cleanup or overhaul;
- visual polish can follow sound workflow and information architecture;
- the next pass should be deliberate rather than a series of isolated button
  moves;
- the overhaul is explicitly authorized to rearrange the interface wherever a
  different structure is cleaner or more intuitive.

The next chat is not required to preserve the current panel grouping, control
order, hierarchy-row shortcuts, labels, or duplicated entry points. It may:

- move controls between panels;
- merge related sections;
- split an overloaded section;
- remove redundant shortcuts while retaining the underlying operation;
- rename buttons and headings for clarity;
- change which advanced panels start collapsed;
- replace icon-only actions with labels, menus, or selected-take actions.

This is permission to redesign the interface, not to silently change take
semantics, render behavior, persistent data, or safety guarantees.

The user is new to GitHub. Explain branches, commits, pushes, and releases in
plain language when they matter. The project may remain completely public, and
the user has no intention of profiting from it.

## Conversation and release summary

This project was brought onto the public GitHub repository:

`https://github.com/tannerjriddle97/blender-take-system`

The work completed in this chat includes:

1. GitHub authentication and initial public repository setup.
2. Phase 6 automatic take recording.
3. Batch-render diagnosis and regression coverage.
4. Version 0.6.1's inherited per-take render-profile editor:
   - Engine & Sampling;
   - Resolution & Frame;
   - Output & Format;
   - Film Transparency;
   - Color Management.
5. Version 0.6.2's focused render-profile additions:
   - native Cycles render-denoiser selection;
   - Cycles Transparent Glass.

Current release state:

- Main commit: `701e6a274c638de3672de73983c0b8702236526d`
- Release: `v0.6.2`
- Release page:
  `https://github.com/tannerjriddle97/blender-take-system/releases/tag/v0.6.2`
- ZIP:
  `C:\Codex_Playpen\blender-take-system\dist\blender_take_system_v0_6_2.zip`
- ZIP SHA-256:
  `7FB000AA3CB1E189DF5ED70F271ED9F4131D50E8ACD6089275B9C23C3FBF3DFF`
- Persistent schema: `2`
- Declared Blender support: `4.0+`
- Verified Blender versions: `5.1.2` and `5.2.0`

At the end of the v0.6.2 update, the add-on was active in:

`C:\Users\obbra\Desktop\TakeTest.blend`

The scene then contained 3 takes, 73 overrides, 3 objects, and 2 materials. A
comprehensive before/after fingerprint was identical when v0.6.2 was activated.
Do not assume those counts are still current; inspect them again read-only.

## Current UI inventory

The production UI is concentrated in:

- `blender_take_system\ui.py`
- dialog and action operators in `blender_take_system\operators.py`
- persistent per-take batch fields in `blender_take_system\model.py`
- queue, preflight, path derivation, and render transaction logic in
  `blender_take_system\engine.py`

The current Properties > Scene structure is:

1. **Take Manager**
   - separate Selected and Applied labels;
   - seven-row hierarchy list;
   - an apply radio icon, editable take name, override count, record button,
     camera indicator, render-profile button, and batch checkbox on every row;
   - Top-Level and Child creation buttons;
   - Duplicate, Delete, and Reparent buttons;
   - Apply Selected Take and Reapply;
   - a full Automatic Recording box;
   - a full Recent Action box;
   - selected-take details.
2. **Selected Take Camera & Render Settings**
   - resolved camera and ownership;
   - camera configuration/inheritance;
   - compact resolved render summary;
   - render-profile editor and Inherit All.
3. **Batch Render Takes**
   - only an included count;
   - Include in Batch and Output Override for the selected take;
   - one Render Included Takes button;
   - two warning labels.
4. **Selected Take Overrides**
   - direct override list;
   - capture-by-path action and right-click tip.

The render-profile editor is a native Blender dialog and is functionally sound.
Its layout can be revisited for consistency, but its transactional behavior
must remain intact.

## Why the current workflow feels confusing

These are evidence-based hypotheses from the current code and should be checked
against the live UI and the user's reactions before implementation:

- The main hierarchy row carries too many unlabeled icons with unrelated jobs.
- Selection and application are correctly separate internally but visually
  compete for attention, so it is easy to edit or render the wrong conceptual
  take.
- Primary actions, diagnostics, recording controls, and advanced metadata all
  appear at the same visual level.
- Camera, profile, batch inclusion, and batch output are split across row icons,
  child panels, and the render-profile dialog.
- Batch configuration is selected-take-centric while rendering is queue-centric.
  The user cannot see the whole queue that will run.
- Before pressing Render, the UI does not visibly answer:
  - Which takes will render, and in what order?
  - Which camera will each take use?
  - Where will each file be written?
  - Which settings are inherited?
  - Is each take valid and ready?
  - What will stop the whole queue?
- Output derivation and collision handling are robust in the engine but invisible
  to the user.
- The only final feedback is an operator report. There is no persistent queue
  result summary in the panel.
- The warning about irreversible files is accurate but disconnected from the
  concrete files about to be written.

## Product goals for the overhaul

The cleaned-up UI should let a first-time user understand the normal workflow
without reading the full manual:

1. Create or select a take.
2. Clearly see whether it is merely selected or actually applied.
3. Apply it when live editing is required.
4. Record/capture changes without competing controls.
5. Configure camera and render-profile ownership.
6. Build and inspect a render queue.
7. Resolve validation problems before committing to file output.
8. Render with clear progress/result feedback while preserving the live scene.

The UI should progressively disclose complexity:

- frequent actions are obvious;
- current state is more prominent than configuration history;
- destructive or irreversible actions are deliberate;
- advanced override records remain available but visually secondary;
- icons supplement labels and tooltips instead of replacing all explanation.

## Recommended target information architecture

Treat this as a starting proposal to validate in Blender, not a rigid mockup.

### 1. Take Manager

Keep the take hierarchy as the anchor. Above it, use one compact state header:

- **Applied: Take Name**
- **Selected: Take Name** only when it differs
- a prominent **Apply Selected** action when needed
- a smaller **Reapply** action when selected and applied match

Simplify each hierarchy row to the state that must be scanned across many takes:

- applied indicator;
- hierarchy indentation and name;
- one compact override/status badge;
- batch-inclusion toggle.

Move recording, camera, profile, duplicate, delete, and reparent actions out of
the crowded row unless live testing proves a specific shortcut is essential.
Use a compact selected-take action strip or menu for secondary actions.

### 2. Selected Take

Group selected-take controls by intent:

- identity and hierarchy;
- camera;
- render profile;
- recording/capture.

When the selected take is not applied, show one clear explanation and Apply
button instead of several disabled control groups repeating the same message.

Automatic Recording and Capture Most Recent Action are alternatives over the
same tracker. Present them as one coherent **Capture Changes** section:

- current recording status;
- Start/Stop Recording;
- Commit Pending only when relevant;
- Capture Last Action only when automatic recording is stopped.

### 3. Batch Render

Redesign this as a queue, not as a selected-take property box.

The queue should show every take in render order with, at minimum:

- included toggle;
- take name;
- resolved camera;
- final or preview output path;
- readiness/error status.

Useful optional columns or selected-row details:

- image format;
- resolution;
- direct/inherited output ownership;
- per-take output override;
- warning when an output name was made unique automatically.

Provide queue-level actions such as:

- Include All / Include None;
- Preflight or Refresh Plan;
- Render N Ready Takes.

Before actual file output, show a confirmation or review dialog containing the
take count and final destinations. Clearly state that completed files cannot be
undone and that the live Blender state will be restored.

After completion or failure, keep a compact result summary visible:

- rendered count;
- failed take, if any;
- files already written;
- restoration status;
- first actionable error.

Do not claim background rendering. The current implementation is synchronous
still rendering.

### 4. Advanced Overrides

Keep the direct override inspector collapsed by default. Improve scanability
with concise target/property/value presentation and clear missing-reference
errors, but do not let this advanced data dominate the normal workflow.

## Required batch planning seam

A clearer batch UI likely needs a non-rendering public engine helper that builds
the same plan used by the renderer. Do not duplicate render rules in `ui.py`.

A candidate API is conceptually:

```python
plan = engine.build_batch_plan(scene, take_uuids=None)
```

Each planned row could expose:

- take UUID and name;
- hierarchy/render order;
- resolved camera;
- raw and final output path;
- file extension and format;
- resolved resolution;
- readiness;
- structured issues and warnings.

The render operator should consume or rebuild this same authoritative plan so
preview and execution cannot drift apart. Refactor incrementally around the
already-tested `_build_batch_queue`, output derivation, collision handling, and
whole-queue preflight. Preserve the existing `render_take_batch()` public seam
for compatibility.

Use two deliberately separate validation depths:

1. The plan shown during ordinary panel drawing must be read-only. It may
   resolve stored values and detect missing references, invalid cameras,
   unsupported formats, unsaved-relative paths, and output collisions, but it
   must not apply takes.
2. A user-invoked **Preflight** action may perform the existing deeper
   transactional apply-and-restore validation. It must never run implicitly
   from `draw()`, and its result should be reported separately from the
   lightweight plan.

Whether the plan is runtime-only or cached must be decided deliberately. Prefer
a freshly derived, runtime-only view unless profiling proves otherwise.
Merely drawing the panel must not apply takes or mutate scene data.

## Implementation sequence

### Phase A - Read-only audit and baseline

1. Create a feature branch, suggested name: `ui-overhaul`.
2. Confirm the worktree is clean and v0.6.2 tests pass.
3. Inspect the live UI through Blender MCP.
4. Capture screenshots or a written layout inventory at narrow and wide
   Properties-panel widths.
5. Walk the workflows: create, select, apply, record, set camera/profile, include
   takes, preflight, render, inspect errors.
6. Ask the user only the small number of preference questions that materially
   change information architecture.

### Phase B - State/view-model helpers

1. Extract small read-only helpers for selected/applied status and row badges.
2. Add the authoritative batch-plan/preflight result model.
3. Unit/integration-test planning independently from actual rendering.
4. Keep schema 2 unless a persistent field is demonstrably necessary.

### Phase C - Batch workflow first

1. Replace the selected-take-only batch box with a visible queue.
2. Show derived output and validation per take.
3. Add clear bulk inclusion controls.
4. Add a review/confirmation step.
5. Persist a runtime-only last-result summary.
6. Validate failure, cancellation, partial output, and exact restoration.

This is the highest-value vertical slice and should establish the visual language
for the rest of the add-on.

### Phase D - Main Take Manager cleanup

1. Simplify hierarchy rows.
2. Consolidate selected/applied state and Apply behavior.
3. Consolidate capture/recording controls.
4. Move secondary take actions into a compact, predictable location.
5. Remove repeated status labels and duplicated settings.

### Phase E - Camera, render profile, and override inspector

1. Align headings, ownership language, icons, spacing, and disabled-state help.
2. Make direct versus inherited state visible without long paragraphs.
3. Preserve the existing native render-profile controls and transactional dialog.
4. Keep the override inspector advanced and collapsed.

### Phase F - Validation, polish, and release

1. Test narrow and wide panel layouts.
2. Run a non-background GUI draw smoke test for every panel/dialog.
3. Run the full Blender suite and clean packaged-install lane.
4. Reinstall into the live Blender session only after a scene fingerprint.
5. Compare the exact before/after fingerprint and do not save the user's file.
6. Publish a versioned release only after the user is comfortable with the
   workflow.

## Invariants that must not regress

- Main is the single hierarchy root.
- Selected take and applied take remain separate states.
- Live capture/edit operations require the intended non-Main take to be applied.
- Inheritance remains Main-to-leaf with deepest override winning.
- UI draw code is read-only.
- Automatic recording commits or stops safely around internal operations.
- Render-profile Apply and Cancel remain transactional.
- Batch preflight validates the whole queue before output begins.
- Batch order remains displayed hierarchy order unless an explicit ordering
  feature is designed and tested.
- Batch rendering remains still-image-only for now.
- Relative `//` output requires a saved `.blend`.
- Output collision handling remains deterministic and safe.
- Batch rendering restores live values, camera, frame, render settings, selected
  take, applied take, and selected override even after failure.
- Files already written cannot be rolled back; UI must report them honestly.
- Hot reload, unregister, and packaged installation must remain clean.
- Existing `.blend` data must open without migration unless a real schema change
  is intentionally introduced.

## Testing expectations

Keep all existing lanes green:

```powershell
& 'C:\Codex_Playpen\blender-take-system\tests\run_take_system_tests.ps1'
```

Add focused coverage for:

- batch plan order;
- per-take camera/output/format/resolution preview;
- inclusion changes and Include All/None;
- collision-renamed output preview;
- unsaved-relative-path errors;
- missing camera and unsupported format errors;
- plan/execution parity;
- no scene mutation while drawing or planning;
- persistent result reporting after success, partial failure, and cancellation;
- selected/applied state presentation;
- all panel/dialog draw paths at realistic widths.

The existing suite already covers real rendering, output derivation, collisions,
whole-queue preflight, partial failures, cancellation, exact restoration,
save/reload, and isolated ZIP installation. Extend it rather than replacing it.

## Acceptance criteria

The overhaul is successful when:

- a new user can identify selected versus applied state immediately;
- the most common next action is visually obvious;
- hierarchy rows are scannable and no longer resemble an icon toolbar;
- recording and one-shot capture read as one coherent workflow;
- the complete batch queue is visible before rendering;
- every queued take shows its resolved camera, destination, and readiness;
- the render action states exactly how many takes will run;
- errors appear beside the affected take and again in a concise summary;
- output preview matches actual output paths;
- no UI draw or preview mutates the live scene;
- the full regression suite, GUI smoke tests, and packaged install pass;
- the user's live `.blend` remains byte-unsaved and state-identical during
  activation.

## Scope discipline

Do not combine the first UI pass with unrelated Phase 7 work such as JSON
exchange, thumbnails, or drag-and-drop.

Do not rewrite the proven hierarchy, override, recording, or render transaction
engine merely to make panel code shorter. Add narrow read-only view models and a
shared batch-plan seam where the new workflow genuinely requires them.

Do not treat either the existing layout or the target layout in this handoff as
fixed. Rearrange, consolidate, rename, or remove redundant UI entry points when
that produces a cleaner workflow. Validate the direction with the user after
the first working batch vertical slice, then apply the same language to the
remaining panels.
