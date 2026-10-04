"""
Scene manipulation tools: spawning, reading back, moving, deleting actors.

NOTE on API coverage: these use unreal.EditorActorSubsystem, the modern
(5.0+) replacement for the older EditorLevelLibrary spawn/actor functions.
All five tools here are confirmed working against a live Unreal Editor 5.8
session; see README.md's status section.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    actor_component,
    actor_subsystem,
    find_actor_by_name,
    guarded,
    json_dumps,
    jsonable,
    load_asset,
    missing_actor_message,
    rotator,
    seq,
    vector,
)


def spawn_actor(
    class_path: str,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """
    Spawns an actor of the given class. class_path is a full Unreal class
    path, e.g. '/Script/Engine.StaticMeshActor' for a native class, or
    '/Game/Blueprints/BP_Thing.BP_Thing_C' for a Blueprint class.
    location/rotation are (x, y, z) and (pitch, yaw, roll).
    """
    security.enforce_tier("spawn_actor")
    x, y, z = location
    pitch, yaw, roll = rotation

    expr = json_dumps(
        seq(
            f"(lambda act: act.get_name())("
            f"{actor_subsystem()}.spawn_actor_from_class("
            f"{UNREAL}.load_class(None, {class_path!r}), "
            f"{vector(x, y, z)}, "
            f"{rotator(pitch, yaw, roll)}))"
        )
    )
    name = get_bridge().run_python(expr)
    return {"success": True, "actor_name": name}


def list_actors() -> dict:
    """Returns every actor currently in the open level."""
    security.enforce_tier("list_actors")
    expr = json_dumps(
        "[{'name': a.get_name(), 'label': a.get_actor_label(), "
        "'class': a.get_class().get_name()} "
        f"for a in {actor_subsystem()}.get_all_level_actors()]"
    )
    actors = get_bridge().run_python(expr)
    return {"success": True, "actors": actors, "count": len(actors)}


def get_scene_state() -> dict:
    """Lightweight scene snapshot: right now, just the actor list and count."""
    security.enforce_tier("get_scene_state")
    return list_actors()


def set_actor_transform(
    actor_name: str,
    location: tuple[float, float, float] | None = None,
    rotation: tuple[float, float, float] | None = None,
) -> dict:
    """Moves and/or rotates an existing actor, found by its internal name (from list_actors)."""
    security.enforce_tier("set_actor_transform")
    actor_expr = find_actor_by_name(actor_name)
    parts: list[str] = []

    if location is not None:
        x, y, z = location
        parts.append(
            f"{actor_expr}.set_actor_location({vector(x, y, z)}, False, False)"
        )
    if rotation is not None:
        pitch, yaw, roll = rotation
        parts.append(
            f"{actor_expr}.set_actor_rotation({rotator(pitch, yaw, roll)}, False)"
        )

    if not parts:
        return {"success": False, "error": "Nothing to do: pass location and/or rotation."}

    parts.append(repr(actor_name))
    expr = json_dumps(seq(*parts))
    result = get_bridge().run_python(expr)
    return {"success": True, "actor_name": result}


def set_property(actor_name: str, property_name: str, value: str | int | float | bool) -> dict:
    """
    Sets a scalar property (string, number, or bool) on an existing actor.
    For location/rotation use set_actor_transform instead, this is for
    everything else (e.g. a Blueprint-exposed float/bool/string variable).
    """
    security.enforce_tier("set_property")
    if not isinstance(value, (str, int, float, bool)):
        return {"success": False, "error": "set_property only accepts scalar values."}

    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(
        seq(
            f"setattr({actor_expr}, {property_name!r}, {value!r})",
            repr(actor_name),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "actor_name": result, "property": property_name, "value": value}


def get_property(actor_name: str, property_name: str) -> dict:
    """
    Reads one property off an actor by its Python (snake_case) UPROPERTY name,
    the same naming set_property writes. Returns non-scalars too: enums as
    their name, Vector as [x, y, z], Rotator and colors as named-field dicts,
    object references as {class, name}. See remote_snippets.jsonable for the
    full conversion.

    An unknown name, or a missing actor, comes back as success=False with the
    reason rather than raising.
    """
    security.enforce_tier("get_property")
    actor_expr = find_actor_by_name(actor_name, optional=True)

    body = (
        f"a = {actor_expr}\n"
        f"raw = a.get_editor_property({property_name!r}) if a is not None else None\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'value': {jsonable('raw')} if a is not None else None,\n"
        f"      'type': type(raw).__name__ if a is not None else None}}"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "property": property_name,
            "value": None,
            "error": payload.get("error"),
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "property": property_name,
        "value": payload["value"],
        "value_type": payload["type"],
    }


def duplicate_actor(
    actor_name: str,
    offset: tuple[float, float, float] = (100.0, 0.0, 0.0),
) -> dict:
    """
    Duplicates an existing actor. offset is (x, y, z) in Unreal units; it
    defaults to 100 along X because a zero offset stacks the copy in place.

    A missing actor is reported as an error rather than passed through:
    duplicate_actor is documented as returning None on failure, but a null
    actor takes the editor down.
    """
    security.enforce_tier("duplicate_actor")
    actor_expr = find_actor_by_name(actor_name, optional=True)
    ox, oy, oz = offset

    body = (
        f"src = {actor_expr}\n"
        f"dup = ({actor_subsystem()}.duplicate_actor(src, None, {vector(ox, oy, oz)})\n"
        f"       if src is not None else None)\n"
        f"OUT = {{'found': src is not None and dup is not None,\n"
        f"      'error': None if src is not None else {missing_actor_message(actor_name)},\n"
        f"      'value': None,\n"
        f"      'type': None,\n"
        f"      'dup_name': dup.get_name() if dup else None,\n"
        f"      'dup_class': dup.get_class().get_name() if dup else None,\n"
        f"      'dup_label': dup.get_actor_label() if dup else None,\n"
        f"      'location': [dup.get_actor_location().x, dup.get_actor_location().y,"
        f" dup.get_actor_location().z] if dup else None}}"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "duplicated_actor_name": None,
            "error": payload.get("error") or "duplicate_actor returned no actor",
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "duplicated_actor_name": payload["dup_name"],
        "duplicated_actor_class": payload["dup_class"],
        "duplicated_actor_label": payload["dup_label"],
        "location": payload["location"],
        "offset": offset,
    }


def delete_actor(actor_name: str, confirm: bool = False) -> dict:
    """Destroys an actor. Destructive: requires confirm=True."""
    security.enforce_tier("delete_actor", confirm=confirm)
    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(seq(f"{actor_subsystem()}.destroy_actor({actor_expr})", repr(actor_name)))
    result = get_bridge().run_python(expr)
    return {"success": True, "deleted_actor_name": result}


def _linear_color(rgba: tuple[float, float, float, float]) -> str:
    """LinearColor is (r, g, b, a) in that order, but build it by keyword anyway."""
    r, g, b, a = rgba
    return f"{UNREAL}.LinearColor(r={float(r)!r}, g={float(g)!r}, b={float(b)!r}, a={float(a)!r})"


def set_mesh_material_slot(
    actor_name: str,
    material_path: str,
    element_index: int = 0,
    scalar_params: dict[str, float] | None = None,
    vector_params: dict[str, tuple[float, float, float, float]] | None = None,
) -> dict:
    """
    Assigns a runtime material to one slot of the actor's StaticMeshComponent.
    Creates a MaterialInstanceDynamic parented to material_path rather than
    editing the asset, so the change is scoped to this one actor and nothing
    else in the project referencing that material.

    This is the per-actor counterpart to set_material_*_parameter: those write
    to a shared MaterialInstanceConstant asset, this writes to an instance
    that exists only for the actor. The parent material must expose each
    parameter name in its graph for an override to render; a name the parent
    doesn't expose stores a value nothing reads.
    """
    security.enforce_tier("set_mesh_material_slot")
    actor_expr = find_actor_by_name(actor_name)

    scalar_calls = [
        f"mid.set_scalar_parameter_value({k!r}, {float(v)!r})"
        for k, v in (scalar_params or {}).items()
    ]
    vector_calls = [
        f"mid.set_vector_parameter_value({k!r}, {_linear_color(v)})"
        for k, v in (vector_params or {}).items()
    ]

    expr = json_dumps(
        seq(
            repr(actor_name),
            f"(lambda a: (lambda c: (lambda mid: ("
            f"[{', '.join(scalar_calls)}], "
            f"[{', '.join(vector_calls)}], "
            f"c.set_material({int(element_index)!r}, mid), "
            f"[mid.get_scalar_parameter_value(k) for k in {list((scalar_params or {}).keys())!r}], "
            f"c.get_material({int(element_index)!r}).get_name())[-1])("
            f"{UNREAL}.MaterialLibrary.create_dynamic_material_instance("
            f"{UNREAL}.get_editor_subsystem({UNREAL}.UnrealEditorSubsystem), "
            f"{load_asset(material_path)})))"
            f"({actor_component(actor_expr, 'StaticMeshComponent')}))({actor_expr})",
        )
    )
    result = get_bridge().run_python(expr)
    return {
        "success": True,
        "actor_name": actor_name,
        "material_path": material_path,
        "element_index": element_index,
        "assigned_instance": result,
        "scalar_params": scalar_params or {},
        "vector_params": vector_params or {},
    }


def get_mesh_material_slot(actor_name: str, element_index: int = 0) -> dict:
    """
    Reads back what material is actually assigned to one slot, independently of
    set_mesh_material_slot's own expression: goes through get_material() on the
    component rather than the assignment path, and reports the parent material
    and whether the slot holds a per-actor dynamic instance.
    """
    security.enforce_tier("get_mesh_material_slot")
    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(
        seq(
            repr(actor_name),
            f"(lambda a: (lambda c: (lambda m: {{"
            f"'slot': {int(element_index)!r}, "
            f"'instance': m.get_name() if m else None, "
            f"'is_dynamic': isinstance(m, {UNREAL}.MaterialInstanceDynamic), "
            f"'parent': m.get_editor_property('parent').get_path_name() "
            f"if m and m.get_editor_property('parent') else None}})("
            f"c.get_material({int(element_index)!r})))"
            f"({actor_component(actor_expr, 'StaticMeshComponent')}))({actor_expr})",
        )
    )
    return {"success": True, "actor_name": actor_name, **get_bridge().run_python(expr)}
