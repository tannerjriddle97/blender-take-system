# Blender Take System

Blender Take System is an installable Blender add-on modeled on Cinema 4D's
Take Manager. It stores property overrides in a Main-rooted hierarchy so one
scene can hold material, transform, visibility, camera, and render-setting
variants without duplicating scene content.

- Current release: **0.6.2 (render-profile controls update)**
- Persistent schema: **2**
- Supported Blender versions: **4.0+**
- Verified Blender versions: **5.1.2 and 5.2.0**

## Documentation

- [User guide and API examples](blender_take_system/README.md)
- [Engineering handoff](BLENDER_TAKE_SYSTEM_HANDOFF.md)
- [Original product specification](blender_take_system_addon_prompt.md)

## Repository layout

- `blender_take_system/` — installable add-on package
- `tests/` — Blender background integration and regression tests
- `examples/` — small example-scene scripts
- `BLENDER_TAKE_SYSTEM_HANDOFF.md` — architecture, invariants, and release notes

## Build and test

From PowerShell:

```powershell
.\tests\run_take_system_tests.ps1
```

The runner selects the newest installed Blender unless `-BlenderPath` is
provided. It rebuilds and verifies the installable ZIP before running all test
lanes.

Build only:

```powershell
.\tests\build_take_system_package.ps1
```

The generated release archive is written to `dist/` and is intentionally not
tracked in Git. Release ZIPs should be attached to a GitHub Release.

## Roadmap

Phases 1–6 are complete. Phase 6 adds opt-in automatic recording for the
applied non-Main take. Supported edits are grouped into atomic override
batches, Main baselines are seeded automatically, and recording fails closed
on invalid or unsupported changes.

Version 0.6.1 added an inherited render-profile editor directly to Take Manager.
Main/current settings are the default, while each child can independently
override engine/sampling, resolution, output/format, transparency, or color
management. Version 0.6.2 adds the native Cycles render-denoiser selector and
Transparent Glass checkbox to those inherited groups.

Phase 7 is reserved for later exchange and workflow features such as JSON
import/export, ordering improvements, and opt-in take previews.

No open-source license has been selected yet.
