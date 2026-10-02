"""
Can't run a live Unreal Editor in this environment, so this is the next
best thing: intercept every snippet right before it would be sent over the
wire, and confirm it's at least syntactically valid Python. Catches f-string
mistakes in the tool modules without needing the editor open.

Separate from test_security.py, which tests the bouncer itself.
"""

from __future__ import annotations

import ast

import pytest

from unreal_mcp import bridge
from unreal_mcp.tools import blueprints, materials, scene


class _FakeBridge:
    """Captures the expression instead of sending it anywhere."""

    def __init__(self):
        self.last_expr: str | None = None

    def run_python(self, expression: str):
        self.last_expr = expression
        ast.parse(expression)  # raises SyntaxError if the f-string building is broken
        return "fake-result"  # satisfies callers expecting a string back


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    fb = _FakeBridge()
    monkeypatch.setattr(bridge, "get_bridge", lambda: fb)
    # Each tools module imported get_bridge directly, so patch there too.
    monkeypatch.setattr(scene, "get_bridge", lambda: fb)
    monkeypatch.setattr(materials, "get_bridge", lambda: fb)
    monkeypatch.setattr(blueprints, "get_bridge", lambda: fb)
    return fb


def test_spawn_actor_snippet_is_valid(fake_bridge):
    scene.spawn_actor("/Script/Engine.StaticMeshActor", (1, 2, 3), (0, 90, 0))


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
