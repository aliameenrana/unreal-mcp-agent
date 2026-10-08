"""
Blueprint graph reading and authoring, via `unreal.BlueprintGraphEditor`.

This module overturns a conclusion the project had carried for a long time.
`TOOL_BUILDING_GUIDE.md` and `PLAN.md` both recorded that Blueprint graph nodes
were unreachable from Python — `EdGraph.Nodes` is a protected property, and nodes
are not addressable by object path — which made Blueprint graph wiring the
flagship example of a capability that "needs the C++ escape hatch". That was
based on the `BlueprintEditorLibrary` surface alone. `unreal.BlueprintGraphEditor`
is a different class, it exposes a full node-authoring API, and its node and pin
handles are ordinary Python objects. Reading and wiring are both buildable.

Every call shape below was read off the live editor or the installed stub, not
inferred. Four things about this API are traps, all measured on a live 5.8
session:

  - **`BlueprintEditorSubsystem` does not exist in this build.** The entry point
    is the static `BlueprintGraphEditor.get_graph_editor_by_name(blueprint,
    graph_name)`.
  - **Three link-related calls wedge the Remote Control request** (empty log, no
    ReturnValue): `BlueprintGraphPin.try_create_connection`,
    `BlueprintGraphPinLibrary.try_create_connection`, and
    `BlueprintGraphPinLibrary.list_connected_pins`. None of them is used here.
    Wiring goes through `destination_pin.assign(source_pin)`, which works.
    There is also no `is_linked` on a pin, so a connection cannot be read back
    directly; `connect_blueprint_pins` verifies by compiling and comparing error
    sets before and after instead, which is an independent path.
  - **`BlueprintGraphPin.get_pin_direction` cannot pythonize its return value**
    (`ByteProperty` -> enum), so pin direction is derived from whether a pin
    appears in `node.list_input_pins()` rather than asked for.
  - Nodes are selected by `get_node_title()`, the same identity problem the
    material graph tools already have and solve the same way: an ambiguous title
    is an error, never a silent first-match pick.

Verified against a live Unreal Editor 5.8 session on Actor Blueprints.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, indent_block


# Injected into every snippet in this file. Written as plain strings rather than
# f-strings so there are no braces to double, and so the emitted code stays
# readable as code.

_PIN_INFO = (
    "def _pin_info(pin, is_input):\n"
    "    try:\n"
    "        name = str(pin.get_pin_name())\n"
    "    except Exception:\n"
    "        return None\n"
    "    if not name:\n"
    "        return None\n"
    "    try:\n"
    "        schema = pin.get_pin_type_as_json_schema()\n"
    "    except Exception:\n"
    "        schema = None\n"
    "    try:\n"
    "        shown = str(pin.get_pin_type_display_string())\n"
    "    except Exception:\n"
    "        shown = None\n"
    "    try:\n"
    "        value = str(pin.get_pin_value())[:120]\n"
    "    except Exception:\n"
    "        value = None\n"
    "    return {'name': name,\n"
    "            'direction': 'in' if is_input else 'out',\n"
    "            'type': schema,\n"
    "            'type_display': shown,\n"
    "            'default': value}\n"
)

_NODE_INFO = (
    "def _err_msg(node):\n"
    "    for attr in ('error_msg', 'get_error_msg'):\n"
    "        try:\n"
    "            value = getattr(node, attr)\n"
    "            if callable(value):\n"
    "                value = value()\n"
    "            text = str(value).strip()\n"
    "            return text or None\n"
    "        except Exception:\n"
    "            continue\n"
    "    return None\n"
    "\n"
    "def _node_info(node):\n"
    "    try:\n"
    "        title = str(node.get_node_title())\n"
    "    except Exception:\n"
    "        title = str(node)\n"
    "    try:\n"
    "        pos = node.get_node_pos()\n"
    "        pos = [int(pos.x), int(pos.y)]\n"
    "    except Exception:\n"
    "        pos = None\n"
    "    try:\n"
    "        size = node.get_node_size()\n"
    "        size = [int(size.x), int(size.y)]\n"
    "    except Exception:\n"
    "        size = None\n"
    "    try:\n"
    "        category = str(node.get_node_category())\n"
    "    except Exception:\n"
    "        category = None\n"
    "    inputs = set()\n"
    "    try:\n"
    "        for p in node.list_input_pins():\n"
    "            inputs.add(str(p.get_pin_name()))\n"
    "    except Exception:\n"
    "        pass\n"
    "    pins = []\n"
    "    try:\n"
    "        all_pins = list(node.list_all_pins())\n"
    "    except Exception:\n"
    "        all_pins = []\n"
    "    for p in all_pins:\n"
    "        try:\n"
    "            is_input = str(p.get_pin_name()) in inputs\n"
    "        except Exception:\n"
    "            is_input = False\n"
    "        info = _pin_info(p, is_input)\n"
    "        if info is not None:\n"
    "            pins.append(info)\n"
    "    return {'title': title,\n"
    "            'class': type(node).__name__,\n"
    "            'pos': pos,\n"
    "            'size': size,\n"
    "            'category': category,\n"
    "            'error': _err_msg(node),\n"
    "            'pins': pins}\n"
)

_PICK = (
    "def _pick_node(nodes, title):\n"
    "    hits = [n for n in nodes if str(n.get_node_title()) == title]\n"
    "    if not hits:\n"
    "        return None, ('no node titled ' + repr(title) + ' in this graph')\n"
    "    if len(hits) > 1:\n"
    "        return None, ('title is ambiguous, ' + repr(title) + ' matches '\n"
    "                       + str(len(hits)) + ' nodes; rename or move one')\n"
    "    return hits[0], None\n"
    "\n"
    "def _pick_pin(node, pin_name):\n"
    "    if str(pin_name).strip().lower() in ('exec', 'execute', '<exec>'):\n"
    "        exec_pin = node.find_execute_pin()\n"
    "        if exec_pin is None:\n"
    "            return None, ('this node has no exec pin: '\n"
    "                          + str(node.get_node_title()))\n"
    "        return exec_pin, None\n"
    "    try:\n"
    "        pins = list(node.list_all_pins())\n"
    "    except Exception:\n"
    "        pins = []\n"
    "    hits = [p for p in pins if str(p.get_pin_name()) == pin_name]\n"
    "    if not hits:\n"
    "        return None, ('no pin named ' + repr(pin_name) + ' on '\n"
    "                      + str(node.get_node_title()))\n"
    "    if len(hits) > 1:\n"
    "        return None, ('pin name is ambiguous: ' + repr(pin_name))\n"
    "    return hits[0], None\n"
    "\n"
    "def _error_titles(editor):\n"
    "    try:\n"
    "        return sorted(str(n.get_node_title())\n"
    "                       for n in editor.list_nodes_with_errors())\n"
    "    except Exception:\n"
    "        return []\n"
)

_HELPERS = _PIN_INFO + _NODE_INFO + _PICK


def _intpoint(x: float, y: float) -> str:
    """Source for an `unreal.IntPoint`, which is what node *positions* are.

    Measured asymmetry worth knowing before extending this file: a node's
    position is an `IntPoint` (`set_node_pos` rejects a Vector2D or a Vector with
    "Failed to convert parameter 'pos'"), while its *size* is a `Vector2D`. A
    comment box, by contrast, does take a Vector2D.
    """
    return f"{UNREAL}.IntPoint(x={int(x)}, y={int(y)})"


def _vec2d(x: float, y: float) -> str:
    """Source for an `unreal.Vector2D`, used for comment-box positions and sizes.

    Built by keyword for the same reason `unreal.Color` is: positional struct
    construction has already produced one silent wrong-value bug in this project.
    """
    return f"{UNREAL}.Vector2D(x={x!r}, y={y!r})"


def _editor(blueprint_path: str, graph_name: str, body: str) -> dict:
    """
    Runs `body` with `bp` (a Blueprint) and `ge` (its graph editor) bound.

    `body` is spliced two levels deep, under `else:` for a loaded Blueprint and
    then under `else:` for a graph that resolved, so it starts at column 8 and
    only has to produce `OUT`. A path that does not load, an object that is not
    a Blueprint, and a graph name that does not exist all come back as
    `found: False` with a readable reason instead of raising in the editor.
    """
    script = (
        f"{_HELPERS}\n"
        f"bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    bp = {UNREAL}.load_asset({blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': {blueprint_path!r} + ' did not load'}}\n"
        f"elif not isinstance(bp, {UNREAL}.Blueprint):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'Not a Blueprint: ' + type(bp).__name__}}\n"
        f"else:\n"
        f"    ge = {UNREAL}.BlueprintGraphEditor.get_graph_editor_by_name(\n"
        f"        bp, {graph_name!r})\n"
        f"    if ge is None:\n"
        f"        have = ', '.join(\n"
        f"            str(g) for g in\n"
        f"            {UNREAL}.BlueprintEditorLibrary.list_graph_names(bp))\n"
        f"        OUT = {{'found': False,\n"
        f"              'error': 'no graph named ' + {graph_name!r}\n"
        f"                     + ' (have: ' + have + ')'}}\n"
        f"    else:\n"
        f"{indent_block(body, spaces=8)}"
    )
    return get_bridge().run_python(guarded(script))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_blueprint_graph_nodes(blueprint_path: str,
                               graph_name: str = "EventGraph") -> dict:
    """
    Lists every node in one Blueprint graph, with each node's position, size,
    category, error text and full pin list (name, direction, type schema,
    display string and default value).

    This is the read side that was previously reported as impossible. It returns
    `pins_discoverable: True`, which `list_blueprint_graphs` cannot: that tool
    reads `EdGraph.Nodes`, a protected property, and has to admit it.

    `graph_name` defaults to 'EventGraph'; use `list_blueprint_graphs` to see the
    rest.
    """
    security.enforce_tier("list_blueprint_graph_nodes")
    body = (
        "nodes = list(ge.list_all_nodes())\n"
        "OUT = {'found': True,\n"
        "       'error': None,\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'node_count': len(nodes),\n"
        "       'pins_discoverable': True,\n"
        "       'nodes': [_node_info(n) for n in nodes]}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def list_blueprint_available_nodes(blueprint_path: str,
                                   graph_name: str = "EventGraph",
                                   node_filter: str = "") -> dict:
    """
    Lists node types that can be added to a graph, optionally filtered by a
    case-insensitive substring.

    `node_filter` matches case-insensitively against the pipe-separated
    "Category|Subcategory|Action" name the API returns, e.g.
    `"Utilities|Casting|CastToObject"`. The class a node comes from is *not* in
    that string, so filtering by a library name such as "KismetSystemLibrary"
    correctly returns nothing; filter on the action instead ("PrintString").

    The unfiltered list is about 40,000 entries, so the result is capped at 200
    and `truncated` says whether anything was dropped.
    """
    security.enforce_tier("list_blueprint_available_nodes")
    body = (
        "needle = " + repr(str(node_filter).lower()) + "\n"
        "available = [str(x) for x in ge.list_available_nodes([])]\n"
        "if needle:\n"
        "    available = [x for x in available if needle in x.lower()]\n"
        "available.sort()\n"
        "OUT = {'found': True,\n"
        "       'error': None,\n"
        "       'filter': " + repr(node_filter) + ",\n"
        "       'count': len(available),\n"
        "       'truncated': len(available) > 200,\n"
        "       'nodes': available[:200]}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def get_blueprint_compile_errors(blueprint_path: str) -> dict:
    """
    Compiles a Blueprint and reports which nodes carry errors, warnings and
    notes, with the message text for each.

    `compile_blueprint` reports only that the compile request was dispatched.
    This one compiles and then reads the resulting per-node messages back, which
    is the difference between "the call did not raise" and "the graph compiled".
    """
    security.enforce_tier("get_blueprint_compile_errors")
    script = (
        f"{_HELPERS}\n"
        f"bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    bp = {UNREAL}.load_asset({blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': {blueprint_path!r} + ' did not load'}}\n"
        f"elif not isinstance(bp, {UNREAL}.Blueprint):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'Not a Blueprint: ' + type(bp).__name__}}\n"
        f"else:\n"
        f"    {UNREAL}.BlueprintEditorLibrary.compile_blueprint(bp)\n"
        f"    detail = {{}}\n"
        f"    for graph in {UNREAL}.BlueprintEditorLibrary.list_graphs(bp):\n"
        f"        editor = {UNREAL}.BlueprintGraphEditor.get_graph_editor(graph)\n"
        f"        if editor is None:\n"
        f"            continue\n"
        f"        for kind, getter in (('error', 'list_nodes_with_errors'),\n"
        f"                              ('warning', 'list_nodes_with_warnings'),\n"
        f"                              ('note', 'list_nodes_with_notes')):\n"
        f"            try:\n"
        f"                nodes = list(getattr(editor, getter)())\n"
        f"            except Exception:\n"
        f"                continue\n"
        f"            for node in nodes:\n"
        f"                title = str(node.get_node_title())\n"
        f"                entry = detail.setdefault(\n"
        f"                    title, {{'node': title, 'class': type(node).__name__,\n"
        f"                            'issues': []}})\n"
        f"                entry['issues'].append(\n"
        f"                    {{'severity': kind, 'message': _err_msg(node)}})\n"
        f"    issues = list(detail.values())\n"
        f"    error_count = sum(\n"
        f"        1 for e in issues\n"
        f"        for i in e['issues'] if i['severity'] == 'error')\n"
        f"    OUT = {{'found': True,\n"
        f"          'error': None,\n"
        f"          'blueprint_path': bp.get_path_name(),\n"
        f"          'status': 'clean' if not error_count else 'errors',\n"
        f"          'error_count': error_count,\n"
        f"          'affected_nodes': issues}}\n"
    )
    return get_bridge().run_python(guarded(script))


# ---------------------------------------------------------------------------
# Author
# ---------------------------------------------------------------------------


def add_blueprint_event_node(blueprint_path: str, event_name: str,
                             graph_name: str = "EventGraph") -> dict:
    """
    Adds a custom event node to a graph and returns its title and pin list.

    The node is created and then re-listed through a separate `list_all_nodes()`
    pass, so the return value is a read of the graph rather than an echo of the
    object the create call handed back.
    """
    security.enforce_tier("add_blueprint_event_node")
    if not event_name.strip():
        return {"success": False, "error": "event_name must not be empty"}
    body = (
        f"node = ge.add_custom_event_node({event_name!r})\n"
        "title = str(node.get_node_title())\n"
        "confirmed = [n for n in ge.list_all_nodes()\n"
        "             if str(n.get_node_title()) == title]\n"
        "OUT = {'found': len(confirmed) == 1,\n"
        "       'error': None if len(confirmed) == 1\n"
        "               else 'the created node was not found on read-back',\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'node': _node_info(confirmed[0]) if len(confirmed) == 1 else None,\n"
        "       'read_back_node_count': len(confirmed)}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def add_blueprint_call_function_node(blueprint_path: str, function_path: str,
                                     graph_name: str = "EventGraph") -> dict:
    """
    Adds a call-function node for a fully qualified function path.

    `function_path` is a class path plus function, e.g.
    '/Script/Engine.KismetSystemLibrary.PrintString'. Use
    `list_blueprint_available_nodes` to discover valid names; the node's own pin
    list is returned so the caller can wire it without guessing pin names.
    """
    security.enforce_tier("add_blueprint_call_function_node")
    if not function_path.strip():
        return {"success": False, "error": "function_path must not be empty"}
    body = (
        f"node = ge.add_call_function_node({function_path!r})\n"
        "title = str(node.get_node_title())\n"
        "confirmed = [n for n in ge.list_all_nodes()\n"
        "             if str(n.get_node_title()) == title]\n"
        "OUT = {'found': len(confirmed) == 1,\n"
        "       'error': None if len(confirmed) == 1\n"
        "               else 'the created node was not found on read-back',\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'function_path': " + repr(function_path) + ",\n"
        "       'node': _node_info(confirmed[0]) if len(confirmed) == 1 else None,\n"
        "       'read_back_node_count': len(confirmed)}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def add_blueprint_variable_node(blueprint_path: str, variable_name: str,
                                mode: str = "get",
                                graph_name: str = "EventGraph") -> dict:
    """
    Adds a variable get or set node for an existing member variable.

    `mode` is 'get' or 'set'. The variable must already exist on the Blueprint;
    use `add_blueprint_member_variable` to create one, or `create_blueprint` on a
    class that already has it.

    A variable that does not exist is reported as an error rather than producing
    an empty node, which is the silent-failure shape this project's read-back
    discipline exists to catch.
    """
    security.enforce_tier("add_blueprint_variable_node")
    if mode not in ("get", "set"):
        return {"success": False,
                "error": f"mode must be 'get' or 'set', got {mode!r}"}
    if not variable_name.strip():
        return {"success": False, "error": "variable_name must not be empty"}
    if mode == "get":
        maker = (f"node = ge.add_get_member_variable_node("
                 f"{variable_name!r})")
    else:
        maker = (f"node = ge.add_set_member_variable_node("
                 f"{variable_name!r})")
    body = (
        f"known = [str(v) for v in ge.list_local_variable_names()]\n"
        f"{maker}\n"
        "title = str(node.get_node_title())\n"
        "confirmed = [n for n in ge.list_all_nodes()\n"
        "             if str(n.get_node_title()) == title]\n"
        "OUT = {'found': len(confirmed) == 1,\n"
        "       'error': None if len(confirmed) == 1\n"
        "               else 'the created node was not found on read-back',\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'variable_name': " + repr(variable_name) + ",\n"
        "       'mode': " + repr(mode) + ",\n"
        "       'node': _node_info(confirmed[0]) if len(confirmed) == 1 else None,\n"
        "       'read_back_node_count': len(confirmed)}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def add_blueprint_branch_node(blueprint_path: str,
                              graph_name: str = "EventGraph") -> dict:
    """
    Adds an if/then/else branch node and returns its exec and data pins.

    The branch node is the basic control-flow building block: wire an incoming
    exec pin to its exec pin, then the condition output to its boolean input.
    """
    security.enforce_tier("add_blueprint_branch_node")
    body = (
        "node = ge.add_branch_node()\n"
        "title = str(node.get_node_title())\n"
        "confirmed = [n for n in ge.list_all_nodes()\n"
        "             if str(n.get_node_title()) == title]\n"
        "OUT = {'found': len(confirmed) == 1,\n"
        "       'error': None if len(confirmed) == 1\n"
        "               else 'the created node was not found on read-back',\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'node': _node_info(confirmed[0]) if len(confirmed) == 1 else None,\n"
        "       'read_back_node_count': len(confirmed)}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def add_blueprint_comment(blueprint_path: str, text: str, x: float = 0.0,
                          y: float = 0.0,
                          graph_name: str = "EventGraph") -> dict:
    """
    Adds a comment box to a graph at the given canvas position.

    Comments are the only grouping primitive in this API, so a graph built by an
    agent is otherwise one undifferentiated pile of nodes. They carry no logic
    and cannot break a compile.
    """
    security.enforce_tier("add_blueprint_comment")
    body = (
        "box = ge.add_comment_node("
        + repr(text)
        + f", {_vec2d(x, y)})\n"
        "OUT = {'found': True,\n"
        "       'error': None,\n"
        "       'blueprint_path': bp.get_path_name(),\n"
        "       'graph_name': " + repr(graph_name) + ",\n"
        "       'comment_class': type(box).__name__,\n"
        "       'comment_nodes': len(list(ge.list_comment_nodes()))}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def create_blueprint_function_graph(blueprint_path: str,
                                    function_name: str) -> dict:
    """
    Creates a new function graph on a Blueprint and returns its graph name.

    Refuses to overwrite: if a graph with that name already exists the call is
    reported as an error rather than replacing it, matching `create_blueprint`.
    """
    security.enforce_tier("create_blueprint_function_graph")
    if not function_name.strip():
        return {"success": False, "error": "function_name must not be empty"}
    script = (
        f"bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    bp = {UNREAL}.load_asset({blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': {blueprint_path!r} + ' did not load'}}\n"
        f"elif not isinstance(bp, {UNREAL}.Blueprint):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'Not a Blueprint: ' + type(bp).__name__}}\n"
        f"else:\n"
        f"    existing = [str(g) for g in\n"
        f"               {UNREAL}.BlueprintEditorLibrary.list_graph_names(bp)]\n"
        f"    if {function_name!r} in existing:\n"
        f"        OUT = {{'found': False,\n"
        f"              'error': 'a graph named ' + {function_name!r}\n"
        f"                     + ' already exists; use a new name'}}\n"
        f"    else:\n"
        f"        {UNREAL}.BlueprintGraphEditor.create_and_edit_function_graph(\n"
        f"            bp, {function_name!r})\n"
        f"        after = [str(g) for g in\n"
        f"                {UNREAL}.BlueprintEditorLibrary.list_graph_names(bp)]\n"
        f"        made = {function_name!r} in after\n"
        f"        OUT = {{'found': made,\n"
        f"              'error': None if made\n"
        f"                      else 'the graph was not present after creation',\n"
        f"              'blueprint_path': bp.get_path_name(),\n"
        f"              'graph_name': {function_name!r},\n"
        f"              'graph_names': after}}\n"
    )
    return get_bridge().run_python(guarded(script))


def add_blueprint_member_variable(blueprint_path: str, variable_name: str,
                                  pin_type: str = "float") -> dict:
    """
    Adds a member variable to a Blueprint and reads the variable list back.

    `pin_type` is a Blueprint type name such as 'float', 'int', 'bool' or
    'string'. The type is resolved through
    `BlueprintEditorLibrary.get_basic_type_by_name`, because a bare
    `EdGraphPinType()` produces a variable that reads back as an integer no
    matter what was asked for.

    Read-back goes through `BlueprintEditorLibrary.list_member_variable_names`.
    It deliberately does not use the Blueprint's own `NewVariables` property,
    which is protected and raises; that was the first thing tried and it is
    recorded here so the next person does not retry it.
    """
    security.enforce_tier("add_blueprint_member_variable")
    if not variable_name.strip():
        return {"success": False, "error": "variable_name must not be empty"}
    script = (
        f"bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    bp = {UNREAL}.load_asset({blueprint_path!r})\n"
        f"if bp is None:\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': {blueprint_path!r} + ' did not load'}}\n"
        f"elif not isinstance(bp, {UNREAL}.Blueprint):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'Not a Blueprint: ' + type(bp).__name__}}\n"
        f"else:\n"
        f"    existing = [str(v) for v in\n"
        f"               {UNREAL}.BlueprintEditorLibrary.list_member_variable_names(bp)]\n"
        f"    if {variable_name!r} in existing:\n"
        f"        OUT = {{'found': False,\n"
        f"              'error': 'a variable named ' + {variable_name!r}\n"
        f"                     + ' already exists; use a new name',\n"
        f"              'variables': existing}}\n"
        f"    else:\n"
        f"        type_obj = {UNREAL}.BlueprintEditorLibrary.get_basic_type_by_name(\n"
        f"            {pin_type!r})\n"
        f"        if type_obj is None:\n"
        f"            OUT = {{'found': False,\n"
        f"                  'error': 'no such Blueprint type: ' + {pin_type!r},\n"
        f"                  'variables': existing}}\n"
        f"        else:\n"
        f"            accepted = {UNREAL}.BlueprintEditorLibrary.add_member_variable(\n"
        f"                bp, {variable_name!r}, type_obj)\n"
        f"            now = [str(v) for v in\n"
        f"                  {UNREAL}.BlueprintEditorLibrary.list_member_variable_names(\n"
        f"                      bp)]\n"
        f"            landed = {variable_name!r} in now\n"
        f"            landed_type = None\n"
        f"            if landed:\n"
        f"                try:\n"
        f"                    landed_type = str(\n"
        f"                        {UNREAL}.BlueprintEditorLibrary.get_member_variable_type(\n"
        f"                            bp, {variable_name!r}))\n"
        f"                except Exception:\n"
        f"                    landed_type = None\n"
        f"            OUT = {{'found': landed,\n"
        f"                  'error': None if landed else 'the variable did not"
        f" appear in the variable list',\n"
        f"                  'blueprint_path': bp.get_path_name(),\n"
        f"                  'variable_name': {variable_name!r},\n"
        f"                  'requested_type': {pin_type!r},\n"
        f"                  'landed_type': landed_type,\n"
        f"                  'library_accepted': bool(accepted),\n"
        f"                  'variables': now}}\n"
    )
    return get_bridge().run_python(guarded(script))


def set_blueprint_node_position(blueprint_path: str, node_title: str, x: float,
                               y: float,
                               graph_name: str = "EventGraph") -> dict:
    """
    Moves a node to a canvas position, verified by reading the position back.

    Agent-built graphs otherwise stack every node at the origin, which is
    unreadable to a human reviewing the work.
    """
    security.enforce_tier("set_blueprint_node_position")
    body = (
        "node, err = _pick_node(list(ge.list_all_nodes()), "
        + repr(node_title) + ")\n"
        "if err is not None:\n"
        "    OUT = {'found': False, 'error': err}\n"
        "else:\n"
        f"    node.set_node_pos({_intpoint(x, y)})\n"
        "    again, err2 = _pick_node(list(ge.list_all_nodes()), "
        + repr(node_title) + ")\n"
        "    pos = None\n"
        "    if again is not None:\n"
        "        p = again.get_node_pos()\n"
        "        pos = [int(p.x), int(p.y)]\n"
        "    OUT = {'found': pos is not None,\n"
        "           'error': None if pos is not None\n"
        "                   else 'the node vanished after the move',\n"
        "           'node_title': " + repr(node_title) + ",\n"
        "           'position': pos}\n"
    )
    return _editor(blueprint_path, graph_name, body)


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


def connect_blueprint_pins(blueprint_path: str, from_node: str, from_pin: str,
                           to_node: str, to_pin: str,
                           graph_name: str = "EventGraph") -> dict:
    """
    Connects `from_node.from_pin` to `to_node.to_pin` by node title and pin name.

    Wiring goes through `destination_pin.assign(source_pin)`. The three
    link-oriented calls the API also offers (`try_create_connection` on either the
    pin or the library, and `list_connected_pins`) all wedge the Remote Control
    request with an empty log and no return value, and no pin exposes
    `is_linked`, so a connection cannot be read back directly. This tool
    therefore verifies by compiling the Blueprint and comparing the set of
    error-bearing node titles before and after the assign, which goes through a
    completely different API path. A newly broken node appears in `new_errors`.

    An ambiguous node title is an error rather than a first-match pick, for the
    same reason the material graph tools do it: wiring the wrong node compiles
    and behaves wrongly.
    """
    security.enforce_tier("connect_blueprint_pins")
    body = (
        "before = _error_titles(ge)\n"
        "nodes = list(ge.list_all_nodes())\n"
        "src_node, err = _pick_node(nodes, " + repr(from_node) + ")\n"
        "if err is not None:\n"
        "    OUT = {'found': False, 'error': err}\n"
        "else:\n"
        "    dst_node, err2 = _pick_node(nodes, " + repr(to_node) + ")\n"
        "    if err2 is not None:\n"
        "        OUT = {'found': False, 'error': err2}\n"
        "    else:\n"
        "        src_p, err3 = _pick_pin(src_node, " + repr(from_pin) + ")\n"
        "        if err3 is not None:\n"
        "            OUT = {'found': False, 'error': err3}\n"
        "        else:\n"
        "            dst_p, err4 = _pick_pin(dst_node, " + repr(to_pin) + ")\n"
        "            if err4 is not None:\n"
        "                OUT = {'found': False, 'error': err4}\n"
        "            else:\n"
        "                dst_p.assign(src_p)\n"
        f"                {UNREAL}.BlueprintEditorLibrary.compile_blueprint(bp)\n"
        "                after = _error_titles(ge)\n"
        "                fresh = [t for t in after if t not in before]\n"
        "                OUT = {'found': True,\n"
        "                       'error': None,\n"
        "                       'from_node': " + repr(from_node) + ",\n"
        "                       'from_pin': " + repr(from_pin) + ",\n"
        "                       'to_node': " + repr(to_node) + ",\n"
        "                       'to_pin': " + repr(to_pin) + ",\n"
        "                       'verified_by': 'compile_error_diff',\n"
        "                       'errors_before': before,\n"
        "                       'errors_after': after,\n"
        "                       'new_errors': fresh}\n"
    )
    return _editor(blueprint_path, graph_name, body)


def delete_blueprint_nodes(blueprint_path: str, node_titles: list[str],
                           confirm: bool = False,
                           graph_name: str = "EventGraph") -> dict:
    """
    Deletes nodes by title. Destructive, so it takes `confirm`.

    Reports which requested titles were found and which were not, and reads the
    graph back afterwards, so a partial delete is visible rather than counted as
    success. Ambiguous titles are an error and nothing is deleted.
    """
    security.enforce_tier("delete_blueprint_nodes", confirm=confirm)
    if not node_titles:
        return {"success": False, "error": "node_titles must not be empty"}
    wanted = ", ".join(repr(t) for t in node_titles)
    body = (
        "nodes = list(ge.list_all_nodes())\n"
        "targets = []\n"
        "missing = []\n"
        "ambiguous = []\n"
        "for title in " + repr(list(node_titles)) + ":\n"
        "    picked, err = _pick_node(nodes, title)\n"
        "    if err is not None:\n"
        "        if 'ambiguous' in err:\n"
        "            ambiguous.append(title)\n"
        "        else:\n"
        "            missing.append(title)\n"
        "    else:\n"
        "        targets.append(picked)\n"
        "if ambiguous or not targets:\n"
        "    OUT = {'found': False,\n"
        "           'error': 'ambiguous: ' + str(ambiguous) if ambiguous\n"
        "                   else 'none of the requested titles exist: '\n"
        "                        + str(missing),\n"
        "           'ambiguous': ambiguous,\n"
        "           'missing': missing,\n"
        "           'deleted': False}\n"
        "else:\n"
        "    ge.remove_nodes(targets)\n"
        f"    {UNREAL}.BlueprintEditorLibrary.compile_blueprint(bp)\n"
        "    remaining = sorted(str(n.get_node_title())\n"
        "                       for n in ge.list_all_nodes())\n"
        "    still = [t for t in " + repr(list(node_titles)) + "\n"
        "             if t in remaining]\n"
        "    OUT = {'found': not still,\n"
        "           'error': None if not still\n"
        "                   else 'these titles are still present: ' + str(still),\n"
        "           'requested': " + repr(list(node_titles)) + ",\n"
        "           'missing_before_delete': missing,\n"
        "           'deleted': not still,\n"
        "           'still_present': still,\n"
        "           'remaining_nodes': remaining}\n"
    )
    del wanted
    return _editor(blueprint_path, graph_name, body)