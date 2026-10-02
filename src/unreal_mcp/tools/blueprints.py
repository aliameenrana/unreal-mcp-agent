"""
Blueprint compilation. This is the one call in the MVP set I'm least certain
of without live verification: unreal.BlueprintEditorLibrary.compile_blueprint
is documented in Epic's Python API reference, but exact behavior (return
value, what it does on failure) needs confirming against a real editor.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, json_dumps, load_asset, seq


def compile_blueprint(blueprint_path: str) -> dict:
    """
    Compiles a Blueprint asset given its path, e.g. '/Game/Blueprints/BP_Thing'.
    Returns success/failure based on whether the remote call raised, not
    (yet) on parsing Unreal's own compiler results log.
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
