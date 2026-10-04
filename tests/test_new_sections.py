"""Snippet and guard tests for the levels, play, animation and physics sections.

Follows the same shape as test_material_graph.py: stub the bridge and let
guarded()'s own compile check do the asserting, so a malformed snippet fails
locally instead of at the editor.
"""
import inspect

import pytest

from unreal_mcp import security
from unreal_mcp.tools import animation, levels, physics, play

MAT = "/Game/MCPTest/SnippetProbe.M"
LEVEL = "/Game/MCPTest/Untitled"


@pytest.fixture
def stubbed(monkeypatch):
    cap = {}

    def fake_guarded(body):
        cap["body"] = body          # guarded() compiles; a bad body raises here
        return "built"

    def fake_run_python(expression, timeout=None):
        cap["expression"] = expression
        cap["timeout"] = timeout
        return dict(cap.get("result") or {"found": True, "error": None})

    def install(module):
        monkeypatch.setattr(module, "guarded", fake_guarded)
        monkeypatch.setattr(module, "get_bridge", lambda: type(
            "B", (), {"run_python": staticmethod(fake_run_python)})())

    for module in (levels, play, animation, physics):
        install(module)
    return cap


def _builds(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return True, ""
    except SyntaxError as exc:
        return False, str(exc)


# --- levels -----------------------------------------------------------------

LEVEL_CALLS = [
    ("list_levels", (), {}),
    ("get_current_level", (), {}),
    ("save_level", (), {"confirm": True}),
    ("load_level", (LEVEL,), {"confirm": True}),
    ("new_level", ("/Game/MCPTest/Blank",), {}),
    ("new_level", ("/Game/MCPTest/Blank",), {"save": True, "confirm": True}),
]


@pytest.mark.parametrize("name,args,kwargs", LEVEL_CALLS)
def test_level_snippets_compile(stubbed, name, args, kwargs):
    ok, err = _builds(getattr(levels, name), *args, **kwargs)
    assert ok, f"{name}: {err}"


def test_save_and_load_are_destructive():
    assert security.TOOL_RISK_TIERS["save_level"] is security.RiskTier.DESTRUCTIVE
    assert security.TOOL_RISK_TIERS["load_level"] is security.RiskTier.DESTRUCTIVE
    assert security.TOOL_RISK_TIERS["new_level"] is security.RiskTier.DESTRUCTIVE


def test_new_level_without_save_needs_no_confirm(stubbed):
    """An unsaved blank level writes nothing, so it is allowed without confirm."""
    ok, err = _builds(levels.new_level, "/Game/MCPTest/Blank")
    assert ok, err


# --- play -------------------------------------------------------------------

PLAY_CALLS = [
    ("get_play_state", (), {}),
    ("start_play_in_editor", (), {}),
    ("start_play_in_editor_simulate", (), {}),
    ("stop_play_in_editor", (), {}),
    ("wait_for_play_state", (True,), {}),
    ("execute_console_command", ("stat fps",), {}),
    ("get_console_variable", ("r.ShadowQuality",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", PLAY_CALLS)
def test_play_snippets_compile(stubbed, name, args, kwargs):
    ok, err = _builds(getattr(play, name), *args, **kwargs)
    assert ok, f"{name}: {err}"


def test_execute_console_command_refuses_an_empty_command():
    """Refused in-process, before anything is dispatched to the editor."""
    r = play.execute_console_command("   ")
    assert r["success"] is False
    assert "empty" in r["error"]


def test_console_command_passes_no_third_argument(stubbed):
    """
    The 3rd parameter of execute_console_command is specific_player, a
    PlayerController. Passing False raises "Cannot nativize 'bool' as
    'SpecificPlayer'", so it has to be left at its default.
    """
    play.execute_console_command("stat fps")
    assert "execute_console_command(ctx," in stubbed["body"]
    assert "), False)" not in stubbed["body"]


def test_console_variable_maps_friendly_names_to_enum_members():
    assert physics.COLLISION_CHANNELS["pawn"] == "ECC_PAWN"
    assert physics.COLLISION_RESPONSES["block"] == "ECR_BLOCK"


def test_start_play_in_editor_simulate_delegates_to_start_play_in_editor():
    """It must be the simulate=True path, not a separate implementation."""
    assert "simulate=True" in inspect.getsource(
        play.start_play_in_editor_simulate)


# --- animation --------------------------------------------------------------

ANIM_CALLS = [
    ("set_animation", ("A", "/Game/A.Y"), {}),
    ("set_animation_mode", ("A", "single_node"), {}),
    ("play_animation", ("A",), {}),
    ("stop_animation", ("A",), {}),
    ("pause_animation", ("A",), {}),
    ("set_play_rate", ("A", 1.5), {}),
    ("get_animation_state", ("A",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", ANIM_CALLS)
def test_animation_snippets_compile(stubbed, name, args, kwargs):
    ok, err = _builds(getattr(animation, name), *args, **kwargs)
    assert ok, f"{name}: {err}"


def test_animation_rejects_an_unknown_mode():
    r = animation.set_animation("A", "/Game/A.Y", animation_mode="sideways")
    assert r["success"] is False
    assert "single_node" in r["error"]


def test_animation_rejects_a_missing_asset_in_process(stubbed):
    stubbed["result"] = {"found": False, "error": "Could not load", "set": False}
    r = animation.set_animation("A", "/Game/A.Y")
    assert r["success"] is False


def test_animation_mode_names_map_to_real_members():
    assert animation.ANIMATION_MODES["single_node"] == "ANIMATION_SINGLE_NODE"
    assert animation.ANIMATION_MODES["blueprint"] == "ANIMATION_BLUEPRINT"


def test_pause_is_a_zero_play_rate_not_a_pause_call(stubbed):
    """
    SkeletalMeshComponent has no pause(). Setting the play rate to 0 is the
    only way to hold a frame from Python.
    """
    animation.pause_animation("A")
    snippet = stubbed["body"]
    assert "set_play_rate(0.0)" in snippet
    assert "smc.pause(" not in snippet


def test_animation_reads_the_asset_through_the_anim_instance(stubbed):
    """
    There is no SkeletalMeshComponent.get_animation(); the asset lives on the
    AnimSingleNodeInstance.
    """
    animation.get_animation_state("A")
    snippet = stubbed["body"]
    assert "get_anim_instance()" in snippet
    assert "get_animation_asset()" in snippet
    assert "smc.get_animation()" not in snippet


def test_play_animation_reads_the_asset_before_playing(stubbed):
    """play() recreates the anim instance, so reading afterwards finds nothing."""
    animation.play_animation("A")
    body = stubbed["body"]
    assert body.index("_single_node_asset") < body.index("smc.play(")


# --- physics ----------------------------------------------------------------

PHYSICS_CALLS = [
    ("get_collision_state", ("A",), {}),
    ("set_collision_enabled", ("A", "query_and_physics"), {}),
    ("set_collision_profile", ("A", "BlockAll"), {}),
    ("set_collision_object_type", ("A", "world_static"), {}),
    ("set_collision_response", ("A", "pawn", "ignore"), {}),
    ("set_simulate_physics", ("A",), {}),
    ("apply_physics_impulse", ("A",), {}),
]


@pytest.mark.parametrize("name,args,kwargs", PHYSICS_CALLS)
def test_physics_snippets_compile(stubbed, name, args, kwargs):
    ok, err = _builds(getattr(physics, name), *args, **kwargs)
    assert ok, f"{name}: {err}"


@pytest.mark.parametrize("call", [
    lambda: physics.set_collision_enabled("A", "sideways"),
    lambda: physics.set_collision_response("A", "pawn", "explode"),
    lambda: physics.set_collision_response("A", "telepathy", "block"),
    lambda: physics.set_collision_object_type("A", "banana"),
    lambda: physics.set_collision_profile("A", ""),
])
def test_physics_validates_arguments_in_process(call):
    """Rejected before dispatch, so a typo never reaches the editor."""
    assert call()["success"] is False


def test_physics_rejects_an_empty_profile():
    assert physics.set_collision_profile("A", "")["success"] is False


def test_physics_uses_the_real_collision_enums(stubbed):
    """
    Channel is unreal.CollisionChannel.ECC_* and response is
    unreal.CollisionResponseType.ECR_*. The names people reach for first do not
    exist: EngineTypes.CollisionChannel and CollisionResponse.ECR_Block.
    """
    physics.set_collision_response("A", "pawn", "ignore")
    snippet = stubbed["body"]
    # UNREAL expands to __import__('unreal'), so match the tail of the call.
    assert "CollisionChannel.ECC_PAWN" in snippet
    assert "CollisionResponseType.ECR_IGNORE" in snippet
    assert "ECR_Block" not in snippet
    assert "EngineTypes.CollisionChannel" not in snippet


def test_simulate_physics_reports_whether_it_took_effect(stubbed):
    """A component with no physics body silently refuses to simulate."""
    stubbed["result"] = {"found": True, "error": None, "set": False,
                         "requested": True, "simulate_physics": False,
                         "simulate_physics_before": False, "took_effect": False}
    r = physics.set_simulate_physics("A", True)
    assert r["took_effect"] is False
    assert r["success"] is False
    assert "no physics body" in r["error"]


def test_impulse_reads_the_world_location(stubbed):
    """
    A PrimitiveComponent has no get_component_location and no
    get_attach_location; get_world_location is the form that exists.
    """
    physics.apply_physics_impulse("A", (0, 0, 100))
    snippet = stubbed["body"]
    assert "get_world_location()" in snippet
    assert "get_component_location()" not in snippet


def test_impulse_does_not_pass_a_bare_name_where_a_name_is_expected(stubbed):
    physics.apply_physics_impulse("A", (0, 0, 100))
    assert "Name('NAME_None')" in stubbed["body"]


def test_every_new_section_tool_has_a_tier():
    for name in (
        "list_levels", "get_current_level", "load_level", "save_level", "new_level",
        "get_play_state", "start_play_in_editor", "start_play_in_editor_simulate",
        "stop_play_in_editor", "wait_for_play_state", "execute_console_command",
        "get_console_variable",
        "set_animation", "set_animation_mode", "play_animation", "stop_animation",
        "pause_animation", "set_play_rate", "get_animation_state",
        "get_collision_state", "set_collision_enabled", "set_collision_profile",
        "set_collision_object_type", "set_collision_response", "set_simulate_physics",
        "apply_physics_impulse",
        "create_material_function", "list_material_function_expressions",
        "create_material_function_expression", "layout_material_function",
        "delete_material_function_expression",
        "delete_all_material_function_expressions",
        "connect_material_function_expressions",
        "set_material_function_expression_property",
        "get_material_function_expression_property",
    ):
        assert name in security.TOOL_RISK_TIERS, name
