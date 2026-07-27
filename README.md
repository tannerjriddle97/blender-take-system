# Blender Take System

Blender Take System is an installable Blender add-on modeled on Cinema 4D's
Take Manager. It stores property overrides in a Main-rooted hierarchy so one
scene can hold material, transform, visibility, camera, and render-setting
variants without duplicating scene content.

- Current release: **0.5.0 (Phase 5)**
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

Phases 1–5 are complete. Phase 6 will add opt-in automatic recording while
preserving strict atomic application, Main baselines, exact typed values, and
the existing large-scene performance characteristics.

No open-source license has been selected yet.
