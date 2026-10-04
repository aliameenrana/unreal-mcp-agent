"""
Animation on a SkeletalMeshComponent.

Instance-level only: this sets an AnimSequence or Animation Blueprint on an
existing component and drives playback. It does not author animation assets,
create montages, or build Animation Blueprints.

The mode has to be set before or alongside the asset, and the ordering is not
obvious. `set_animation` on a component left in ANIMATION_BLUEPRINT mode has no
effect, because that mode ignores the single node. So these tools set the mode
first, then the asset, and verify by reading the mode back rather than trusting
the setter.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    actor_component,
    actor_subsystem,
    guarded,
    indent_block,
    load_asset,
)

# Friendly names for AnimationMode. There is no member for "use a raw
# AnimSequence", which is what ANIMATION_SINGLE_NODE is.
ANIMATION_MODES = {
    "single_node": "ANIMATION_SINGLE_NODE",
    "blueprint": "ANIMATION_BLUEPRINT",
    "custom": "ANIMATION_CUSTOM_MODE",
}


def _single_node_helpers() -> str:
    """
    Source for the readers a SkeletalMeshComponent needs, none of which exist on
    the component itself. Measured on 5.8:

    - **No `get_animation()`.** The assigned asset lives on the
      `AnimSingleNodeInstance` returned by `get_anim_instance()`, read with
      `get_animation_asset()`. In blueprint mode there is no single-node instance
      at all, so the asset reads as None, which is correct: the anim instance
      holds the animation instead.
    - **No `get_playback_position()` / `get_playback_length()`** on the
      component. Those live on the anim instance.
    - **No `pause()`** on the component. Pausing is done by setting the play rate
      to 0, which is why `pause_animation` does that.
    - `get_play_rate()` and `is_playing()` *do* exist on the component.
    - Neither the asset nor `play_rate` is readable via `get_editor_property`;
      both raise.
    """
    return """
def _anim_instance(smc):
    try:
        return smc.get_anim_instance()
    except Exception:
        return None


def _single_node_asset_obj(smc):
    inst = _anim_instance(smc)
    if inst is None:
        return None
    if hasattr(inst, "get_animation_asset"):
        try:
            return inst.get_animation_asset()
        except Exception:
            return None
    return None


def _single_node_asset(smc):
    a = _single_node_asset_obj(smc)
    return str(a) if a is not None else None


def _playback_length(smc):
    # Not from the component and not from the anim instance: the component has no
    # get_playback_length, and AnimSingleNodeInstance's position and length
    # getters are C++-protected and not exposed to Python (AttributeError).
    # The AnimSequence itself does expose get_play_length, so read it there.
    asset = _single_node_asset_obj(smc)
    if asset is None:
        return None
    try:
        return float(asset.get_play_length())
    except Exception:
        return None


def _playback_position(smc):
    # Not readable at all from Python: the component has no getter and the anim
    # instance's is protected. Reported as None rather than guessed at.
    return None

