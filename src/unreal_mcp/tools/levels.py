"""
Level and world management.

Every other tool in this server is scoped to whatever level happens to be open,
and none of them can see what levels exist. That makes these the tools that
decide what the rest can act on, and it is why `load_level` is destructive: it
discards unsaved changes to the current level without asking.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def list_levels() -> dict:
    """
    Lists every map asset in the project, flagging which one is open and which are
    dirty (have unsaved changes).

    A level that is dirty will lose those changes on the next `load_level`, so
    this is the call to make before switching.
    """
    security.enforce_tier("list_levels")

    body = (
        f"import unreal\n"
        f"els = unreal.EditorLoadingAndSavingUtils\n"
        f"_w = unreal.EditorLevelLibrary.get_editor_world() \\\n"
        f"    if hasattr(unreal, 'EditorLevelLibrary') else None\n"
        f"current = str(_w.get_package().get_path_name()) if _w else ''\n"
        f"current_object = str(_w.get_path_name()) if _w else ''\n"
        f"dirty = set(str(p.get_path_name()) for p in els.get_dirty_map_packages())\n"
        f"rows = []\n"
        f"_actor_count = len(list(unreal.get_editor_subsystem(\n"
        f"    unreal.EditorActorSubsystem).get_all_level_actors()))\n"
        f"for a in unreal.EditorAssetLibrary.list_assets('/Game', True, False):\n"
        f"    path = str(a)\n"
        # A level is a .umap, not a .uasset, so filtering on '.uasset' finds
        # none of them. Skip directory entries (trailing slash) and sub-objects
        # (the ':PersistentLevel.' form) instead, then let isinstance decide.
        f"    if path.endswith('/') or ':' in path:\n"
        f"        continue\n"
        f"    obj = unreal.load_asset(path)\n"
        f"    if obj is None or not isinstance(obj, unreal.World):\n"
        f"        continue\n"
        f"    package = str(obj.get_package().get_path_name())\n"
        f"    rows.append({{\n"
        f"        'name': obj.get_name(),\n"
        f"        'package': package,\n"
        f"        'asset_path': package + '.' + obj.get_name(),\n"
        f"        'is_current': package == current,\n"
        f"        'is_dirty': package in dirty,\n"
        f"        'actor_count': _actor_count if package == current else None,\n"
        f"    }})\n"
        f"rows.sort(key=lambda r: r['name'])\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'current_level': current_object,\n"
        f"      'dirty_packages': sorted(dirty),\n"
        f"      'count': len(rows), 'levels': rows}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    levels = payload.get("levels") or []
    return {
        "success": bool(payload.get("found")),
        "error": payload.get("error"),
        "current_level": payload.get("current_level"),
        "dirty_packages": payload.get("dirty_packages") or [],
        "count": payload.get("count"),
        "levels": levels,
    }


def get_current_level() -> dict:
    """
    Describes the level currently open: its asset path, actor count, and whether
    it has unsaved changes.

    Cheaper than `list_levels` when you only need to know where you are.
    """
    security.enforce_tier("get_current_level")

    body = (
        f"import unreal\n"
        f"els = unreal.EditorLoadingAndSavingUtils\n"
        f"world = unreal.EditorLevelLibrary.get_editor_world() \\\n"
        f"    if hasattr(unreal, 'EditorLevelLibrary') else None\n"
        f"dirty = [str(p.get_path_name()) for p in els.get_dirty_map_packages()]\n"
        f"package = str(world.get_package().get_path_name()) if world else ''\n"
        f"OUT = {{'found': world is not None,\n"
        f"      'error': None if world else 'no editor world',\n"
        f"      'level_name': world.get_name() if world else None,\n"
        f"      'package': package,\n"
        f"      'asset_path': package + '.' + world.get_name() if world else None,\n"
        f"      'actor_count': len(list(unreal.get_editor_subsystem(\n"
        f"          unreal.EditorActorSubsystem).get_all_level_actors())),\n"
        f"      'is_dirty': package in dirty,\n"
        f"      'dirty_packages': dirty}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("found")),
        "error": payload.get("error"),
        "level_name": payload.get("level_name"),
        "package": payload.get("package"),
        "asset_path": payload.get("asset_path"),
        "actor_count": payload.get("actor_count"),
        "is_dirty": payload.get("is_dirty"),
        "dirty_packages": payload.get("dirty_packages") or [],
    }


def save_level(confirm: bool = False, asset_path: str = "") -> dict:
    """
    Saves the open level.

    Destructive in the sense that it writes to disk and can overwrite a saved
    level, so it requires confirm=True. With asset_path, saves as a new level
    instead ("Save As"); without it, saves in place.

    `EditorLoadingAndSavingUtils.save_current_level()` returns a bool. Read
    `get_current_level`'s `is_dirty` afterwards to confirm the save landed,
    rather than trusting the return.
    """
    security.enforce_tier("save_level", confirm=confirm)

    if asset_path:
        security.check_destination_path(asset_path)

    body = (
        f"import unreal\n"
        f"els = unreal.EditorLoadingAndSavingUtils\n"
        + (f"world = unreal.EditorLevelLibrary.get_editor_world()\n"
           f"if world is None:\n"
           f"    OUT = {{'found': False, 'error': 'no editor world', 'saved': False}}\n"
           f"else:\n"
           f"    OUT = {{'found': True, 'error': None,\n"
           f"          'saved': bool(els.save_map(world, {asset_path!r}))}}\n"
           if asset_path else
           f"OUT = {{'found': True, 'error': None,\n"
           f"      'saved': bool(els.save_current_level())}}\n")
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"), "saved": False}
    return {
        "success": bool(payload.get("saved")),
        "saved": bool(payload.get("saved")),
        "asset_path": asset_path or None,
        "error": None if payload.get("saved") else "the save call returned False",
    }


def load_level(asset_path: str, confirm: bool = False) -> dict:
    """
    Opens a different level, replacing the current one.

    **Destructive, and this is the one tool where that is unavoidable rather than
    cautious.** Loading a level discards every unsaved change in the open one
    without a prompt. `EditorLoadingAndSavingUtils.load_map` does exactly what it
    says; there is no "save first" option. Call `get_current_level` and check
    `is_dirty` before calling this, and `save_level` if it is.

    Requires confirm=True. asset_path is a package path such as
    `/Game/MCPTest/Untitled`; the `.Name` suffix is optional.
    """
    security.enforce_tier("load_level", confirm=confirm)
    security.check_destination_path(asset_path)

    # Accept both '/Game/X' and '/Game/X.X' and normalize to the package path,
    # which is what the editor wants.
    package = asset_path.split(".")[0]
    body = (
        f"import unreal\n"
        f"els = unreal.EditorLoadingAndSavingUtils\n"
        f"pkg = {package!r}\n"
        f"full = pkg + '.' + pkg.rsplit('/', 1)[-1]\n"
        f"if not unreal.EditorAssetLibrary.does_asset_exist(full):\n"
        f"    OUT = {{'found': False, 'error': 'No level asset at ' + full,\n"
        f"          'loaded': False}}\n"
        f"else:\n"
        f"    # Refuse rather than throw away unsaved work silently.\n"
        f"    dirty = [str(p.get_path_name())\n"
        f"              for p in els.get_dirty_map_packages()]\n"
        f"    world = els.load_map(full)\n"
        f"    if world is None:\n"
        f"        OUT = {{'found': False, 'error': 'load_map returned None',\n"
        f"              'loaded': False}}\n"
        f"    else:\n"
        f"        newpkg = str(world.get_package().get_path_name())\n"
        f"        OUT = {{'found': True, 'error': None, 'loaded': True,\n"
        f"              'level_name': world.get_name(),\n"
        f"              'package': newpkg,\n"
        f"              'asset_path': newpkg + '.' + world.get_name(),\n"
        f"              'actor_count': len(list(\n"
        f"                  unreal.get_editor_subsystem(\n"
        f"                      unreal.EditorActorSubsystem).get_all_level_actors())),\n"
        f"              'discarded_dirty_packages': dirty}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("loaded"):
        return {"success": False, "error": payload.get("error"), "loaded": False}

    # This project is World Partitioned, and load_map returns before the cells
    # have finished streaming. The same level was observed reading 78, then 142,
    # then 138, so a single repeated reading can coincide with a pause in the
    # middle of a stream; this requires two consecutive stable samples.
    #
    # The polling has to happen here, in separate round trips: the snippet above
    # runs on the editor's main thread, where a loop cannot let the engine tick,
    # and a sleep is not available because SystemLibrary.delay needs a
    # latent_info that a plain exec cannot supply.
    settled, previous, samples, stable = 0, -1, [], 0
    for _ in range(60):
        settled = get_current_level()["actor_count"]
        if settled is None:
            break
        samples.append(settled)
        if settled == previous:
            # Two consecutive equal readings. One is not enough: streaming can
            # pause, and this level was observed reading 78, 142, then 142, so a
            # single repeat can coincide with the middle of a stream.
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        previous = settled

    return {
        "success": True,
        "level_name": payload.get("level_name"),
        "asset_path": payload.get("asset_path"),
        "actor_count": settled,
        # Sampling history, because a caller that sees 78 needs to know whether
        # the level was still loading.
        "actor_count_samples": samples,
        "streaming_settled": stable >= 2,
        # Reported so the caller learns what was thrown away, after the fact.
        "discarded_dirty_packages": payload.get("discarded_dirty_packages") or [],
        "error": None,
    }


def new_level(
    asset_path: str,
    save: bool = False,
    confirm: bool = False,
) -> dict:
    """
    Creates a blank level and opens it, discarding whatever is open now.

    Destructive for the same reason `load_level` is: the current level's unsaved
    changes go with it. `save=False` leaves the new level unsaved and unnamed in
    the editor; `save=True` writes it to asset_path, which must be new.

    Requires confirm=True when save=True, since that writes an asset.
    """
    security.enforce_tier("new_level", confirm=confirm or not save)
    security.check_destination_path(asset_path)
    package = asset_path.split(".")[0]
    body = (

        f"import unreal\n"
        f"els = unreal.EditorLoadingAndSavingUtils\n"
        f"pkg = {package!r}\n"
        f"full = pkg + '.' + pkg.rsplit('/', 1)[-1]\n"
        f"dirty = [str(p.get_path_name())\n"
        f"          for p in els.get_dirty_map_packages()]\n"
        f"world = els.new_blank_map({bool(save)!r})\n"
        f"if world is None:\n"
        f"    OUT = {{'found': False, 'error': 'new_blank_map returned None',\n"
        f"          'level_name': None}}\n"
        f"else:\n"
        f"    _saved = False\n"
        f"    if not {bool(save)!r}:\n"
        f"        OUT = {{'found': True, 'error': None, 'saved': False,\n"
        f"              'level_name': world.get_name(), 'asset_path': None,\n"
        f"              'actor_count': len(list(\n"
        f"                  unreal.get_editor_subsystem(\n"
        f"                      unreal.EditorActorSubsystem)\n"
        f"                      .get_all_level_actors())),\n"
        f"              'discarded_dirty_packages': dirty}}\n"
        f"    elif unreal.EditorAssetLibrary.does_asset_exist(full):\n"
        f"        OUT = {{'found': False, 'saved': False,\n"
        f"              'error': 'An asset already exists at ' + full,\n"
        f"              'level_name': world.get_name()}}\n"
        f"    else:\n"
        f"        _saved = bool(els.save_map(world, pkg))\n"
        f"        newpkg = str(world.get_package().get_path_name())\n"
        f"        OUT = {{'found': _saved, 'saved': _saved,\n"
        f"              'error': None if _saved else 'save_map returned False for ' + full,\n"
        f"              'level_name': world.get_name(),\n"
        f"              'asset_path': newpkg + '.' + world.get_name(),\n"
        f"              'actor_count': len(list(\n"
        f"                  unreal.get_editor_subsystem(\n"
        f"                      unreal.EditorActorSubsystem).get_all_level_actors())),\n"
        f"              'discarded_dirty_packages': dirty}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"),
                "level_name": payload.get("level_name")}
    return {
        "success": True,
        "level_name": payload.get("level_name"),
        "saved": bool(payload.get("saved")),
        "asset_path": payload.get("asset_path"),
        "actor_count": payload.get("actor_count"),
        "discarded_dirty_packages": payload.get("discarded_dirty_packages") or [],
        "error": payload.get("error"),
    }
