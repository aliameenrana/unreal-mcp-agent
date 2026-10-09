"""
Compile-checks the snippet every registered MCP tool hands to the bridge.

This is the whole-surface version of tests/test_snippet_syntax.py. That file
checks named tools one at a time, which meant a tool added later could ship
without anything asserting its generated source parses. This walks
server.py's registrations instead, so adding a tool is enough to get it covered.

Why compile-checking matters at all: a tool builds a multi-line snippet by
splicing an f-string into an already-indented block. Dropped in without
indent_block() it still parses on the host, and only fails inside the editor.
guarded() now catches some of that locally, but only for snippets it assembles
itself, and a tool may send its snippet bare.

run_python and run_raw are both replaced with capture stubs, so nothing reaches
the editor and no destructive tool does anything. What is compiled is exactly the
source that would have been sent.
"""

from __future__ import annotations

import contextlib
import inspect
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from unreal_mcp import bridge as bridge_module

SERVER_SRC = Path(__file__).resolve().parents[1] / "src" / "unreal_mcp" / "server.py"

# One representative value per required parameter name, covering every name that
# appears across the registered tools. Values are the right type but semantically
# nonsense: nothing executes, so only the generated source is under test.
# Enum-ish arguments use the spellings the tools actually accept, taken from the
# error messages the tools themselves emit when given something else.
DUMMY = {
    "actor_name": "ZZAudit",
    "actor_names": ["ZZAudit"],
    "animation_mode": "blueprint",
    "animation_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "asset_name": "ZZAudit",
    "asset_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "b": 0.0,
    "base_material_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "blueprint_path": "/Game/MCPTest/ZZAudit",
    "center": [0.0, 0.0, 0.0],
    "channel": "visibility",
    "child_actor_name": "ZZAudit",
    "class_path": "/Script/Engine.StaticMeshActor",
    "code": "x = 1",
    "command": "stat fps",
    "component_class": "StaticMeshComponent",
    "component_name": "StaticMeshComponent0",
    "count": 1,
    "destination_path": "/Game/MCPTest",
    "folder_path": "ZZAudit",
    "function_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "g": 0.0,
    "height": 2,
    "instance_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "material_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "mesh_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "mode": "query_only",
    "mood": "daylight",
    "name": "ZZAudit",
    "object_type": "world_static",
    "param_name": "ZZAudit",
    "parent_actor_name": "ZZAuditParent",
    "parent_material_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "pixels": [255, 0, 0, 255] * 4,
    "play_rate": 1.0,
    "preset": "simple",
    "profile_name": "BlockAll",
    "property_name": "lod_bias",
    "r": 1.0,
    "radius": 100.0,
    "response": "block",
    # An import guard checks the source exists, so point at a real file.
    "source_file": "/Users/apple/ali/mixamo/Ch26_nonPBR.fbx",
    "tag": "ZZAudit",
    "tags": ["ZZAudit"],
    "texture_name": "ZZAudit",
    "texture_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "value": 1.0,
    # apply_material_variant_set wants one dict per variant, not a bare name.
    "variants": [{"name": "ZZAudit", "scalar": {}, "vector": {}}],
    "want_playing": True,
    "width": 2,
    # Optional args a tool needs before it will build any snippet at all.
    "intensity": 1.0,
    "location": [0.0, 0.0, 0.0],
    "rotation": [0.0, 0.0, 0.0],
    "scale": [1.0, 1.0, 1.0],
    "lod_count": 2,
    "lod_screen_sizes": [0.6, 0.2],
    "shader_model": 3,
    "domain": "MD_SURFACE",
    "shading_model": "MSM_DEFAULT_LIT",
    "fog_density": 0.02,
    "sun_intensity": 10.0,
    "ground_albedo": (0.3, 0.3, 0.3),
    "rayleigh_scattering": (0.5, 0.5, 0.5),
    "include_inherited": False,
    "package_path": "/Game/MCPTest",
    "table_name": "ZZAudit",
    "row_struct_class": "/Script/GameplayTags.GameplayTagTableRow",
    "table_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "fmt": "csv",
    "max_rows": 5,
    # blueprint_graph. Node/pin selectors are titles, so the audit value just has
    # to be a plausible string; nothing in the audit resolves them.
    "event_name": "ZZAudit",
    "variable_name": "ZZAudit",
    "pin_type": "float",
    "function_name": "ZZAudit",
    "graph_name": "EventGraph",
    "text": "ZZAudit",
    "node_title": "ZZAudit",
    "node_titles": ["ZZAudit"],
    "from_node": "ZZAudit",
    "from_pin": "ZZAudit",
    "to_node": "ZZAudit",
    "to_pin": "ZZAudit",
    "x": 0.0,
    "y": 0.0,
    "new_label": "ZZAudit",
    "parameter_name": "User.Scale",
    "value": 1.5,
    "location": [0.0, 0.0, 100.0],
    "system_path": "/Niagara/DefaultAssets/DefaultSystem.DefaultSystem",
}


