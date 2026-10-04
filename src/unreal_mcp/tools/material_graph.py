"""
Material graph authoring.

The read tools here (`materials.get_material_parameter_list` and friends) can
tell you what parameters a material exposes. They cannot tell you what the graph
inside actually does, which is why a whole family of authoring tools exists:
an agent that can only set parameters can never build a material, and every
material it wants has to already exist.

Three things about this API are not obvious and are the source of most of the
care in the code below.

**Nodes have no identity beyond their description.** `MaterialExpression` has no
id, no name and no guid. Its `desc` (the editable title drawn on the node) is the
only handle that survives a recompile, and it is empty on most nodes. So every
tool here takes a `expression` selector that is matched in a defined order, and
**reports an error when a selector matches more than one node** instead of
picking one. Silently wiring the wrong node is the single worst failure this
tool family has, because the material still compiles and still renders.

**Connecting to a material input is a different call from node-to-node.**
`connect_material_expressions` takes two nodes and two pin names;
`connect_material_property` takes one node and a `MaterialProperty`. There is no
overload that does both.

**`recompile_material` returns the shader errors, it does not raise.** A
material with a broken graph compiles anyway, with errors reported as a return
value. A tool that ignores that return value reports success on a material that
does not compile, so every mutating call in this module recompiles and returns
the resulting errors.

Node reference selectors, matched in this order:

- ``BaseColor``          the node whose `desc` is exactly this
- ``Multiply@120,80``    class name and node position
- ``Multiply``           the only node of that class; an error if there are two
- ``#2``                 the third node in editor order (0-based)
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, indent_block, load_asset

# Material inputs worth offering by name. MaterialProperty in 5.8 has no
# roughness-from-metallic or clear-coat member: those are shading models, not
# inputs, so they cannot be wired here.
MATERIAL_INPUTS = {
    "base_color": "MP_BASE_COLOR",
    "emissive_color": "MP_EMISSIVE_COLOR",
    "normal": "MP_NORMAL",
    "metallic": "MP_METALLIC",
    "roughness": "MP_ROUGHNESS",
    "specular": "MP_SPECULAR",
    "ambient_occlusion": "MP_AMBIENT_OCCLUSION",
    "opacity": "MP_OPACITY",
    "opacity_mask": "MP_OPACITY_MASK",
    "refraction": "MP_REFRACTION",
    "subsurface_color": "MP_SUBSURFACE_COLOR",
    "world_position_offset": "MP_WORLD_POSITION_OFFSET",
    "tangent": "MP_TANGENT",
    "anisotropy": "MP_ANISOTROPY",
    "front_material": "MP_FRONT_MATERIAL",
    "material_attributes": "MP_MATERIAL_ATTRIBUTES",
}

# Parameter node classes, keyed by the friendly name an agent would use.
#
# The property that carries a parameter's default differs per class, which is
# worth stating because `r` is the obvious guess and is wrong for every
# parameter node. Measured on 5.8 by attempting each property on each class:
#
#   class                          r    default_value    parameter_name   texture
#   MaterialExpressionConstant     yes   -                -                -
#   ScalarParameter                -     yes (float)      yes              -
#   VectorParameter                -     yes (LinearColor) yes             -
#   StaticSwitchParameter          -     yes (bool)       yes              -
#   TextureSampleParameter2D       -     -                yes              yes
PARAMETER_CLASSES = {
    "scalar": "MaterialExpressionScalarParameter",
    "vector": "MaterialExpressionVectorParameter",
    "texture": "MaterialExpressionTextureSampleParameter2D",
    "static_switch": "MaterialExpressionStaticSwitchParameter",
}


# Bare class suffixes an agent is likely to type, e.g. "Multiply" rather than
# the full "MaterialExpressionMultiply".
_KNOWN_SUFFIXES = frozenset({
    "Constant", "Constant2Vector", "Constant3Vector", "Constant4Vector",
    "Multiply", "Add", "Subtract", "Divide", "Saturate", "OneMinus", "Abs",
    "Power", "Sine", "Cosine", "Clamp", "Lerp", "LinearInterpolate",
    "Normalize", "ComponentMask", "AppendVector", "DotProduct", "Fresnel",
    "TextureSample", "TextureSampleParameter2D", "TextureCoordinate",
    "ScalarParameter", "VectorParameter", "StaticSwitch",
    "StaticSwitchParameter", "Comment",
})


def _selector_predicate(selector: str) -> str:
    """
    Builds the source for a node predicate from a selector string, so every tool
    resolves nodes the same way.

    The predicate is emitted into a list comprehension whose loop variable is
    `e`, so it must refer to `e`. An earlier version named `_target`, which is
    only bound after the comprehension has already run, and every selector
    raised NameError.

    Returns (predicate_source, label) where the predicate is True for the single
    matching node, and raises nothing: the caller reports ambiguity, because
    whether two nodes share a desc is only known on the editor side.
    """
    sel = selector.strip()
    if sel.startswith("#") and sel[1:].isdigit():
        return (
            f"list(mel.get_material_expressions(m)).index(e) == {int(sel[1:])}",
            f"index {int(sel[1:])}",
        )
    if "@" in sel:
        head, _, coords = sel.partition("@")
        x, _, y = coords.partition(",")
        pos = f"list(mel.get_material_expression_node_position(e)) == [int({x.strip()}), int({y.strip()})]"
        bare = head.replace(" ", "")
        # The head is whatever _describe emitted: a class name for an unnamed
        # node, or a title for a named one. Accept either, because the selector
        # a tool hands back is always in that composite form.
        return (
            f"({pos} and (e.get_class().get_name() == {head!r}"
            f" or e.get_class().get_name() == 'MaterialExpression' + {head!r}"
            f" or str(e.get_editor_property('desc')).replace(' ', '') == {bare!r}"
            f" or (hasattr(e, 'parameter_name')"
            f" and str(e.get_editor_property('parameter_name')).replace(' ', '')"
            f" == {bare!r})))",
            f"{head} at {x.strip()},{y.strip()}",
        )
    # A node title is matched by description, a class by name. Testing "is this a
    # description?" with a shape heuristic put a bare "Multiply" on the
    # description path even though no node has that desc, so the test is whether
    # the selector actually looks like an expression class.
    if sel.startswith("MaterialExpression") or sel in _KNOWN_SUFFIXES:
        return (
            f"e.get_class().get_name() == {sel!r} "
            f"or e.get_class().get_name() == 'MaterialExpression' + {sel!r}",
            f"class {sel!r}",
        )
    # Both description and parameter_name are str(Name), which renders with an
    # internal space, so compare on whitespace-stripped text rather than
    # requiring the caller to know that str() adds one.
    bare = sel.replace(" ", "")
    return (
        f"(str(e.get_editor_property('desc')).replace(' ', '') == {bare!r}"
        f" or (hasattr(e, 'parameter_name')"
        f" and str(e.get_editor_property('parameter_name')).replace(' ', '')"
        f" == {bare!r}))",
        f"description {sel!r}",
    )


def _resolve_snippet(selector: str) -> str:
    """
    Source that binds `_target` to the one node matching `selector`, and sets
    `_err` describing why it failed instead of raising. Callers check `_err`.

    Resolving to exactly one node is the whole point: a graph tool that acts on
    "the first match" will eventually wire the wrong node and produce a material
    that compiles and renders wrong.

    Two constraints shape the emitted code, both learned the hard way:

    - **No braces anywhere in this f-string's text.** Braces belong to the
      format syntax, so any {...} in the emitted snippet is evaluated here while
      the snippet is being built, not while it runs. An earlier version emitted
      literal n and d placeholders and raised NameError before the snippet was
      ever sent anywhere. The same applies to comments: they are inside the
      f-string too, so a brace in a comment breaks the build.
    - **`_describe` is defined before `_resolve`.** `_resolve` calls it, and when
      this block is nested inside another function it cannot see a name bound
      later at that level. Module-level code tolerates either order.

    Error messages are built by concatenation and the label is already quoted by
    the caller, so it is not nested inside a second pair of quotes.
    """
    predicate, label = _selector_predicate(selector)
    # _describe is defined first on purpose: _resolve calls it, and when this
    # block is nested inside another function _resolve cannot see a name that is
    # only bound later at that level. Module-level code would tolerate either
    # order; a nested one raises NameError.
    #
    # label is interpolated as a literal below, never referenced by name. It
    # exists only on this side of the bridge, so the emitted code has no binding
    # for it and every failure path raised NameError instead of reporting which
    # selector was ambiguous.
    return f'''
def _describe(e):
    d = str(e.get_editor_property("desc") or "")
    cls = e.get_class().get_name()
    pos = list(mel.get_material_expression_node_position(e))
    return (d + " " if d else "") + cls + "@" + str(pos[0]) + "," + str(pos[1])


def _resolve():
    nodes = list(mel.get_material_expressions(m))
    hits = [e for e in nodes if {predicate}]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, ("no node matching " + {label!r} + "; the material has "
                      + str(len(nodes)) + " expression(s): "
                      + ", ".join(_describe(e) for e in nodes[:12]))
    # No braces anywhere in this emitted code. The whole snippet is a single
    # f-string, so any {...} here is evaluated while BUILDING the snippet, not
    # while it runs in the editor. An earlier version wrote literal n and d
    # placeholders inside braces and got a NameError at build time, before the
    # snippet was ever sent anywhere.
    return None, ({label!r} + " matches " + str(len(hits)) + " nodes ("
                  + ", ".join(_describe(e) for e in hits[:8])
                  + "); disambiguate with a description, Class@x,y, or #index")


_target, _err = _resolve()
'''


def _node_summary(m) -> str:
    """Source listing every node compactly. Used by the read tools."""
    return '''
def _describe(e):
    d = str(e.get_editor_property("desc") or "")
    cls = e.get_class().get_name()
    pos = list(mel.get_material_expression_node_position(e))
    label = (d + " " if d else "") + cls + "@" + str(pos[0]) + "," + str(pos[1])
    try:
        outs = list(mel.get_material_expression_output_names(e))
    except Exception:
        outs = []
    try:
        wired = mel.get_inputs_for_material_expression(m, e)
        # This returns the expressions wired into each input, NOT pin names.
        # Report how many inputs there are and which are occupied; the names are
        # not readable from Python at all.
        ins = {
            "count": len(wired),
            "wired": sum(1 for i in wired if i is not None),
            # Input pin names are a per-class convention (A/B for Add and
            # Multiply, Coordinates/RGB/R/G/B/A for a TextureSample) and cannot
            # be discovered, so they are not invented here.
            "names_discoverable": False,
        }
    except Exception:
        ins = None
    return {"class": cls, "desc": d, "x": pos[0], "y": pos[1],
            "outputs": outs, "inputs": ins, "selector": label}


_nodes = [_describe(e) for e in mel.get_material_expressions(m)]

'''


def _compile_errors(material_path: str, recompile: bool = True, indent: str = "") -> str:
    """
    Source for `errors`. With recompile=False the variable is simply left empty,
    so the OUT dicts that reference it still build and the tool still returns a
    `compiled_clean` field (as None, meaning "not checked").

    This matters more than it looks: `recompile_material` queues a shader
    compile, and compiling a material whose domain or shading model just changed
    blocks the editor's main thread long enough that the Remote Control endpoint
    stops answering. A script that creates twenty nodes with the recompile on
    default will hammer the editor into that state, so batching exists.
    """
    pad = indent
    if not recompile:
        return f"{pad}errors = None\n"
    return (
        f"{pad}errors = [str(e) for e in "
        f"{UNREAL}.MaterialEditingLibrary.recompile_material(m)]\n"
    )


def _recompiled_result(indent: str = "              ") -> str:
    """
    Source for the two compile fields every OUT dict carries. indent must match
    the continuation lines of the dict it lands in, because this is spliced
    between them and a mismatched depth is an IndentationError.
    """
    pad = indent
    return (
        f"{pad}'recompile_errors': errors,\n"
        f"{pad}'compiled_clean': (len(errors) == 0) if errors is not None else None,\n"
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_material_expressions(material_path: str) -> dict:
    """
    Lists every node in a material's graph with its class, its description (the
    editable node title, which is the only stable handle one has), its position,
    and the names of its input and output pins.

    Positions double as selectors: a node can be addressed later as
    ``Multiply@120,80``.
    """
    security.enforce_tier("list_material_expressions")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'count': None, 'nodes': []}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        + indent_block(_node_summary("m"), 4) +
        f"    OUT = {{'found': True, 'error': None, 'count': len(_nodes), 'nodes': _nodes}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    nodes = payload.get("nodes") or []
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "count": payload.get("count"),
        "nodes": nodes,
    }


def get_material_inputs(material_path: str) -> dict:
    """
    Reports which material inputs (base colour, normal, roughness, ...) exist and
    whether anything is currently connected to them.

    `MP_MAX` and `MP_LAST_CUSTOMIZED_U_VS` are internal sentinels rather than
    real inputs, so they are excluded.
    """
    security.enforce_tier("get_material_inputs")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'domain': None, 'inputs': []}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    skip = ('MP_MAX', 'MP_LAST_CUSTOMIZED_U_VS')\n"
        f"    rows = []\n"
        f"    for name in {sorted(MATERIAL_INPUTS.values())!r}:\n"
        f"        if name in skip:\n"
        f"            continue\n"
        f"        try:\n"
        f"            prop = getattr({UNREAL}.MaterialProperty, name)\n"
        f"            node = mel.get_material_property_input_node(m, prop)\n"
        f"            rows.append({{'enum': name,\n"
        f"                          'connected': node is not None,\n"
        f"                          'node_class': node.get_class().get_name()\n"
        f"                                      if node else None,\n"
        f"                          'node_desc': str(node.get_editor_property('desc') or '')\n"
        f"                                       if node else ''}})\n"
        f"        except Exception as exc:\n"
        f"            rows.append({{'enum': name, 'connected': False,\n"
        f"                          'error': type(exc).__name__}})\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'domain': str(m.get_editor_property('material_domain')),\n"
        f"          'inputs': rows}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "domain": payload.get("domain"),
        "inputs": payload.get("inputs") or [],
    }


def get_material_graph_stats(material_path: str) -> dict:
    """
    Shader cost of a material: pixel and vertex instruction counts, sampler and
    texture-sample counts, UV and interpolator scalars.

    This is the check that catches a material which works but is too expensive.
    Pixel instruction counts are the number to watch; a material for a large
    surface that runs into the thousands is a performance bug even though every
    functional test passes.
    """
    security.enforce_tier("get_material_graph_stats")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'stats': None}}\n"
        f"else:\n"
        f"    s = {UNREAL}.MaterialEditingLibrary.get_statistics(m)\n"
        f"    OUT = {{'found': True, 'error': None, 'stats': {{\n"
        f"        'pixel_shader_instructions': s.get_editor_property('num_pixel_shader_instructions'),\n"
        f"        'vertex_shader_instructions': s.get_editor_property('num_vertex_shader_instructions'),\n"
        f"        'samplers': s.get_editor_property('num_samplers'),\n"
        f"        'pixel_texture_samples': s.get_editor_property('num_pixel_texture_samples'),\n"
        f"        'vertex_texture_samples': s.get_editor_property('num_vertex_texture_samples'),\n"
        f"        'virtual_texture_samples': s.get_editor_property('num_virtual_texture_samples'),\n"
        f"        'uv_scalars': s.get_editor_property('num_uv_scalars'),\n"
        f"        'interpolator_scalars': s.get_editor_property('num_interpolator_scalars')}}}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "stats": payload.get("stats"),
    }


def get_material_used_textures(material_path: str) -> dict:
    """
    Lists the textures a material references, by asset path. Note this follows
    the graph, so a texture that is wired into a node that itself feeds nothing is
    not counted.
    """
    security.enforce_tier("get_material_used_textures")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'textures': []}}\n"
        f"else:\n"
        f"    tex = {UNREAL}.MaterialEditingLibrary.get_material_used_textures(m)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'textures': [t.get_path_name() for t in tex]}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "count": len(payload.get("textures") or []),
        "textures": payload.get("textures") or [],
    }


def find_material_parameter_usage(material_path: str) -> dict:
    """
    Reverse lookup: which child material instances inherit from this material,
    and which of them override each of its parameters.

    `get_material_parameter_list` tells you what a material exposes; this tells
    you who depends on it, which is the question you need before changing a
    default or renaming a parameter.
    """
    security.enforce_tier("find_material_parameter_usage")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'children': []}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    kids = mel.get_child_instances(m)\n"
        # get_child_instances returns AssetData, not loaded objects, so
        # get_path_name() does not exist on them. package_name is the package
        # path without the object suffix, which is what callers pass to the
        # other material tools, so build the full object path from it.
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'children': [str(k.package_name) + '.'\n"
        f"                       + str(k.package_name).rsplit('/', 1)[-1]\n"
        f"                       for k in kids]}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "count": len(payload.get("children") or []),
        "children": payload.get("children") or [],
    }


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------


def create_material_expression(
    material_path: str,
    expression_class: str,
    node_x: int = 0,
    node_y: int = 0,
    description: str = "",
    properties: dict[str, object] | None = None,
    parameter_name: str = "",
    group: str = "",
    recompile: bool = True,
) -> dict:
    """
    Adds a node to a material's graph. expression_class is a
    `MaterialExpression...` class name, e.g. `MaterialExpressionMultiply`.

    parameter_name and group exist because **a parameter node with an empty
    `parameter_name` is not a parameter.** Creating a
    `MaterialExpressionTextureSampleParameter2D` and leaving the name unset adds
    nothing to `get_texture_parameter_names()`: the node reads as a parameter in
    the graph while being invisible to every instance. Prefer
    create_material_parameter, which sets name, group and default together and
    then confirms the name registered. The returned scalar_names /
    vector_names / texture_names are read back from the material so a caller can
    see whether it took.

    description sets the node's title, which is the only stable way to refer to
    it afterwards, so set it whenever the node matters. properties is an optional
    map of editor property name to value, applied before returning, which is how
    a node gets configured (a Constant's `r`, a TextureSample's `texture`).

    Every mutating tool here recompiles afterwards and returns
    `recompile_errors`, because a graph with a type error still "compiles" and
    reports the problem as a return value rather than an exception.
    """
    security.enforce_tier("create_material_expression")
    security.check_destination_path(material_path)

    if not expression_class.startswith("MaterialExpression"):
        return {
            "success": False,
            "error": f"expression_class must be a MaterialExpression class name, got "
                     f"{expression_class!r}.",
        }
    if properties and not isinstance(properties, dict):
        return {"success": False, "error": "properties must be an object."}

    # Built at the 8-space depth these lines sit at inside the nested else, and
    # written here rather than reindented at the splice site so the depth is
    # visible where the lines are defined.
    config_lines = []
    if description:
        config_lines.append(f"    _expr.set_editor_property('desc', {description!r})")
    if parameter_name:
        config_lines.append(
            f"    _expr.set_editor_property('parameter_name', "
            f"{UNREAL}.Name({parameter_name!r}))"
        )
    if group:
        config_lines.append(
            f"    _expr.set_editor_property('group', {UNREAL}.Name({group!r}))"
        )
    for key, value in (properties or {}).items():
        config_lines.append(
            f"    _expr.set_editor_property({key!r}, {value!r})"
        )
    # indent_block joins lines with "\n" and adds no trailing one, so an empty
    # element is appended to guarantee the block ends in a newline. Without it
    # the next line is concatenated onto the last, which is unparseable.
    config = indent_block("\n".join(config_lines + [""]), 4)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'selector': None}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _cls = getattr({UNREAL}, {expression_class!r}, None)\n"
        f"    if _cls is None:\n"
        f"        OUT = {{'found': False, 'error': 'No such expression class: ' + "
        f"{expression_class!r}, 'selector': None}}\n"
        f"    else:\n"
        f"        _expr = mel.create_material_expression(m, _cls, {int(node_x)}, {int(node_y)})\n"
        + config +
        f"        _pos = list(mel.get_material_expression_node_position(_expr))\n"
        f"        _d = str(_expr.get_editor_property('desc') or '')\n"
        f"        _sel = ((_d + ' ') if _d else '') + _expr.get_class().get_name()\n"
        f"        _sel = _sel + '@' + str(_pos[0]) + ',' + str(_pos[1])\n"
        # errors is assigned at column 0 and the OUT dict below is at depth 8,
        # so the helper cannot be indented without changing its meaning. It is
        # placed before the else: instead, where column 0 is correct.
        + _compile_errors(material_path, recompile, "        ") +
        f"        _scalar_names = [str(n) for n in mel.get_scalar_parameter_names(m)]\n"
        f"        _vector_names = [str(n) for n in mel.get_vector_parameter_names(m)]\n"
        f"        _texture_names = [str(n) for n in mel.get_texture_parameter_names(m)]\n"
        f"        OUT = {{'found': True, 'error': None, 'selector': _sel,\n"
        f"              'class': _expr.get_class().get_name(), 'desc': _d,\n"
        f"              'x': _pos[0], 'y': _pos[1],\n"
        f"              'scalar_names': _scalar_names,\n"
        f"              'vector_names': _vector_names,\n"
        f"              'texture_names': _texture_names,\n"
        + _recompiled_result("              ") +
        f"              'expression_count': mel.get_num_material_expressions(m)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": True,
        "material_path": material_path,
        "selector": payload.get("selector"),
        "expression_class": payload.get("class"),
        "description": payload.get("desc"),
        "position": [payload.get("x"), payload.get("y")],
        "expression_count": payload.get("expression_count"),
        "scalar_names": payload.get("scalar_names"),
        "vector_names": payload.get("vector_names"),
        "texture_names": payload.get("texture_names"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def delete_material_expression(
    material_path: str,
    expression: str,
    confirm: bool = False,
    recompile: bool = True,
) -> dict:
    """
    Removes one node and everything wired into it. Destructive: requires
    confirm=True, because the node's inputs go with it and there is no undo from
    Python.

    Refuses to guess when the selector is ambiguous.
    """
    security.enforce_tier("delete_material_expression", confirm=confirm)
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'deleted': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        + indent_block(_resolve_snippet(expression), 4) +
        f"    _before = mel.get_num_material_expressions(m)\n"
        f"    _removed = None\n"
        f"    if _target is not None:\n"
        f"        _removed = (str(_target.get_editor_property('desc') or '')\n"
        f"                   or _target.get_class().get_name())\n"
        f"        mel.delete_material_expression(m, _target)\n"
        f"        mel.delete_material_expression(m, _target)\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    _after = mel.get_num_material_expressions(m)\n"
        f"    OUT = {{'found': _err is None, 'error': _err,\n"
        f"          'deleted': _target is not None,\n"
        f"          'removed': _removed,\n"
        f"          'expression_count_before': _before,\n"
        f"          'expression_count_after': _after,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if payload.get("error") and not payload.get("deleted"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("deleted")),
        "material_path": material_path,
        "expression": expression,
        "removed": payload.get("removed"),
        "expression_count_before": payload.get("expression_count_before"),
        "expression_count_after": payload.get("expression_count_after"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def delete_unused_material_expressions(
    material_path: str,
    confirm: bool = False,
    recompile: bool = True,
) -> dict:
    """
    Deletes every node that feeds nothing. Destructive: requires confirm=True.
    This is the cleanup after an experiment, and it is the only safe way to prune
    a graph, since it cannot remove a node that is actually in use.
    """
    security.enforce_tier("delete_unused_material_expressions", confirm=confirm)
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'removed': None}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _before = mel.get_num_material_expressions(m)\n"
        f"    mel.delete_unused_expressions(m)\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    _after = mel.get_num_material_expressions(m)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'expression_count_before': _before,\n"
        f"          'expression_count_after': _after,\n"
        f"          'removed': _before - _after,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": True,
        "material_path": material_path,
        "removed": payload.get("removed"),
        "expression_count_before": payload.get("expression_count_before"),
        "expression_count_after": payload.get("expression_count_after"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def layout_material_graph(material_path: str) -> dict:
    """
    Auto-arranges the graph into a readable left-to-right layout. Cheap, and the
    right thing to run after a scripted build, since node positions set by an
    import tool are all at the origin and produce an unreadable pile.

    Not a material change in any semantic sense, but it does dirty the asset, so
    the expression count is reported back for confirmation.
    """
    security.enforce_tier("layout_material_graph")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'count': None}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    mel.layout_material_expressions(m)\n"
        f"    nodes = list(mel.get_material_expressions(m))\n"
        f"    OUT = {{'found': True, 'error': None, 'count': len(nodes),\n"
        f"          'positions': [[list(mel.get_material_expression_node_position(e))[0],\n"
        f"                        list(mel.get_material_expression_node_position(e))[1]]\n"
        f"                       for e in nodes]}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "count": payload.get("count"),
        "positions": payload.get("positions"),
    }


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


def connect_material_expressions(
    material_path: str,
    from_expression: str,
    from_output: str,
    to_expression: str,
    to_input: str,
    recompile: bool = True,
) -> dict:
    """
    Wires one node's output pin into another node's input pin, replacing whatever
    was connected there.

    Pin names are case-sensitive and are *display* names, not property names: an
    Add node's first input is "A", a Multiply's inputs are "A" and "B", a
    TextureSample's are "Coordinates", "RGB", "R", "G", "B", "A", "Alpha". Use
    list_material_expressions to read the real names rather than guessing;
    an empty `from_output` ("") means the node's only output, which is what most
    single-output nodes use.

    Returns the recompile errors. A connection between mismatched types produces
    a shader error here and a material that renders black, not an exception.
    """
    security.enforce_tier("connect_material_expressions")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'connected': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _from = None\n"
        f"    _to = None\n"
        f"    _errs = []\n"
        + indent_block(_resolve_snippet(from_expression), 4) +
        f"    _from = _target\n"
        f"    _from_err = _err\n"
        + indent_block(_resolve_snippet(to_expression), 4) +
        f"    _to = _target\n"
        f"    _to_err = _err\n"
        f"    _connected = False\n"
        f"    if _from is None:\n"
        f"        _errs.append('from: ' + str(_from_err))\n"
        f"    if _to is None:\n"
        f"        _errs.append('to: ' + str(_to_err))\n"
        f"    if _from is not None and _to is not None:\n"
        f"        _outs = [str(o) for o in mel.get_material_expression_output_names(_from)]\n"
        # Output pin names ARE discoverable, and worth checking before calling:
        # get_material_expression_output_names returns '' / 'RGB' / 'R' / ...
        f"        if _outs and {from_output!r} not in _outs:\n"
        f"            _errs.append('no output pin ' + {from_output!r} + ' on ' + "
        f"{from_expression!r} + '; available: ' + repr(_outs))\n"
        # Input pin names are NOT discoverable in Python, so they cannot be
        # validated this way. get_inputs_for_material_expression returns the
        # expressions wired into each input, not the pin names, which is why an
        # earlier version reported a Multiply as having inputs ['None', 'None'].
        # The connect is attempted and then verified by read-back instead.
        f"        _pin_count = len(list(mel.get_inputs_for_material_expression(m, _to)))\n"
        f"        if not _errs:\n"
        f"            _connected = bool(mel.connect_material_expressions(\n"
        f"                _from, {from_output!r}, _to, {to_input!r}))\n"
        f"            if not _connected:\n"
        f"                _errs.append(\n"
        f"                    'connect_material_expressions returned False for input pin '\n"
        f"                    + {to_input!r} + ' on a node with ' + str(_pin_count)\n"
        f"                    + ' input(s); input pin names are not readable from'\n"
        f"                    + ' Python, so check the class reference for the usual'\n"
        f"                    + ' names (A/B for Add and Multiply, Coordinates/RGB/R/G/B/A'\n"
        f"                    + ' for a TextureSample)')\n"
        f"            else:\n"
        f"                _ins = list(mel.get_inputs_for_material_expression(m, _to))\n"
        f"                _wired = [i for i in _ins if i is not None]\n"
        f"                if not any(i == _from for i in _wired):\n"
        f"                    _errs.append(\n"
        f"                        'connect returned True but the source expression is not'\n"
        f"                        + ' present in the targets inputs afterwards')\n"
        f"                    _connected = False\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    OUT = {{'found': not _errs, 'error': '; '.join(_errs) if _errs else None,\n"
        f"          'connected': _connected,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("connected")),
        "material_path": material_path,
        "from_expression": from_expression,
        "from_output": from_output,
        "to_expression": to_expression,
        "to_input": to_input,
        "error": payload.get("error"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def connect_material_input(
    material_path: str,
    from_expression: str,
    from_output: str,
    material_input: str,
    recompile: bool = True,
) -> dict:
    """
    Wires a node's output straight to a material input: base_color, normal,
    roughness, metallic, emissive_color, opacity, and the rest of
    `MaterialProperty`.

    This is a different call from connect_material_expressions: the destination
    is a material input rather than a node pin, and there is no overload that
    accepts both. Wiring a result to `base_color` is what makes a material
    actually show anything.

    The domain matters: a material set to MD_UI has no base colour input to
    connect to, and this reports the pin list rather than failing obscurely.
    """
    security.enforce_tier("connect_material_input")
    security.check_destination_path(material_path)

    enum_name = MATERIAL_INPUTS.get(material_input)
    if enum_name is None:
        return {
            "success": False,
            "error": f"Unknown material input {material_input!r}. Known: "
                     f"{', '.join(sorted(MATERIAL_INPUTS))}.",
        }

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'connected': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _err = None\n"
        f"    _connected = False\n"
        + indent_block(_resolve_snippet(from_expression), 4) +
        f"    if _target is None:\n"
        f"        _err = str(_err)\n"
        f"    else:\n"
        f"        _outs = [str(o) for o in mel.get_material_expression_output_names(_target)]\n"
        f"        if _outs and {from_output!r} not in _outs:\n"
        f"            _err = ('no output pin ' + {from_output!r} + '; available: ' + repr(_outs))\n"
        f"        else:\n"
        f"            _prop = getattr({UNREAL}.MaterialProperty, {enum_name!r})\n"
        f"            _connected = bool(mel.connect_material_property(\n"
        f"                _target, {from_output!r}, _prop))\n"
        f"            if not _connected:\n"
        f"                _err = ('connect_material_property returned False; the material'\n"
        f"                       ' domain may not expose this input')\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    OUT = {{'found': _err is None, 'error': _err, 'connected': _connected,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("connected")),
        "material_path": material_path,
        "from_expression": from_expression,
        "from_output": from_output,
        "material_input": material_input,
        "material_property": enum_name,
        "error": payload.get("error"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def disconnect_material_input(
    material_path: str,
    to_expression: str,
    to_input: str,
    confirm: bool = False,
    recompile: bool = True,
) -> dict:
    """
    Unwires one input pin of a node. The node stays, and anything it fed becomes
    unconnected, so this is how you isolate a branch before rewiring it.

    Destructive in the sense that the link is not recoverable from Python;
    requires confirm=True.
    """
    security.enforce_tier("disconnect_material_input", confirm=confirm)
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'disconnected': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _disconnected = False\n"
        + indent_block(_resolve_snippet(to_expression), 4) +
        f"    if _target is not None:\n"
        f"        _ins = [str(i) for i in mel.get_inputs_for_material_expression(m, _target)]\n"
        f"        if {to_input!r} not in _ins:\n"
        f"            _err = ('no input pin ' + {to_input!r} + '; available: ' + repr(_ins))\n"
        f"        else:\n"
        f"            _disconnected = bool(mel.disconnect_material_expressions(\n"
        f"                _target, {to_input!r}))\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    OUT = {{'found': _err is None, 'error': _err, 'disconnected': _disconnected,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("disconnected")),
        "material_path": material_path,
        "to_expression": to_expression,
        "to_input": to_input,
        "error": payload.get("error"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


# ---------------------------------------------------------------------------
# Node properties
# ---------------------------------------------------------------------------


def _value_snippet(value, value_type: str) -> str:
    """
    Source for a Python literal coerced to the editor type Unreal wants.

    Unreal's property setters reject most Python types outright, and the right
    type depends on the property rather than the value, so this guesses from the
    shape of the value and lets an explicit value_type override. A 3-list is
    genuinely ambiguous between Vector and LinearColor and resolves to Vector,
    which is right far more often than not; pass value_type when it is not.
    """
    if value_type in ("auto", "", None):
        if isinstance(value, bool):
            value_type = "bool"
        elif isinstance(value, int):
            value_type = "int"
        elif isinstance(value, float):
            value_type = "float"
        elif isinstance(value, str):
            value_type = "str"
        elif isinstance(value, list) and len(value) == 3:
            value_type = "vector"
        elif isinstance(value, list) and len(value) == 4:
            value_type = "linear_color"
        else:
            value_type = "str"

    constructors = {
        # repr(True) is "True", already valid source, so no wrapper is needed.
        "bool": lambda v: repr(bool(v)),
        "int": lambda v: f"int({v!r})",
        "float": lambda v: f"float({v!r})",
        "str": lambda v: repr(str(v)),
        "name": lambda v: f"{UNREAL}.Name({str(v)!r})",
        "vector": lambda v: f"{UNREAL}.Vector({v[0]!r}, {v[1]!r}, {v[2]!r})",
        "linear_color": lambda v: (
            f"{UNREAL}.LinearColor({v[0]!r}, {v[1]!r}, {v[2]!r}, {v[3]!r})"
        ),
        "color": lambda v: f"{UNREAL}.Color({v[0]!r}, {v[1]!r}, {v[2]!r}, {v[3]!r})",
    }
    if value_type in ("texture", "texture2d"):
        return f"{UNREAL}.load_asset({value!r})"
    if value_type not in constructors:
        raise ValueError(
            f"Unknown value_type {value_type!r}. Use one of: "
            f"auto, bool, int, float, str, name, vector, linear_color, color, texture."
        )
    return constructors[value_type](value)


def set_material_expression_property(
    material_path: str,
    expression: str,
    property_name: str,
    value,
    value_type: str = "auto",
    recompile: bool = True,
) -> dict:
    """
    Sets one editor property on a node: a Constant's `r`, a TextureSample's
    `texture`, a parameter node's `parameter_name` or `group`.

    value_type is "auto" by default, which infers from the Python value. A
    3-element list becomes a Vector and a 4-element list a LinearColor; pass
    value_type explicitly when the property wants a Color instead of a
    LinearColor, or a Name instead of a str.

    Returns the recompile errors, since setting a property is the step most
    likely to introduce a type mismatch that still "compiles".
    """
    security.enforce_tier("set_material_expression_property")
    security.check_destination_path(material_path)

    try:
        literal = _value_snippet(value, value_type)
    except (ValueError, IndexError, TypeError) as exc:
        return {"success": False, "error": str(exc)}

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'set': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _set = False\n"
        + indent_block(_resolve_snippet(expression), 4) +
        f"    if _target is not None:\n"
        f"        _target.set_editor_property({property_name!r}, {literal})\n"
        f"        _set = True\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    OUT = {{'found': _err is None, 'error': _err, 'set': _set,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("set")),
        "material_path": material_path,
        "expression": expression,
        "property_name": property_name,
        "error": payload.get("error"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def get_material_expression_property(
    material_path: str,
    expression: str,
    property_name: str,
) -> dict:
    """
    Reads one editor property off a node, and reports it as a plain value rather
    than an opaque repr.

    Use this after setting something, rather than trusting the write: an unknown
    property name raises inside the snippet and comes back as an error, not as a
    sentinel, and a *known* property can still hold something other than what you
    wrote.
    """
    security.enforce_tier("get_material_expression_property")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'value': None}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _value = None\n"
        f"    _repr = None\n"
        + indent_block(_resolve_snippet(expression), 4) +
        f"    if _target is not None:\n"
        f"        _raw = _target.get_editor_property({property_name!r})\n"
        f"        _repr = str(_raw)\n"
        f"        if isinstance(_raw, (bool, int, float, str)):\n"
        f"            _value = _raw\n"
        f"        elif hasattr(_raw, 'x') and hasattr(_raw, 'y'):\n"
        f"            _value = {{'x': _raw.x, 'y': _raw.y,\n"
        f"                      'z': getattr(_raw, 'z', None),\n"
        f"                      'w': getattr(_raw, 'w', None),\n"
        f"                      'r': getattr(_raw, 'r', None),\n"
        f"                      'g': getattr(_raw, 'g', None),\n"
        f"                      'b': getattr(_raw, 'b', None),\n"
        f"                      'a': getattr(_raw, 'a', None)}}\n"
        f"        else:\n"
        f"            _value = _repr\n"
        f"    OUT = {{'found': _err is None, 'error': _err,\n"
        f"          'value': _value, 'repr': _repr}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": True,
        "material_path": material_path,
        "expression": expression,
        "property_name": property_name,
        "value": payload.get("value"),
    }


def recompile_material_graph(material_path: str) -> dict:
    """
    Forces a recompile and returns the shader errors.

    `MaterialEditingLibrary.recompile_material` returns its errors rather than
    raising, so a material can fail to compile and every other tool still reports
    success. This exists to make that checkable on demand. An empty list means it
    compiled clean.
    """
    # Always compiles: that is the entire purpose of this tool. The shared
    # _compile_errors helper takes a flag so batch builds can skip it, but this
    # one has no reason to.
    recompile = True
    security.enforce_tier("recompile_material_graph")
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'recompile_errors': [], 'compiled_clean': False}}\n"
        f"else:\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    OUT = {{'found': True, 'error': None,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "material_path": material_path,
        "error": payload.get("error"),
        "recompile_errors": payload.get("recompile_errors") or [],
        "compiled_clean": bool(payload.get("compiled_clean")),
    }


# ---------------------------------------------------------------------------
# Parameter nodes
# ---------------------------------------------------------------------------


def create_material_parameter(
    material_path: str,
    parameter_type: str,
    parameter_name: str,
    default_value=None,
    group: str = "",
    node_x: int = 0,
    node_y: int = 0,
    sort_priority: int = 0,
    recompile: bool = True,
) -> dict:
    """
    Creates a parameter node *and* gives it a name, which is the step that is easy
    to miss: a ScalarParameter with an empty `parameter_name` is not a parameter
    at all, it is an anonymous constant as far as every instance and every
    `get_*_parameter_names` call is concerned. Nothing downstream would show it.

    parameter_type is "scalar", "vector", "texture" or "static_switch". The
    default is applied to the property that class actually uses: `r` for a scalar
    (note: `r`, not `value` or `const_r`, none of which exist),
    `default_value` for a vector, `texture` for a texture.

    group is the parameter's grouping in the instance editor, which is how a
    material with thirty parameters stays usable.
    """
    security.enforce_tier("create_material_parameter")
    security.check_destination_path(material_path)

    cls = PARAMETER_CLASSES.get(parameter_type)
    if cls is None:
        return {
            "success": False,
            "error": f"Unknown parameter_type {parameter_type!r}. Choose one of: "
                     f"{', '.join(sorted(PARAMETER_CLASSES))}.",
        }

    if default_value is None:
        default_lines = ""
    elif parameter_type == "scalar":
        default_lines = (
            f"    _expr.set_editor_property("
            f"'default_value', float({float(default_value)!r}))\n"
        )
    elif parameter_type == "vector":
        v = [float(x) for x in default_value]
        if len(v) == 3:
            # A VectorParameter's default_value is a LinearColor, not a Vector,
            # so a 3-number default has to be widened with alpha or the editor
            # rejects it: "Cannot nativize 'Vector' as 'LinearColor'". RGB order
            # is the same; LinearColor is RGBA.
            v = v + [1.0]
        elif len(v) != 4:
            return {"success": False, "error": "A vector default needs 3 or 4 numbers."}
        lit = _value_snippet(v, "linear_color")
        default_lines = f"    _expr.set_editor_property('default_value', {lit})\n"
    elif parameter_type == "texture":
        default_lines = (
            f"    _expr.set_editor_property("
            f"'texture', {UNREAL}.load_asset({str(default_value)!r}))\n"
        )
    else:
        default_lines = (
            f"    _expr.set_editor_property("
            f"'default_value', bool({bool(default_value)!r}))\n"
        )

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'selector': None}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _expr = mel.create_material_expression(\n"
        f"        m, {UNREAL}.{cls}, {int(node_x)}, {int(node_y)})\n"
        f"    _expr.set_editor_property('parameter_name', {UNREAL}.Name({parameter_name!r}))\n"
        f"    _expr.set_editor_property('group', {UNREAL}.Name({group!r}))\n"
        f"    _expr.set_editor_property('desc', {parameter_name!r})\n"
        f"    _expr.set_editor_property('sort_priority', {int(sort_priority)})\n"
        + default_lines.replace("    ", "    ", 1) +
        f"    _pos = list(mel.get_material_expression_node_position(_expr))\n"
        f"    _sel = (str(_expr.get_editor_property('parameter_name')) + '@'\n"
        f"           + str(_pos[0]) + ',' + str(_pos[1]))\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    _sc = [str(n) for n in mel.get_scalar_parameter_names(m)]\n"
        f"    _vc = [str(n) for n in mel.get_vector_parameter_names(m)]\n"
        f"    _tc = [str(n) for n in mel.get_texture_parameter_names(m)]\n"
        f"    OUT = {{'found': True, 'error': None, 'selector': _sel,\n"
        f"          'parameter_name': str(_expr.get_editor_property('parameter_name')),\n"
        f"          'group': str(_expr.get_editor_property('group')),\n"
        f"          'class': _expr.get_class().get_name(),\n"
        f"          'x': _pos[0], 'y': _pos[1],\n"
        f"          'scalar_names': _sc, 'vector_names': _vc,\n"
        f"          'texture_names': _tc,\n"
        + _recompiled_result() +
        f"          }}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": True,
        "material_path": material_path,
        "parameter_type": parameter_type,
        "parameter_name": payload.get("parameter_name"),
        "group": payload.get("group"),
        "selector": payload.get("selector"),
        "expression_class": payload.get("class"),
        "position": [payload.get("x"), payload.get("y")],
        "scalar_names": payload.get("scalar_names"),
        "vector_names": payload.get("vector_names"),
        "texture_names": payload.get("texture_names"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }


def set_material_static_switch_parameter(
    material_path: str,
    parameter_name: str,
    default_value: bool,
    confirm: bool = False,
) -> dict:
    """
    Sets the *default* value of a static switch parameter on a base material.

    Distinct from the instance-level switch, which already exists: this changes
    what every instance inherits unless it overrides the switch itself. A static
    switch is a compile-time branch, so flipping one recompiles every instance
    that inherits it.

    There is **no** `MaterialEditingLibrary.set_material_default_static_switch_
    parameter_value`: the getter exists, the setter does not. The base-material
    default lives on the `MaterialExpressionStaticSwitchParameter` node's own
    `default_value`, so this finds the node by parameter name and sets it there,
    which is the only route through the API. The read-back uses the real getter,
    so the reported value comes from the editor and not from what was written.

    Requires confirm=True.
    """
    # Always recompiles: flipping a static switch changes the generated shader for
    # every instance that inherits it, so the result must not go unverified.
    recompile = True
    security.enforce_tier("set_material_static_switch_parameter", confirm=confirm)
    security.check_destination_path(material_path)

    body = (
        f"m = {load_asset(material_path)}\n"
        f"if m is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},"
        f" 'set': False}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _existing = [str(n) for n in mel.get_static_switch_parameter_names(m)]\n"
        f"    _known = {parameter_name!r} in _existing\n"
        f"    _node = None\n"
        f"    if _known:\n"
        f"        for _e in mel.get_material_expressions(m):\n"
        f"            if _e.get_class().get_name() != 'MaterialExpressionStaticSwitchParameter':\n"
        f"                continue\n"
        f"            _pn = str(_e.get_editor_property('parameter_name')).replace(' ', '')\n"
        f"            if _pn == {parameter_name.replace(' ', '')!r}:\n"
        f"                _node = _e\n"
        f"                break\n"
        f"    _before = None\n"
        f"    if _known:\n"
        f"        _before = mel.get_material_default_static_switch_parameter_value(\n"
        f"            m, {parameter_name!r})\n"
        f"    _set = False\n"
        f"    if _node is not None:\n"
        f"        _node.set_editor_property('default_value', bool({bool(default_value)!r}))\n"
        f"        _set = True\n"
        + _compile_errors(material_path, recompile, "    ") +
        f"    _after = None\n"
        f"    if _known:\n"
        f"        _after = mel.get_material_default_static_switch_parameter_value(\n"
        f"            m, {parameter_name!r})\n"
        f"    _now = [str(n) for n in mel.get_static_switch_parameter_names(m)]\n"
        f"    _ok = _known and _node is not None and bool(_set)\n"
        f"    if _ok:\n"
        f"        _err = None\n"
        f"    elif not _known:\n"
        f"        _err = ('no static switch parameter named ' + {parameter_name!r}\n"
        f"                + '; this material has: ' + repr(_existing))\n"
        f"    else:\n"
        f"        _err = ('no MaterialExpressionStaticSwitchParameter node carries the'\n"
        f"                + ' name ' + {parameter_name!r})\n"
        f"    OUT = {{'found': _ok, 'error': _err, 'set': bool(_set),\n"
        f"          'switches': _now,\n"
        f"          'value_before': str(_before) if _before is not None else None,\n"
        f"          'value_after': str(_after) if _after is not None else None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("set")),
        "material_path": material_path,
        "parameter_name": parameter_name,
        "default_value": bool(default_value),
        "switches": payload.get("switches"),
        "recompile_errors": payload.get("recompile_errors"),
        "compiled_clean": payload.get("compiled_clean"),
    }