"""
UMG: creating widget Blueprints, and driving live widget instances.

The first version of this module worked on the Widget Blueprint's *generated
class* and failed on every property. The correct target is an **instance**:

- `unreal.new_object(generated_class, outer)` builds one. Note the argument
  order is `(type, outer)` -- passing them the other way round raises
  "Cannot nativize 'World' as 'Class'".
- `UserWidget.add_to_viewport()` exists and is a plain void call.
- `Widget.set_visibility` / `get_visibility` exist on the instance. The class
  exposes neither, because a class holds defaults and visibility is per-instance.

What still cannot be done is build a widget *tree*. `WidgetTree` in 5.8 exposes
no methods beyond base `Object` -- no `root_widget`, no `all_widgets`, no
`add_child`, no `construct_widget` -- so a designer-authored tree cannot be read
or written from Python. `set_widget_text` works around that by creating a
standalone `TextBlock` rather than editing one inside a blueprint, and says so.
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

    `parent_class` is the parent widget class path; `UserWidget` is the normal
    choice. It is reported back as `parent_resolved`, so an unresolvable parent
    shows up as False rather than producing a blueprint of the wrong type.
    """
    security.enforce_tier("create_widget_blueprint")
    body = (
        f"_folder = {destination_path!r}.rsplit('/', 1)[0]\n"
        f"_name = {name!r} if {name!r} else {destination_path!r}.rsplit('/', 1)[-1]\n"
        f"_exists = bool({UNREAL}.EditorAssetLibrary.does_asset_exist({destination_path!r}))\n"
        f"OUT = {{'created': False, 'path': {destination_path!r},\n"
        f"      'parent_class': {parent_class!r}, 'parent_resolved': False}}\n"
        f"if _exists:\n"
        f"    OUT['error'] = 'asset already exists at ' + {destination_path!r}\n"
        f"else:\n"
        f"    _parent = {UNREAL}.load_object(None, {parent_class!r})\n"
        f"    OUT['parent_resolved'] = _parent is not None\n"
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


def _instance_body(blueprint_path: str) -> str:
    """Prefix that loads a blueprint and instantiates its generated class."""
    return (
        f"_bp = {UNREAL}.load_object(None, {blueprint_path!r})\n"
        f"OUT = {{'path': {blueprint_path!r}}}\n"
        f"if _bp is None:\n"
        f"    OUT['error'] = 'no asset at ' + {blueprint_path!r}\n"
        f"else:\n"
        f"    _gen = _bp.generated_class() if hasattr(_bp, 'generated_class') else None\n"
        f"    if _gen is None:\n"
        f"        OUT['error'] = 'blueprint has no generated class'\n"
        f"    else:\n"
        f"        _inst = {UNREAL}.new_object(\n"
        f"            _gen, {UNREAL}.EditorLevelLibrary.get_editor_world())\n"
    )


def add_widget_to_viewport(blueprint_path: str) -> dict:
    """
    Instantiates a Widget Blueprint and adds it to the editor viewport.

    Returns the instance's object path, which is the handle
    `set_widget_visibility` needs. `add_to_viewport()` is a void call, so
    success is confirmed by the instance existing and being a `UserWidget`,
    not by a return value.
    """
    security.enforce_tier("add_widget_to_viewport")
    body = (
        _instance_body(blueprint_path)
        + f"        _inst.add_to_viewport()\n"
        f"        OUT['added'] = isinstance(_inst, {UNREAL}.UserWidget)\n"
        f"        OUT['instance'] = _inst.get_path_name()\n"
        f"        OUT['class'] = str(_inst.get_class().get_name())\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("added")),
        "path": payload.get("path"),
        "instance": payload.get("instance"),
        "class": payload.get("class"),
        "error": payload.get("error"),
    }


def remove_widget_from_viewport() -> dict:
    """
    Removes every widget currently added to the editor viewport.

    `WidgetLayoutLibrary` exposes `remove_all_widgets` but no per-widget
    removal, so this is all-or-nothing. There is no `unreal.WidgetBlueprintLibrary`
    in this build, which is why widgets go on the viewport through
    `UserWidget.add_to_viewport` rather than the library the docs describe.
    """
    security.enforce_tier("remove_widget_from_viewport")
    body = (
        f"{UNREAL}.WidgetLayoutLibrary.remove_all_widgets(\n"
        f"    {UNREAL}.EditorLevelLibrary.get_editor_world())\n"
        f"OUT = {{'removed': True, 'note': 'remove_all_widgets clears every viewport widget'}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("removed")),
        "note": payload.get("note"),
        "error": payload.get("error"),
    }


def set_widget_visibility(
    blueprint_path: str, visibility: str = "Visible"
) -> dict:
    """
    Sets a widget's `SlateVisibility` on a live instance, and reads it back.

    `visibility` is a `SlateVisibility` name: `Visible`, `Collapsed`, `Hidden`,
    or `HitTestInvisible`. This is per-instance state, so it is applied to a
    freshly created instance of the blueprint rather than to the class.
    """
    security.enforce_tier("set_widget_visibility")
    body = (
        _instance_body(blueprint_path)
        + f"        OUT['before'] = str(_inst.get_visibility())\n"
        f"        _inst.set_visibility({UNREAL}.SlateVisibility.{visibility.upper()})\n"
        f"        OUT['after'] = str(_inst.get_visibility())\n"
        f"        OUT['set'] = OUT['after'] == str(\n"
        f"            {UNREAL}.SlateVisibility.{visibility.upper()})\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("set")),
        "path": payload.get("path"),
        "visibility": visibility,
        "before": payload.get("before"),
        "after": payload.get("after"),
        "error": payload.get("error"),
    }


def set_widget_text(text: str = "") -> dict:
    """
    Creates a `TextBlock` carrying `text` and reads the text back off it.

    This does **not** edit a widget inside a Widget Blueprint. `WidgetTree`
    exposes no way to add a child or reach a root widget from Python, so a
    designer-authored tree cannot be written to at all.

    What it does instead is build a standalone `TextBlock` and verify the text
    landed, which is useful for an editor overlay you drive yourself. It
    cannot put the block on the viewport itself: `add_to_viewport` exists on
    `UserWidget`, not on `TextBlock`, and there is no way to attach a bare
    widget to a tree. Use `add_widget_to_viewport` for that, with a Widget
    Blueprint whose text you set in the designer.
    """
    security.enforce_tier("set_widget_text")
    body = (
        f"_world = {UNREAL}.EditorLevelLibrary.get_editor_world()\n"
        f"_tb = {UNREAL}.new_object({UNREAL}.TextBlock, _world)\n"
        f"_tb.set_text({UNREAL}.Text({text!r}))\n"
        f"OUT = {{'set': False, 'text': {text!r}, 'readback': str(_tb.get_text()),\n"
        f"      'widget': _tb.get_path_name(), 'on_viewport': False}}\n"
        f"OUT['set'] = OUT['readback'] == {text!r}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return {
        "success": bool(payload.get("set")),
        "text": payload.get("text"),
        "readback": payload.get("readback"),
        "widget": payload.get("widget"),
        "on_viewport": payload.get("on_viewport"),
        "error": payload.get("error"),
    }
