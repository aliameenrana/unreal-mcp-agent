"""Regression tests for the Niagara instance-level tools.

The live runs behind these found four separate signature mistakes that the
snippet audit passed, because the audit only compiles generated source and never
calls into the engine:

- `spawn_system_attached` takes a SceneComponent, not an Actor. Passing the
  actor produced a TypeError naming `attach_to_component`.
- it also takes an AttachLocation enum and an attach point name, which the
  catalog version passed positionally in the wrong places.
- `Actor` has no `get_root_component` in the Python API, so the component list
  is the only route to a SceneComponent.
- `attach_point_name` rejects Python None; it needs an empty string.

Each of those is asserted below against the generated snippet source, since the
engine itself is not reachable from the unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unreal_mcp.tools import niagara  # noqa: E402


def _body(monkeypatch, fn, **kwargs) -> str:
    """Runs a tool against a capturing bridge and returns the generated source."""
    captured: list[str] = []

    def _capture(body, **_kwargs):
        captured.append(body)
        return {"ok": True, "set": True, "found": True}

    fake = SimpleNamespace(
        run_python=_capture,
        run_raw=lambda code, **kw: {"success": True, "result": None, "output": ""},
    )
    monkeypatch.setattr(niagara, "get_bridge", lambda: fake, raising=False)
    monkeypatch.setattr(niagara, "guarded", lambda body: body, raising=False)
    fn(**kwargs)
    assert captured, "tool sent no snippet"
    return captured[0]


@pytest.fixture(autouse=True)
def _registered():
    """These tools sit behind the tier gate, which blocks unknown names."""
    from unreal_mcp import security

    known = {n for n in dir(security) if n.isupper()}
    tier_map = next(
        getattr(security, n) for n in known
        if isinstance(getattr(security, n), dict) and "get_mesh_bounds" in getattr(security, n)
    )
    for name in (
        "spawn_niagara_system",
        "set_niagara_parameter",
        "get_niagara_user_parameters",
    ):
        tier_map.setdefault(name, security.RiskTier.CONSTRUCTIVE)


def test_spawn_attached_passes_a_scene_component_not_an_actor(monkeypatch):
    body = _body(
        monkeypatch,
        niagara.spawn_niagara_system,
        system_path="/Niagara/DefaultAssets/DefaultSystem.DefaultSystem",
        location=[0.0, 0.0, 0.0],
        attach_to_actor="SomeActor",
    )
    # The engine rejects an Actor here; the component list is the only route.
    assert "get_components_by_class" in body
    assert "target.get_root_component()" not in body


def test_spawn_attached_uses_a_real_socket_or_falls_back(monkeypatch):
    body = _body(
        monkeypatch,
        niagara.spawn_niagara_system,
        system_path="/Niagara/DefaultAssets/DefaultSystem.DefaultSystem",
        location=[0.0, 0.0, 0.0],
        attach_to_actor="SomeActor",
        socket_name="hand_r",
    )
    assert "does_socket_exist" in body
    assert "if root is None and _scenes" in body


def test_attach_point_name_is_never_python_none(monkeypatch):
    """`attach_point_name` rejects None; an empty string is what it wants."""
    body = _body(
        monkeypatch,
        niagara.spawn_niagara_system,
        system_path="/Niagara/DefaultAssets/DefaultSystem.DefaultSystem",
        location=[0.0, 0.0, 0.0],
        attach_to_actor="SomeActor",
    )
    assert "attach_to_component" not in body
    # The attach point must be a quoted string literal, never a bare None.
    assert ", None, " not in body


def test_set_parameter_validates_against_the_exposed_list(monkeypatch):
    body = _body(
        monkeypatch,
        niagara.set_niagara_parameter,
        system_path="/Niagara/VectorFields/VectorFieldVisualizationSystem",
        parameter_name="FieldIntensity",
        value=3.5,
    )
    assert "get_all_user_parameters" in body
    assert "not in known" in body


def test_set_parameter_covers_all_four_setter_families(monkeypatch):
    """bool/int/vector/float must each reach their own Niagara setter."""
    body = _body(
        monkeypatch,
        niagara.set_niagara_parameter,
        system_path="/Niagara/VectorFields/VectorFieldVisualizationSystem",
        parameter_name="FieldIntensity",
        value=1.0,
    )
    for setter in ("set_bool_parameter", "set_int_parameter",
                   "set_vector_parameter", "set_float_parameter"):
        assert setter in body, f"{setter} missing from generated snippet"


def test_get_parameters_uses_the_function_library(monkeypatch):
    """NiagaraSystem has no parameter accessor; the read lives on the library."""
    body = _body(
        monkeypatch,
        niagara.get_niagara_user_parameters,
        system_path="/Niagara/DefaultAssets/DefaultSystem.DefaultSystem",
    )
    assert "NiagaraFunctionLibrary.get_all_user_parameters" in body