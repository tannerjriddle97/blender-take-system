# Prompt: Blender "Take System" Addon (Cinema 4D Take Manager Parity)

## Context

I'm a 3D production professional working primarily in Blender (with an MCP-driven scripted/conversational workflow) alongside Cinema 4D, KeyShot, and Photoshop. In C4D, the **Take System** is essential to my pipeline — I use it to manage CMF (color/material/finish) variants, camera setups, and render configurations without duplicating scenes. Blender has no native equivalent. I want to build a full addon that replicates C4D's Take Manager as closely as possible, adapted to Blender's data model.

Build this as a real, installable Blender addon (Python, `bpy`), targeting Blender 4.x. Work in phases — don't try to do everything in one pass. Start with the data model and core override/apply mechanism before touching UI polish.

## What the Take System must do (functional spec, modeled on C4D)

1. **Take hierarchy**
   - A tree of Takes, always rooted at a "Main" take (like C4D's Main Take) representing the base scene state.
   - Takes can have parent/child relationships, arbitrarily nested.
   - Child takes inherit all overrides from their parent chain, and can further override or add their own on top. Resolution order: walk from Main down to the active take, applying overrides in order so the most specific (deepest) wins per-property.
   - Reordering/reparenting takes in the tree should be supported (even if drag-and-drop comes later — at minimum, operators to move a take under a new parent).

2. **Overrides (the core mechanic)**
   - An override = a specific object + a specific property/data-path + a stored value, scoped to a specific take.
   - Must support overriding: object transforms, custom properties, material slot assignment (swap which material an object/slot points to — this is my main CMF use case), material node input values (color, roughness, etc.), object visibility (viewport + render), modifier properties, and camera data-block properties.
   - Should NOT require duplicating objects/materials per take — override the *value*, not the datablock, exactly like C4D stores a "TakeData" override table rather than cloning the scene.
   - Support both:
     - **Manual override add**: user selects a property (e.g. right-click in the N-panel or Properties editor → "Add Take Override"), and it's captured to the currently active take.
     - **Auto-take / recording mode**: an optional toggle where, while a take is active and "recording," any property change the user makes in the UI is automatically captured as an override on that take (mirrors C4D's record dot per take).

3. **Take-level scene overrides**
   - Each take can optionally override: the active camera (Take Camera), and render settings (resolution, output path, engine-specific settings) — same as C4D's per-take camera and render-setting overrides.
   - A checkbox per take to include/exclude it from batch rendering, plus a "render all takes" batch operator that iterates non-excluded takes and renders each to a per-take output path/filename.

4. **Take Manager UI**
   - A dedicated panel (own tab in the Properties editor or a custom Outliner-style area) showing the take tree.
   - Each row: take name (editable), a checkbox/dot for record mode, an icon indicating it has a camera override, a render-inclusion toggle.
   - Buttons: Add Take, Add Child Take, Duplicate Take, Delete Take, Add Override (context-sensitive to current selection/active property).
   - Double-click / "Go to Take" applies that take's fully-resolved state to the scene.
   - A way to inspect what overrides exist on a given take (expandable list of property paths + values), so I can audit/remove individual overrides without deleting the whole take.

5. **Apply/resolve mechanism**
   - Switching the active take must: walk the parent chain top-down, build a resolved dict of `{(object_id, data_path): value}`, then apply each via Blender's RNA (`id.path_resolve` / `setattr`) in one pass.
   - Must handle multiple value types generically: float, int, bool, string, enum, color (RGBA), vector (location/rotation/scale), and datablock pointers (e.g. material slot assignment, camera pointer).
   - Applying a take should not destroy overrides on other takes — it's non-destructive, values are restored from storage every time.

## Suggested technical architecture

- **Data model**: custom `PropertyGroup` types registered on the Scene:
  - `TakeOverride`: stores `id_type`, `id_name` (or a pointer property where possible), `data_path` (string, RNA path), `prop_type` (enum: FLOAT/INT/BOOL/STRING/COLOR/VECTOR/POINTER/ENUM), and one value field per type (Blender doesn't support true variant properties, so store parallel optional fields and use the one matching `prop_type`).
  - `Take`: `name`, `parent` (pointer to another Take, or index/UUID reference since PropertyGroups can't self-reference by pointer easily — likely use a string ID + a helper to look up children by parent ID), `overrides` (CollectionProperty of TakeOverride), `camera_override` (pointer to Object, optional), `render_settings_overrides` (optional nested overrides or a dedicated struct), `is_recording` (bool), `include_in_render` (bool).
  - `Scene.take_system` holding: `takes` (CollectionProperty of Take, with "Main" always present and non-deletable), `active_take_index`.
- **Path resolution**: use `bpy_struct.path_resolve()` for reading current values when capturing overrides, and a small setter helper that resolves the parent struct + final attribute name so `setattr()` works for property types that don't support `path_resolve` writes directly (needed for pointer properties like material slots).
- **Change detection for auto-take/recording**: use `bpy.msgbus` subscriptions on commonly-overridden RNA paths (transform, material slots, visibility) plus a `depsgraph_update_post` handler as a fallback/catch-all for broader property changes while a take is in recording mode. Be explicit about the performance tradeoffs of msgbus subscription breadth vs. depsgraph polling — pick the more reliable approach for correctness first, optimize later.
- **UI**: a `UIList` for the take tree (with indentation to represent hierarchy depth, computed at draw time from parent links), custom operators for add/remove/reparent/duplicate/apply, and a context-menu operator (`wm.context_menu` hook or a manual right-click entry via `bpy.types.UI_MT_button_context_menu.append`) for "Add Take Override" on arbitrary properties.
- **Persistence**: all data lives in the .blend file via the PropertyGroups (no external files needed for core function). Stretch goal: JSON import/export of a take tree for reuse across files, since a lot of my CMF variants are reused across similar Bobcat/Bose asset files.

## Explicit non-goals for v1 (call these out so scope doesn't creep)

- No need to replicate C4D's full "Take Render Queue" UI polish — a functional batch-render-by-take operator is enough.
- No requirement to support geometry-level overrides (i.e., don't try to override mesh data itself, only transform/material/visibility/modifier-parameter/camera/render values).
- Don't build drag-and-drop tree reordering in v1 — operator-based reparenting (a dropdown or "Set Parent" operator) is fine to start.

## Deliverables per phase

1. **Phase 1** — Data model + registration (PropertyGroups, Scene pointer, Main take bootstrap on addon enable).
2. **Phase 2** — Manual override capture + apply/resolve engine, tested against transform + material slot + visibility overrides on a simple test scene (a few cubes with 2-3 material variants).
3. **Phase 3** — Take Manager UI panel: tree display, add/remove/duplicate, apply-on-select.
4. **Phase 4** — Parent/child inheritance resolution correctness (unit-test with a 3-level hierarchy and conflicting overrides at different levels).
5. **Phase 5** — Camera override + render-settings override per take, batch render operator.
6. **Phase 6** — Auto-take/recording mode via msgbus + depsgraph handler.
7. **Phase 7** (stretch) — JSON import/export of take trees, drag-and-drop reordering, thumbnails per take.

For each phase, give me working code I can drop into a single-file or package addon, a short note on what to test manually in Blender, and flag any Blender API limitations you hit that force a deviation from true C4D parity (e.g. anywhere pointer-type overrides can't cleanly round-trip) so I know where the analogy breaks down.

Start with Phase 1 and Phase 2 together, since the apply/resolve engine is the part most likely to need iteration before the UI is worth building on top of it.
