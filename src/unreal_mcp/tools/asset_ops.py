"""
Asset management: listing, renaming, moving, duplicating, and blast-radius
queries.

`delete_asset` already existed in `assets.py` and is not redefined here. What
this module adds is everything you want *before* a destructive call, plus the
rename/move/duplicate trio that PLAN.md files under "Asset management".

Two API facts worth knowing, both found by reading the 5.8 stub:

- `EditorAssetLibrary.list_assets` returns **strings** (`'/Game/Folder/Name'`),
  not asset objects. Calling `get_editor_property` on a result element raises
  `AttributeError`, and `guarded()` turns that into a normal error result, so a
  loop written that way silently does nothing while appearing to run. Split on
  `.` for the asset name.
- There is no `fix_up_redirectors` in Python at all: neither
  `RedirectorFixer` nor `fixup_redirectors` appears anywhere in the stub. The
  redirector cleanup PLAN.md asks for is not buildable, and it matters after
  rename/move, so a rename here can leave a redirector behind.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def list_assets_in_path(
    path: str = "/Game",
    recursive: bool = True,
    include_subdirectories: bool = True,
) -> dict:
    """
    Lists asset paths under `path`.

    Returns the full `/Game/Folder/Name` strings as the engine reports them,
    plus the bare asset names, because both get asked for and the difference is
    easy to trip over.
    """
    security.enforce_tier("list_assets_in_path")
    body = (
        f"raw = {UNREAL}.EditorAssetLibrary.list_assets(\n"
        f"    {path!r}, {bool(recursive)!r}, {bool(include_subdirectories)!r})\n"
        f"rows = []\n"
        f"for p in raw:\n"
        f"    s = str(p)\n"
        f"    rows.append({{'path': s,\n"
        f"                 'name': s.rsplit('.', 1)[-1],\n"
        f"                 'folder': s.rsplit('/', 1)[0] if '/' in s else ''}})\n"
        f"OUT = {{'found': True, 'path': {path!r},\n"
        f"      'count': len(rows), 'assets': rows}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": True,
        "path": path,
        "count": payload.get("count", 0),
        "assets": payload.get("assets", []),
    }


def _asset_exists_expr(asset_path: str) -> str:
    return f"bool({UNREAL}.EditorAssetLibrary.does_asset_exist({asset_path!r}))"


def rename_asset(asset_path: str, new_name: str, confirm: bool = False) -> dict:
    """
    Renames an asset in place, keeping its folder. Destructive: needs
    confirm=True, because the old path stops resolving immediately and any
    reference to it is not updated here.

    Returns the new path only when the asset is confirmed present afterwards,
    since `rename_asset` reports success without always having moved anything.
    """
    security.enforce_tier("rename_asset", confirm=confirm)
    body = (
        f"existed = {_asset_exists_expr(asset_path)}\n"
        f"OUT = {{'found': existed, 'error': None if existed else 'No asset at ' + {asset_path!r},\n"
        f"      'renamed': False, 'new_path': None, 'still_at_old_path': None}}\n"
        f"if existed:\n"
        f"    _ok = {UNREAL}.EditorAssetLibrary.rename_asset({asset_path!r}, {new_name!r})\n"
        f"    _folder = {asset_path!r}.rsplit('/', 1)[0]\n"
        f"    _new = _folder + '/' + {new_name!r}\n"
        f"    OUT['renamed'] = bool(_ok)\n"
        f"    OUT['new_path'] = _new\n"
        f"    OUT['still_at_old_path'] = {_asset_exists_expr(asset_path)}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "asset_path": asset_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("renamed")) and not payload.get("still_at_old_path"),
        "asset_path": asset_path,
        "new_path": payload.get("new_path"),
        "renamed": payload.get("renamed"),
        "still_at_old_path": payload.get("still_at_old_path"),
    }


def move_asset(asset_path: str, destination_path: str, confirm: bool = False) -> dict:
    """
    Moves an asset to a new `/Game/` folder, keeping its name. Destructive:
    needs confirm=True.

    The engine's move is a rename to a new full path, so the destination here is
    a package path including the asset name, not just a folder.
    """
    security.enforce_tier("move_asset", confirm=confirm)
    body = (
        f"existed = {_asset_exists_expr(asset_path)}\n"
        f"OUT = {{'found': existed, 'error': None if existed else 'No asset at ' + {asset_path!r},\n"
        f"      'moved': False, 'still_at_old_path': None}}\n"
        f"if existed:\n"
        f"    _ok = {UNREAL}.EditorAssetLibrary.rename_asset({asset_path!r}, {destination_path!r})\n"
        f"    OUT['moved'] = bool(_ok)\n"
        f"    OUT['still_at_old_path'] = {_asset_exists_expr(asset_path)}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "asset_path": asset_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("moved")) and not payload.get("still_at_old_path"),
        "asset_path": asset_path,
        "destination_path": destination_path,
        "moved": payload.get("moved"),
        "still_at_old_path": payload.get("still_at_old_path"),
    }


def duplicate_asset(
    asset_path: str, destination_path: str, confirm: bool = False
) -> dict:
    """
    Duplicates an asset to `destination_path`. Destructive: needs confirm=True,
    because it will silently overwrite an existing asset there.

    `EditorAssetLibrary.duplicate_asset` returns None even on success, which is
    why `assets.py` documents it as unusable by return value. Existence of the
    destination is the real check, and that is what `success` reflects here.
    """
    security.enforce_tier("duplicate_asset", confirm=confirm)
    body = (
        f"existed = {_asset_exists_expr(asset_path)}\n"
        f"OUT = {{'found': existed, 'error': None if existed else 'No asset at ' + {asset_path!r},\n"
        f"      'destination_exists': False, 'overwrote': False}}\n"
        f"if existed:\n"
        f"    _before = {_asset_exists_expr(destination_path)}\n"
        f"    {UNREAL}.EditorAssetLibrary.duplicate_asset({asset_path!r}, {destination_path!r})\n"
        f"    OUT['overwrote'] = _before\n"
        f"    OUT['destination_exists'] = {_asset_exists_expr(destination_path)}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "asset_path": asset_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("destination_exists")),
        "asset_path": asset_path,
        "destination_path": destination_path,
        "destination_exists": payload.get("destination_exists"),
        "overwrote": payload.get("overwrote"),
    }


def _dependency_body(asset_path: str, method: str) -> str:
    return (
        f"reg = {UNREAL}.AssetRegistryHelpers.get_asset_registry()\n"
        f"_pkg = {asset_path!r}.split('.', 1)[0]\n"
        f"OUT = {{'found': False, 'package': _pkg, 'packages': []}}\n"
        f"if not {UNREAL}.EditorAssetLibrary.does_asset_exist({asset_path!r}):\n"
        f"    OUT['error'] = 'No asset at ' + {asset_path!r}\n"
        f"else:\n"
        f"    _opts = {UNREAL}.AssetRegistryDependencyOptions()\n"
        f"    _opts.set_editor_property('include_soft_package_references', True)\n"
        f"    _res = reg.{method}(_pkg, _opts)\n"
        f"    OUT['found'] = _res is not None\n"
        f"    OUT['packages'] = sorted([str(p) for p in _res]) if _res else []\n"
        f"    OUT['count'] = len(OUT['packages'])\n"
    )


def get_asset_referencers(asset_path: str) -> dict:
    """
    Lists the packages that reference `asset_path`.

    This is the blast radius to check before deleting or renaming: anything in
    here holds a reference that the operation will leave dangling. On-disk
    references only, so an in-memory-only reference will not appear.
    """
    security.enforce_tier("get_asset_referencers")
    payload = get_bridge().run_python(guarded(_dependency_body(asset_path, "get_referencers")))
    if not payload.get("found"):
        return {
            "success": False,
            "asset_path": asset_path,
            "error": payload.get("error", "asset not found"),
            "referencers": [],
            "count": 0,
        }
    return {
        "success": True,
        "asset_path": asset_path,
        "package": payload.get("package"),
        "referencers": payload.get("packages", []),
        "count": payload.get("count", 0),
    }


def get_asset_dependencies(asset_path: str) -> dict:
    """
    Lists the packages `asset_path` depends on: what it would break if those
    were removed, which is the opposite question from `get_asset_referencers`.
    """
    security.enforce_tier("get_asset_dependencies")
    payload = get_bridge().run_python(guarded(_dependency_body(asset_path, "get_dependencies")))
    if not payload.get("found"):
        return {
            "success": False,
            "asset_path": asset_path,
            "error": payload.get("error", "asset not found"),
            "dependencies": [],
            "count": 0,
        }
    return {
        "success": True,
        "asset_path": asset_path,
        "package": payload.get("package"),
        "dependencies": payload.get("packages", []),
        "count": payload.get("count", 0),
    }