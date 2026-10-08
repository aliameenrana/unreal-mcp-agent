"""
Regression tests for tools/blueprint_graph.py.

The central one is `test_no_wedging_link_calls_are_emitted`. Three calls in the
`BlueprintGraphEditor` link surface wedge the Remote Control request: they return
an empty log and no ReturnValue, and recovering needs an editor restart. Wiring
goes through `pin.assign(other)` instead. Nothing about that is visible in the
source unless a test looks for it, and reintroducing `try_create_connection`
because it reads like the more natural API would silently break every wiring
tool, so the forbidden names are asserted against the generated source rather
than trusted to review.
"""
from __future__ import annotations

import inspect

import pytest

from unreal_mcp import bridge, security
from unreal_mcp.tools import blueprint_graph as bg

# Measured on a live UE 5.8 editor: each of these hangs the request with
# `RemoteCommandFailedError: Remote command failed: []`.
WEDGING_CALLS = (
    "try_create_connection",
    "list_connected_pins",
)

TOOLS = [
    obj
    for name, obj in vars(bg).items()
    if name.startswith(("list_", "get_", "add_", "set_", "create_", "connect_",
                        "delete_"))
    and inspect.isfunction(obj)
    and obj.__module__ == bg.__name__
]


class _FakeBridge:
    def __init__(self):
        self.sent: list[str] = []

    def run_python(self, code, **kwargs):
        self.sent.append(code)
        return {"found": True, "error": None}


@pytest.fixture()
def fake_bridge(monkeypatch):
    fb = _FakeBridge()
    monkeypatch.setattr(bridge, "get_bridge", lambda: fb)
    monkeypatch.setattr(bg, "get_bridge", lambda: fb)
    return fb


def _kwargs_for(fn):
    """One representative value per parameter, by type name."""
    out = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if name == "blueprint_path":
            out[name] = "/Game/MCPTest/BP_Thing"
        elif name == "node_titles":
            out[name] = ["Thing"]
        elif param.annotation in (int, float):
            out[name] = 1
        elif param.annotation is bool:
            out[name] = True
        else:
            out[name] = "Thing"
    return out


def test_every_tool_is_exported():
    # A tool that is not registered in server.py is invisible to an MCP client,
    # which is indistinguishable from not having built it.
    names = {fn.__name__ for fn in TOOLS}
    registered = {k for k in security.TOOL_RISK_TIERS if k in names}
    assert registered == names, (
        f"tools with no risk tier: {sorted(names - registered)}"
    )


def test_no_wedging_link_calls_are_emitted(fake_bridge):
    for fn in TOOLS:
        kwargs = _kwargs_for(fn)
        if "confirm" in kwargs:
            kwargs["confirm"] = True
        try:
            fn(**kwargs)
        except Exception:
            pass  # Argument validation is covered elsewhere; the source is what matters.
        for code in fake_bridge.sent:
            for banned in WEDGING_CALLS:
                assert banned not in code, (
                    f"{fn.__name__} emits {banned}, which wedges the editor's "
                    f"Remote Control request"
                )


def test_wiring_uses_pin_assign(fake_bridge):
    bg.connect_blueprint_pins(
        "/Game/MCPTest/BP_Thing", "From", "exec", "To", "exec"
    )
    assert fake_bridge.sent, "no snippet was sent"
    snippet = fake_bridge.sent[-1]
    assert "assign(" in snippet, "wiring must go through destination.assign(source)"
    assert "'exec'" in snippet, "the exec pin selector should be interpolated"


def test_ambiguous_title_is_an_error_not_a_first_match():
    # The selectors are titles because nodes have no stable id, so a duplicate
    # title has to be refused. Wiring the wrong node compiles and misbehaves,
    # which is the failure this guard exists to prevent.
    source = bg._PICK
    assert "ambiguous" in source
    assert "len(hits) > 1" in source


def test_exec_pin_null_name_is_handled():
    # A CustomEvent's exec pin has a null Name, so it renders as the string
    # 'None' and cannot be addressed by name. The selector routes 'exec' to
    # find_execute_pin() instead; without that, exec wiring is unreachable.
    source = bg._PICK
    assert "find_execute_pin" in source
    for token in ("exec", "<exec>"):
        assert token in source


def test_member_variable_readback_avoids_protected_property(fake_bridge):
    # Blueprint.NewVariables is protected and raises. Reading the variable list
    # back through it produced a tool that never worked, so the working path is
    # asserted against the generated snippet rather than left to a future reader
    # to rediscover.
    bg.add_blueprint_member_variable("/Game/MCPTest/BP_Thing", "Thing", "float")
    assert fake_bridge.sent, "no snippet was sent"
    snippet = fake_bridge.sent[-1]
    assert "get_editor_property('NewVariables')" not in snippet
    assert "list_member_variable_names" in snippet


def test_node_positions_are_intpoint():
    # set_node_pos rejects a Vector2D with "Failed to convert parameter 'pos'".
    assert "_intpoint(" in inspect.getsource(bg.set_blueprint_node_position)
    assert "IntPoint" in inspect.getsource(bg._intpoint)


def test_getter_does_not_import_the_protected_edgraph_nodes():
    # The whole point of this module is that node reading is possible without
    # touching EdGraph.Nodes, which is protected. If a future edit reintroduces
    # that path the module's central claim becomes false.
    source = inspect.getsource(bg)
    assert "EdGraph" not in source or "protected" in source


def test_delete_requires_confirm():
    with pytest.raises(security.SecurityViolation):
        bg.delete_blueprint_nodes("/Game/MCPTest/BP_Thing", ["Thing"])


def test_graph_name_is_interpolated_not_referenced(fake_bridge):
    # A bare `graph_name` inside an emitted snippet is a NameError on the editor
    # side. Every tool that reports the graph name has to interpolate it.
    for fn in TOOLS:
        kwargs = _kwargs_for(fn)
        if "confirm" in kwargs:
            kwargs["confirm"] = True
        try:
            fn(**kwargs)
        except Exception:
            pass
    for code in fake_bridge.sent:
        for line in code.splitlines():
            if "'graph_name':" in line:
                assert "graph_name," not in line, (
                    f"graph_name is referenced but not bound: {line.strip()}"
                )