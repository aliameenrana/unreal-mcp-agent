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


def find_actor_by_name(actor_name: str) -> str:
    return (
        f"next(a for a in {actor_subsystem()}.get_all_level_actors() "
        f"if a.get_name() == {actor_name!r})"
    )


def asset_tools() -> str:
    return f"{UNREAL}.AssetToolsHelpers.get_asset_tools()"


def load_asset(asset_path: str) -> str:
    return f"{UNREAL}.load_asset({asset_path!r})"
