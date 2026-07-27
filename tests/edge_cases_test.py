"""Validation and reconciliation edge cases for Pass Reconstruct."""

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


def expect_build_error(callback, expected_text):
    try:
        callback()
    except operators.BuildError as exc:
        require(expected_text in str(exc), f"Unexpected error: {exc}")
    else:
        raise AssertionError(f"Expected BuildError containing {expected_text!r}")


pass_reconstruct.register()
extra_scene = None
try:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    settings = scene.pxr_pass_reconstruct
    for category in naming.CATEGORIES:
        setattr(settings, naming.enable_property(category), False)
    settings.enable_diffuse = True
    view_layer = scene.view_layers[0]
    view_layer.use_pass_diffuse_direct = True
    view_layer.use_pass_diffuse_indirect = True
    view_layer.use_pass_diffuse_color = True
    view_layer.cycles.denoising_store_passes = True
    settings.create_render_layers_if_missing = True

    original_group_builder = node_groups.build_category_group

    def injected_group_failure(*_args, **_kwargs):
        raise RuntimeError("Injected staging failure")

    node_groups.build_category_group = injected_group_failure
    try:
        try:
            operators.build_categories(
                bpy.context,
                ("diffuse",),
                remove_disabled=False,
            )
        except RuntimeError as exc:
            require("Injected staging failure" in str(exc), "Unexpected staging error")
        else:
            raise AssertionError("Injected staging failure did not abort")
    finally:
        node_groups.build_category_group = original_group_builder
    require(scene.compositing_node_group is None, "Failed build left a new root tree")
    require(not settings.owner_uid, "Failed build left a new owner UID")

    view_layer.use_pass_diffuse_direct = False
    view_layer.use_pass_diffuse_indirect = False
    view_layer.use_pass_diffuse_color = False
    settings.create_render_layers_if_missing = False

    expect_build_error(
        lambda: operators.build_categories(
            bpy.context,
            ("diffuse",),
            remove_disabled=False,
        ),
        "No Render Layers",
    )

    settings.create_render_layers_if_missing = True
    built, warnings = operators.build_categories(
        bpy.context,
        ("diffuse",),
        remove_disabled=False,
    )
    require(not built, "Disabled View Layer pass should not build")
    require(warnings and "required pass toggles" in warnings[0], "Missing pass warning absent")

    tree = scene.compositing_node_group
    render_layers = [node for node in tree.nodes if node.type == "R_LAYERS"]
    require(len(render_layers) == 1, "Expected an auto-created Render Layers node")
    source = render_layers[0]
    view_layer.use_pass_diffuse_direct = True
    view_layer.use_pass_diffuse_indirect = True
    view_layer.use_pass_diffuse_color = True
    view_layer.cycles.denoising_store_passes = True

    built, warnings = operators.build_categories(
        bpy.context,
        ("diffuse",),
        remove_disabled=False,
    )
    require(built == ("diffuse",), "Diffuse did not build after enabling passes")
    require(not warnings, f"Unexpected guide warnings: {warnings}")

    second = tree.nodes.new("CompositorNodeRLayers")
    second.scene = scene
    second.layer = view_layer.name
    source.select = False
    second.select = False
    tree.nodes.active = None
    expect_build_error(
        lambda: operators.build_categories(
            bpy.context,
            ("diffuse",),
            remove_disabled=False,
        ),
        "Multiple Render Layers",
    )

    tree.nodes.active = second
    built, _warnings = operators.build_categories(
        bpy.context,
        ("diffuse",),
        remove_disabled=False,
    )
    require(built == ("diffuse",), "Active Render Layers precedence failed")

    settings.enable_diffuse = False
    operators.build_categories(
        bpy.context,
        tuple(naming.CATEGORIES),
        remove_disabled=True,
    )
    require(
        not any(
            node.get(naming.TAG_CATEGORY) == "diffuse"
            and node.get(naming.TAG_ROLE) in {"instance", "standalone_instance"}
            for node in tree.nodes
        ),
        "Unchecked category was not removed by Build All reconciliation",
    )

    settings.enable_diffuse = True
    settings.enable_glossy = True
    settings.route_diffuse = "COMPOSITE"
    settings.route_glossy = "COMPOSITE"
    expect_build_error(
        lambda: operators.build_categories(
            bpy.context,
            ("diffuse", "glossy"),
            remove_disabled=False,
        ),
        "Only one category",
    )

    settings.route_glossy = "NONE"
    extra_scene = bpy.data.scenes.new("PXR Shared Tree Probe")
    extra_scene.compositing_node_group = tree
    expect_build_error(
        lambda: operators.build_categories(
            bpy.context,
            ("diffuse",),
            remove_disabled=False,
        ),
        "shared by multiple owners",
    )
    extra_scene.compositing_node_group = None
    bpy.data.scenes.remove(extra_scene)
    extra_scene = None

    result = bpy.ops.compositor.pxr_teardown_passes()
    require(result == {"FINISHED"}, "Teardown failed in edge-case test")
    print("PXR_EDGE_CASES_OK")
finally:
    if extra_scene is not None and extra_scene.name in bpy.data.scenes:
        bpy.data.scenes.remove(extra_scene)
    pass_reconstruct.unregister()
