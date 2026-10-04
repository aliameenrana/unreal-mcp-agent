"""
Small helpers for building the single-expression Python snippets we send to
Unreal. Every named tool sends one expression, evaluated with
MODE_EVAL_STATEMENT, that ends up wrapped in json.dumps(...) so bridge.py can
always parse the result as JSON regardless of the underlying type.

`seq()` exists because EVAL_STATEMENT can only run one expression, not a
multi-statement script, but we often need "do a thing, then return a
confirmation value." A Python tuple evaluates each element left to right, so
`(a, b, c)[-1]` runs a, b, and c in order and yields c. That's the trick.
"""

from __future__ import annotations

UNREAL = "__import__('unreal')"


def seq(*expressions: str) -> str:
    """Evaluates each expression in order, returns the value of the last one."""
    if len(expressions) == 1:
        return expressions[0]
    return "(" + ", ".join(expressions) + ")[-1]"


def json_dumps(expr: str) -> str:
    return f"__import__('json').dumps({expr})"


def actor_subsystem() -> str:
    return f"{UNREAL}.get_editor_subsystem({UNREAL}.EditorActorSubsystem)"


def find_actor(predicate: str, optional: bool = False) -> str:
    """
    Actor expression for the first level actor matching `predicate`, which is
    source text for the condition, e.g. "a.get_name() == 'Cube_0'". With
    optional=True a missing actor yields None instead of raising StopIteration,
    so a tool can report it as {"success": False, ...} rather than surfacing a
    generator exception.
    """
    genexp = f"(a for a in {actor_subsystem()}.get_all_level_actors() if {predicate})"
    return f"next({genexp}, None)" if optional else f"next({genexp})"


def find_actor_by_name(actor_name: str, optional: bool = False) -> str:
    return find_actor(f"a.get_name() == {actor_name!r}", optional)


def find_actor_by_class(class_name: str) -> str:
    """
    The level's single instance of a class, used for the lighting singletons
    (DirectionalLight, SkyLight, SkyAtmosphere, ExponentialHeightFog) whose
    object names carry a UAID suffix and can't be spelled ahead of time.
    """
    return find_actor(f"a.get_class().get_name() == {class_name!r}")


def missing_actor_message(actor_name: str) -> str:
    """Source for an expression yielding the standard missing-actor message."""
    return f"'No actor named ' + {actor_name!r} + ' in the current level'"


def rotator(pitch: float, yaw: float, roll: float) -> str:
    """
    Rotator's positional constructor is (roll, pitch, yaw), not the
    (pitch, yaw, roll) order tools here document, and a positional build tilts
    the actor instead of turning it without raising. Same trap as
    unreal.Color's (b, g, r, a).
    """
    return (
        f"{UNREAL}.Rotator(pitch={float(pitch)!r}, "
        f"yaw={float(yaw)!r}, roll={float(roll)!r})"
    )


def vector(x: float, y: float, z: float) -> str:
    return f"{UNREAL}.Vector(x={float(x)!r}, y={float(y)!r}, z={float(z)!r})"


def asset_tools() -> str:
    return f"{UNREAL}.AssetToolsHelpers.get_asset_tools()"


def load_asset(asset_path: str) -> str:
    return f"{UNREAL}.load_asset({asset_path!r})"


def rotator(pitch: float, yaw: float, roll: float) -> str:
    """
    Rotator's positional constructor is (roll, pitch, yaw), not the
    (pitch, yaw, roll) order tools here document, and a positional build tilts
    the actor instead of turning it without raising. Same trap as
    unreal.Color's (b, g, r, a).
    """
    return (
        f"{UNREAL}.Rotator(pitch={float(pitch)!r}, "
        f"yaw={float(yaw)!r}, roll={float(roll)!r})"
    )


def vector(x: float, y: float, z: float) -> str:
    return f"{UNREAL}.Vector(x={float(x)!r}, y={float(y)!r}, z={float(z)!r})"


