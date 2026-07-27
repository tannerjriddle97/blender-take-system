"""Save/reload persistence smoke test for Pass Reconstruct."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import pass_reconstruct
from pass_reconstruct import naming, node_groups, operators


def require(condition, message):
    if not condition:
        raise AssertionError(message)


blend_path = str(WORKSPACE / "pxr_save_reload_test.blend")
pass_reconstruct.register()
try:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    view_layer = scene.view_layers[0]
    view_layer.use_pass_diffuse_direct = True
    view_layer.use_pass_diffuse_indirect = True
    view_layer.use_pass_diffuse_color = True
    view_layer.cycles.denoising_store_passes = True

    settings = scene.pxr_pass_reconstruct
    for category in naming.CATEGORIES:
        setattr(settings, naming.enable_property(category), category == "diffuse")
    settings.diffuse_direct_strength = 0.35
    settings.diffuse_color_influence = 0.6
    built, warnings = operators.build_categories(
        bpy.context,
        tuple(naming.CATEGORIES),
        remove_disabled=True,
    )
    require(built == ("diffuse",), "Initial persistence build failed")
    require(not warnings, f"Unexpected initial warnings: {warnings}")
    original_owner = settings.owner_uid
    require(original_owner, "Owner UID missing before save")

    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    scene = bpy.context.scene
    settings = scene.pxr_pass_reconstruct
    require(settings.owner_uid == original_owner, "Owner UID did not survive reload")
    require(
        abs(settings.diffuse_direct_strength - 0.35) < 1e-6
        and abs(settings.diffuse_color_influence - 0.6) < 1e-6,
        "Pass controls did not survive reload",
    )
    tree = scene.compositing_node_group
    instances = [
        node
        for node in tree.nodes
        if node.get(naming.TAG_CATEGORY) == "diffuse"
        and node.get(naming.TAG_ROLE) == "instance"
    ]
    require(len(instances) == 1, "Managed instance did not survive reload")
    direct_strength = next(
        socket for socket in instances[0].inputs if socket.name == "Direct Strength"
    )
    color_influence = next(
        socket for socket in instances[0].inputs if socket.name == "Color Influence"
    )
    require(
        abs(direct_strength.default_value - 0.35) < 1e-6
        and abs(color_influence.default_value - 0.6) < 1e-6,
        "Built pass controls did not survive reload",
    )
    require(
        len(list(node_groups.owned_groups(original_owner, "diffuse"))) == 1,
        "Owned group discovery failed after reload",
    )

    rebuilt, rebuild_warnings = operators.build_categories(
        bpy.context,
        ("diffuse",),
        remove_disabled=False,
    )
    require(rebuilt == ("diffuse",), "Reloaded category did not rebuild")
    require(not rebuild_warnings, f"Unexpected post-reload warnings: {rebuild_warnings}")
    require(
        len(
            [
                node
                for node in tree.nodes
                if node.get(naming.TAG_CATEGORY) == "diffuse"
                and node.get(naming.TAG_ROLE) == "instance"
            ]
        )
        == 1,
        "Rebuild after reload duplicated the category",
    )
    bpy.ops.compositor.pxr_teardown_passes()
    print("PXR_SAVE_RELOAD_OK")
finally:
    pass_reconstruct.unregister()
