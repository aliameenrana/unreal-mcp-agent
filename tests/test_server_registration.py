"""
Tests that the server module actually imports and that every tool module it
imports contributes a reachable tool.

These exist because of a real outage. `server.py` registered three Niagara
tools while never importing the niagara module. Every other test still passed
-- the suite imports tools from `unreal_mcp.tools.*` directly -- so 375 green
tests coexisted with `import unreal_mcp.server` raising `NameError` and the
entire bridge being unreachable from any MCP client. Nothing in the suite
could distinguish a working server from a dead one.

So these tests deliberately go through `unreal_mcp.server`, the only path an
MCP client actually takes. The import itself is the regression test; the
per-module checks then catch the subtler variant where a module is imported
but its tools are never registered.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SERVER_PY = SRC / "unreal_mcp" / "server.py"
TOOLS_DIR = SRC / "unreal_mcp" / "tools"


def _imported_tool_modules() -> list[str]:
    """Tool module names from the `from .tools import (...)` block in server.py."""
    tree = ast.parse(SERVER_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tools":
            return [alias.name for alias in node.names]
    raise AssertionError(
        "server.py no longer has a `from .tools import (...)` block; this "
        "test needs updating to wherever tools are now registered from"
    )


def _module_functions(module_name: str) -> set[str]:
    """Public top-level function names defined in a tools/*.py module.

    These are the only names a tool could be registered under, so they bound
    what the server is able to expose from that module.
    """
    tree = ast.parse((TOOLS_DIR / f"{module_name}.py").read_text())
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def test_server_module_imports():
    """The outage test. Importing must not raise.

    Catches a missing `from .tools import x` for a module whose tools are
    registered below it, which is otherwise invisible to every other test.
    """
    from unreal_mcp import server

    assert hasattr(server, "mcp")


def test_every_tool_module_is_imported():
    """A module present on disk but absent from server.py's import block is
    dead code: its tools can never be reached, whatever its own tests say."""
    on_disk = {p.stem for p in TOOLS_DIR.glob("*.py")} - {"__init__"}
    missing = sorted(on_disk - set(_imported_tool_modules()))
    assert not missing, f"tool modules never imported by server.py: {missing}"


def test_every_imported_module_registers_at_least_one_tool():
    """An imported module that registers none of its functions is either dead
    weight or a half-finished registration; both are worth seeing at once
    rather than after someone tries the tool and finds nothing there."""
    from unreal_mcp import server

    registered = set(server.mcp._tool_manager._tools)
    unregistered = [
        name
        for name in _imported_tool_modules()
        if not (_module_functions(name) & registered)
    ]
    assert not unregistered, (
        f"imported by server.py but register no tool: {unregistered}"
    )


def test_known_tools_are_reachable():
    """Spot-check specific tools by name, so a tool vanishing from
    registration is caught even when its module is still imported."""
    from unreal_mcp import server

    registered = set(server.mcp._tool_manager._tools)
    for name in (
        "spawn_actor",
        "create_material",
        "list_blueprint_graph_nodes",
        "get_niagara_user_parameters",
        "spawn_niagara_system",
        "set_niagara_parameter",
    ):
        assert name in registered, f"{name} is not registered"