# Per-tool extras, for arguments whose meaning is tool-specific.
#
# `offset` and `limit` deliberately live here rather than in DUMMY: both names are
# reused across tools with different types. scene.duplicate_actor takes an
# optional `offset` that it unpacks as three floats, while
# data_tables.list_data_table_rows takes an `offset` int index. Supplying one
# global value silently broke duplicate_actor, which raised unpacking an int
# before it ever built a snippet. A globally supplied optional argument is a
# hazard: it reaches tools the entry was never written for.
PER_TOOL = {
    "list_data_table_rows": {"offset": 0, "limit": 2},
    # Every replication flag is optional and defaults to None, which this tool
    # treats as a no-op, so the audit has to ask for one explicitly.
    "set_replication_flags": {"replicates": True},
    # `mode` is shared with another tool's "query_only" default, so
    # add_blueprint_variable_node's own get/set vocabulary is supplied per tool.
    "add_blueprint_variable_node": {"mode": "get"},
    # A destructive tool needs confirm=True before it will build a snippet.
    "delete_blueprint_nodes": {"confirm": True},
}

# A payload shaped like a real editor reply. Tools read specific keys out of it
# and some post-process what they read, so sequences must be sequences where the
# tool calls len() or iterates, and anything the tool branches on must be present.
PAYLOAD = {
    "found": True,
    "error": None,
    "type": None,
    "value": None,
    "success": True,
    "exists": True,
    "components": [{"name": "ZZAudit", "class": "StaticMeshComponent"}],
    "component_name": "ZZAudit",
    "component_class": "StaticMeshComponent",
    "root_component": "StaticMeshComponent0",
    "instance_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "asset_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "material_path": "/Game/MCPTest/ZZAudit.ZZAudit",
    "dup_name": "ZZAudit",
    "dup_class": "StaticMeshActor",
    "dup_label": "ZZAudit",
    "location": [0.0, 0.0, 0.0],
    "removed": True,
    "previous": None,
    "scalar": [],
    "vector": [],
    "texture": [],
    "static_switch": [],
    "expression_count": 0,
    "min": [0.0, 0.0, 0.0],
    "max": [1.0, 1.0, 1.0],
    "center": [0.0, 0.0, 0.0],
    "extent": [0.5, 0.5, 0.5],
    "bounds": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
    "mesh": "ZZAudit",
    "class": "StaticMeshActor",
    "generated": [0, 1],
    "rows": [],
    "nodes": [],
    "items": [],
    "tags": [],
}


def _registered_tools() -> list[tuple[str, object]]:
    """Every function server.py passes to _wrap, as (name, function)."""
    source = SERVER_SRC.read_text()
    modules: dict[str, object] = {}
    tools: list[tuple[str, object]] = []
    for target in re.findall(r"_wrap\(\s*([A-Za-z_][\w.]*)", source):
        if "." not in target:
            continue
        module_name, fn_name = target.split(".")
        if module_name not in modules:
            modules[module_name] = __import__(
                f"unreal_mcp.tools.{module_name}", fromlist=[fn_name]
            )
        tools.append((fn_name, getattr(modules[module_name], fn_name)))
    return tools


TOOLS = _registered_tools()

# The tools modules that were imported while collecting TOOLS. Scanned explicitly
# because patching sys.modules would also catch unrelated stdlib imports.
TOOL_MODULES = {
    name: module
    for name, module in sys.modules.items()
    if name.startswith("unreal_mcp.tools.") and module is not None
}


def _kwargs(fn) -> dict | None:
    """Representative kwargs for fn, or None if an argument is not mapped."""
    params = inspect.signature(fn).parameters
    extra = PER_TOOL.get(fn.__name__, {})
    out = {}
    for name, param in params.items():
        if name in extra:
            out[name] = extra[name]
        elif param.default is not inspect.Parameter.empty:
            # The destructive tier gates on confirm, which defaults to False.
            if name == "confirm":
                out[name] = True
            elif name in DUMMY:
                out[name] = DUMMY[name]
        elif name in DUMMY:
            out[name] = DUMMY[name]
        else:
            return None
    return out


def test_every_registered_tool_is_covered_by_this_file():
    """
    Guards the guard: a new tool with an argument this file does not know about
    would be skipped silently, which is how the 18 previously-unmapped tools
    went unverified. Fail loudly instead.
    """
    unmapped = [name for name, fn in TOOLS if _kwargs(fn) is None]
    assert not unmapped, (
        f"{len(unmapped)} registered tool(s) have required arguments this audit "
        f"does not supply, so their snippets are never compiled: {unmapped}. "
        f"Add them to DUMMY in this file."
    )


@pytest.mark.parametrize("name,fn", TOOLS, ids=[n for n, _ in TOOLS])
def test_tool_snippet_compiles(monkeypatch, name, fn):
    captured: list[str] = []

    def _capture(body, **kwargs):
        captured.append(body)
        return dict(PAYLOAD)

    # Each tools module imported get_bridge directly, and run_python/run_raw are
    # looked up on the bridge instance it returns, so patch both levels.
    fake = SimpleNamespace(
        run_python=_capture,
        run_raw=lambda code, **kw: {"success": True, "result": None, "output": ""},
    )
    monkeypatch.setattr(bridge_module, "get_bridge", lambda: fake)
    for module in TOOL_MODULES.values():
        monkeypatch.setattr(module, "get_bridge", lambda: fake, raising=False)

    kwargs = _kwargs(fn)
    assert kwargs is not None, f"{name}: unmapped required arguments"

    with contextlib.suppress(Exception):
        fn(**kwargs)

    if not captured:
        # execute_python forwards caller code verbatim through run_raw rather
        # than assembling a snippet, so there is no generated source to compile.
        # It is covered by the security bouncer tests instead.
        assert name == "execute_python", (
            f"{name} built no snippet, so its generated source is untested. Either "
            f"its argument validation is rejecting these dummy arguments, or it "
            f"sends source in a way this audit cannot see."
        )
        return

    for body in captured:
        compile(body, f"<{name}>", "exec")
