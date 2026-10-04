"""
Component tools: adding, removing, listing, and setting properties on the
components an actor owns.

Two of PLAN.md's named APIs do not exist in this engine version, confirmed
against the live 5.8 editor and the generated stub:

- `Actor.add_component_by_class` is not exposed. `dir()` on a live Actor has no
  member that adds a component at all. The working equivalent is
  `unreal.new_object(ComponentClass, actor, name)`, which attaches the new
  component to the actor immediately.
- `Actor.destroy_component` is not exposed either. `ActorComponent.destroy_component`
  is, and takes the component to destroy as its argument; passing the component
  itself removes it, verified by the component count dropping.

`new_object` and `destroy_component` are in `unreal.new_object` and
`unreal.ActorComponent` respectively; see PLAN.md for the deviation note.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    find_actor_by_name,
    guarded,
    missing_actor_message,
)


def _resolve(actor_name: str) -> str:
    return find_actor_by_name(actor_name, optional=True)


def add_component(actor_name: str, component_class: str, name: str | None = None) -> dict:
    """
    Adds a component of `component_class` (an Unreal class path such as
    '/Script/Engine.StaticMeshComponent', or a Blueprint class path) to an
    existing actor. name is the component's object name; Unreal appends its own
    suffix, so treat the returned name as the only reliable one.

    Uses unreal.new_object with the actor as outer, which attaches the
    component immediately. There is no Actor.add_component_by_class on this
    engine version to wrap.
    """
    security.enforce_tier("add_component")
    if not component_class.strip():
        return {"success": False, "error": "component_class must not be empty."}

    default_name = component_class.rsplit(".", 1)[-1].strip("'\"")
    comp_name = name or f"MCP_{default_name}"
    body = (
        f"a = {_resolve(actor_name)}\n"
        f"cls = {UNREAL}.load_class(None, {component_class!r})\n"
        f"c = {UNREAL}.new_object(cls, a, {comp_name!r}) if cls is not None and a is not None else None\n"
        f"OUT = {{'found': a is not None and c is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'component_name': c.get_name() if c else None,\n"
        f"      'component_class': type(c).__name__ if c else None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "component_name": None,
            "error": payload.get("error") or f"could not load class {component_class!r}",
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "component_name": payload["component_name"],
        "component_class": payload["component_class"],
    }


def remove_component(actor_name: str, component_name: str, confirm: bool = False) -> dict:
    """
    Removes one component from an actor, by its object name from
    list_components. Destructive: the component cannot be recreated except by
    re-adding it, so requires confirm=True.

    Uses ActorComponent.destroy_component, which takes the component to destroy
    as its argument. There is no Actor.destroy_component on this engine version
    to wrap.
    """
    security.enforce_tier("remove_component", confirm=confirm)
    body = (
        f"a = {_resolve(actor_name)}\n"
        f"c = next((x for x in (a.get_components_by_class({UNREAL}.ActorComponent)\n"
        f"                          if a is not None else [])\n"
        f"          if x.get_name() == {component_name!r}), None)\n"
        f"OUT = {{'found': a is not None and c is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'removed': c.get_name() if c else None}}\n"
        f"c.destroy_component(c) if c is not None else None\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "component_name": component_name,
            "error": payload.get("error") or f"no component named {component_name!r}",
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "removed_component_name": payload["removed"],
    }


def set_component_property(
    actor_name: str,
    component_name: str,
    property_name: str,
    value: str | int | float | bool,
) -> dict:
    """
    Sets a scalar property on one of an actor's components. component_name is
    the object name from list_components. Unlike set_property this goes through
    set_editor_property, because plain setattr is rejected on component
    properties.
    """
    security.enforce_tier("set_component_property")
    if not isinstance(value, (str, int, float, bool)):
        return {"success": False, "error": "set_component_property only accepts scalar values."}

    body = (
        f"a = {_resolve(actor_name)}\n"
        f"c = next((x for x in (a.get_components_by_class({UNREAL}.ActorComponent)\n"
        f"                          if a is not None else [])\n"
        f"          if x.get_name() == {component_name!r}), None)\n"
        f"raw = c.get_editor_property({property_name!r}) if c is not None else None\n"
        f"c.set_editor_property({property_name!r}, {value!r}) if c is not None else None\n"
        f"OUT = {{'found': c is not None,\n"
        f"      'error': None if c is not None else 'No component named ' + {component_name!r}"
        f" + ' on ' + {actor_name!r},\n"
        f"      'previous': raw,\n"
        f"      'value': c.get_editor_property({property_name!r}) if c is not None else None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "component_name": component_name,
            "error": payload.get("error"),
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "component_name": component_name,
        "property": property_name,
        "value": payload["value"],
        "previous_value": payload["previous"],
    }


def list_components(actor_name: str) -> dict:
    """
    Lists every component on an actor with its class, object name, and whether
    it is the actor's root (the one carrying the transform). Object names, not
    the labels shown in the Details panel.
    """
    security.enforce_tier("list_components")
    body = (
        f"a = {_resolve(actor_name)}\n"
        f"comps = a.get_components_by_class({UNREAL}.ActorComponent) if a is not None else []\n"
        f"root = a.get_editor_property('root_component') if a is not None else None\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'components': [{{'name': c.get_name(),\n"
        f"                       'class': type(c).__name__,\n"
        f"                       'is_root': c == root,\n"
        f"                       'is_active': c.is_active()}} for c in comps],\n"
        f"      'root_component': root.get_name() if root else None}}"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "components": [],
            "error": payload.get("error"),
        }
    components = payload["components"]
    return {
        "success": True,
        "actor_name": actor_name,
        "components": components,
        "count": len(components),
        "root_component": payload["root_component"],
    }