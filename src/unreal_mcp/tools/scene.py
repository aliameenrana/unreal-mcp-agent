"""
Scene manipulation tools: spawning, reading back, moving, deleting actors.

NOTE on API coverage: these use unreal.EditorActorSubsystem, the modern
(5.0+) replacement for the older EditorLevelLibrary spawn/actor functions.
This has not been exercised against a live editor yet (this package was
built without GUI access to Unreal); treat the exact method names as
"written from documented API, pending verification on first real run."
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, actor_subsystem, find_actor_by_name, json_dumps, seq


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
            f"{UNREAL}.Vector({x}, {y}, {z}), "
            f"{UNREAL}.Rotator({pitch}, {yaw}, {roll})))"
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
    security.enforce_tier("set_property")
    actor_expr = find_actor_by_name(actor_name)
    parts: list[str] = []

    if location is not None:
        x, y, z = location
        parts.append(
            f"{actor_expr}.set_actor_location({UNREAL}.Vector({x}, {y}, {z}), False, False)"
        )
    if rotation is not None:
        pitch, yaw, roll = rotation
        parts.append(
            f"{actor_expr}.set_actor_rotation({UNREAL}.Rotator({pitch}, {yaw}, {roll}), False)"
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


def delete_actor(actor_name: str, confirm: bool = False) -> dict:
    """Destroys an actor. Destructive: requires confirm=True."""
    security.enforce_tier("delete_actor", confirm=confirm)
    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(seq(f"{actor_subsystem()}.destroy_actor({actor_expr})", repr(actor_name)))
    result = get_bridge().run_python(expr)
    return {"success": True, "deleted_actor_name": result}
