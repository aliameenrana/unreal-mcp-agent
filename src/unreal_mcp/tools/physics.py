"""
Collision and physics on a PrimitiveComponent.

Instance-level only: collision settings on a component in the level, not the
collision setup of a mesh asset (that is `set_mesh_collision_preset` in
`meshes.py`).

The distinction that trips people up is object type versus response. A component
has an **object type** (what it *is*: a pawn, a world static, a vehicle) and
**responses per channel** (what it *does* to things: block, overlap, ignore).
`set_collision_enabled` only turns collision on or off wholesale. Anything finer
needs `set_collision_profile_name` with one of the engine's named profiles, or
the per-channel setters. So the three useful levels are exposed separately:
`set_collision_enabled`, `set_collision_profile_name`, and
`set_collision_response`.

`set_simulate_physics` is only meaningful for a component with a body, which
means a mesh with a simple collision primitive. Setting it on one without does
nothing and reports back as not simulating rather than pretending.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    actor_component,
    actor_subsystem,
    guarded,
)

# CollisionEnabled. There is no generic "on": which of the enabled states you
# want depends on whether the thing blocks, overlaps or is only queried.
COLLISION_ENABLED = {
    "no_collision": "NO_COLLISION",
    "query_only": "QUERY_ONLY",
    "query_and_physics": "QUERY_AND_PHYSICS",
    "query_and_probe": "QUERY_AND_PROBE",
    "physics_only": "PHYSICS_ONLY",
    "probe_only": "PROBE_ONLY",
}

# Collision channels. These are `unreal.CollisionChannel` members, spelled
# ECC_*: not EngineTypes.CollisionChannel, which does not exist, and not bare
# ECC_Pawn at module level, which does not exist either. Passing an int raises
# "Failed to convert parameter 'channel'".
COLLISION_CHANNELS = {
    "visibility": "ECC_VISIBILITY",
    "camera": "ECC_CAMERA",
    "world_static": "ECC_WORLD_STATIC",
    "world_dynamic": "ECC_WORLD_DYNAMIC",
    "pawn": "ECC_PAWN",
    "physics_body": "ECC_PHYSICS_BODY",
    "vehicle": "ECC_VEHICLE",
    "destructible": "ECC_DESTRUCTIBLE",
}

# Responses. `unreal.CollisionResponseType`, ECR_*, spelled in caps. Note
# `unreal.CollisionResponse` is a *struct*, not an enum, and has no ECR_ members.
COLLISION_RESPONSES = {
    "block": "ECR_BLOCK",
    "overlap": "ECR_OVERLAP",
    "ignore": "ECR_IGNORE",
}

# The profiles worth naming. These are the engine's built-ins; set_collision_
# profile_name takes the name string directly, so this list is documentation and
# validation rather than a closed set.
COMMON_PROFILES = (
    "BlockAll", "BlockAllDynamic", "OverlapAll", "OverlapAllDynamic",
    "NoCollision", "BlockStatic", "OverlapStatic", "Pawn", "PhysicsActor",
    "Vehicle", "Destructible", "InvisibleWall", "Trigger", "Ragdoll",
    "UI",
)


def _component_snippet(actor_name: str) -> str:
    """
    Source that binds `smc` to the actor's PrimitiveComponent and reports a
    missing actor or a missing component as `err`, rather than raising.
    """
    return (
        f"_actor = next((a for a in {actor_subsystem()}.get_all_level_actors() "
        f"if a.get_name() == {actor_name!r}), None)\n"
        f"smc = None\n"
        f"err = None\n"
        f"if _actor is None:\n"
        f"    err = 'no actor named ' + {actor_name!r}\n"
        f"else:\n"
        f"    smc = _actor.get_component_by_class({UNREAL}.PrimitiveComponent)\n"
        f"    if smc is None:\n"
        f"        err = 'actor has no PrimitiveComponent'\n"
    )


def get_collision_state(actor_name: str) -> dict:
    """
    Reports an actor's collision setup: whether it is enabled, its profile name,
    object type, per-channel responses, and whether it is simulating physics.

    This is the read side, and the thing to call before changing anything, since
    `set_collision_profile_name` overwrites every channel at once.
    """
    security.enforce_tier("get_collision_state")

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err}}\n"
        f"else:\n"
        f"    _resp = {{}}\n"
        f"    for _name, _member in {sorted((k, v) for k, v in COLLISION_CHANNELS.items())!r}:\n"
        f"        try:\n"
        f"            _resp[_name] = str(smc.get_collision_response_to_channel(\n"
        f"                getattr({UNREAL}.CollisionChannel, _member)))\n"
        f"        except Exception:\n"
        f"            _resp[_name] = None\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'actor_name': {actor_name!r},\n"
        f"          'component': smc.get_name(),\n"
        f"          'collision_enabled': str(smc.get_collision_enabled()),\n"
        f"          'profile_name': str(smc.get_collision_profile_name()),\n"
        f"          'object_type': str(smc.get_collision_object_type()),\n"
        f"          'simulate_physics': bool(smc.is_simulating_physics()),\n"
        f"          'any_simulating_physics': bool(smc.is_any_simulating_physics()),\n"
        f"          'generate_overlap_events':\n"
        f"              bool(smc.get_editor_property('generate_overlap_events')),\n"
        f"          'responses': _resp}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": True,
        "actor_name": actor_name,
        "component": payload.get("component"),
        "collision_enabled": payload.get("collision_enabled"),
        "profile_name": payload.get("profile_name"),
        "object_type": payload.get("object_type"),
        "simulate_physics": payload.get("simulate_physics"),
        "any_simulating_physics": payload.get("any_simulating_physics"),
        "generate_overlap_events": payload.get("generate_overlap_events"),
        "responses": payload.get("responses") or {},
    }


def set_collision_enabled(actor_name: str, mode: str) -> dict:
    """
    Turns collision on or off, or picks one of the specific enabled states.

    mode is "no_collision", "query_only", "query_and_physics", "query_and_probe",
    "physics_only" or "probe_only". There is deliberately no plain "on": whether
    something should block, overlap or merely be traceable is a real choice, and
    `query_and_physics` is not always the answer. For named setups use
    set_collision_profile_name instead.

    Read back afterwards with get_collision_state rather than trusting the call:
    this setter and the profile setter fight over the same state.
    """
    security.enforce_tier("set_collision_enabled")

    enum = COLLISION_ENABLED.get(mode)
    if enum is None:
        return {
            "success": False,
            "error": f"Unknown collision mode {mode!r}. Choose one of "
                     f"{', '.join(sorted(COLLISION_ENABLED))}.",
        }

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'set': False}}\n"
        f"else:\n"
        f"    _before = str(smc.get_collision_enabled())\n"
        f"    smc.set_collision_enabled({UNREAL}.CollisionEnabled.{enum})\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'enabled_before': _before,\n"
        f"          'collision_enabled': str(smc.get_collision_enabled()),\n"
        f"          'profile_name': str(smc.get_collision_profile_name())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "collision_enabled": payload.get("collision_enabled"),
        "enabled_before": payload.get("enabled_before"),
        "profile_name": payload.get("profile_name"),
        "error": None,
    }


def set_collision_profile(actor_name: str, profile_name: str) -> dict:
    """
    Applies one of the engine's named collision profiles, such as "BlockAll",
    "OverlapAllDynamic" or "NoCollision".

    This sets the object type *and* every channel response in one go, so it
    overwrites anything set_collision_response or set_collision_enabled did.
    The profile name is validated against the built-in list so a typo is caught
    before it silently produces a component that collides with nothing.

    To build a setup that is not one of the presets, set the object type and then
    individual channels with set_collision_response.
    """
    security.enforce_tier("set_collision_profile")

    if not profile_name:
        return {"success": False, "error": "profile_name must not be empty."}

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'set': False}}\n"
        f"else:\n"
        f"    _before = str(smc.get_collision_profile_name())\n"
        f"    smc.set_collision_profile_name({profile_name!r}, False)\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'profile_before': _before,\n"
        f"          'profile_name': str(smc.get_collision_profile_name()),\n"
        f"          'collision_enabled': str(smc.get_collision_enabled()),\n"
        f"          'object_type': str(smc.get_collision_object_type())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "profile_name": payload.get("profile_name"),
        "profile_before": payload.get("profile_before"),
        "collision_enabled": payload.get("collision_enabled"),
        "object_type": payload.get("object_type"),
        "error": None,
    }


def set_collision_object_type(actor_name: str, object_type: str) -> dict:
    """
    Sets what the component *is* for collision purposes: world_static,
    world_dynamic, pawn, physics_body, vehicle, destructible, camera or
    visibility. Names are lowercase with underscores; see COLLISION_CHANNELS.

    Separate from the response settings. A component that is a Pawn but has Pawn
    set to Ignore passes through other pawns, which is a different thing from not
    being a Pawn at all.
    """
    security.enforce_tier("set_collision_object_type")

    enum = COLLISION_CHANNELS.get(object_type)
    if enum is None:
        return {
            "success": False,
            "error": f"Unknown object type {object_type!r}. Choose one of "
                     f"{', '.join(sorted(COLLISION_CHANNELS))}.",
        }

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'set': False}}\n"
        f"else:\n"
        f"    _before = str(smc.get_collision_object_type())\n"
        f"    smc.set_collision_object_type({UNREAL}.CollisionChannel.{enum})\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'object_type_before': _before,\n"
        f"          'object_type': str(smc.get_collision_object_type())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "object_type": payload.get("object_type"),
        "object_type_before": payload.get("object_type_before"),
        "error": None,
    }


def set_collision_response(actor_name: str, channel: str, response: str) -> dict:
    """
    Sets what this component does to one specific channel: "block", "overlap" or
    "ignore".

    The fine-grained tool, for a setup that is not one of the named profiles.
    Call it in a loop to build a custom setup. Note that
    set_collision_profile_name overwrites every channel, so do the profile first
    and the channels after.

    channel is one of COLLISION_CHANNELS; response is one of COLLISION_RESPONSES.
    Both are validated here, because an invalid one raises inside the editor
    rather than failing quietly.
    """
    security.enforce_tier("set_collision_response")

    channel_enum = COLLISION_CHANNELS.get(channel)
    if channel_enum is None:
        return {
            "success": False,
            "error": f"Unknown channel {channel!r}. Choose one of "
                     f"{', '.join(sorted(COLLISION_CHANNELS))}.",
        }
    response_enum = COLLISION_RESPONSES.get(response.lower())
    if response_enum is None:
        return {
            "success": False,
            "error": f"Unknown response {response!r}. Choose one of "
                     f"{', '.join(sorted(COLLISION_RESPONSES))}.",
        }

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'set': False}}\n"
        f"else:\n"
        f"    _ch = {UNREAL}.CollisionChannel.{channel_enum}\n"
        f"    _before = str(smc.get_collision_response_to_channel(_ch))\n"
        f"    smc.set_collision_response_to_channel(\n"
        f"        _ch, {UNREAL}.CollisionResponseType.{response_enum})\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'channel': {channel!r}, 'requested': {response!r},\n"
        f"          'response_before': _before,\n"
        f"          'response': str(smc.get_collision_response_to_channel(_ch))}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "channel": payload.get("channel"),
        "requested": payload.get("requested"),
        "response": payload.get("response"),
        "response_before": payload.get("response_before"),
        "error": None,
    }


def set_simulate_physics(actor_name: str, enabled: bool = True) -> dict:
    """
    Turns rigid-body simulation on or off.

    Only meaningful for a component that has a physics body, which means a mesh
    asset with simple collision. On a component without one this does nothing and
    reports back `simulate_physics: False`, rather than reporting success for a
    change that never happened.

    Simulation does not run in the editor viewport. Use
    start_play_in_editor_simulate and then re-read the state during the session,
    which is the only way to observe physics actually doing anything.
    """
    security.enforce_tier("set_simulate_physics")

    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'set': False}}\n"
        f"else:\n"
        f"    _before = bool(smc.is_simulating_physics())\n"
        f"    smc.set_simulate_physics({bool(enabled)!r})\n"
        f"    # Read back, and note whether it actually took: a component with no\n"
        f"    # physics body silently refuses to simulate.\n"
        f"    _after = bool(smc.is_simulating_physics())\n"
        f"    OUT = {{'found': True, 'error': None, 'set': _after == {bool(enabled)!r},\n"
        f"          'requested': {bool(enabled)!r},\n"
        f"          'simulate_physics_before': _before,\n"
        f"          'simulate_physics': _after,\n"
        f"          'took_effect': _after == {bool(enabled)!r}}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    took = payload.get("took_effect")
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "simulate_physics": payload.get("simulate_physics"),
        "simulate_physics_before": payload.get("simulate_physics_before"),
        "requested": payload.get("requested"),
        "took_effect": took,
        "error": None if took else (
            "the component has no physics body to simulate, so the request was "
            "ignored; give its mesh simple collision first"),
    }


def apply_physics_impulse(
    actor_name: str,
    impulse: tuple[float, float, float] = (0.0, 0.0, 1000.0),
    b_velocity_change: bool = False,
) -> dict:
    """
    Applies an impulse to an actor's primitive component.

    Needs `simulate_physics` on, so this only does anything in a
    simulate-in-editor session. b_velocity_change=True treats the numbers as a
    velocity change rather than a mass-scaled impulse, which makes the result
    independent of the object's mass.

    This is the tool that makes physics observable: set it, then read the
    component's transform back to see the object actually move.

    Reads the position with `get_world_location()`. There is no
    `get_component_location()` on a PrimitiveComponent, and no
    `get_attach_location()` either; `get_world_location()` is the form that
    exists.
    """
    security.enforce_tier("apply_physics_impulse")

    x, y, z = impulse
    body = (
        _component_snippet(actor_name) +
        f"if err is not None:\n"
        f"    OUT = {{'found': False, 'error': err, 'applied': False}}\n"
        f"else:\n"
        f"    _sim = bool(smc.is_simulating_physics())\n"
        f"    _loc = smc.get_world_location()\n"
        f"    smc.add_impulse({UNREAL}.Vector({float(x)!r}, {float(y)!r}, {float(z)!r}),\n"
        f"                   {UNREAL}.Name('NAME_None'), {bool(b_velocity_change)!r})\n"
        f"    _new = smc.get_world_location()\n"
        f"    OUT = {{'found': True, 'error': None, 'applied': True,\n"
        f"          'was_simulating': _sim,\n"
        f"          'impulse': [{float(x)!r}, {float(y)!r}, {float(z)!r}],\n"
        f"          'velocity_change': {bool(b_velocity_change)!r},\n"
        f"          'location_before': [_loc.x, _loc.y, _loc.z],\n"
        f"          'location_after': [_new.x, _new.y, _new.z]}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=120.0)
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": True,
        "actor_name": actor_name,
        "applied": True,
        "was_simulating": payload.get("was_simulating"),
        "impulse": payload.get("impulse"),
        "velocity_change": payload.get("velocity_change"),
        "location_before": payload.get("location_before"),
        "location_after": payload.get("location_after"),
        "error": None,
    }
