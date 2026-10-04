"""
Can't run a live Unreal Editor in this environment, so this is the next
best thing: intercept every snippet right before it would be sent over the
wire, and confirm it's at least syntactically valid Python. Catches f-string
mistakes in the tool modules without needing the editor open.

Separate from test_security.py, which tests the bouncer itself.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import types
import warnings
from unittest import mock

import pytest

from unreal_mcp import bridge
from unreal_mcp.remote_snippets import (
    actor_component,
    find_actor_by_name,
    guarded,
)
from unreal_mcp.tools import (
    blueprints,
    components,
    lighting,
    materials,
    meshes,
    presets,
    scene,
)


class _FakeBridge:
    """Captures the expression instead of sending it anywhere."""

    def __init__(self):
        self.last_expr: str | None = None
        self.result = "fake-result"

    def run_python(self, expression: str):
        self.last_expr = expression
        _assert_no_lambda_subscript(expression)
        ast.parse(expression)  # raises SyntaxError if the f-string building is broken
        return self.result


def _assert_no_lambda_subscript(expr: str) -> None:
    """
    Subscripting a lambda's result, as in `(lambda c: ...)[-1]`, compiles, but
    CPython emits "'function' object is not subscriptable; perhaps you missed
    a comma?" while compiling it. Unreal surfaces that warning in LogOutput and
    comes back with an empty ReturnValue, which bridge.run_python reports as a
    failure. Index the tuple inside the lambda body instead, the way
    lighting._apply_to_component does.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(expr, "<snippet>", "eval")
    offending = [str(w.message) for w in caught if "not subscriptable" in str(w.message)]
    assert not offending, f"subscripting a lambda result: {offending}"


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    fb = _FakeBridge()
    monkeypatch.setattr(bridge, "get_bridge", lambda: fb)
    # Each tools module imported get_bridge directly, so patch there too.
    monkeypatch.setattr(scene, "get_bridge", lambda: fb)
    monkeypatch.setattr(materials, "get_bridge", lambda: fb)
    monkeypatch.setattr(blueprints, "get_bridge", lambda: fb)
    monkeypatch.setattr(lighting, "get_bridge", lambda: fb)
    monkeypatch.setattr(components, "get_bridge", lambda: fb)
    monkeypatch.setattr(meshes, "get_bridge", lambda: fb)
    # presets calls the primitives in-process rather than the bridge itself, so
    # it has no get_bridge of its own to patch.
    return fb


def test_spawn_actor_snippet_is_valid(fake_bridge):
    scene.spawn_actor("/Script/Engine.StaticMeshActor", (1, 2, 3), (0, 90, 0))


def test_rotation_is_built_with_keywords(fake_bridge):
    """
    unreal.Rotator's positional constructor is (roll, pitch, yaw), not
    (pitch, yaw, roll), so a positional build tilts the actor instead of
    turning it and raises nothing. Confirmed live: asking for yaw=45 produced
    an actor reading yaw 0.
    """
    scene.spawn_actor("/Script/Engine.StaticMeshActor", (0, 0, 0), (10, 20, 30))
    expr = fake_bridge.last_expr
    assert "Rotator(pitch=10.0, yaw=20.0, roll=30.0)" in expr
    assert "Rotator(10, 20, 30)" not in expr
    assert "Rotator(10.0, 20.0, 30.0)" not in expr


def test_set_actor_transform_uses_keyword_rotator(fake_bridge):
    scene.set_actor_transform("Cube_0", rotation=(0, 45, 0))
    assert "Rotator(pitch=0.0, yaw=45.0, roll=0.0)" in fake_bridge.last_expr


def test_list_actors_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "run_python", lambda e: (ast.parse(e), [])[-1])
    scene.list_actors()


def test_set_actor_transform_snippet_is_valid(fake_bridge):
    scene.set_actor_transform("Cube_0", location=(1, 1, 1), rotation=(0, 0, 0))


def test_set_property_snippet_is_valid(fake_bridge):
    scene.set_property("Cube_0", "bHidden", True)


def test_delete_actor_snippet_is_valid(fake_bridge):
    scene.delete_actor("Cube_0", confirm=True)


def test_create_material_snippet_is_valid(fake_bridge):
    materials.create_material("/Game/Materials", "M_Test")


