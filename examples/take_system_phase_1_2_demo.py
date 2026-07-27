"""Build a small three-cube CMF demo for the Take System addon.

Run this once from Blender's Scripting workspace after enabling the addon.
"""

import bpy

from blender_take_system import engine


DEMO_COLLECTION = "TS_Phase_1_2_Demo"


def cube_mesh(name):
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(
        (
            (-1, -1, -1),
            (1, -1, -1),
            (1, 1, -1),
            (-1, 1, -1),
            (-1, -1, 1),
            (1, -1, 1),
            (1, 1, 1),
            (-1, 1, 1),
        ),
        (),
        (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (4, 0, 3, 7),
        ),
    )
    mesh.update()
    return mesh


def material(name, color, roughness):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1.0)
    result.roughness = roughness
    return result


if bpy.data.collections.get(DEMO_COLLECTION):
    raise RuntimeError(
        f"Collection '{DEMO_COLLECTION}' already exists; delete it before rerunning"
    )

scene = bpy.context.scene
main = engine.ensure_main_take(scene)
collection = bpy.data.collections.new(DEMO_COLLECTION)
scene.collection.children.link(collection)

white = material("TS_Demo_White", (0.65, 0.65, 0.65), 0.32)
red = material("TS_Demo_Red", (0.55, 0.015, 0.01), 0.2)
blue = material("TS_Demo_Blue", (0.01, 0.06, 0.5), 0.4)

cubes = []
for index, x_position in enumerate((-3.0, 0.0, 3.0), start=1):
    obj = bpy.data.objects.new(
        f"TS_Demo_Cube_{index}",
        cube_mesh(f"TS_Demo_Cube_{index}"),
    )
    collection.objects.link(obj)
    obj.location.x = x_position
    obj.data.materials.append(white)
    obj.material_slots[0].link = "OBJECT"
    obj.material_slots[0].material = white
    cubes.append(obj)


def capture_edit(take, target, data_path, value):
    engine.capture_override(scene, target, data_path, take.uuid)
    engine.write_path_value(target, data_path, value)
    engine.capture_override(scene, target, data_path, take.uuid)


def make_variant_take(
    name,
    target_material,
    first_cube_height,
    visibility_object,
    visibility_path,
):
    engine.apply_take(scene, main.uuid, strict=True)
    take = engine.create_take(
        scene,
        name,
        parent_uuid=main.uuid,
        make_active=True,
    )
    for obj in cubes:
        capture_edit(
            take,
            obj,
            "material_slots[0].material",
            target_material,
        )
    first_location = tuple(cubes[0].location)
    capture_edit(
        take,
        cubes[0],
        "location",
        (first_location[0], first_location[1], first_cube_height),
    )
    capture_edit(take, visibility_object, visibility_path, True)
    return take


red_take = make_variant_take(
    "CMF — Red",
    red,
    1.5,
    cubes[2],
    "hide_render",
)
blue_take = make_variant_take(
    "CMF — Blue",
    blue,
    -1.0,
    cubes[1],
    "hide_viewport",
)
engine.apply_take(scene, red_take.uuid, strict=True)

print(
    "TAKE_SYSTEM_DEMO_READY",
    {
        "collection": collection.name,
        "takes": [take.name for take in scene.take_system.takes],
        "active": red_take.name,
        "tip": (
            "Main is white/base; Red lifts Cube 1 and hides Cube 3 at render; "
            "Blue lowers Cube 1 and hides Cube 2 in the viewport."
        ),
    },
)
