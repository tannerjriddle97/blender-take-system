"""Install/enable/disable smoke test for the packaged legacy add-on ZIP."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
ZIP_PATH = WORKSPACE / "dist" / "pass_reconstruct.zip"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


result = bpy.ops.preferences.addon_install(
    filepath=str(ZIP_PATH),
    overwrite=True,
)
require(result == {"FINISHED"}, f"ZIP install failed: {result}")

result = bpy.ops.preferences.addon_enable(module="pass_reconstruct")
require(result == {"FINISHED"}, f"Add-on enable failed: {result}")
require(
    "pass_reconstruct" in bpy.context.preferences.addons,
    "Enabled add-on is absent from preferences",
)
require(
    hasattr(bpy.types.Scene, "pxr_pass_reconstruct"),
    "Add-on registration did not attach Scene settings",
)

module = sys.modules.get("pass_reconstruct")
require(module is not None, "Installed add-on module was not imported")
require(module.bl_info["version"] == (1, 1, 0), "Installed add-on version mismatch")

result = bpy.ops.preferences.addon_disable(module="pass_reconstruct")
require(result == {"FINISHED"}, f"Add-on disable failed: {result}")
require(
    not hasattr(bpy.types.Scene, "pxr_pass_reconstruct"),
    "Add-on unregister left Scene settings attached",
)
print("PXR_INSTALL_ENABLE_OK")