def test_create_material_instance_snippet_is_valid(fake_bridge):
    materials.create_material_instance("/Game/Materials", "MI_Test", "/Game/Materials/M_Test")


def test_set_material_scalar_parameter_snippet_is_valid(fake_bridge):
    materials.set_material_scalar_parameter("/Game/Materials/MI_Test", "Roughness", 0.5)


def test_set_material_vector_parameter_snippet_is_valid(fake_bridge):
    materials.set_material_vector_parameter("/Game/Materials/MI_Test", "BaseColor", 1.0, 0.0, 0.0)


def test_compile_blueprint_snippet_is_valid(fake_bridge):
    blueprints.compile_blueprint("/Game/Blueprints/BP_Test")


def test_light_scene_preset_snippet_is_valid(monkeypatch, fake_bridge):
    # The preset's read-back getters unpack a dict, not a string.
    monkeypatch.setattr(fake_bridge, "result", {"intensity": 1.0})
    presets.light_scene_preset("golden_hour")


def test_light_scene_preset_rejects_unknown_mood_before_dispatch(fake_bridge):
    result = presets.light_scene_preset("not_a_mood")
    assert result["success"] is False
    assert "not_a_mood" in result["error"]
    assert fake_bridge.last_expr is None, "unknown mood must not reach the bridge"


def test_set_dressing_pass_snippet_is_valid(fake_bridge):
    presets.set_dressing_pass("/Script/Engine.StaticMeshActor", 2, (0, 0, 0), 100.0)


def test_apply_material_variant_set_snippet_is_valid(fake_bridge):
    presets.apply_material_variant_set(
        "/Game/Materials",
        "/Game/Materials/M_Base",
        [{"name": "MI_Red", "scalar": {"Roughness": 0.2}, "vector": {"Tint": (1, 0, 0, 1)}}],
    )


# --- lighting primitives ---


def test_set_light_properties_snippet_is_valid(fake_bridge):
    lighting.set_light_properties(intensity=4.0, color=(1.0, 0.5, 0.2), temperature=3200.0)


def test_set_light_properties_rejects_empty_request(fake_bridge):
    result = lighting.set_light_properties("SomeLight")
    assert result["success"] is False
    assert fake_bridge.last_expr is None


def test_set_light_properties_rejects_temperature_on_skylight(fake_bridge):
    result = lighting.set_light_properties(temperature=3000.0, default_class="SkyLight")
    assert result["success"] is False
    assert "no temperature control" in result["error"]
    assert fake_bridge.last_expr is None


def test_get_light_properties_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"intensity": 1.0, "color": [1, 1, 1]})
    assert lighting.get_light_properties()["success"] is True


def test_set_exponential_fog_params_snippet_is_valid(fake_bridge):
    lighting.set_exponential_fog_params(fog_density=0.08, volumetric_fog=True)


def test_get_exponential_fog_params_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"fog_density": 0.0436})
    assert lighting.get_exponential_fog_params()["success"] is True


def test_set_sky_atmosphere_params_snippet_is_valid(fake_bridge):
    lighting.set_sky_atmosphere_params(
        atmosphere_height=60.0, ground_albedo=(0.2, 0.2, 0.2), rayleigh_scattering=(5e-6, 13e-6, 22e-6)
    )


def test_get_sky_atmosphere_params_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"atmosphere_height": 60.0})
    assert lighting.get_sky_atmosphere_params()["success"] is True


def test_light_color_uses_keyword_args_not_positional(fake_bridge):
    # Guards the BGRA trap: unreal.Color is (b, g, r, a) positionally, so a
    # positional build would silently swap red and blue.
    lighting.set_light_properties(color=(1.0, 0.0, 0.0))
    assert "Color(r=255, g=0, b=0, a=255)" in fake_bridge.last_expr
    assert "Color(255, 0, 0, 255)" not in fake_bridge.last_expr


# --- get_property / the snippet helpers it depends on ---


def _seq_last_element(expr: str) -> str:
    """Source of whatever seq() returns: its last tuple element, else the whole expr."""
    inner = expr[len("__import__('json').dumps(") : -1]
    node = ast.parse(inner, mode="eval").body
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Tuple):
        return ast.get_source_segment(inner, node.value.elts[-1]) or ""
    return inner


