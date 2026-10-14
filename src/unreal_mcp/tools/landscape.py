"""
Landscape and foliage.

Foliage is buildable. Landscape is not, and that is a stub-level finding rather
than a judgement call: `LandscapeEditorObject`, `LandscapeInfo`,
`LandscapeEditorSubsystem` and `FoliageEditorSubsystem` have **zero occurrences**
in the UE 5.8 Python stub. `LandscapeComponent` exists, and so does
`LandscapeBrushParameters`, but there is nothing to construct either one from or
hand either one to -- no heightmap editor, no layer-weight painter. PLAN.md's
`sculpt_landscape_heightmap` and `paint_landscape_layer` are therefore not
buildable in pure Python, and neither is the `.ff` import workaround, which
needs a subsystem to apply it.

What remains is the foliage half: authoring a `FoliageType_InstancedStaticMesh`
and scattering instances of it with `InstancedFoliageActor.add_instances`.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def add_foliage_type(
    destination_path: str, mesh_path: str, align_to_normal: bool = True
) -> dict:
    """
    Creates a `FoliageType_InstancedStaticMesh` asset at `destination_path`
    pointing at `mesh_path`.

    The dedicated `FoliageType` asset and `add_foliage_type` do not exist as
    separate concepts in the 5.8 Python API; the factory is
    `FoliageType_InstancedStaticMeshFactory` and the asset it makes is a
    subclass of `FoliageType`. Alignment is set after creation because the
    factory does not take it.
    """
    security.enforce_tier("add_foliage_type")
    body = (
        f"_mesh = {UNREAL}.load_object(None, {mesh_path!r})\n"
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"OUT = {{'created': False, 'path': {destination_path!r}, 'mesh': {mesh_path!r},\n"
        f"      'mesh_found': _mesh is not None, 'class': None}}\n"
        f"if _mesh is None:\n"
        f"    OUT['error'] = 'no mesh at ' + {mesh_path!r}\n"
        f"elif {UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}):\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"else:\n"
        f"    _ft = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"        _name, _folder, {UNREAL}.FoliageType_InstancedStaticMesh,\n"
        f"        {UNREAL}.FoliageType_InstancedStaticMeshFactory())\n"
        f"    if _ft is None:\n"
        f"        OUT['error'] = 'factory returned None'\n"
        f"    else:\n"
        f"        _ft.set_editor_property('mesh', _mesh)\n"
        f"        _ft.set_editor_property('align_to_normal', {bool(align_to_normal)!r})\n"
        f"        {UNREAL}.EditorAssetLibrary.save_asset({destination_path!r},\n"
        f"                                           only_if_is_dirty=False)\n"
        f"        _back = {UNREAL}.load_object(None, {destination_path!r})\n"
        f"        OUT['class'] = str(_back.get_class().get_name()) if _back else None\n"
        f"        OUT['created'] = bool(\n"
        f"            _back is not None and _back.get_editor_property('mesh') is not None)\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "path": destination_path,
        "mesh": payload.get("mesh"),
        "mesh_found": payload.get("mesh_found"),
        "class": payload.get("class"),
        "error": payload.get("error"),
    }


def paint_foliage_instances(
    foliage_type_path: str,
    locations: list[list[float]],
    actor_name: str | None = None,
) -> dict:
    """
    Scatters instances of a foliage type onto an `InstancedFoliageActor`.

    Locations are explicit points rather than a radius fill, because a
    procedural fill would place instances somewhere the caller cannot verify.
    Passing an empty `locations` is rejected rather than scattering at the
    origin.

    **This tool cannot confirm it worked, and says so.** The real signature is
    `add_instances(world_context_object, foliage_type, transforms) -> None`: it
    returns nothing, and `InstancedFoliageActor` exposes no instance-count or
    foliage-list getter at all (only `add_instances` and
    `remove_all_instances`). So `called` means the engine call was made, and
    `verified` is always False. Confirm placement visually, or by reading the
    actor's instances in the foliage editor.
    """
    security.enforce_tier("paint_foliage_instances")
    _named = actor_name is not None
    pts_expr = (
        "[" + ", ".join(
            f"{UNREAL}.Transform({UNREAL}.Vector({float(p[0])}, {float(p[1])}, "
            f"{float(p[2])}), {UNREAL}.Rotator(0.0, 0.0, 0.0), {UNREAL}.Vector(1.0, 1.0, 1.0))"
            for p in locations
        ) + "]"
        if locations else "[]"
    )
    body = (
        f"_ft = {UNREAL}.load_object(None, {foliage_type_path!r})\n"
        f"OUT = {{'called': False, 'verified': False, 'foliage_type': {foliage_type_path!r},\n"
        f"      'requested': {len(locations)}, 'actor': None,\n"
        f"      'note': 'no instance-count getter exists on InstancedFoliageActor'}}\n"
        f"if _ft is None:\n"
        f"    OUT['error'] = 'no foliage type at ' + {foliage_type_path!r}\n"
        f"elif not {pts_expr}:\n"
        f"    OUT['error'] = 'no locations given; refusing to scatter at the origin'\n"
        f"else:\n"
        f"    _target = None\n"
        f"    for _a in {UNREAL}.EditorLevelLibrary.get_all_level_actors():\n"
        f"        if isinstance(_a, {UNREAL}.InstancedFoliageActor):\n"
        f"            _target = _a\n"
        f"            break\n"
        f"    if {_named!r}:\n"
        f"        _target = None\n"
        f"        for _a in {UNREAL}.EditorLevelLibrary.get_all_level_actors():\n"
        f"            if str(_a.get_name()) == {actor_name!r}:\n"
        f"                _target = _a\n"
        f"                break\n"
        f"        if _target is None:\n"
        f"            OUT['error'] = 'no actor named ' + {actor_name!r}\n"
        f"    elif _target is None:\n"
        f"        OUT['error'] = 'no InstancedFoliageActor in the level'\n"
        f"    if _target is not None and OUT.get('error') is None:\n"
        f"        {UNREAL}.InstancedFoliageActor.add_instances(\n"
        f"            _target, _ft, {pts_expr})\n"
        f"        OUT['called'] = True\n"
        f"        OUT['actor'] = str(_target.get_name())\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("called")),
        "verified": bool(payload.get("verified")),
        "foliage_type": payload.get("foliage_type"),
        "actor": payload.get("actor"),
        "requested": payload.get("requested"),
        "note": payload.get("note"),
        "error": payload.get("error"),
    }
