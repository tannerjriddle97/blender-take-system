"""Headless Blender smoke test for Pass Reconstruct."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import pass_reconstruct
from pass_reconstruct import naming, node_groups, operators, properties


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def set_nested_attr(value, path, state):
    parts = path.split(".")
    target = value
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], state)


def managed_category_nodes(tree, category):
    return [
        node
        for node in tree.nodes
        if node.get(naming.TAG_MANAGED)
        and node.get(naming.TAG_CATEGORY) == category
        and node.get(naming.TAG_ROLE) in {"instance", "standalone_instance"}
    ]


def owned_groups(owner_uid):
    return list(node_groups.owned_groups(owner_uid))


def link_signature(tree):
    return sorted(
        (
            link.from_node.name,
            link.from_socket.identifier,
            link.to_node.name,
            link.to_socket.identifier,
        )
        for link in tree.links
    )


def has_link(tree, from_node, from_name, to_node, to_name, socket_type):
    return any(
        link.from_node == from_node
        and link.from_socket.name == from_name
        and link.from_socket.type == socket_type
        and link.to_node == to_node
        and link.to_socket.name == to_name
        and link.to_socket.type == socket_type
        for link in tree.links
    )


pass_reconstruct.register()
try:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    view_layer = scene.view_layers[0]
    for category, config in naming.CATEGORIES.items():
        for flag in config["flags"]:
            set_nested_attr(view_layer, flag, True)
    view_layer.cycles.denoising_store_passes = True

    root = bpy.data.node_groups.new("PXR Smoke Root", "CompositorNodeTree")
    scene.compositing_node_group = root

    file_output = root.nodes.new("CompositorNodeOutputFile")
    file_output.name = "Smoke File Output"
    file_output.label = "Smoke File Output"
    file_output.file_output_items.new(socket_type="RGBA", name="Glossy")
    file_choices = [
        item
        for item in properties.route_items(scene.pxr_pass_reconstruct, bpy.context)
        if item[0].startswith("FILE_")
    ]
    require(len(file_choices) == 1, "Expected one dynamic File Output route")
    glossy_route_id = file_choices[0][0]
    _route_node, glossy_route_socket = properties.resolve_file_route(
        scene,
        glossy_route_id,
    )
    glossy_socket_identifier = glossy_route_socket.identifier
    file_output.file_output_items.new(socket_type="RGBA", name="Other")
    file_output.file_output_items.move(0, 1)
    reordered_ids = {
        item[0]
        for item in properties.route_items(
            scene.pxr_pass_reconstruct,
            bpy.context,
        )
    }
    require(glossy_route_id in reordered_ids, "File route ID changed after slot reorder")
    _route_node, reordered_socket = properties.resolve_file_route(
        scene,
        glossy_route_id,
    )
    require(
        reordered_socket.identifier == glossy_socket_identifier,
        "File route resolved to a different socket after slot reorder",
    )

    settings = scene.pxr_pass_reconstruct
    for category in naming.CATEGORIES:
        setattr(settings, naming.enable_property(category), True)
    settings.route_diffuse = "COMPOSITE"
    settings.route_glossy = glossy_route_id

    built, warnings = operators.build_categories(
        bpy.context,
        tuple(naming.CATEGORIES),
        remove_disabled=True,
    )
    require(set(built) == set(naming.CATEGORIES), f"First build incomplete: {built}")
    require(not warnings, f"Unexpected first-build warnings: {warnings}")
    owner_uid = settings.owner_uid
    require(bool(owner_uid), "Owner UID was not initialized")

    root = scene.compositing_node_group
    for category, config in naming.CATEGORIES.items():
        instances = managed_category_nodes(root, category)
        require(len(instances) == 1, f"{category}: expected exactly one instance")
        instance = instances[0]
        require(instance.node_tree is not None, f"{category}: missing node group")
        require(instance.label, f"{category}: missing visible label")
        require(instance.use_custom_color, f"{category}: missing custom color")
        require(
            all(node.get(naming.TAG_MANAGED) for node in instance.node_tree.nodes),
            f"{category}: untagged internal node",
        )
        require(
            all(node.label and node.use_custom_color for node in instance.node_tree.nodes),
            f"{category}: incomplete internal labels/colors",
        )
        expected_nodes = 13 if config["kind"] == "reconstruct" else 4
        require(
            len(instance.node_tree.nodes) == expected_nodes,
            f"{category}: unexpected internal node count",
        )
        panel_names = {
            item.name
            for item in instance.node_tree.interface.items_tree
            if item.item_type == "PANEL"
        }
        require(
            panel_names
            == {
                naming.INTERFACE_PANEL_PASSES,
                naming.INTERFACE_PANEL_GUIDES,
                naming.INTERFACE_PANEL_CONTROLS,
            },
            f"{category}: group interface panels are incomplete",
        )
        interface_inputs = {
            item.name: item
            for item in instance.node_tree.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"
        }
        pass_input_names = (
            {"Direct", "Indirect", "Color"}
            if config["kind"] == "reconstruct"
            else {"Image"}
        )
        guide_input_names = (
            {"Albedo (guide)", "Specular Albedo (guide)", "Normal (guide)"}
            if config["kind"] == "reconstruct"
            else {"Albedo", "Normal"}
        )
        for socket_name in pass_input_names:
            require(
                interface_inputs[socket_name].parent.name
                == naming.INTERFACE_PANEL_PASSES,
                f"{category}: {socket_name} is in the wrong interface panel",
            )
        for socket_name in guide_input_names:
            require(
                interface_inputs[socket_name].parent.name
                == naming.INTERFACE_PANEL_GUIDES,
                f"{category}: {socket_name} is in the wrong interface panel",
            )
        input_names = {socket.name for socket in instance.inputs}
        require("Factor" not in input_names, f"{category}: anonymous Factor input remains")
        for control in naming.controls_for_category(category):
            require(
                control["socket"] in input_names,
                f"{category}: missing {control['socket']} input",
            )
            control_socket = next(
                socket
                for socket in instance.inputs
                if socket.name == control["socket"] and socket.type == "VALUE"
            )
            require(
                abs(control_socket.default_value - control["default"]) < 1e-6,
                f"{category}: incorrect {control['socket']} default",
            )
            interface_control = interface_inputs[control["socket"]]
            require(
                interface_control.parent.name == naming.INTERFACE_PANEL_CONTROLS,
                f"{category}: {control['socket']} is in the wrong interface panel",
            )
            require(
                abs(interface_control.min_value - control["min"]) < 1e-6
                and abs(interface_control.max_value - control["max"]) < 1e-6,
                f"{category}: incorrect bounds for {control['socket']}",
            )
            scale_node = next(
                node
                for node in instance.node_tree.nodes
                if node.get(naming.TAG_ROLE) == control["role"]
            )
            factor_socket = next(
                socket
                for socket in scale_node.inputs
                if socket.name == "Factor" and socket.type == "VALUE"
            )
            require(factor_socket.is_linked, f"{category}: unlinked control factor")
            require(
                factor_socket.links[0].from_socket.name == control["socket"],
                f"{category}: control is linked to the wrong group input",
            )
            require(
                scale_node.clamp_factor == control["clamp_factor"],
                f"{category}: incorrect factor clamp for {control['socket']}",
            )
            neutral = next(
                socket
                for socket in scale_node.inputs
                if socket.name == "A" and socket.type == "RGBA"
            ).default_value
            expected_neutral = (
                (1.0, 1.0, 1.0, 1.0)
                if control["neutral"] == "WHITE"
                else (0.0, 0.0, 0.0, 0.0)
            )
            require(
                all(abs(a - b) < 1e-6 for a, b in zip(neutral, expected_neutral)),
                f"{category}: incorrect neutral color for {control['socket']}",
            )

        if config["kind"] == "reconstruct":
            role_nodes = {
                node.get(naming.TAG_ROLE): node
                for node in instance.node_tree.nodes
            }
            for pass_role, control_role in (
                ("direct_denoise", "direct_scale"),
                ("indirect_denoise", "indirect_scale"),
                ("color_denoise", "color_scale"),
            ):
                require(
                    has_link(
                        instance.node_tree,
                        role_nodes[pass_role],
                        "Image",
                        role_nodes[control_role],
                        "B",
                        "RGBA",
                    ),
                    f"{category}: {pass_role} is not routed through {control_role}",
                )
            require(
                has_link(
                    instance.node_tree,
                    role_nodes["direct_scale"],
                    "Result",
                    role_nodes["add"],
                    "A",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["indirect_scale"],
                    "Result",
                    role_nodes["add"],
                    "B",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["add"],
                    "Result",
                    role_nodes["multiply"],
                    "A",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["color_scale"],
                    "Result",
                    role_nodes["multiply"],
                    "B",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["multiply"],
                    "Result",
                    role_nodes["output_scale"],
                    "B",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["output_scale"],
                    "Result",
                    role_nodes["group_output"],
                    "Image",
                    "RGBA",
                ),
                f"{category}: reconstruction control topology is incomplete",
            )
            for role in ("add", "multiply"):
                combine = next(
                    node
                    for node in instance.node_tree.nodes
                    if node.get(naming.TAG_ROLE) == role
                )
                factor = next(
                    socket
                    for socket in combine.inputs
                    if socket.name == "Factor" and socket.type == "VALUE"
                )
                require(
                    not factor.is_linked and abs(factor.default_value - 1.0) < 1e-6,
                    f"{category}: {role} factor should be fixed at 1",
                )
        else:
            role_nodes = {
                node.get(naming.TAG_ROLE): node
                for node in instance.node_tree.nodes
            }
            require(
                has_link(
                    instance.node_tree,
                    role_nodes["image_denoise"],
                    "Image",
                    role_nodes["image_scale"],
                    "B",
                    "RGBA",
                )
                and has_link(
                    instance.node_tree,
                    role_nodes["image_scale"],
                    "Result",
                    role_nodes["group_output"],
                    "Image",
                    "RGBA",
                ),
                f"{category}: standalone strength topology is incomplete",
            )

    require(len(owned_groups(owner_uid)) == len(naming.CATEGORIES), "Owned group leak")
    group_output = next(node for node in root.nodes if node.type == "GROUP_OUTPUT")
    composite_input = next(
        socket
        for socket in group_output.inputs
        if socket.type == "RGBA" and socket.identifier != "__extend__"
    )
    require(composite_input.is_linked, "Composite route was not connected")
    require(
        composite_input.links[0].from_node.get(naming.TAG_CATEGORY) == "diffuse",
        "Composite route connected the wrong category",
    )
    _file_node, glossy_file_input = properties.resolve_file_route(
        scene,
        settings.route_glossy,
    )
    require(glossy_file_input.is_linked, "File Output route was not connected")
    require(
        glossy_file_input.links[0].from_node.get(naming.TAG_CATEGORY) == "glossy",
        "File Output route connected the wrong category",
    )

    settings.diffuse_direct_strength = 0.25
    settings.diffuse_indirect_strength = 1.5
    settings.diffuse_color_influence = 0.4
    settings.diffuse_output_strength = 1.25
    settings.emission_strength = 0.6
    for socket_name, expected in (
        ("Direct Strength", 0.25),
        ("Indirect Strength", 1.5),
        ("Color Influence", 0.4),
        ("Output Strength", 1.25),
    ):
        socket = next(
            socket
            for socket in managed_category_nodes(root, "diffuse")[0].inputs
            if socket.name == socket_name
        )
        require(
            abs(socket.default_value - expected) < 1e-6,
            f"Live Diffuse control did not update {socket_name}",
        )
    emission_strength = next(
        socket
        for socket in managed_category_nodes(root, "emission")[0].inputs
        if socket.name == "Strength"
    )
    require(
        abs(emission_strength.default_value - 0.6) < 1e-6,
        "Live standalone Strength did not update",
    )
    settings.diffuse_color_influence = 2.0
    require(
        abs(settings.diffuse_color_influence - 1.0) < 1e-6,
        "Color Influence RNA property did not clamp to 1",
    )
    settings.diffuse_color_influence = 0.4

    settings.quality = "HIGH"
    for group in owned_groups(owner_uid):
        for node in group.nodes:
            if node.type == "DENOISE":
                require(
                    node.inputs["Quality"].default_value == "High",
                    "Live denoise quality update failed",
                )

    diffuse_instance = managed_category_nodes(root, "diffuse")[0]
    direct_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Direct Strength"
    )
    indirect_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Indirect Strength"
    )
    direct_socket.default_value = 0.37
    settings.diffuse_color_influence = 0.41
    require(
        abs(direct_socket.default_value - 0.37) < 1e-6,
        "Updating one sidebar control overwrote another direct socket edit",
    )
    settings.diffuse_color_influence = 0.4
    strength_driver = root.nodes.new("ShaderNodeValue")
    strength_driver.name = "PXR Smoke Strength Driver"
    strength_driver.outputs[0].default_value = 0.73
    root.links.new(strength_driver.outputs[0], indirect_socket)

    original_groups = {
        category: managed_category_nodes(root, category)[0].node_tree
        for category in naming.CATEGORIES
    }
    original_links = link_signature(root)
    original_apply_route = operators._apply_route

    def fail_after_glossy(*args, **kwargs):
        original_apply_route(*args, **kwargs)
        category = args[4]
        if category == "glossy":
            raise RuntimeError("Injected commit failure")

    operators._apply_route = fail_after_glossy
    try:
        try:
            operators.build_categories(
                bpy.context,
                tuple(naming.CATEGORIES),
                remove_disabled=True,
            )
        except RuntimeError as exc:
            require("Injected commit failure" in str(exc), "Unexpected injected failure")
        else:
            raise AssertionError("Injected failure did not abort the build")
    finally:
        operators._apply_route = original_apply_route

    for category in naming.CATEGORIES:
        require(
            managed_category_nodes(root, category)[0].node_tree
            == original_groups[category],
            f"{category}: rollback did not restore the previous node group",
        )
    require(
        link_signature(root) == original_links,
        "Failed build did not restore the previous top-level links",
    )
    require(
        len(owned_groups(owner_uid)) == len(naming.CATEGORIES),
        "Failed build leaked staging groups",
    )
    diffuse_instance = managed_category_nodes(root, "diffuse")[0]
    direct_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Direct Strength"
    )
    indirect_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Indirect Strength"
    )
    require(
        abs(settings.diffuse_direct_strength - 0.37) < 1e-6
        and abs(direct_socket.default_value - 0.37) < 1e-6,
        "Failed rebuild did not retain a direct group-socket edit",
    )
    require(
        indirect_socket.is_linked
        and indirect_socket.links[0].from_node == strength_driver,
        "Failed rebuild did not restore a linked control input",
    )

    root_counts_before = (len(root.nodes), len(root.links))
    built_again, warnings_again = operators.build_categories(
        bpy.context,
        tuple(naming.CATEGORIES),
        remove_disabled=True,
    )
    require(set(built_again) == set(naming.CATEGORIES), "Second build incomplete")
    require(not warnings_again, f"Unexpected second-build warnings: {warnings_again}")
    require(
        (len(root.nodes), len(root.links)) == root_counts_before,
        "Idempotent build changed top-level node/link counts",
    )
    require(len(owned_groups(owner_uid)) == len(naming.CATEGORIES), "Second build leaked groups")
    diffuse_instance = managed_category_nodes(root, "diffuse")[0]
    direct_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Direct Strength"
    )
    indirect_socket = next(
        socket
        for socket in diffuse_instance.inputs
        if socket.name == "Indirect Strength"
    )
    require(
        abs(direct_socket.default_value - 0.37) < 1e-6,
        "Successful rebuild did not retain a direct group-socket edit",
    )
    require(
        indirect_socket.is_linked
        and indirect_socket.links[0].from_node == strength_driver,
        "Successful rebuild did not restore a linked control input",
    )
    for category in naming.CATEGORIES:
        category_nodes = managed_category_nodes(root, category)
        require(len(category_nodes) == 1, f"{category}: duplicate instance after second build")
        require(
            category_nodes[0].node_tree.name
            == naming.group_datablock_name(scene.name, owner_uid, category),
            f"{category}: unstable group datablock name",
        )

    shared_scene = bpy.data.scenes.new("PXR Shared Callback Guard")
    shared_scene.compositing_node_group = root
    shared_denoise = next(
        node
        for node in managed_category_nodes(root, "diffuse")[0].node_tree.nodes
        if node.type == "DENOISE"
    )
    shared_quality = shared_denoise.inputs["Quality"].default_value
    settings.diffuse_direct_strength = 0.81
    settings.quality = "BALANCED"
    require(
        abs(direct_socket.default_value - 0.37) < 1e-6,
        "Live control callback mutated a shared compositor root",
    )
    require(
        shared_denoise.inputs["Quality"].default_value == shared_quality,
        "Live denoise callback mutated a shared compositor root",
    )
    shared_scene.compositing_node_group = None
    bpy.data.scenes.remove(shared_scene)
    settings.diffuse_direct_strength = 0.37
    settings.quality = "HIGH"

    old_emission_group = managed_category_nodes(root, "emission")[0].node_tree
    external_root = bpy.data.node_groups.new(
        "PXR External Group User",
        "CompositorNodeTree",
    )
    external_instance = external_root.nodes.new("CompositorNodeGroup")
    external_instance.node_tree = old_emission_group
    old_quality = next(
        node.inputs["Quality"].default_value
        for node in old_emission_group.nodes
        if node.type == "DENOISE"
    )
    rebuilt, rebuild_warnings = operators.build_categories(
        bpy.context,
        ("emission",),
        remove_disabled=False,
    )
    require(rebuilt == ("emission",), "External-user rebuild failed")
    require(not rebuild_warnings, f"Unexpected rebuild warnings: {rebuild_warnings}")
    settings.quality = "FAST"
    current_emission_group = managed_category_nodes(root, "emission")[0].node_tree
    require(
        next(
            node.inputs["Quality"].default_value
            for node in current_emission_group.nodes
            if node.type == "DENOISE"
        )
        == "Fast",
        "Live denoise setting did not update the current attached group",
    )
    require(
        next(
            node.inputs["Quality"].default_value
            for node in old_emission_group.nodes
            if node.type == "DENOISE"
        )
        == old_quality,
        "Live denoise setting mutated an externally retained obsolete group",
    )
    bpy.data.node_groups.remove(external_root)
    node_groups.remove_group_if_unused(old_emission_group)

    diffuse_instance = managed_category_nodes(root, "diffuse")[0]
    diffuse_instance.name = "User Renamed Diffuse"
    duplicate = root.nodes.new("CompositorNodeGroup")
    duplicate.node_tree = diffuse_instance.node_tree
    for key in (
        naming.TAG_MANAGED,
        naming.TAG_OWNER,
        naming.TAG_CATEGORY,
        naming.TAG_ROLE,
        naming.TAG_SCHEMA,
    ):
        duplicate[key] = diffuse_instance[key]
    diffuse_instance[naming.TAG_SCHEMA] = 1
    diffuse_instance.node_tree[naming.TAG_SCHEMA] = 1
    require(
        not operators.category_schema_current(scene, "diffuse"),
        "Older generated schema was reported as current",
    )
    settings.quality = "FAST"
    rebuilt, rebuild_warnings = operators.build_categories(
        bpy.context,
        ("diffuse",),
        remove_disabled=False,
    )
    require(rebuilt == ("diffuse",), "Category rebuild failed")
    require(not rebuild_warnings, f"Unexpected rebuild warnings: {rebuild_warnings}")
    require(
        len(managed_category_nodes(root, "diffuse")) == 1,
        "Category rebuild did not reconcile a duplicate",
    )
    require(
        operators.category_schema_current(scene, "diffuse"),
        "Category rebuild did not migrate the generated schema",
    )
    diffuse_group = managed_category_nodes(root, "diffuse")[0].node_tree
    denoise_nodes = [node for node in diffuse_group.nodes if node.type == "DENOISE"]
    require(len(denoise_nodes) == 3, "Diffuse group should contain three denoise nodes")
    for node in denoise_nodes:
        require(node.inputs["Quality"].default_value == "Fast", "Quality was not updated")
    for socket_name, expected in (
        ("Direct Strength", 0.37),
        ("Indirect Strength", 1.5),
        ("Color Influence", 0.4),
        ("Output Strength", 1.25),
    ):
        socket = next(
            socket
            for socket in managed_category_nodes(root, "diffuse")[0].inputs
            if socket.name == socket_name
        )
        require(
            abs(socket.default_value - expected) < 1e-6,
            f"Rebuild did not preserve {socket_name}",
        )

    reset_result = bpy.ops.compositor.pxr_reset_category_controls(category="diffuse")
    require(reset_result == {"FINISHED"}, "Reset controls operator failed")
    for control in naming.controls_for_category("diffuse"):
        socket = next(
            socket
            for socket in managed_category_nodes(root, "diffuse")[0].inputs
            if socket.name == control["socket"]
        )
        require(
            abs(socket.default_value - control["default"]) < 1e-6,
            f"Reset did not restore {control['socket']}",
        )

    result = bpy.ops.compositor.pxr_teardown_passes()
    require(result == {"FINISHED"}, f"Teardown operator failed: {result}")
    require(
        not any(node.get(naming.TAG_MANAGED) for node in root.nodes),
        "Teardown left managed top-level nodes",
    )
    require(not owned_groups(owner_uid), "Teardown left owned groups")
    require(not settings.owner_uid, "Teardown did not clear owner UID")

    print("PXR_SMOKE_TEST_OK")
finally:
    pass_reconstruct.unregister()