def test_get_property_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": True, "value": 1.0, "type": "float"})
    assert scene.get_property("Cube_0", "custom_time_dilation")["value"] == 1.0


def test_get_property_failure_reports_no_value(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge,
        "result",
        {"found": False, "value": None, "type": None, "error": "no such property"},
    )
    result = scene.get_property("Cube_0", "nope")
    assert result["success"] is False
    assert result["value"] is None, "a failed read must not report a value"


def test_get_property_resolves_the_actor_optionally(monkeypatch, fake_bridge):
    # A missing actor must not reach next() unguarded: StopIteration's str() is
    # empty, so the caller would see a blank error.
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "x", "value": None})
    scene.get_property("Nope", "custom_time_dilation")
    assert find_actor_by_name("Nope", optional=True) in fake_bridge.last_expr


def _run_guarded(body: str) -> dict:
    """Evaluate a guarded() body locally, with a stub unreal module in place."""
    stub = types.ModuleType("unreal")
    with mock.patch.dict(sys.modules, {"unreal": stub}):
        return json.loads(eval(guarded(body)))  # noqa: S307 - guarded() builds this


def test_guarded_error_message_is_never_blank():
    # StopIteration stringifies to '', which is how a missing actor surfaces
    # from next(); guarded() prefixes the type so error is never empty.
    assert _run_guarded("raise StopIteration")["error"] == "StopIteration"


def test_guarded_carries_the_exception_message():
    assert _run_guarded("raise ValueError('x')")["error"] == "ValueError: x"


def test_actor_component_parenthesizes_the_actor_expression():
    # `next(a for a in xs if cond).method()` parses but binds method() into the
    # comprehension's condition, so it silently reads the wrong thing.
    assert actor_component("next(g)", "StaticMeshComponent").startswith("(next(g))")


@pytest.mark.parametrize(
    "call",
    [
        lambda: lighting.get_light_properties(),
        lambda: lighting.get_exponential_fog_params(),
        lambda: lighting.get_sky_atmosphere_params(),
        lambda: scene.get_mesh_material_slot("Cube_0"),
    ],
)
def test_read_back_tools_end_on_the_payload(monkeypatch, fake_bridge, call):
    """
    seq() returns its last element, so a getter that puts the actor name after
    the payload silently returns the name, and the tool then fails unpacking a
    string where it expected a dict.
    """
    monkeypatch.setattr(fake_bridge, "result", {"ok": True})
    call()
    assert "lambda" in _seq_last_element(fake_bridge.last_expr)


def test_set_mesh_material_slot_ends_on_the_assignment(fake_bridge):
    scene.set_mesh_material_slot(
        "Cube_0", "/Game/Materials/M_Base", scalar_params={"Roughness": 0.3}
    )
    assert _seq_last_element(fake_bridge.last_expr).startswith("(lambda a:")


# --- components ---


def test_list_components_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": True, "components": [], "root_component": None})
    assert components.list_components("Cube_0")["count"] == 0


def test_list_components_reports_a_missing_actor(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "gone", "components": []})
    result = components.list_components("Nope")
    assert result["success"] is False
    assert result["components"] == []


# --- meshes ---


def test_get_mesh_bounds_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge,
        "result",
        {"found": True, "mesh": "Cube", "lod_count": 1, "min": [-50.0] * 3, "max": [50.0] * 3},
    )
    result = meshes.get_mesh_bounds("/Engine/BasicShapes/Cube.Cube")
    assert result["size"] == [100.0] * 3
    assert result["extent"] == [50.0] * 3


def test_get_mesh_bounds_reports_a_missing_asset(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "nope"})
    assert meshes.get_mesh_bounds("/Game/Nope.Nope")["success"] is False


def test_set_mesh_lods_rejects_percentages(fake_bridge):
    # percent_triangles is a 0..1 fraction, not a percentage.
    result = meshes.set_mesh_lods("/Game/M.SM", [50.0], confirm=True)
    assert result["success"] is False
    assert fake_bridge.last_expr is None, "bad input must not reach the bridge"


def test_set_mesh_lods_requires_confirm(fake_bridge):
    try:
        meshes.set_mesh_lods("/Game/M.SM", [0.5])
        assert False, "expected SecurityViolation"
    except Exception as exc:
        assert type(exc).__name__ == "SecurityViolation"
