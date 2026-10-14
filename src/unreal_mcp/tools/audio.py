"""
Audio: importing a wave, wrapping it in a cue, playing it, and attenuation.

The class name in PLAN.md is wrong. There is no `SoundWaveFactory` in UE 5.8;
the import factory is `SoundFactory` (AudioEditor module). Verified against the
5.8 Python stub, where `SoundWaveFactory` has zero occurrences and
`SoundFactory` is defined at SoundFactory.h.

`SoundCueFactoryNew` creates the cue but does not populate it -- a cue with no
nodes is silent. Wiring a wave into one is `SoundNodeWavePlayer.set_sound`,
which is why `create_sound_cue` takes the wave rather than being two steps.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, indent_block, load_asset


def import_sound_wave(
    source_file: str,
    destination_folder: str,
    replace_existing: bool = True,
) -> dict:
    """
    Imports an audio file (wav, mp3, ogg) as a `SoundWave` at
    `destination_path`. `source_file` is a real filesystem path and is allowed
    to live outside the project; `destination_path` must be under `/Game/`.

    `AssetImportTask.destination_path` is a **folder** and the imported asset
    takes the *source file's* stem, so it cannot be renamed during import --
    and `rename_asset` is refused by this engine anyway. `destination_folder`
    is therefore honestly a folder, and the resulting asset path comes back as
    `asset_path`, which is what every other tool in this module needs.
    """
    security.enforce_tier("import_sound_wave")
    body = (
        f"_folder = {destination_folder!r}\n"
        f"_stem = {source_file!r}.rsplit('/', 1)[-1].rsplit('.', 1)[0]\n"
        f"_task = {UNREAL}.AssetImportTask()\n"
        f"_task.set_editor_property('filename', {source_file!r})\n"
        f"_task.set_editor_property('destination_path', _folder)\n"
        f"_task.set_editor_property('automated', True)\n"
        f"_task.set_editor_property('replace_existing', {bool(replace_existing)!r})\n"
        f"_task.set_editor_property('save', True)\n"
        f"_factory = {UNREAL}.SoundFactory()\n"
        f"_factory.set_editor_property('auto_create_cue', False)\n"
        f"_task.set_editor_property('factory', _factory)\n"
        f"{UNREAL}.AssetToolsHelpers.get_asset_tools().import_asset_tasks([_task])\n"
        f"_got = list({UNREAL}.EditorAssetLibrary.list_assets(_folder, True, True))\n"
        f"_hit = [p for p in _got if p.rsplit('.', 1)[-1] == _stem]\n"
        f"_real = _hit[0] if _hit else None\n"
        f"_loaded = {UNREAL}.EditorAssetLibrary.load_asset(_real) if _real else None\n"
        f"OUT = {{'imported': _loaded is not None,\n"
        f"      'asset_path': _real,\n"
        f"      'destination_folder': {destination_folder!r},\n"
        f"      'class': str(_loaded.get_class().get_name()) if _loaded else None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("imported")),
        "asset_path": payload.get("asset_path"),
        "destination_folder": payload.get("destination_folder"),
        "class": payload.get("class"),
    }


def create_sound_cue(destination_path: str) -> dict:
    """
    Creates a `SoundCue` asset at `destination_path`.

    A cue with no nodes is silent, and that is all this tool produces: in UE
    5.8 `SoundNodeWavePlayer` exposes exactly one Python-visible member,
    `child_nodes`, and `SoundCue` exposes no `add_node`. So a wave cannot be
    wired into a cue from Python at all, and there is no argument here for one
    -- an argument that quietly did nothing would be worse than its absence.

    Populate the cue's nodes in the Sound Cue editor, after which
    `play_sound_at_location` and `set_sound_attenuation_settings` work on it.
    """
    security.enforce_tier("create_sound_cue")
    body = (
        f"_exists = bool({UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}))\n"
        f"OUT = {{'created': False, 'cue_path': {destination_path!r}, 'has_nodes': False}}\n"
        f"if _exists:\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"else:\n"
        f"    _cue = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"        {destination_path!r}.rsplit('/', 1)[-1],\n"
        f"        {destination_path!r}.rsplit('/', 1)[0],\n"
        f"        {UNREAL}.SoundCue, {UNREAL}.SoundCueFactoryNew())\n"
        f"    if _cue is None:\n"
        f"        OUT['error'] = 'factory returned None'\n"
        f"    else:\n"
        f"        {UNREAL}.EditorAssetLibrary.save_asset({destination_path!r},\n"
        f"                                           only_if_is_dirty=False)\n"
        f"        _back = {load_asset(destination_path)}\n"
        f"        OUT['created'] = _back is not None\n"
        f"        OUT['has_nodes'] = _back.get_editor_property('first_node') is not None\n"
        f"        OUT['duration'] = float(_back.get_editor_property('duration'))\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "cue_path": destination_path,
        "has_nodes": payload.get("has_nodes"),
        "error": payload.get("error"),
    }


def play_sound_at_location(
    sound_path: str, location: list[float] | None = None
) -> dict:
    """
    Plays a sound once at a world location. Editor-only verification aid: it
    calls `GameplayStatics.play_sound_at_location`, which only has an audible
    effect during play-in-editor, and returns None either way, so success here
    means "the call was made", not "a sound was heard".
    """
    security.enforce_tier("play_sound_at_location")
    loc = location or [0.0, 0.0, 0.0]
    call = indent_block(
        f"{UNREAL}.GameplayStatics.play_sound_at_location(\n"
        f"{UNREAL}.EditorLevelLibrary.get_editor_world(), _snd,\n"
        f"{UNREAL}.Vector({float(loc[0])}, {float(loc[1])}, {float(loc[2])}),\n"
        f"{UNREAL}.Rotator(0.0, 0.0, 0.0), 1.0, 1.0, 0.0, None, None)\n",
        4,
    )
    body = (
        f"_snd = {load_asset(sound_path)}\n"
        f"OUT = {{'played': False, 'sound': {sound_path!r},\n"
        f"      'class': str(_snd.get_class().get_name()) if _snd else None}}\n"
        f"if _snd is None:\n"
        f"    OUT['error'] = 'no sound asset at ' + {sound_path!r}\n"
        f"else:\n"
        f"{call}"
        f"    OUT['played'] = True\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("played")),
        "sound": sound_path,
        "class": payload.get("class"),
        "error": payload.get("error"),
    }


def create_sound_attenuation(
    destination_path: str,
    inner_radius: float = 400.0,
    falloff_distance: float = 3600.0,
) -> dict:
    """
    Creates a `SoundAttenuation` asset with the given inner radius and falloff
    distance, and reads both back.

    This takes a *`SoundAttenuation` asset path*, not a sound path, and that is
    not a workaround: in 5.8 `SoundWave` and `SoundCue` expose no attenuation
    property at all, so there is no Python route from a sound to its
    attenuation. The attenuation asset is the only editable surface.

    Inside it, the inner radius is `omni_radius` -- there is no property called
    `inner_radius` -- and the falloff is `falloff_distance`.
    """
    security.enforce_tier("create_sound_attenuation")
    body = (
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"OUT = {{'created': False, 'path': {destination_path!r}, 'after': None}}\n"
        f"if {UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}):\n"
        f"    _att_asset = {load_asset(destination_path)}\n"
        f"else:\n"
        f"    _att_asset = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"        _name, _folder, {UNREAL}.SoundAttenuation,\n"
        f"        {UNREAL}.SoundAttenuationFactory())\n"
        f"if _att_asset is None:\n"
        f"    OUT['error'] = 'factory returned None'\n"
        f"else:\n"
        f"    _att = _att_asset.get_editor_property('attenuation')\n"
        f"    if _att is None:\n"
        f"        _att = {UNREAL}.SoundAttenuationSettings()\n"
        f"        _att_asset.set_editor_property('attenuation', _att)\n"
        f"    _att.set_editor_property('omni_radius', {float(inner_radius)!r})\n"
        f"    _att.set_editor_property('falloff_distance', {float(falloff_distance)!r})\n"
        f"    _att_asset.modify()\n"
        f"    {UNREAL}.EditorAssetLibrary.save_asset({destination_path!r},\n"
        f"                                       only_if_is_dirty=False)\n"
        f"    _a2 = {load_asset(destination_path)}.get_editor_property('attenuation')\n"
        f"    OUT['after'] = {{'omni_radius': float(_a2.get_editor_property('omni_radius')),\n"
        f"                  'falloff_distance': float(_a2.get_editor_property('falloff_distance'))}}\n"
        f"    OUT['created'] = (OUT['after']['omni_radius'] == {float(inner_radius)!r}\n"
        f"                    and OUT['after']['falloff_distance'] == {float(falloff_distance)!r})\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "path": payload.get("path"),
        "after": payload.get("after"),
        "error": payload.get("error"),
    }
