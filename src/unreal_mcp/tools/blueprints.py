"""
Blueprint compilation, wrapping unreal.BlueprintEditorLibrary.compile_blueprint.

Verified against a live Unreal Editor 5.8 session on a real Blueprint asset.
Note that success here means the compile call was dispatched and the editor
accepted it, not that the resulting graph compiled without errors; reading
that back is get_blueprint_compile_errors' job, which isn't built yet.
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
