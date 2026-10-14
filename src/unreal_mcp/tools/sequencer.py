"""
Sequencer: creating Level Sequences and adding tracks and sections.

Everything here is deliberately limited to calls that do not require a
**binding**, because the binding-creating calls hard-hang the editor.
`MovieSceneSequence.add_possessable`, `MovieSceneSequenceExtensions.add_possessable`
and `add_spawnable_from_class` have each wedged a live 5.8 editor, and the
recovery is a disposable editor process. That is recorded in
TOOL_BUILDING_GUIDE.md under "add_possessable hard-hangs the editor" and it is
the reason this module stops where it does.

That constraint shapes what is here:

- `MovieSceneSequence.add_track(track_type)` takes only a track class and
  succeeds. The binding-based sibling, `MovieSceneSequenceExtensions.add_track`,
  needs a `MovieSceneBindingProxy`, and the only documented ways to get one are
  the hanging calls. So tracks added here are master tracks.
- `MovieSceneTrackExtensions.add_section(track)` plus the section's own
  `set_range(start_frame, end_frame)` work fine and are verified.

There is deliberately **no** key-setting tool. Keys live on channels, the
`add_key` methods that exist belong to specific channel classes
(`MovieSceneScriptingConstraintChannel`, `MovieSceneScriptingActorReferenceChannel`),
and there is no Python route from a track or section to its channels:
`MovieSceneScriptingLayer` exposes no channel methods, and
`SequencerChannelProxy` is a UI-selection concept. Adding a *section with a
frame range* is the deepest verifiable equivalent, which is what
`add_sequence_section` does.

`FrameRate` is a struct with `numerator` and `denominator`, not a float and not
callable -- there is no `evaluate()` on it in 5.8 -- so rates are read back as
`numerator / denominator`.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def create_level_sequence(
    destination_path: str, frame_rate: float | None = None
) -> dict:
    """
    Creates a `LevelSequence` asset at `destination_path` and saves it.

    `frame_rate` sets the display rate; 30 is the default for a new sequence.
    The tick resolution is left at the factory default rather than forced to
    match, because Sequencer wants the two distinct.
    """
    security.enforce_tier("create_level_sequence")
    _set_rate = frame_rate is not None
    body = (
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"OUT = {{'created': False, 'path': {destination_path!r}, 'display_rate': None}}\n"
        f"if {UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}):\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"else:\n"
        f"    _seq = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"        _name, _folder, {UNREAL}.LevelSequence, {UNREAL}.LevelSequenceFactoryNew())\n"
        f"    if _seq is None:\n"
        f"        OUT['error'] = 'factory returned None'\n"
        f"    else:\n"
        f"        if {_set_rate!r}:\n"
        f"            _seq.set_display_rate({UNREAL}.FrameRate({float(frame_rate or 0)!r}))\n"
        f"        {UNREAL}.EditorAssetLibrary.save_asset({destination_path!r}, only_if_is_dirty=False)\n"
        f"        _back = {UNREAL}.load_object(None, {destination_path!r})\n"
        f"        _r = _back.get_display_rate() if _back is not None else None\n"
        f"        OUT['created'] = _back is not None\n"
        f"        OUT['display_rate'] = (_r.numerator / _r.denominator) if _r is not None else None\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "path": destination_path,
        "display_rate": payload.get("display_rate"),
        "error": payload.get("error"),
    }


def get_sequence_info(sequence_path: str) -> dict:
    """
    Reads a sequence's display rate, tick resolution, playback range, and its
    tracks with the section count on each.

    Safe to call on any sequence: it only reads.
    """
    security.enforce_tier("get_sequence_info")
    body = (
        f"_seq = {UNREAL}.load_object(None, {sequence_path!r})\n"
        f"OUT = {{'found': False, 'path': {sequence_path!r}, 'tracks': [], 'track_count': 0}}\n"
        f"if _seq is None or not isinstance(_seq, {UNREAL}.LevelSequence):\n"
        f"    OUT['error'] = 'no LevelSequence at ' + {sequence_path!r}\n"
        f"else:\n"
        f"    _ext = {UNREAL}.MovieSceneTrackExtensions\n"
        f"    for _t in _seq.get_tracks():\n"
        f"        OUT['tracks'].append({{'name': str(_t.get_name()), 'class': str(_t.get_class().get_name()), 'section_count': len(_ext.get_sections(_t))}})\n"
        f"    OUT['track_count'] = len(OUT['tracks'])\n"
        f"    _dr = _seq.get_display_rate()\n"
        f"    OUT['display_rate'] = _dr.numerator / _dr.denominator\n"
        f"    OUT['playback_start'] = int(_seq.get_playback_start())\n"
        f"    OUT['playback_end'] = int(_seq.get_playback_end())\n"
        f"    if hasattr(_seq, 'get_tick_resolution'):\n"
        f"        _tr = _seq.get_tick_resolution()\n"
        f"        OUT['tick_resolution'] = _tr.numerator / _tr.denominator\n"
        f"    OUT['found'] = True\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {
            "success": False,
            "path": sequence_path,
            "error": payload.get("error"),
            "tracks": [],
            "track_count": 0,
        }
    return {
        "success": True,
        "path": sequence_path,
        "display_rate": payload.get("display_rate"),
        "tick_resolution": payload.get("tick_resolution"),
        "playback_start": payload.get("playback_start"),
        "playback_end": payload.get("playback_end"),
        "track_count": payload.get("track_count"),
        "tracks": payload.get("tracks", []),
    }


def add_sequence_track(sequence_path: str, track_type: str) -> dict:
    """
    Adds a master track of `track_type` to a sequence, and reads the resulting
    track count back.

    `track_type` is a `MovieScene*Track` class name, for example
    `MovieSceneTransformTrack`, `MovieSceneFloatTrack`,
    `MovieSceneCameraCutTrack`. This adds an unowned master track: attaching one
    to an actor needs a binding, and every documented way to create a binding is
    on the list of calls that hang the editor.
    """
    security.enforce_tier("add_sequence_track")
    body = (
        f"_seq = {UNREAL}.load_object(None, {sequence_path!r})\n"
        f"_cls = getattr({UNREAL}, {track_type!r}, None)\n"
        f"OUT = {{'added': False, 'path': {sequence_path!r}, 'track_type': {track_type!r}, 'resolved': _cls is not None, 'track_count': None}}\n"
        f"if _seq is None:\n"
        f"    OUT['error'] = 'no asset at ' + {sequence_path!r}\n"
        f"elif _cls is None:\n"
        f"    OUT['error'] = 'no class named ' + {track_type!r} + ' in unreal'\n"
        f"else:\n"
        f"    _before = len(_seq.get_tracks())\n"
        f"    _t = _seq.add_track(_cls)\n"
        f"    OUT['before'] = _before\n"
        f"    OUT['track_count'] = len(_seq.get_tracks())\n"
        f"    OUT['added'] = _t is not None and OUT['track_count'] > _before\n"
        f"    OUT['track_name'] = str(_t.get_name()) if _t is not None else None\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("added")),
        "path": payload.get("path"),
        "track_type": track_type,
        "resolved": payload.get("resolved"),
        "track_name": payload.get("track_name"),
        "track_count": payload.get("track_count"),
        "error": payload.get("error"),
    }


def add_sequence_section(
    sequence_path: str,
    track_index: int = 0,
    start_frame: int = 0,
    end_frame: int = 60,
) -> dict:
    """
    Adds a section spanning `start_frame` to `end_frame` to one of the
    sequence's tracks, and confirms the section count rose.

    This is the deepest verified equivalent of "add a key": keys live on
    channels, and no Python route reaches a track's channels from here. A
    section with a frame range is verifiable, so that is what this does.
    """
    security.enforce_tier("add_sequence_section")
    body = (
        f"_seq = {UNREAL}.load_object(None, {sequence_path!r})\n"
        f"OUT = {{'added': False, 'path': {sequence_path!r}, 'section_count': None}}\n"
        f"if _seq is None:\n"
        f"    OUT['error'] = 'no asset at ' + {sequence_path!r}\n"
        f"else:\n"
        f"    _tracks = list(_seq.get_tracks())\n"
        f"    if not _tracks:\n"
        f"        OUT['error'] = 'sequence has no tracks; add one first'\n"
        f"    elif not (0 <= {int(track_index)!r} < len(_tracks)):\n"
        f"        OUT['error'] = 'track_index out of range'\n"
        f"    else:\n"
        f"        _t = _tracks[{int(track_index)!r}]\n"
        f"        _ext = {UNREAL}.MovieSceneTrackExtensions\n"
        f"        _before = len(_ext.get_sections(_t))\n"
        f"        _sec = _ext.add_section(_t)\n"
        f"        if _sec is None:\n"
        f"            OUT['error'] = 'add_section returned None'\n"
        f"        else:\n"
        f"            _sec.set_range({int(start_frame)!r}, {int(end_frame)!r})\n"
        f"            OUT['before'] = _before\n"
        f"            OUT['section_count'] = len(_ext.get_sections(_t))\n"
        f"            OUT['added'] = OUT['section_count'] > _before\n"
        f"            OUT['track'] = str(_t.get_name())\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("added")),
        "path": payload.get("path"),
        "track": payload.get("track"),
        "section_count": payload.get("section_count"),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "error": payload.get("error"),
    }