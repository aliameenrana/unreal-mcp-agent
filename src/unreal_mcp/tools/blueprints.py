"""
Blueprint structure reading, plus asset creation so there is something to read.

Verified against a live Unreal Editor 5.8 session on a real Actor Blueprint.

**What this module cannot do, and it is not a gap in the code:** read the nodes
inside a graph. `EdGraph.Nodes` is a protected property and raises
"Property 'Nodes' for attribute 'nodes' on 'EdGraph' is protected and cannot be
read". Graph nodes are also not addressable by object path, so there is no way to
obtain a node handle, and therefore no way to call the node-level helpers that do
exist in the API (`get_node_title`, `get_node_pos`, `get_node_size`,
`get_node_category`, `list_input_pins`, `list_output_pins`, `list_all_pins`,
`get_nodes_in_comment`). Enumerating them needs the C++ escape hatch. So what is
readable here is a Blueprint's *structure*: parent class, graphs, functions,
events, event dispatchers and variables. That answers "what does this Blueprint
do and what can it be given", which is what most inspection needs.

`compile_blueprint` only reports that the compile call was dispatched and
accepted by the editor; it does not parse the compiler results log.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    guarded,
    indent_block,
    json_dumps,
    load_asset,
    seq,
)

# NSLOCTEXT("NSLOCTEXT", "Key", "Some tooltip") leaks through BlueprintFunctionInfo
# descriptions and a few run to hundreds of characters. Truncate rather than ship
# the raw macro, and keep the tooltip text rather than the namespace and key.
# Injected into every snippet that reports descriptions.
_CLEAN_HELPER = (
    "def _clean(value):\n"
    "    text = str(value or '').strip()\n"
    "    if text.startswith('NSLOCTEXT('):\n"
    "        parts = text.rsplit('\"', 2)\n"
    "        text = parts[-2] if len(parts) >= 2 else text\n"
    "    text = ' '.join(text.split())\n"
    f"    return text[:{160 - 3}] + '...' if len(text) > {160} else text\n"
)


# Injected as a plain global into the snippets that report it, so the reason is
# stated once here rather than re-spelled in each tool.
_NODES_REASON = (
    "EdGraph.Nodes is a protected property and cannot be read from Python, and "
    "graph nodes are not addressable by object path, so node contents cannot "
    "be listed without the C++ escape hatch"
)


def _read_blueprint(blueprint_path: str, body: str) -> dict:
    """
    Runs `body` with `bp` bound to a loaded Blueprint.

    `body` is indented under an `else:` branch, so it can assume `bp` is a
    Blueprint and only has to produce `OUT`. When the path is missing or is not a
    Blueprint, the payload comes back with `found` False and a readable reason,
    which every caller turns into its own error shape.
    """
    script = (
        f"_NODES_REASON = {_NODES_REASON!r}\n"
        f"bp = {load_asset(blueprint_path)}\n"
        f"if bp is None:\n"
        f"    OUT = {{'found': False, 'error': {blueprint_path!r} + ' did not load'}}\n"
        f"elif not isinstance(bp, {UNREAL}.Blueprint):\n"
        f"    OUT = {{'found': False, 'error': 'Not a Blueprint: ' + type(bp).__name__}}\n"
        f"else:\n"
        f"{indent_block(body, spaces=4)}"
    )
    return get_bridge().run_python(guarded(script))


def _fail(blueprint_path: str, payload: dict, fallback: str) -> dict:
    return {
        "success": False,
        "blueprint_path": blueprint_path,
        "error": payload.get("error") or fallback,
    }


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def create_blueprint(blueprint_path: str,
                     parent_class: str = "/Script/Engine.Actor") -> dict:
    """
    Creates a Blueprint asset. `blueprint_path` is a package path with no object
    suffix, e.g. '/Game/MCPTest/BP_Thing'; the asset takes its name from the last
    segment.

    `parent_class` is a full class path, not a bare name; it defaults to Actor.

    Two measured quirks of `create_blueprint_asset_with_parent` are worked around
    here, because both make its raw return value untrustworthy:

    - Given a dotted path like '/Game/MCPTest/BP_Thing.BP_Thing' it does not
      reject it, it sanitises the dot into the asset name and creates
      'BP_Thing_BP_Thing'. So this refuses a dotted path outright.
    - It returns None when the asset already exists, and it can also return None
      in cases where it did create the asset anyway. The return value is
      therefore not used to decide success; the asset is loaded back and that is
      the evidence.
    """
    security.enforce_tier("create_blueprint")
    if not blueprint_path.strip():
        return {"success": False, "error": "blueprint_path must not be empty."}
    if "." in blueprint_path.rsplit("/", 1)[-1]:
        return {"success": False, "blueprint_path": None,
                "error": "blueprint_path must not have an object suffix: pass "
                         "'/Game/Package/Name', not '/Game/Package/Name.Name'. "
                         "A dotted path is silently created with the dot turned "
                         "into an underscore in the asset name."}

    script = (
        f"cls = {UNREAL}.load_class(None, {parent_class!r})\n"
        f"package = {blueprint_path!r}\n"
        f"if cls is None:\n"
        f"    OUT = {{'found': False, 'error': 'no such parent class ' + {parent_class!r}}}\n"
        f"elif {UNREAL}.EditorAssetLibrary.does_asset_exist(package):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'an asset already exists at ' + package + '; use a new name'}}\n"
        f"else:\n"
        f"    returned = {UNREAL}.BlueprintEditorLibrary.create_blueprint_asset_with_parent(\n"
        f"        package, cls)\n"
        f"    name = package.rsplit('/', 1)[-1]\n"
        f"    bp = returned\n"
        f"    if bp is None:\n"
        f"        bp = {UNREAL}.load_object(None, name)\n"
        f"    if bp is None:\n"
        f"        bp = {UNREAL}.load_asset(package)\n"
        f"    ok = isinstance(bp, {UNREAL}.Blueprint)\n"
        f"    OUT = {{'found': ok,\n"
        f"          'error': None if ok else 'the editor created nothing at ' + package,\n"
        f"          'blueprint_path': bp.get_path_name() if ok else None,\n"
        f"          'parent_class': cls.get_name() if cls else None,\n"
        f"          'editor_returned_none': returned is None}}\n"
    )
    payload = get_bridge().run_python(guarded(script))
    if not payload.get("found"):
        return {"success": False, "blueprint_path": None,
                "error": payload.get("error") or "could not create blueprint"}
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "parent_class": payload.get("parent_class"),
            "editor_returned_none": payload.get("editor_returned_none")}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def get_blueprint_info(blueprint_path: str) -> dict:
    """
    Summarises a Blueprint in one call: parent class, graph names, and counts of
    functions, events, event dispatchers and member variables. Use the matching
    list_blueprint_* tools when you need the full lists.
    """
    security.enforce_tier("get_blueprint_info")
    body = (
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'parent_class': B.get_blueprint_parent_class(bp).get_name(),\n"
        f"      'graphs': [str(n) for n in B.list_graph_names(bp)],\n"
        f"      'function_count': len(list(B.list_functions(bp))),\n"
        f"      'event_count': len(list(B.list_events(bp))),\n"
        f"      'dispatcher_count': len(list(B.list_event_dispatchers(bp))),\n"
        f"      'variable_count': len(list(B.list_member_variable_names(bp))),\n"
        f"      'nodes_readable': False}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not read blueprint")
    return {
        "success": True,
        "blueprint_path": payload.get("blueprint_path"),
        "parent_class": payload.get("parent_class"),
        "graphs": payload.get("graphs") or [],
        "function_count": payload.get("function_count"),
        "event_count": payload.get("event_count"),
        "dispatcher_count": payload.get("dispatcher_count"),
        "variable_count": payload.get("variable_count"),
        "nodes_readable": False,
    }


def list_blueprint_graphs(blueprint_path: str) -> dict:
    """
    Lists a Blueprint's graphs by name, with each graph's object path and whether
    it is the event graph.

    This is graph *identity*, not graph *contents*. `nodes_readable` is always
    False, with the reason attached, so a caller never mistakes this for a node
    listing.
    """
    security.enforce_tier("list_blueprint_graphs")
    body = (
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"event_graph = B.find_event_graph(bp)\n"
        f"event_name = str(event_graph.get_name()) if event_graph is not None else None\n"
        f"rows = []\n"
        f"for g in list(B.list_graphs(bp)):\n"
        f"    name = str(g.get_name())\n"
        f"    rows.append({{'name': name,\n"
        f"                 'object_path': g.get_path_name(),\n"
        f"                 'is_event_graph': name == event_name}})\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'count': len(rows), 'graphs': rows,\n"
        f"      'nodes_readable': False,\n"
        f"      'nodes_unreadable_reason': _NODES_REASON}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not list graphs")
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "count": payload.get("count"),
            "graphs": payload.get("graphs") or [],
            "nodes_readable": False,
            "nodes_unreadable_reason": payload.get("nodes_unreadable_reason")}


def list_blueprint_functions(blueprint_path: str) -> dict:
    """
    Lists a Blueprint's functions with each one's implemented flag and tooltip.

    `is_implemented` is the field worth filtering on: it separates functions you
    can actually call from inherited boilerplate.
    """
    security.enforce_tier("list_blueprint_functions")
    body = (
        f"{_CLEAN_HELPER}"
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"rows = [{{'name': str(f.name),\n"
        f"         'is_implemented': bool(f.is_implemented),\n"
        f"         'description': _clean(f.description)}}\n"
        f"        for f in list(B.list_functions(bp))]\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'count': len(rows), 'functions': rows}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not list functions")
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "count": payload.get("count"),
            "functions": payload.get("functions") or []}


def list_blueprint_events(blueprint_path: str) -> dict:
    """
    Lists the events a Blueprint's parent class exposes, with implemented flags.

    An Actor Blueprint reports every event its parent can raise, most of them not
    implemented, so filter on `is_implemented` to get the ones actually handled.
    """
    security.enforce_tier("list_blueprint_events")
    body = (
        f"{_CLEAN_HELPER}"
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"rows = [{{'name': str(e.name),\n"
        f"         'is_implemented': bool(e.is_implemented),\n"
        f"         'description': _clean(e.description)}}\n"
        f"        for e in list(B.list_events(bp))]\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'count': len(rows),\n"
        f"      'implemented_count': len([r for r in rows if r['is_implemented']]),\n"
        f"      'events': rows}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not list events")
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "count": payload.get("count"),
            "implemented_count": payload.get("implemented_count"),
            "events": payload.get("events") or []}


def list_blueprint_variables(blueprint_path: str,
                             include_inherited: bool = False) -> dict:
    """
    Lists a Blueprint's member variables with type, category and replication.

    `include_inherited` defaults to False so you get the Blueprint's own
    variables; True pulls in every property the parent contributes, which for an
    Actor Blueprint is a long list of engine fields.

    **The `type` column is a JSON schema, not a type name**, because
    `EdGraphPinType` exposes no readable fields: `str()` on one renders as
    `<Struct 'EdGraphPinType' (0x...) {}>` for every type, identical apart from
    the address. `BlueprintEditorLibrary.pin_type_to_json_schema` is the only
    rendering that carries information, so that is what is returned, e.g.
    '{"type": "boolean"}'. Its limitation is that it does not distinguish `int32`
    from `float`: both report '{"type": "integer"}'. The Blueprint's parent class
    is passed as the schema's self context, which is what the variable resolves
    against.
    """
    security.enforce_tier("list_blueprint_variables")
    body = (
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"context = B.get_blueprint_parent_class(bp)\n"
        f"rows = []\n"
        f"for n in list(B.list_member_variable_names(bp, {include_inherited!r})):\n"
        f"    name = str(n)\n"
        f"    row = {{'name': name}}\n"
        f"    try:\n"
        f"        pin_type = B.get_member_variable_type(bp, name)\n"
        f"        row['type'] = ' '.join(\n"
        f"            B.pin_type_to_json_schema(pin_type, context).split())\n"
        f"    except Exception as exc:\n"
        f"        row['type'] = None\n"
        f"        row['type_error'] = type(exc).__name__\n"
        f"    for label, getter in (('category', B.get_blueprint_variable_category),\n"
        f"                         ('replication', B.get_blueprint_variable_replication)):\n"
        f"        try:\n"
        f"            row[label] = str(getter(bp, name))\n"
        f"        except Exception as exc:\n"
        f"            row[label] = None\n"
        f"            row[label + '_error'] = type(exc).__name__\n"
        f"    rows.append(row)\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'include_inherited': {include_inherited!r},\n"
        f"      'type_format': 'json schema from pin_type_to_json_schema; int32 and'\n"
        f"          ' float both read as integer',\n"
        f"      'count': len(rows), 'variables': rows}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not list variables")
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "include_inherited": payload.get("include_inherited"),
            "type_format": payload.get("type_format"),
            "count": payload.get("count"),
            "variables": payload.get("variables") or []}


def list_blueprint_event_dispatchers(blueprint_path: str) -> dict:
    """
    Lists a Blueprint's custom event dispatchers by name. Empty for most
    Blueprints; a non-empty list means it is using custom events.
    """
    security.enforce_tier("list_blueprint_event_dispatchers")
    body = (
        f"B = {UNREAL}.BlueprintEditorLibrary\n"
        f"names = [str(n) for n in B.list_event_dispatchers(bp)]\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'blueprint_path': bp.get_path_name(),\n"
        f"      'count': len(names), 'dispatchers': names}}\n"
    )
    payload = _read_blueprint(blueprint_path, body)
    if not payload.get("found"):
        return _fail(blueprint_path, payload, "could not list event dispatchers")
    return {"success": True,
            "blueprint_path": payload.get("blueprint_path"),
            "count": payload.get("count"),
            "dispatchers": payload.get("dispatchers") or []}


def compile_blueprint(blueprint_path: str) -> dict:
    """
    Compiles a Blueprint asset given its path, e.g. '/Game/Blueprints/BP_Thing'.
    Returns success/failure based on whether the remote call raised, not on
    parsing Unreal's own compiler results log. Reading that back is not built,
    because the results log is not exposed to Python either.
    """
    security.enforce_tier("compile_blueprint")
    expr = json_dumps(
        seq(
            f"{UNREAL}.BlueprintEditorLibrary.compile_blueprint({load_asset(blueprint_path)})",
            repr(blueprint_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "blueprint_path": result}


def set_blueprint_parent_class(blueprint_path: str, parent_class: str) -> dict:
    """
    Reparents a Blueprint to a new parent class and reads the result back.

    Reparenting is not a property write: existing nodes, inherited variables and
    overridden functions are all remapped, so the result is confirmed rather
    than assumed. The route is `BlueprintEditorLibrary.reparent_blueprint`; the
    read-back uses `get_blueprint_parent_class`.
    """
    security.enforce_tier("set_blueprint_parent_class")
    body = (
        f"_bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"_new = {UNREAL}.load_object(None, {parent_class!r})\n"
        f"OUT = {{'set': False, 'path': {blueprint_path!r}, 'requested': {parent_class!r}, 'resolved': _new is not None, 'before': None, 'after': None}}\n"
        f"if _bp is None or not isinstance(_bp, {UNREAL}.Blueprint):\n"
        f"    OUT['error'] = 'no Blueprint at ' + {blueprint_path!r}\n"
        f"elif _new is None:\n"
        f"    OUT['error'] = 'could not load parent class ' + {parent_class!r}\n"
        f"else:\n"
        f"    _bel = {UNREAL}.BlueprintEditorLibrary\n"
        f"    OUT['before'] = str(_bel.get_blueprint_parent_class(_bp))\n"
        f"    _bel.reparent_blueprint(_bp, _new)\n"
        f"    _bp2 = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"    OUT['after'] = str(_bel.get_blueprint_parent_class(_bp2))\n"
        f"    OUT['set'] = OUT['after'] is not None\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("set")),
        "path": payload.get("path"),
        "requested": payload.get("requested"),
        "resolved": payload.get("resolved"),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "error": payload.get("error"),
    }


def create_game_mode(
    destination_path: str, parent_class: str = "/Script/Engine.GameModeBase"
) -> dict:
    """
    Creates a Blueprint GameMode asset at `destination_path`.

    `parent_class` defaults to `/Script/Engine.GameModeBase`, the plain gameplay
    GameMode with no rules. It must be a **full class path**: the bare name
    `GameModeBase` does not resolve through `load_object`, and says so through
    `parent_resolved` rather than quietly producing a wrong-typed Blueprint.
    """
    security.enforce_tier("create_game_mode")
    body = (
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"_parent = {UNREAL}.load_object(None, {parent_class!r})\n"
        f"OUT = {{'created': False, 'path': {destination_path!r}, 'parent_class': {parent_class!r}, 'parent_resolved': _parent is not None}}\n"
        f"if {UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}):\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"elif _parent is None:\n"
        f"    OUT['error'] = 'could not load parent class ' + {parent_class!r}\n"
        f"else:\n"
        f"    _bp = {UNREAL}.BlueprintEditorLibrary.create_blueprint_asset_with_parent(\n"
        f"        {destination_path!r}, _parent)\n"
        f"    if _bp is None:\n"
        f"        OUT['error'] = 'create_blueprint_asset_with_parent returned None'\n"
        f"    else:\n"
        f"        {UNREAL}.EditorAssetLibrary.save_asset({destination_path!r}, only_if_is_dirty=False)\n"
        f"        _back = {UNREAL}.load_object(None, {destination_path!r})\n"
        f"        OUT['created'] = _back is not None\n"
        f"        if _back is not None:\n"
        f"            OUT['actual_parent'] = str({UNREAL}.BlueprintEditorLibrary.get_blueprint_parent_class(_back))\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "path": destination_path,
        "parent_class": payload.get("parent_class"),
        "parent_resolved": payload.get("parent_resolved"),
        "actual_parent": payload.get("actual_parent"),
        "error": payload.get("error"),
    }


def set_default_game_mode(blueprint_path: str) -> dict:
    """
    Sets a GameMode Blueprint as the current level's default game mode, through
    the level's `WorldSettings`.

    This goes via `WorldSettings`, not `ProjectSettings`, because there is no
    `ProjectSettings` class in this build's Python API. That makes it a
    per-level setting rather than a project-wide one, which is the honest scope
    of what is reachable from here.
    """
    security.enforce_tier("set_default_game_mode")
    body = (
        f"_gm = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"_ws = {UNREAL}.EditorLevelLibrary.get_editor_world().get_world_settings()\n"
        f"OUT = {{'set': False, 'path': {blueprint_path!r}, 'before': None, 'after': None}}\n"
        f"if _gm is None or not isinstance(_gm, {UNREAL}.Blueprint):\n"
        f"    OUT['error'] = 'no Blueprint at ' + {blueprint_path!r}\n"
        f"else:\n"
        f"    OUT['before'] = str(_ws.get_editor_property('default_game_mode'))\n"
        f"    _gen = _gm.generated_class()\n"
        f"    if _gen is None:\n"
        f"        OUT['error'] = 'GameMode blueprint has no generated class'\n"
        f"    else:\n"
        f"        _ws.set_editor_property('default_game_mode', _gen)\n"
        f"        OUT['after'] = str(_ws.get_editor_property('default_game_mode'))\n"
        f"        OUT['set'] = _gen.get_path_name() in (OUT['after'] or '')\n"
        f"        {UNREAL}.EditorLevelLibrary.save_current_level()\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("set")),
        "path": payload.get("path"),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "error": payload.get("error"),
    }