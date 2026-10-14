"""
UMG: creating widget Blueprints and setting properties on the widgets inside
them.

That leaves exactly one buildable tool, and the reason is specific rather than
cautious. A Widget Blueprint's visual tree is authored in the UMG designer, and
the generated class of a freshly created blueprint exposes **no properties at
all** -- `visibility`, `render_transform` and `color_and_opacity` all raise
"Failed to find property" because there is no root widget to own them. Once a
tree is authored in the designer the widget exists, but resolving a named widget
inside that tree is a separate problem with no Python route found.

So `set_widget_text` and `set_widget_visibility` were built, live-probed, and
removed: they cannot succeed against an empty blueprint and cannot reach an
authored one. `add_widget_to_viewport` was dropped for the same reason --
`unreal` in this build has no `WidgetBlueprintLibrary` at all.

`add_widget_to_viewport` was planned and is **not buildable**: in this build
`unreal` has no `WidgetBlueprintLibrary` attribute at all, so there is no way to
instantiate a widget from a class and add it to a viewport from Python. The
stub omits the class as well; this was confirmed by probing, not guessed.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded


def create_widget_blueprint(
    destination_path: str,
    parent_class: str = "UserWidget",
    name: str | None = None,
) -> dict:
    """
    Creates a Widget Blueprint at `destination_path`.

    `parent_class` is the parent widget class name; `UserWidget` is the normal
    choice and the only one verified here. Any other parent is accepted as a
    string and reported back unresolved rather than guessed at.
    """
    security.enforce_tier("create_widget_blueprint")
    body = (
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {name!r} if {name!r} else {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"_exists = bool({UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}))\n"
        f"OUT = {{'created': False, 'path': {destination_path!r},\n"
        f"      'parent_class': {parent_class!r}}}\n"
        f"if _exists:\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"else:\n"
        f"    _parent = None\n"
        f"    _cls = {UNREAL}.load_object(None, {parent_class!r})\n"
        f"    if _cls is not None:\n"
        f"        _parent = _cls\n"
        f"    OUT['parent_resolved'] = _cls is not None\n"
        f"    _factory = {UNREAL}.WidgetBlueprintFactory()\n"
        f"    _factory.set_editor_property('parent_class', _parent)\n"
        f"    _asset = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"        _name, _folder, {UNREAL}.WidgetBlueprint, _factory)\n"
        f"    if _asset is None:\n"
        f"        OUT['error'] = 'factory returned None'\n"
        f"    else:\n"
        f"        OUT['created'] = bool(\n"
        f"            {UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}))\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("created")),
        "path": destination_path,
        "parent_class": payload.get("parent_class"),
        "parent_resolved": payload.get("parent_resolved"),
        "error": payload.get("error"),
    }