"""

def set_animation(
    actor_name: str,
    animation_path: str,
    animation_mode: str = "single_node",
) -> dict:
    """
    Puts an animation asset on a character's skeletal mesh component.

    animation_path is a package path to an AnimSequence or an Animation Blueprint.
    animation_mode is "single_node" (the default, for a plain AnimSequence),
    "blueprint" or "custom". **The mode is set first**, because setting an asset
    on a component left in blueprint mode silently does nothing: that mode reads
    the anim instance, not the single node.

    **Switching mode away from single_node clears the assigned asset.** Verified:
    set_animation, then switch to blueprint, then back to single_node, and the
    animation reads as unassigned again. So after changing mode with
    set_animation_mode, call set_animation again rather than assuming the asset
    survived.

    Does not start playback. Use play_animation for that.
    """
    security.enforce_tier("set_animation")
    security.check_destination_path(animation_path)

    mode_enum = ANIMATION_MODES.get(animation_mode)
    if mode_enum is None:
        return {
            "success": False,
            "error": f"Unknown animation_mode {animation_mode!r}. Choose one of "
                     f"{', '.join(sorted(ANIMATION_MODES))}.",
        }

    body = (
        f"anim = {load_asset(animation_path)}\n"
        f"if anim is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {animation_path!r},\n"
        f"          'set': False}}\n"
        f"else:\n"
        f"    smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"    if smc is None:\n"
        f"        OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"              'set': False}}\n"
        f"    else:\n"
        + indent_block(_single_node_helpers(), 8) +
        f"        smc.set_animation_mode({UNREAL}.AnimationMode.{mode_enum})\n"
        f"        smc.set_animation(anim)\n"
        # There is no SkeletalMeshComponent.get_animation(); the asset is read
        # back through the AnimSingleNodeInstance the helper defines.
        f"        OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"              'animation_mode': str(smc.get_animation_mode()),\n"
        f"              'animation': _single_node_asset(smc),\n"
        f"              'animation_class': anim.get_class().get_name(),\n"
        f"              'playing': bool(smc.is_playing())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "animation_path": animation_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "animation_path": animation_path,
        "animation_class": payload.get("animation_class"),
        "animation_mode": payload.get("animation_mode"),
        # Read back rather than reporting what was requested.
        "animation": payload.get("animation"),
        "playing": payload.get("playing"),
        "error": None,
    }


def set_animation_mode(actor_name: str, animation_mode: str) -> dict:
    """
    Sets only the animation mode, leaving whatever asset is assigned in place.

    Useful for switching an already-configured character between a single
    AnimSequence and an Animation Blueprint.
    """
    security.enforce_tier("set_animation_mode")

    mode_enum = ANIMATION_MODES.get(animation_mode)
    if mode_enum is None:
        return {
            "success": False,
            "error": f"Unknown animation_mode {animation_mode!r}. Choose one of "
                     f"{', '.join(sorted(ANIMATION_MODES))}.",
        }

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"          'set': False}}\n"
        f"else:\n"
        f"    _before = str(smc.get_animation_mode())\n"
        f"    smc.set_animation_mode({UNREAL}.AnimationMode.{mode_enum})\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'mode_before': _before,\n"
        f"          'animation_mode': str(smc.get_animation_mode())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "animation_mode": payload.get("animation_mode"),
        "mode_before": payload.get("mode_before"),
        "error": None,
    }


def play_animation(actor_name: str, looping: bool = False) -> dict:
    """
    Starts playback on a character's skeletal mesh component.

    With no animation assigned, or in blueprint mode with no anim instance,
    this does nothing and reports `playing: False` rather than pretending.
    """
    security.enforce_tier("play_animation")

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"          'playing': False}}\n"
        f"else:\n"
        + indent_block(_single_node_helpers(), 4) +
        # Read the asset and its length BEFORE play(): play() recreates the
        # anim instance, and the replacement has no asset on it yet, so reading
        # afterwards reports the animation as unassigned.
        f"    _asset = _single_node_asset(smc)\n"
        f"    _len = _playback_length(smc)\n"
        f"    smc.play({bool(looping)!r})\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'playing': bool(smc.is_playing()),\n"
        f"          'animation': _asset,\n"
        f"          'animation_mode': str(smc.get_animation_mode()),\n"
        f"          'playback_position': _playback_position(smc),\n"
        f"          'playback_length': _len,\n"
        f"          'looping': {bool(looping)!r}}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "playing": False, "error": payload.get("error")}
    return {
        "success": bool(payload.get("playing")),
        "actor_name": actor_name,
        "playing": payload.get("playing"),
        "animation": payload.get("animation"),
        "animation_mode": payload.get("animation_mode"),
        "playback_position": payload.get("playback_position"),
        "playback_length": payload.get("playback_length"),
        "looping": payload.get("looping"),
        "error": None,
    }


def stop_animation(actor_name: str) -> dict:
    """
    Stops playback and rewinds to the start.
    """
    security.enforce_tier("stop_animation")

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"          'playing': False}}\n"
        f"else:\n"
        f"    _was = bool(smc.is_playing())\n"
        f"    smc.stop()\n"
        f"    OUT = {{'found': True, 'error': None, 'was_playing': _was,\n"
        f"          'playing': bool(smc.is_playing()),\n"
        f"          'playback_position': None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "playing": False, "error": payload.get("error")}
    return {
        "success": not payload.get("playing"),
        "actor_name": actor_name,
        "playing": payload.get("playing"),
        "was_playing": payload.get("was_playing"),
        "playback_position": None,
        "error": None,
    }


def pause_animation(actor_name: str) -> dict:
    """
    Holds playback on the current frame by setting the play rate to 0.

    There is no `pause()` on a SkeletalMeshComponent in 5.8, and the protected
    position getter means the frame cannot be reported back, so this reads as a
    rate change rather than a paused state. `play_animation` then resumes.
    `stop` would rewind, which is why this exists at all.
    """
    security.enforce_tier("pause_animation")

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"          'paused': False}}\n"
        f"else:\n"
        f"    _rate_before = smc.get_play_rate()\n"
        f"    smc.set_play_rate(0.0)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'paused': smc.get_play_rate() == 0.0,\n"
        f"          'playing': bool(smc.is_playing()),\n"
        f"          'play_rate_before': _rate_before,\n"
        f"          'play_rate': smc.get_play_rate()}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "paused": False, "error": payload.get("error")}
    return {
        "success": bool(payload.get("paused")),
        "actor_name": actor_name,
        "paused": payload.get("paused"),
        "playing": payload.get("playing"),
        "play_rate": payload.get("play_rate"),
        "play_rate_before": payload.get("play_rate_before"),
        "error": None,
    }


def set_play_rate(actor_name: str, play_rate: float) -> dict:
    """
    Sets playback speed. 1.0 is normal, 2.0 is double, 0.0 effectively holds the
    frame, and a negative rate plays backwards where the asset allows it.
    """
    security.enforce_tier("set_play_rate")

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent',\n"
        f"          'set': False}}\n"
        f"else:\n"
        f"    _before = smc.get_play_rate()\n"
        f"    smc.set_play_rate(float({float(play_rate)!r}))\n"
        f"    OUT = {{'found': True, 'error': None, 'set': True,\n"
        f"          'play_rate_before': _before,\n"
        f"          'play_rate': smc.get_play_rate()}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "actor_name": actor_name,
        "play_rate": payload.get("play_rate"),
        "play_rate_before": payload.get("play_rate_before"),
        "error": None,
    }


def get_animation_state(actor_name: str) -> dict:
    """
    Reports a character's animation setup: mode, assigned asset, whether it is
    playing or paused, playback position and length, and play rate.

    This is the read side of the tools above, and the one to call before
    changing anything: it shows the mode, which is what determines whether an
    asset assignment will have any effect.
    """
    security.enforce_tier("get_animation_state")

    body = (
        f"smc = {actor_component(find_actor_expr(actor_name), 'SkeletalMeshComponent')}\n"
        f"if smc is None:\n"
        f"    OUT = {{'found': False, 'error': 'no SkeletalMeshComponent'}}\n"
        f"else:\n"
        + indent_block(_single_node_helpers(), 4) +
        f"    _mesh = smc.get_skeletal_mesh_asset()\n"
        f"    _asset = _single_node_asset_obj(smc)\n"
        f"    _len = _playback_length(smc)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'has_component': True,\n"
        f"          'animation_mode': str(smc.get_animation_mode()),\n"
        f"          'animation': str(_asset) if _asset else None,\n"
        f"          'animation_class': _asset.get_class().get_name()\n"
        f"                          if _asset else None,\n"
        f"          'skeletal_mesh': str(_mesh) if _mesh else None,\n"
        f"          'is_playing': bool(smc.is_playing()),\n"
        f"          'playback_position': _playback_position(smc),\n"
        f"          'playback_length': _len,\n"
        f"          'play_rate': smc.get_play_rate(),\n"
        f"          'looping': (bool(_asset) and _len > 0)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name, "error": payload.get("error")}
    return {
        "success": True,
        "actor_name": actor_name,
        "has_component": True,
        "animation_mode": payload.get("animation_mode"),
        "animation": payload.get("animation"),
        "animation_class": payload.get("animation_class"),
        "skeletal_mesh": payload.get("skeletal_mesh"),
        "is_playing": payload.get("is_playing"),
        "playback_position": payload.get("playback_position"),
        "playback_length": payload.get("playback_length"),
        "play_rate": payload.get("play_rate"),
        "looping": payload.get("looping"),
    }


def find_actor_expr(actor_name: str) -> str:
    """
    Actor lookup that reports a missing actor rather than raising, so every tool
    here returns {"success": False, "error": ...} for a bad name instead of
    surfacing StopIteration.
    """
    return (
        f"next((a for a in {actor_subsystem()}.get_all_level_actors() "
        f"if a.get_name() == {actor_name!r}), None)"
    )