def actor_component(actor_expr: str, component_class: str) -> str:
    """
    The named component class off an already-resolved actor expression.
    actor_expr is parenthesized because an unparenthesized
    `next(a for a in ... if cond).method()` binds `.method()` into the
    comprehension's condition rather than onto next()'s result, and still
    parses.
    """
    return f"({actor_expr}).get_component_by_class({UNREAL}.{component_class})"


def actors_with_component(component_class: str) -> str:
    """Every level actor carrying the named component class."""
    return (
        f"[a for a in {actor_subsystem()}.get_all_level_actors() "
        f"if a.get_component_by_class({UNREAL}.{component_class})]"
    )


# One ternary per type, most specific first. A UObject is identified by
# get_name() and an enum by .name/.value; checking UObject first stops an
# object carrying a .name attribute from being read as an enum.
#
# unreal.Array is in the sequence check because it is a MutableSequence, not a
# list: testing (list, tuple) alone misses every Array[X] property and falls
# through to str(), reporting a repr as the value.
_JSONABLE_BODY = """
    v if v is None or isinstance(v, (bool, int, float, str)) else
    {'class': v.get_class().get_name(), 'name': v.get_name()}
        if depth and hasattr(v, 'get_name') else
    v.name if hasattr(v, 'name') and hasattr(v, 'value') else
    [v.x, v.y, v.z] if isinstance(v, UNREAL.Vector) else
    {'pitch': v.pitch, 'yaw': v.yaw, 'roll': v.roll}
        if isinstance(v, UNREAL.Rotator) else
    {'r': v.r, 'g': v.g, 'b': v.b, 'a': v.a}
        if isinstance(v, (UNREAL.Color, UNREAL.LinearColor)) else
    [self(self, depth - 1, i) for i in v]
        if depth and isinstance(v, (list, tuple, UNREAL.Array)) else
    str(v)
"""


def jsonable(value_expr: str, max_depth: int = 6) -> str:
    """
    Wraps an expression so its Unreal value is converted into something
    json.dumps can encode. Necessary because almost nothing Unreal returns is
    JSON-serializable: json.dumps on a Vector raises TypeError, and an enum
    stringifies to "<NetDormancy.DORM_AWAKE: 1>" rather than the name.

    Enums come back as their name, Vector as [x, y, z], Rotator and the color
    types as named-field dicts, UObjects as {class, name}, and anything
    unrecognized as str() once max_depth is exhausted, so a cyclic property
    graph can't recurse forever.
    """
    body = _JSONABLE_BODY.replace("UNREAL", UNREAL)
    return f"(lambda f: f(f, {max_depth!r}, {value_expr}))(lambda self, depth, v:{body})"


def guarded(body: str) -> str:
    """
    Runs `body` (Python source lines) on the Unreal side inside a try/except
    and returns an expression evaluating to a JSON string of `OUT`, with any
    exception turned into {found: False, error: <type and message>}.
    `body` must assign a dict to OUT.

    Needed because several Unreal calls raise on bad input rather than
    returning a sentinel, and bridge.run_python reports a raise as a
    RemoteCommandFailedError with an empty LogOutput, so the reason would
    never reach the caller. A single EvaluateStatement can't express
    try/except, hence the exec().

    Guarding on dir(a) membership instead would be cheaper but wrong: 'layers'
    and 'replicate_movement' are both documented Actor properties that dir()
    omits, while still being readable through get_editor_property.
    """
    indented = "\n".join(f"    {line}" for line in body.splitlines())
    src = (
        "import unreal\n"
        "OUT = None\n"
        "try:\n"
        f"{indented}\n"
        "except Exception as _e:\n"
        "    _m = str(_e)\n"
        # StopIteration, which is how a missing actor surfaces from next(),
        # stringifies to the empty string; always prefix the type so the
        # caller's error field is never blank.
        "    OUT = {'found': False, 'value': None, 'type': None,"
        " 'error': type(_e).__name__ + (': ' + _m if _m else '')}"
    )
    return f"(lambda g: (exec({src!r}, g), __import__('json').dumps(g['OUT']))[-1])({{}})"
