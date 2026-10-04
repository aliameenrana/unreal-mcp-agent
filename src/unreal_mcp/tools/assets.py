"""
General asset management.

`EditorAssetLibrary.duplicate_asset` is worth knowing about before writing
anything else here: it returns None even when it succeeds, so a tool built on
its return value reports failure on success. Existence has to be checked with
`does_asset_exist` instead. `delete_asset` does return a bool.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, json_dumps


def asset_exists(asset_path: str) -> dict:
    """
    Whether an asset exists at `asset_path`. Use this to confirm the result of
    any call whose return value is unreliable, such as duplicate_asset.
    """
    security.enforce_tier("asset_exists")
    body = (
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'exists': bool({UNREAL}.EditorAssetLibrary.does_asset_exist({asset_path!r}))}}\n"
    )
    return {"success": True, "asset_path": asset_path, **get_bridge().run_python(guarded(body))}


def delete_asset(asset_path: str, confirm: bool = False) -> dict:
    """
    Deletes the asset at `asset_path` from the project. Destructive: this is not
    undoable from Python and is not recoverable from source control if the
    asset was never committed, so it requires confirm=True.

    Deleting an asset other actors in the level reference leaves those
    references dangling; there is no dependency check here.
    """
    security.enforce_tier("delete_asset", confirm=confirm)
    body = (
        f"existed = bool({UNREAL}.EditorAssetLibrary.does_asset_exist({asset_path!r}))\n"
        f"OUT = {{'found': existed,\n"
        f"      'error': None if existed else 'No asset at ' + {asset_path!r},\n"
        f"      'deleted': {UNREAL}.EditorAssetLibrary.delete_asset({asset_path!r})\n"
        f"         if existed else False,\n"
        f"      'still_exists': bool({UNREAL}.EditorAssetLibrary.does_asset_exist({asset_path!r}))}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "asset_path": asset_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("deleted")) and not payload.get("still_exists"),
        "asset_path": asset_path,
        "deleted": payload.get("deleted"),
        "still_exists": payload.get("still_exists"),
    }

def save_asset(asset_path: str) -> dict:
    """
    Saves an asset to disk so the asset registry knows about it.

    Not obvious, and it matters for reverse lookups: setting a material
    instance's parent only changes it in memory, and
    `MaterialEditingLibrary.get_child_instances` walks the *registry*. A freshly
    created, unsaved instance therefore has a parent but is not reported as a
    child of anything. Save before asking who depends on what.

    Returns the object's short package name, which is not a useful identifier,
    so this reports success rather than pretending to return a path.
    """
    security.enforce_tier("save_asset")
    security.check_destination_path(asset_path)

    body = (
        f"a = {UNREAL}.load_asset({asset_path!r})\n"
        f"if a is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {asset_path!r},"
        f" 'saved': False}}\n"
        f"else:\n"
        f"    ok = {UNREAL}.EditorAssetLibrary.save_asset({asset_path!r}, only_if_is_dirty=True)\n"
        f"    OUT = {{'found': True, 'error': None, 'saved': bool(ok)}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=120.0)
    if not payload.get("found"):
        return {"success": False, "asset_path": asset_path, "error": payload.get("error")}
    return {"success": bool(payload.get("saved")), "asset_path": asset_path}
