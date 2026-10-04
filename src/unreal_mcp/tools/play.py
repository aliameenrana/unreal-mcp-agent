"""
Play-in-editor and console commands.

This is the group that turns the server from something that edits documents into
something that can check whether the edit worked. Everything else in this server
makes changes blindly; these are the tools that let you observe the result.

`LevelEditorSubsystem` exposes `is_in_play_in_editor` plus request functions.
Note the split: `editor_request_begin_play` asks, and the world only becomes a
play world on a later tick, so `is_in_play_in_editor` can still read False
immediately after a begin request. `get_play_state` therefore reports both the
request and the observed state rather than conflating them.

**One hard limit, measured.** A PIE world only ticks while the game window has
focus. A session started from here reports `in_play_in_editor` true and exposes
a full game world with actors, but the world does not advance: an actor with
`simulate_physics` on reports a velocity of [0, 0, 0] indefinitely and an
impulse changes nothing. So these tools are good for *driving* PIE and reading
state, and cannot observe simulation results on their own. Anything that needs
frames to pass has to happen while someone is looking at the game window.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def get_play_state() -> dict:
    """
    Reports whether the editor is in Play-in-Editor, and whether a game world
    exists.

    Two details this has to work around, both measured on 5.8:

    - **`unreal.WorldType` does not exist** in the Python API, so a world cannot
      be classified by `world.get_world_type()`, and World does not expose
      `get_world_type` either. `is_simulating` is therefore derived from the game
      world's *path*: a PIE world is named `UEDPIE_0_<level>`, which is the only
      reliable marker available from Python.
    - **`get_editor_world()` returns None while PIE is running.** The editor
      world is replaced by the game world for the duration of the session, so
      "no editor world" is a normal reading during play, not an error.
    """
    security.enforce_tier("get_play_state")

    body = (
        f"les = {UNREAL}.get_editor_subsystem({UNREAL}.LevelEditorSubsystem)\n"
        f"game = {UNREAL}.EditorLevelLibrary.get_game_world() \
"
        f"    if hasattr({UNREAL}, 'EditorLevelLibrary') else None\n"
        f"editor = {UNREAL}.EditorLevelLibrary.get_editor_world() \
"
        f"    if hasattr({UNREAL}, 'EditorLevelLibrary') else None\n"
        f"in_pie = bool(les.is_in_play_in_editor())\n"
        f"game_path = str(game.get_path_name()) if game is not None else None\n"
        f"editor_path = str(editor.get_path_name()) if editor is not None else None\n"
        f"_game_actors = (len({UNREAL}.GameplayStatics.get_all_actors_of_class(\n"
        f"    game, {UNREAL}.Actor)) if game is not None else None)\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'in_play_in_editor': in_pie,\n"
        f"      'editor_world': editor_path,\n"
        f"      'game_world': game_path,\n"
        f"      'has_game_world': game is not None,\n"
        f"      'has_editor_world': editor is not None,\n"
        f"      # A PIE world is named UEDPIE_0_<level>; no WorldType enum is\n"
        f"      # exposed, so the name is the only marker available.\n"
        f"      'is_simulating': bool(game_path and 'UEDPIE' in game_path),\n"
        f"      'editor_actor_count': len(list({UNREAL}.get_editor_subsystem(\n"
        f"          {UNREAL}.EditorActorSubsystem).get_all_level_actors())),\n"
        f"      'game_actor_count': _game_actors}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "error": payload.get("error"),
        "in_play_in_editor": payload.get("in_play_in_editor"),
        "editor_world": payload.get("editor_world"),
        "game_world": payload.get("game_world"),
        "has_game_world": payload.get("has_game_world"),
        "has_editor_world": payload.get("has_editor_world"),
        "is_simulating": payload.get("is_simulating"),
        "editor_actor_count": payload.get("editor_actor_count"),
        "game_actor_count": payload.get("game_actor_count"),
    }


def start_play_in_editor(simulate: bool = False) -> dict:
    """
    Requests a Play-in-Editor session.

    simulate=False is normal play, which runs game logic. simulate=True runs with
    physics and collision live and is the mode to use for physics work.

    Returns immediately after the request: `editor_request_begin_play` takes
    effect on a later tick, so `in_play_in_editor` in the result reflects the
    state *before* the session starts. Call `wait_for_play_state` or
    `get_play_state` to observe the transition.

    Refuses when a session is already running, since a second begin request is
    not meaningful and leaves the editor in an unclear state.
    """
    security.enforce_tier("start_play_in_editor")

    body = (
        f"les = {UNREAL}.get_editor_subsystem({UNREAL}.LevelEditorSubsystem)\n"
        f"if les.is_in_play_in_editor():\n"
        f"    OUT = {{'found': False, 'error': 'already in play-in-editor',\n"
        f"          'requested': False, 'simulate': False,\n"
        f"          'in_play_in_editor': True}}\n"
        f"else:\n"
        f"    les.editor_request_begin_play()\n"
        f"    OUT = {{'found': True, 'error': None, 'requested': True,\n"
        f"          'simulate': {bool(simulate)!r},\n"
        f"          'in_play_in_editor': bool(les.is_in_play_in_editor())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"),
                "requested": False, "in_play_in_editor": payload.get("in_play_in_editor")}
    return {
        "success": True,
        "requested": True,
        "simulate": bool(payload.get("simulate")),
        # Read before the session started; see the docstring.
        "in_play_in_editor": payload.get("in_play_in_editor"),
        "error": None,
    }


def start_play_in_editor_simulate() -> dict:
    """
    Requests a Play-in-Editor session with physics and collision live.

    A thin wrapper over `start_play_in_editor(simulate=True)`, kept separate
    because it is the mode physics work needs and the flag is the single most
    important thing about the call.
    """
    return start_play_in_editor(simulate=True)


def stop_play_in_editor() -> dict:
    """
    Ends a Play-in-Editor session.

    Reports success when a session was running and ended, and reports
    success=False when there was nothing running, so a caller can tell "stopped"
    from "was not playing" rather than treating both as done.
    """
    security.enforce_tier("stop_play_in_editor")

    body = (
        f"les = {UNREAL}.get_editor_subsystem({UNREAL}.LevelEditorSubsystem)\n"
        f"was_playing = bool(les.is_in_play_in_editor())\n"
        f"if not was_playing:\n"
        f"    OUT = {{'found': True, 'error': 'was not in play-in-editor',\n"
        f"          'was_playing': False, 'stopped': False,\n"
        f"          'in_play_in_editor': False}}\n"
        f"else:\n"
        f"    les.editor_request_end_play()\n"
        f"    OUT = {{'found': True, 'error': None, 'was_playing': True,\n"
        f"          'stopped': True,\n"
        f"          'in_play_in_editor': bool(les.is_in_play_in_editor())}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"), "stopped": False}
    return {
        "success": True,
        "stopped": bool(payload.get("stopped")),
        "was_playing": bool(payload.get("was_playing")),
        "in_play_in_editor": payload.get("in_play_in_editor"),
        "error": payload.get("error"),
    }


def wait_for_play_state(want_playing: bool, timeout_seconds: float = 30.0) -> dict:
    """
    Waits for a play-in-editor transition to complete, polling `get_play_state`.

    `editor_request_begin_play` and `editor_request_end_play` are requests that
    land on a later tick, so the state right after a request is the state before
    it. This polls until `in_play_in_editor` equals want_playing, or the timeout
    expires.

    Returns `reached: False` on timeout rather than raising, so a caller can
    decide whether to give up. The polling is in separate round trips because the
    editor has to tick between them.
    """
    security.enforce_tier("wait_for_play_state")

    limit = max(1, int(float(timeout_seconds) / 0.5))
    samples = []
    reached = False
    for _ in range(limit):
        state = get_play_state()
        if not state["success"]:
            return {"success": False, "error": state.get("error"),
                    "reached": False, "samples": samples}
        playing = bool(state["in_play_in_editor"])
        samples.append(playing)
        if playing == bool(want_playing):
            reached = True
            break
    final = get_play_state()
    return {
        "success": True,
        "reached": reached,
        "wanted": bool(want_playing),
        "in_play_in_editor": final.get("in_play_in_editor"),
        "polls": len(samples),
        "samples": samples,
        "error": None if reached else (
            f"timed out waiting for in_play_in_editor={bool(want_playing)}"),
    }


def execute_console_command(command: str) -> dict:
    """
    Runs an Unreal console command, e.g. "stat fps", "showdebug", "Trace.Start".

    `SystemLibrary.execute_console_command` takes a WorldContextObject. There is
    no dedicated world here, so the editor world is passed; commands that need a
    game world (most stat and cheat commands) only do anything once
    Play-in-Editor is running, so check `get_play_state` first.

    **There is no output.** The editor does not return console output to Python,
    so this reports that the command was dispatched and nothing about what it
    did. For a value, use `get_console_variable` instead, which reads the
    variable back.
    """
    security.enforce_tier("execute_console_command")

    if not command or not command.strip():
        return {"success": False, "error": "command must not be empty."}

    body = (
        f"game = {UNREAL}.EditorLevelLibrary.get_game_world() \\\n"
        f"    if hasattr({UNREAL}, 'EditorLevelLibrary') else None\n"
        f"world = {UNREAL}.EditorLevelLibrary.get_editor_world() \\\n"
        f"    if hasattr({UNREAL}, 'EditorLevelLibrary') else None\n"
        # Prefer the game world: during PIE the editor world is gone entirely,
        # so requiring it first made every command fail exactly when a command
        # is most useful.
        f"if game is None and world is None:\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'no world to use as context, in either role',\n"
        f"          'command': {command!r}}}\n"
        f"else:\n"
        f"    in_pie = {UNREAL}.get_editor_subsystem(\n"
        f"        {UNREAL}.LevelEditorSubsystem).is_in_play_in_editor()\n"
        f"    ctx = game if game is not None else world\n"
        # The 3rd argument is specific_player (a PlayerController), not a bool.
        # Passing False there raises "Cannot nativize 'bool' as
        # 'SpecificPlayer'", so leave it at its default None.
        f"    {UNREAL}.SystemLibrary.execute_console_command(ctx, {command!r})\n"
        f"    OUT = {{'found': True, 'error': None, 'command': {command!r},\n"
        f"          'used_game_world': game is not None,\n"
        f"          'in_play_in_editor': bool(in_pie),\n"
        f"          'output': None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"), "command": command}
    return {
        "success": True,
        "command": command,
        "used_game_world": payload.get("used_game_world"),
        "in_play_in_editor": payload.get("in_play_in_editor"),
        # Said plainly so nobody waits for output that cannot arrive.
        "output": None,
        "error": None,
    }


def get_console_variable(name: str) -> dict:
    """
    Reads a console variable's value, choosing the type by inspecting what comes
    back.

    `SystemLibrary` has four typed getters and **no existence check**. Every
    getter returns that type's default for an unknown name rather than raising:
    `get_console_variable_string_value("nonsense")` is `""`, and the int getter
    is 0. So a naive "try each in turn" reports every unknown variable as an
    empty string.

    This therefore rejects a value equal to its type's default and reports the
    variable as not found. That is the honest reading, with one caveat worth
    knowing: **a console variable genuinely set to 0, False or "" is reported as
    missing**, because there is no way to tell "unset" from "set to the default"
    through this API. There is no `ConsoleManager` type in the Python API, so
    nothing better is available.

    Returns `value_type` so a caller knows how to read it. This is the way to
    check the effect of `execute_console_command`, since that returns no output.
    """
    security.enforce_tier("get_console_variable")

    body = (
        f"sl = {UNREAL}.SystemLibrary\n"
        f"name = {name!r}\n"
        f"value = None\n"
        f"kind = None\n"
        f"error = None\n"
        # Every getter returns a default rather than raising for an unknown
        # name: get_console_variable_string_value on a missing variable yields
        # "" and never fails, so trying them in order and taking the first result
        # would report every unknown variable as an empty string. Only accept a
        # value that is not the type's default.
        f"value = None\n"
        f"kind = None\n"
        f"error = None\n"
        f"tried = []\n"
        f"for _fn, _kind, _default in (\n"
        f"        ('get_console_variable_int_value', 'int', 0),\n"
        f"        ('get_console_variable_bool_value', 'bool', False),\n"
        f"        ('get_console_variable_float_value', 'float', 0.0),\n"
        f"        ('get_console_variable_string_value', 'string', '')):\n"
        f"    try:\n"
        f"        _v = getattr(sl, _fn)(name)\n"
        f"    except Exception as exc:\n"
        f"        tried.append(_kind + ': ' + type(exc).__name__)\n"
        f"        continue\n"
        f"    if _v != _default:\n"
        f"        value = _v\n"
        f"        kind = _kind\n"
        f"        break\n"
        f"    tried.append(_kind + ': ' + repr(_v))\n"
        f"if value is None:\n"
        f"    error = ('no console variable named ' + name + '; tried '\n"
        f"             + ', '.join(tried))\n"
        f"OUT = {{'found': True, 'error': error, 'name': name,\n"
        f"      'value': value, 'value_type': kind}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"), "name": name}
    return {
        "success": True,
        "name": name,
        "value": payload.get("value"),
        "value_type": payload.get("value_type"),
        "error": payload.get("error"),
    }
