"""
Scene manipulation tools: spawning, reading back, moving, deleting actors.

NOTE on API coverage: these use unreal.EditorActorSubsystem, the modern
(5.0+) replacement for the older EditorLevelLibrary spawn/actor functions.
All five tools here are confirmed working against a live Unreal Editor 5.8
session; see README.md's status section.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    actor_component,
    actor_subsystem,
    find_actor_by_name,
    guarded,
    json_dumps,
    jsonable,
    load_asset,
    missing_actor_message,
    rotator,
    seq,
    vector,
)


def spawn_actor(
    class_path: str,
    location: tuple[float, float, float] = (0.0, 0.0, 0.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict:
    """
    Spawns an actor of the given class. class_path is a full Unreal class
    path, e.g. '/Script/Engine.StaticMeshActor' for a native class, or
    '/Game/Blueprints/BP_Thing.BP_Thing_C' for a Blueprint class.
    location/rotation are (x, y, z) and (pitch, yaw, roll).
    """
    security.enforce_tier("spawn_actor")
    x, y, z = location
    pitch, yaw, roll = rotation

    expr = json_dumps(
        seq(
            f"(lambda act: act.get_name())("
            f"{actor_subsystem()}.spawn_actor_from_class("
            f"{UNREAL}.load_class(None, {class_path!r}), "
            f"{vector(x, y, z)}, "
            f"{rotator(pitch, yaw, roll)}))"
        )
    )
    name = get_bridge().run_python(expr)
    return {"success": True, "actor_name": name}


def list_actors() -> dict:
    """Returns every actor currently in the open level."""
    security.enforce_tier("list_actors")
    expr = json_dumps(
        "[{'name': a.get_name(), 'label': a.get_actor_label(), "
        "'class': a.get_class().get_name()} "
        f"for a in {actor_subsystem()}.get_all_level_actors()]"
    )
    actors = get_bridge().run_python(expr)
    return {"success": True, "actors": actors, "count": len(actors)}


def get_scene_state() -> dict:
    """Lightweight scene snapshot: right now, just the actor list and count."""
    security.enforce_tier("get_scene_state")
    return list_actors()


def set_actor_transform(
    actor_name: str,
    location: tuple[float, float, float] | None = None,
    rotation: tuple[float, float, float] | None = None,
) -> dict:
    """Moves and/or rotates an existing actor, found by its internal name (from list_actors)."""
    security.enforce_tier("set_actor_transform")
    actor_expr = find_actor_by_name(actor_name)
    parts: list[str] = []

    if location is not None:
        x, y, z = location
        parts.append(
            f"{actor_expr}.set_actor_location({vector(x, y, z)}, False, False)"
        )
    if rotation is not None:
        pitch, yaw, roll = rotation
        parts.append(
            f"{actor_expr}.set_actor_rotation({rotator(pitch, yaw, roll)}, False)"
        )

    if not parts:
        return {"success": False, "error": "Nothing to do: pass location and/or rotation."}

    parts.append(repr(actor_name))
    expr = json_dumps(seq(*parts))
    result = get_bridge().run_python(expr)
    return {"success": True, "actor_name": result}


def set_property(actor_name: str, property_name: str, value: str | int | float | bool) -> dict:
    """
    Sets a scalar property (string, number, or bool) on an existing actor.
    For location/rotation use set_actor_transform instead, this is for
    everything else (e.g. a Blueprint-exposed float/bool/string variable).
    """
    security.enforce_tier("set_property")
    if not isinstance(value, (str, int, float, bool)):
        return {"success": False, "error": "set_property only accepts scalar values."}

    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(
        seq(
            f"setattr({actor_expr}, {property_name!r}, {value!r})",
            repr(actor_name),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "actor_name": result, "property": property_name, "value": value}


def get_property(actor_name: str, property_name: str) -> dict:
    """
    Reads one property off an actor by its Python (snake_case) UPROPERTY name,
    the same naming set_property writes. Returns non-scalars too: enums as
    their name, Vector as [x, y, z], Rotator and colors as named-field dicts,
    object references as {class, name}. See remote_snippets.jsonable for the
    full conversion.

    An unknown name, or a missing actor, comes back as success=False with the
    reason rather than raising.
    """
    security.enforce_tier("get_property")
    actor_expr = find_actor_by_name(actor_name, optional=True)

    body = (
        f"a = {actor_expr}\n"
        f"raw = a.get_editor_property({property_name!r}) if a is not None else None\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'value': {jsonable('raw')} if a is not None else None,\n"
        f"      'type': type(raw).__name__ if a is not None else None}}"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "property": property_name,
            "value": None,
            "error": payload.get("error"),
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "property": property_name,
        "value": payload["value"],
        "value_type": payload["type"],
    }


def duplicate_actor(
    actor_name: str,
    offset: tuple[float, float, float] = (100.0, 0.0, 0.0),
) -> dict:
    """
    Duplicates an existing actor. offset is (x, y, z) in Unreal units; it
    defaults to 100 along X because a zero offset stacks the copy in place.

    A missing actor is reported as an error rather than passed through:
    duplicate_actor is documented as returning None on failure, but a null
    actor takes the editor down.
    """
    security.enforce_tier("duplicate_actor")
    actor_expr = find_actor_by_name(actor_name, optional=True)
    ox, oy, oz = offset

    body = (
        f"src = {actor_expr}\n"
        f"dup = ({actor_subsystem()}.duplicate_actor(src, None, {vector(ox, oy, oz)})\n"
        f"       if src is not None else None)\n"
        f"OUT = {{'found': src is not None and dup is not None,\n"
        f"      'error': None if src is not None else {missing_actor_message(actor_name)},\n"
        f"      'value': None,\n"
        f"      'type': None,\n"
        f"      'dup_name': dup.get_name() if dup else None,\n"
        f"      'dup_class': dup.get_class().get_name() if dup else None,\n"
        f"      'dup_label': dup.get_actor_label() if dup else None,\n"
        f"      'location': [dup.get_actor_location().x, dup.get_actor_location().y,"
        f" dup.get_actor_location().z] if dup else None}}"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {
            "success": False,
            "actor_name": actor_name,
            "duplicated_actor_name": None,
            "error": payload.get("error") or "duplicate_actor returned no actor",
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "duplicated_actor_name": payload["dup_name"],
        "duplicated_actor_class": payload["dup_class"],
        "duplicated_actor_label": payload["dup_label"],
        "location": payload["location"],
        "offset": offset,
    }


def delete_actor(actor_name: str, confirm: bool = False) -> dict:
    """Destroys an actor. Destructive: requires confirm=True."""
    security.enforce_tier("delete_actor", confirm=confirm)
    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(seq(f"{actor_subsystem()}.destroy_actor({actor_expr})", repr(actor_name)))
    result = get_bridge().run_python(expr)
    return {"success": True, "deleted_actor_name": result}


def _linear_color(rgba: tuple[float, float, float, float]) -> str:
    """LinearColor is (r, g, b, a) in that order, but build it by keyword anyway."""
    r, g, b, a = rgba
    return f"{UNREAL}.LinearColor(r={float(r)!r}, g={float(g)!r}, b={float(b)!r}, a={float(a)!r})"


def set_mesh_material_slot(
    actor_name: str,
    material_path: str,
    element_index: int = 0,
    scalar_params: dict[str, float] | None = None,
    vector_params: dict[str, tuple[float, float, float, float]] | None = None,
) -> dict:
    """
    Assigns a runtime material to one slot of the actor's StaticMeshComponent.
    Creates a MaterialInstanceDynamic parented to material_path rather than
    editing the asset, so the change is scoped to this one actor and nothing
    else in the project referencing that material.

    This is the per-actor counterpart to set_material_*_parameter: those write
    to a shared MaterialInstanceConstant asset, this writes to an instance
    that exists only for the actor. The parent material must expose each
    parameter name in its graph for an override to render; a name the parent
    doesn't expose stores a value nothing reads.
    """
    security.enforce_tier("set_mesh_material_slot")
    actor_expr = find_actor_by_name(actor_name)

    scalar_calls = [
        f"mid.set_scalar_parameter_value({k!r}, {float(v)!r})"
        for k, v in (scalar_params or {}).items()
    ]
    vector_calls = [
        f"mid.set_vector_parameter_value({k!r}, {_linear_color(v)})"
        for k, v in (vector_params or {}).items()
    ]

    expr = json_dumps(
        seq(
            repr(actor_name),
            f"(lambda a: (lambda c: (lambda mid: ("
            f"[{', '.join(scalar_calls)}], "
            f"[{', '.join(vector_calls)}], "
            f"c.set_material({int(element_index)!r}, mid), "
            f"[mid.get_scalar_parameter_value(k) for k in {list((scalar_params or {}).keys())!r}], "
            f"c.get_material({int(element_index)!r}).get_name())[-1])("
            f"{UNREAL}.MaterialLibrary.create_dynamic_material_instance("
            f"{UNREAL}.get_editor_subsystem({UNREAL}.UnrealEditorSubsystem), "
            f"{load_asset(material_path)})))"
            f"({actor_component(actor_expr, 'StaticMeshComponent')}))({actor_expr})",
        )
    )
    result = get_bridge().run_python(expr)
    return {
        "success": True,
        "actor_name": actor_name,
        "material_path": material_path,
        "element_index": element_index,
        "assigned_instance": result,
        "scalar_params": scalar_params or {},
        "vector_params": vector_params or {},
    }


def get_mesh_material_slot(actor_name: str, element_index: int = 0) -> dict:
    """
    Reads back what material is actually assigned to one slot, independently of
    set_mesh_material_slot's own expression: goes through get_material() on the
    component rather than the assignment path, and reports the parent material
    and whether the slot holds a per-actor dynamic instance.
    """
    security.enforce_tier("get_mesh_material_slot")
    actor_expr = find_actor_by_name(actor_name)
    expr = json_dumps(
        seq(
            repr(actor_name),
            f"(lambda a: (lambda c: (lambda m: {{"
            f"'slot': {int(element_index)!r}, "
            f"'instance': m.get_name() if m else None, "
            f"'is_dynamic': isinstance(m, {UNREAL}.MaterialInstanceDynamic), "
            f"'parent': m.get_editor_property('parent').get_path_name() "
            f"if m and m.get_editor_property('parent') else None}})("
            f"c.get_material({int(element_index)!r})))"
            f"({actor_component(actor_expr, 'StaticMeshComponent')}))({actor_expr})",
        )
    )
    return {"success": True, "actor_name": actor_name, **get_bridge().run_python(expr)}


# ---------------------------------------------------------------------------
# Scene graph: attachment, outliner organization, tags, selection
# ---------------------------------------------------------------------------


def attach_actor(
    child_actor_name: str,
    parent_actor_name: str,
    socket_name: str = "",
    location_rule: str = "KEEP_WORLD",
    rotation_rule: str = "KEEP_WORLD",
    scale_rule: str = "KEEP_WORLD",
) -> dict:
    """
    Parents `child_actor_name` to `parent_actor_name`, optionally to a socket
    on the parent. The default rules are KEEP_WORLD, so the child does not move
    when it is attached; SNAP_TO_TARGET instead snaps it onto the socket or the
    parent's origin.

    `attach_to_actor` returns True on success and False when the attachment
    would create a cycle, which is reported as a failure rather than ignored.

    The attached socket is read back with
    `Actor.get_attach_parent_socket_name()`. There is no
    `Actor.get_attach_component()` in 5.8; the stub does not list one either,
    but the stub does list `SceneComponent.get_attach_socket_name`, which is
    the same idea one level down.
    """
    security.enforce_tier("attach_actor")
    child = find_actor_by_name(child_actor_name, optional=True)
    parent = find_actor_by_name(parent_actor_name, optional=True)

    body = (
        f"c = {child}\n"
        f"p = {parent}\n"
        f"OUT = {{'found': c is not None and p is not None,\n"
        f"      'error': None if c is not None else {missing_actor_message(child_actor_name)},\n"
        f"      'attached': False,\n"
        f"      'parent_before': c.get_actor_label() if c and c.get_attach_parent_actor() else None,\n"
        f"      'parent_after': None}}\n"
        f"if c is not None and p is not None:\n"
        f"    OUT['attached'] = c.attach_to_actor(\n"
        f"        p, {socket_name!r},\n"
        f"        unreal.AttachmentRule.{location_rule},\n"
        f"        unreal.AttachmentRule.{rotation_rule},\n"
        f"        unreal.AttachmentRule.{scale_rule})\n"
        f"    ap = c.get_attach_parent_actor()\n"
        f"    OUT['parent_after'] = ap.get_actor_label() if ap else None\n"
        f"    OUT['socket'] = str(c.get_attach_parent_socket_name())\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "error": payload.get("error")}
    return {
        "success": bool(payload.get("attached")),
        "child_actor_name": child_actor_name,
        "parent_actor_name": parent_actor_name,
        "socket_name": socket_name,
        "parent_before": payload.get("parent_before"),
        "parent_after": payload.get("parent_after"),
        "socket_resolved": payload.get("socket"),
        "error": None if payload.get("attached") else "attach_to_actor returned False",
    }


def detach_actor(
    actor_name: str,
    location_rule: str = "KEEP_WORLD",
    rotation_rule: str = "KEEP_WORLD",
    scale_rule: str = "KEEP_WORLD",
) -> dict:
    """
    Unparents `actor_name`, leaving it in the world. KEEP_WORLD (the default)
    preserves the actor's current world transform; KEEP_RELATIVE instead leaves
    it sitting at its former relative transform, which usually looks like a
    sudden jump.

    Note the asymmetry with attach: the enums are named differently on each side
    (AttachmentRule when attaching, DetachmentRule when detaching), and
    DetachmentRule has no SNAP_TO_TARGET.
    """
    security.enforce_tier("detach_actor")
    actor = find_actor_by_name(actor_name, optional=True)

    body = (
        f"a = {actor}\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'had_parent': False, 'parent_before': None, 'parent_after': None}}\n"
        f"if a is not None:\n"
        f"    ap = a.get_attach_parent_actor()\n"
        f"    OUT['had_parent'] = ap is not None\n"
        f"    OUT['parent_before'] = ap.get_actor_label() if ap else None\n"
        f"    a.detach_from_actor(\n"
        f"        unreal.DetachmentRule.{location_rule},\n"
        f"        unreal.DetachmentRule.{rotation_rule},\n"
        f"        unreal.DetachmentRule.{scale_rule})\n"
        f"    ap2 = a.get_attach_parent_actor()\n"
        f"    OUT['parent_after'] = ap2.get_actor_label() if ap2 else None\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "error": payload.get("error")}
    return {
        "success": payload.get("parent_after") is None,
        "actor_name": actor_name,
        "had_parent": payload.get("had_parent"),
        "parent_before": payload.get("parent_before"),
        "parent_after": payload.get("parent_after"),
    }


def set_actor_folder(actor_name: str, folder_path: str) -> dict:
    """
    Moves an actor into a folder in the World Outliner. folder_path is a
    slash-separated path like "Props/Lamps"; an empty string puts the actor at
    the outliner root. Folders are created on demand, so the path does not have
    to exist first.
    """
    security.enforce_tier("set_actor_folder")
    actor = find_actor_by_name(actor_name, optional=True)

    body = (
        f"a = {actor}\n"
        f"def _folder(a):\n"
        f"    # An actor at the outliner root has a null folder Name, and str() of a\n"
        f"    # null Name is the literal text 'None', not ''. Normalizing here keeps\n"
        f"    # the round trip honest: what you pass in is what you read back.\n"
        f"    fp = a.get_folder_path()\n"
        f"    return '' if fp is None or str(fp) == 'None' else str(fp)\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'folder_before': _folder(a) if a else None,\n"
        f"      'folder_after': None}}\n"
        f"if a is not None:\n"
        f"    a.set_folder_path({folder_path!r})\n"
        f"    OUT['folder_after'] = _folder(a)\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "error": payload.get("error")}
    return {
        "success": True,
        "actor_name": actor_name,
        "folder_before": payload.get("folder_before"),
        "folder_after": payload.get("folder_after"),
    }


def set_actor_label(actor_name: str, new_label: str) -> dict:
    """
    Renames an actor in the World Outliner, which is the label a human sees.

    This is the one actor-naming surface that had a getter and no setter:
    `list_actors` and `get_selected_actors` both read `get_actor_label()`, and
    nothing wrote one. `set_actor_folder` and `tag_actor` each have both halves.

    Unlike the object name, the label does not have to be unique, so this never
    fails on a collision. Read-back goes through `get_actor_label()` rather than
    trusting the setter's return, and the outliner row is what actually changes;
    Python code that keys off `actor.name` is unaffected.
    """
    security.enforce_tier("set_actor_label")
    if not new_label.strip():
        return {"success": False, "error": "new_label must not be empty"}
    actor = find_actor_by_name(actor_name, optional=True)

    body = (
        f"a = {actor}\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'label_before': str(a.get_actor_label()) if a else None,\n"
        f"      'label_after': None,\n"
        f"      'object_name_unchanged': None}}\n"
        f"if a is not None:\n"
        f"    object_name = str(a.get_name())\n"
        f"    a.set_actor_label({new_label!r})\n"
        f"    OUT['label_after'] = str(a.get_actor_label())\n"
        f"    OUT['object_name_unchanged'] = str(a.get_name()) == object_name\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "error": payload.get("error")}
    before = payload.get("label_before")
    after = payload.get("label_after")
    if after != new_label:
        return {
            "success": False,
            "error": f"label did not take: asked for {new_label!r}, "
                     f"read back {after!r}",
            "actor_name": actor_name,
            "label_before": before,
            "label_after": after,
        }
    return {
        "success": True,
        "actor_name": actor_name,
        "label_before": before,
        "label_after": after,
        "object_name_unchanged": payload.get("object_name_unchanged"),
    }


def tag_actor(actor_name: str, tags: list[str], remove: list[str] | None = None) -> dict:
    """
    Adds and/or removes tags on an actor. Actor.tags is a Set[str], so ordering
    and duplicates are not preserved. remove defaults to nothing.
    """
    security.enforce_tier("tag_actor")
    actor = find_actor_by_name(actor_name, optional=True)
    add = list(dict.fromkeys(tags))
    drop = list(dict.fromkeys(remove or []))

    body = (
        f"a = {actor}\n"
        f"OUT = {{'found': a is not None,\n"
        f"      'error': None if a is not None else {missing_actor_message(actor_name)},\n"
        f"      'tags_before': sorted(str(t) for t in a.tags) if a else None,\n"
        f"      'tags_after': None}}\n"
        f"if a is not None:\n"
        f"    current = set(str(t) for t in a.tags)\n"
        f"    current.update({add!r})\n"
        f"    current.difference_update({drop!r})\n"
        f"    a.tags = sorted(current)\n"
        f"    OUT['tags_after'] = sorted(str(t) for t in a.tags)\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "error": payload.get("error")}
    return {
        "success": True,
        "actor_name": actor_name,
        "tags_before": payload.get("tags_before"),
        "tags_after": payload.get("tags_after"),
        "added": add,
        "removed": drop,
    }


def find_actors_by_tag(tag: str, exact: bool = True) -> dict:
    """
    Lists level actors carrying `tag`. This filters get_all_level_actors()
    because EditorActorSubsystem exposes no tag search in 5.8; with
    exact=False the tag matches as a case-insensitive substring instead.

    With no tag at all it lists every tag in use with how many actors have it,
    which is the way to find out what the tag vocabulary in a level actually is.
    """
    security.enforce_tier("find_actors_by_tag")
    body = (
        f"actors = {actor_subsystem()}.get_all_level_actors()\n"
        f"def _tags(a):\n"
        f"    return [str(t) for t in a.tags]\n"
        f"if not {tag!r}:\n"
        f"    counts = {{}}\n"
        f"    for a in actors:\n"
        f"        for t in _tags(a):\n"
        f"            counts[t] = counts.get(t, 0) + 1\n"
        f"    OUT = {{'found': True, 'error': None, 'mode': 'all_tags',\n"
        f"          'tags': sorted(counts.items()), 'actors': []}}\n"
        f"else:\n"
        f"    if {exact!r}:\n"
        f"        hit = [a for a in actors if {tag!r} in _tags(a)]\n"
        f"    else:\n"
        f"        low = {tag!r}.lower()\n"
        f"        hit = [a for a in actors if any(low in t.lower() for t in _tags(a))]\n"
        f"    OUT = {{'found': True, 'error': None, 'mode': 'by_tag',\n"
        f"          'tags': [],\n"
        f"          'actors': [{{'name': a.get_name(), 'label': a.get_actor_label(),\n"
        f"                       'class': a.get_class().get_name(),\n"
        f"                       'tags': sorted(_tags(a))}} for a in hit]}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    actors = payload.get("actors") or []
    return {
        "success": bool(payload.get("found")),
        "tag": tag,
        "exact": exact,
        "mode": payload.get("mode"),
        "tags": payload.get("tags"),
        "count": len(actors) if payload.get("mode") == "by_tag" else None,
        "actors": actors,
    }


def select_actors(
    actor_names: list[str],
    replace: bool = False,
    confirm: bool = False,
) -> dict:
    """
    Selects actors in the viewport so the agent can see and act on the current
    selection state.

    replace=False (the default) adds to the selection one actor at a time via
    set_actor_selection_state, leaving anything the user already had selected
    alone. replace=True calls set_selected_level_actors, which *replaces* the
    entire selection and so silently discards the user's selection; it is
    therefore treated as destructive and needs confirm=True.

    Never use select_all or invert_selection: they act on whatever the user
    happened to have selected.
    """
    # The tier is DESTRUCTIVE because replace=True clobbers the selection.
    # Additive selection is harmless, so treat it as already confirmed.
    security.enforce_tier("select_actors", confirm=confirm or not replace)
    names = list(dict.fromkeys(actor_names))

    body = (
        f"subsystem = {actor_subsystem()}\n"
        f"actors = {actor_subsystem()}.get_all_level_actors()\n"
        f"by_name = {{a.get_name(): a for a in actors}}\n"
        f"wanted, missing = [], []\n"
        f"for n in {names!r}:\n"
        f"    (wanted if n in by_name else missing).append(n)\n"
        f"if {replace!r}:\n"
        f"    subsystem.set_selected_level_actors([by_name[n] for n in wanted])\n"
        f"else:\n"
        f"    for n in wanted:\n"
        f"        subsystem.set_actor_selection_state(by_name[n], True)\n"
        f"selected = [a.get_name() for a in subsystem.get_selected_level_actors()]\n"
        f"OUT = {{'found': True, 'error': None, 'missing': missing,\n"
        f"      'selected_now': sorted(selected), 'count': len(selected)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    return {
        "success": bool(payload.get("found")) and not payload.get("missing"),
        "requested": names,
        "replaced": replace,
        "missing": payload.get("missing"),
        "selected_now": payload.get("selected_now"),
        "selection_count": payload.get("count"),
    }


def get_selected_actors() -> dict:
    """Lists the actors currently selected in the level."""
    security.enforce_tier("get_selected_actors")
    body = (
        f"sel = {actor_subsystem()}.get_selected_level_actors()\n"
        f"OUT = {{'found': True, 'error': None, 'count': len(sel),\n"
        f"      'actors': [{{'name': a.get_name(), 'label': a.get_actor_label(),\n"
        f"                   'class': a.get_class().get_name(),\n"
        f"                   'folder': ('' if a.get_folder_path() is None\n"
        f"                            or str(a.get_folder_path()) == 'None'\n"
        f"                            else str(a.get_folder_path())),\n"
        f"                   'tags': sorted(str(t) for t in a.tags)}} for a in sel]}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    actors = payload.get("actors") or []
    return {
        "success": bool(payload.get("found")),
        "count": payload.get("count"),
        "actors": actors,
    }
