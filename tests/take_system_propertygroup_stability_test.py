"""Stress stable UUID use across Blender CollectionProperty growth."""

import sys
from pathlib import Path

import bpy


WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import blender_take_system
from blender_take_system import engine


def require(condition, message):
    if not condition:
        raise AssertionError(message)


registered = False
try:
    blender_take_system.register()
    registered = True
    scene = bpy.context.scene
    main_uuid = engine.ensure_main_take(scene).uuid

    subject = bpy.data.objects.new("TS_PropertyGroup_Stable", None)
    scene.collection.objects.link(subject)
    source = engine.create_take(
        scene,
        "Stable Source",
        parent_uuid=main_uuid,
        make_active=False,
    )
    source_uuid = source.uuid
    source.include_in_render = False
    source.render_output_path = "C:\\renders\\stability_source"
    engine.capture_override(
        scene,
        subject,
        "hide_render",
        source_uuid,
    )
    subject.hide_render = True
    engine.capture_override(
        scene,
        subject,
        "hide_render",
        source_uuid,
    )

    created_uuids = []
    for index in range(128):
        parent_uuid = source_uuid if index % 3 == 0 else main_uuid
        created = engine.create_take(
            scene,
            f"Stable Child {index:03d}",
            parent_uuid=parent_uuid,
            make_active=False,
        )
        created_uuid = created.uuid
        created_uuids.append(created_uuid)
        stored = engine.find_take(scene, created_uuid)
        require(
            stored is not None and stored.parent_uuid == parent_uuid,
            "CollectionProperty growth corrupted a newly stored parent UUID",
        )

    duplicate_uuids = []
    for _index in range(64):
        duplicate = engine.duplicate_take(
            scene,
            source_uuid,
            make_active=False,
        )
        duplicate_uuid = duplicate.uuid
        duplicate_uuids.append(duplicate_uuid)
        stored = engine.find_take(scene, duplicate_uuid)
        copied = engine.find_override(stored, subject, "hide_render")
        require(
            stored is not None
            and stored.parent_uuid == main_uuid
            and stored.include_in_render is False
            and stored.render_output_path
            == "C:\\renders\\stability_source"
            and copied is not None
            and engine.decoded_override_value(copied) is True,
            "Take duplication read an invalidated source wrapper",
        )

    victim_uuid = created_uuids[-1]
    engine.apply_take(scene, victim_uuid, strict=True)
    victim_index = next(
        index
        for index, take in enumerate(scene.take_system.takes)
        if take.uuid == victim_uuid
    )
    scene.take_system.active_take_index = victim_index
    fallback = engine.delete_take(scene, victim_uuid)
    require(
        engine.find_take(scene, victim_uuid) is None
        and fallback is not None
        and scene.take_system.active_take_uuid == fallback.uuid
        and scene.take_system.takes[
            scene.take_system.active_take_index
        ].uuid
        == fallback.uuid,
        "Take deletion used an invalidated removed-item wrapper",
    )

    require(
        all(
            engine.find_take(scene, take_uuid) is not None
            for take_uuid in duplicate_uuids
        ),
        "Stress growth lost a duplicated take",
    )
    print(
        "TAKE_SYSTEM_PROPERTYGROUP_STABILITY_OK",
        {
            "created": len(created_uuids),
            "duplicated": len(duplicate_uuids),
            "takes": len(scene.take_system.takes),
        },
    )
finally:
    if registered:
        blender_take_system.unregister()
