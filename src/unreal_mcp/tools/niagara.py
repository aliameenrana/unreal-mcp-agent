"""
Niagara tools: spawning systems and driving their user parameters.

The catalog listed six. Three are buildable and one of those needed a route the
docs did not have. The status of each, measured against the 5.8 editor rather
than inferred:

- `spawn_niagara_system` — buildable. `NiagaraFunctionLibrary.spawn_system_at_location`
  and `spawn_system_attached` both exist and return a live `NiagaraComponent`.
- `set_niagara_parameter` — buildable, via `NiagaraComponent.set_float_parameter`
  and siblings, not via `set_variable_*` as guessed.
- `get_niagara_user_parameters` — buildable, and only because `NiagaraSystem`
  exposes **no** parameter accessor at all. The read lives on the function
  library instead: `get_all_user_parameters(system)` returns an array of
  `NiagaraUserParameterInfo` structs carrying name, type name and size.
- `create_niagara_system_asset` — **not buildable.** `NiagaraSystemFactoryNew`
  accepts `create_asset` and hands back a `NiagaraSystem`, but the package will
  not save; `load_asset` returns None afterwards, so no system asset exists to
  work on. Engine plugin content under `/Niagara` is the only usable source.
- `add_niagara_emitter_from_template` — not buildable. `NiagaraSystem` exposes no
  emitter handle list or adder; the underlying `emitter_handles` property is not
  reachable from Python, and populating an emitter needs graph authoring, which
  is confirmed out of scope elsewhere in these docs.
- `set_niagara_renderer_material` — not buildable. `NiagaraEmitter` has a
  `renderer_bindings` property and no public accessor for the renderer list.

Note on `set_variable_*` vs `set_*_parameter`: the `set_variable_*` family writes
the component's instance simulation state, while `set_float_parameter` and
friends write the user parameters exposed to the system's graphs. User-facing
parameter setting is the second family, so that is what this module uses.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, load_asset


def spawn_niagara_system(
    system_path: str,
    location: list[float],
    rotation: list[float] | None = None,
    attach_to_actor: str | None = None,
    socket_name: str = "",
    auto_destroy: bool = True,
) -> dict:
    """
    Spawns a Niagara system into the level, either at a world location or
    attached to an existing actor.

    Attaching is the mode that matters for most recipes: a system attached to a
    socket follows that socket every frame, which is the whole basis of the
    `cast_spell_effect` and `equip_weapon` recipes. Free spawning cannot be
    attached after the fact without a second component, so the two modes are
    separate code paths here rather than a flag bolted onto one.

    Returns the spawned component's actor name. The actor is left in the level
    for inspection unless `auto_destroy` is True, which destroys it immediately
    after creation; the latter is what you want when you only need to prove the
    system and parameters are valid, because it leaves no debris behind.

    `auto_destroy` refers to the *spawned component's* `bAutoDestroy` flag, which
    makes Niagara remove the component once the system finishes. It does not
    delete a pre-existing actor.
    """
    security.enforce_tier("spawn_niagara_system")

    rot = rotation or [0.0, 0.0, 0.0]
    attached = attach_to_actor is not None

    if not attached:
        body = (
            f"sys = {load_asset(system_path)}\n"
            f"if sys is None or not isinstance(sys, {UNREAL}.NiagaraSystem):\n"
            f"    OUT = {{'ok': False,\n"
            f"          'error': {system_path!r} + ' is not a NiagaraSystem',\n"
            f"          'component': None}}\n"
            f"else:\n"
            f"    comp = {UNREAL}.NiagaraFunctionLibrary.spawn_system_at_location(\n"
            f"        {UNREAL}.EditorLevelLibrary.get_editor_world(), sys,\n"
            f"        {UNREAL}.Vector({float(location[0])}, {float(location[1])}, {float(location[2])}),\n"
            f"        {UNREAL}.Rotator({float(rot[0])}, {float(rot[1])}, {float(rot[2])}),\n"
            f"        {UNREAL}.Vector(1.0, 1.0, 1.0), {bool(auto_destroy)!r}, True)\n"
            f"    if comp is None:\n"
            f"        OUT = {{'ok': False, 'error': 'engine returned no component',\n"
            f"              'component': None}}\n"
            f"    else:\n"
            f"        comp.set_auto_destroy({bool(auto_destroy)!r})\n"
            f"        OUT = {{'ok': True, 'error': None,\n"
            f"              'component': str(comp.get_path_name()),\n"
            f"              'actor': str(comp.get_owner().get_name()) if comp.get_owner() else None,\n"
            f"              'asset': str(comp.get_asset().get_name()) if comp.get_asset() else None}}\n"
        )
    else:
        # Actor lookup happens inside the snippet so a bad name surfaces as a
        # normal error row instead of a NameError traceback in the bridge.
        body = (
            f"sys = {load_asset(system_path)}\n"
            f"actors = {UNREAL}.EditorLevelLibrary.get_all_level_actors()\n"
            f"target = None\n"
            f"for _a in actors:\n"
            f"    if str(_a.get_name()) == {attach_to_actor!r}:\n"
            f"        target = _a\n"
            f"        break\n"
            f"if sys is None or not isinstance(sys, {UNREAL}.NiagaraSystem):\n"
            f"    OUT = {{'ok': False,\n"
            f"          'error': {system_path!r} + ' is not a NiagaraSystem',\n"
            f"          'component': None}}\n"
            f"elif target is None:\n"
            f"    OUT = {{'ok': False,\n"
            f"          'error': 'no actor named ' + {attach_to_actor!r},\n"
            f"          'component': None}}\n"
            f"else:\n"
            f"    # The real signature takes a SceneComponent, not an actor, and\n"
            f"    # the attach point is a socket name on that component. A named\n"
            f"    # socket only resolves on a skeletal mesh, so prefer one when\n"
            f"    # the actor has it and fall back to the root component.\n"
            f"    root = None\n"
            f"    # Actor has no get_root_component in the Python API, so the\n"
            f"    # component list is the only route to a SceneComponent.\n"
            f"    _scenes = target.get_components_by_class({UNREAL}.SceneComponent)\n"
            f"    if {socket_name!r}:\n"
            f"        for _c in _scenes:\n"
            f"            if _c.does_socket_exist({socket_name!r}):\n"
            f"                root = _c\n"
            f"                break\n"
            f"    if root is None and _scenes:\n"
            f"        root = _scenes[0]\n"
            f"    if root is None:\n"
            f"        OUT = {{'ok': False,\n"
            f"              'error': 'actor has no root component',\n"
            f"              'component': None}}\n"
            f"    else:\n"
            f"      comp = {UNREAL}.NiagaraFunctionLibrary.spawn_system_attached(\n"
            f"        sys, root, {socket_name!r}, {UNREAL}.Vector(0.0, 0.0, 0.0),\n"
            f"        {UNREAL}.Rotator({float(rot[0])}, {float(rot[1])}, {float(rot[2])}),\n"
            f"        {UNREAL}.AttachLocation.KEEP_RELATIVE_OFFSET,\n"
            f"        {bool(auto_destroy)!r}, True)\n"
            f"      if comp is None:\n"
            f"        OUT = {{'ok': False, 'error': 'engine returned no component',\n"
            f"              'component': None}}\n"
            f"      else:\n"
            f"        OUT = {{'ok': True, 'error': None,\n"
            f"              'component': str(comp.get_path_name()),\n"
            f"              'actor': str(comp.get_owner().get_name()) if comp.get_owner() else None,\n"
            f"              'asset': str(comp.get_asset().get_name()) if comp.get_asset() else None}}\n"
        )

    payload = get_bridge().run_python(guarded(body))
    if not payload.get("ok"):
        return {"success": False, "error": payload.get("error")}
    return {
        "success": True,
        "system_path": system_path,
        "attached": attached,
        "attached_to": attach_to_actor,
        "socket_name": socket_name if attached else "",
        "actor": payload.get("actor"),
        "component": payload.get("component"),
    }


def get_niagara_user_parameters(system_path: str) -> dict:
    """
    Lists a Niagara system's exposed user parameters: name, type name and size
    in bytes.

    This is the prerequisite for `set_niagara_parameter`. Setting a parameter the
    system does not expose is silently ignored by Niagara, so the recipe check
    is to confirm the name appears here first. `is_enum_type` is included
    because an enum parameter needs an int value rather than its own type name,
    and getting that backwards is otherwise invisible.

    The read lives on `NiagaraFunctionLibrary`, not on the system: `NiagaraSystem`
    exposes no parameter accessor whatsoever, so a `system.get_parameters()` style
    call does not exist to be discovered later.
    """
    security.enforce_tier("get_niagara_user_parameters")

    body = (
        f"sys = {load_asset(system_path)}\n"
        f"if sys is None or not isinstance(sys, {UNREAL}.NiagaraSystem):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': {system_path!r} + ' is not a NiagaraSystem',\n"
        f"          'parameters': [], 'parameter_count': 0}}\n"
        f"else:\n"
        f"    infos = {UNREAL}.NiagaraFunctionLibrary.get_all_user_parameters(sys)\n"
        f"    rows = []\n"
        f"    for info in infos:\n"
        f"        rows.append({{'name': str(info.get_editor_property('parameter_name')),\n"
        f"                     'type': str(info.get_editor_property('type_name')),\n"
        f"                     'size_bytes': int(info.get_editor_property('parameter_size_bytes')),\n"
        f"                     'is_enum': bool(info.get_editor_property('is_enum_type'))}})\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'system_path': {system_path!r},\n"
        f"          'parameter_count': len(rows), 'parameters': rows}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return {"success": False, "error": payload.get("error"),
                "parameters": [], "parameter_count": 0}
    return {
        "success": True,
        "system_path": system_path,
        "parameter_count": payload.get("parameter_count"),
        "parameters": payload.get("parameters", []),
    }


def set_niagara_parameter(
    system_path: str,
    parameter_name: str,
    value: float | int | bool | list[float],
    attach_to_actor: str | None = None,
) -> dict:
    """
    Spawns a Niagara system and sets one of its user parameters, which is the
    usual shape of "play this effect with these settings".

    Setting a parameter a system does not expose is silently ignored by Niagara,
    so this reads the exposed parameter list first and fails loudly on an
    unknown name. That check is the difference between a working effect and one
    that appears to succeed while ignoring you, which is the failure mode worth
    guarding. `known_parameters` comes back with the error so the caller can
    retry without a second round trip.

    `value` type picks the setter: bool uses `set_bool_parameter`, int uses
    `set_int_parameter`, a three-element list uses `set_vector_parameter`, and
    anything else is treated as a float.

    When `attach_to_actor` is None a suitable level actor is chosen inside the
    same snippet rather than by a separate lookup call, which keeps this to one
    round trip and avoids handing back an actor that a level edit invalidated
    between two requests.
    """
    security.enforce_tier("set_niagara_parameter")

    body = (
        f"sys = {load_asset(system_path)}\n"
        f"if sys is None or not isinstance(sys, {UNREAL}.NiagaraSystem):\n"
        f"    OUT = {{'ok': False, 'set': False, 'known_parameters': None,\n"
        f"          'error': {system_path!r} + ' is not a NiagaraSystem'}}\n"
        f"else:\n"
        f"    infos = {UNREAL}.NiagaraFunctionLibrary.get_all_user_parameters(sys)\n"
        f"    known = [str(i.get_editor_property('parameter_name')) for i in infos]\n"
        f"    if {parameter_name!r} not in known:\n"
        f"        OUT = {{'ok': False, 'set': False, 'known_parameters': known,\n"
        f"              'error': 'system exposes no user parameter named '\n"
        f"                      + {parameter_name!r}}}\n"
        f"    else:\n"
        f"        actors = {UNREAL}.EditorLevelLibrary.get_all_level_actors()\n"
        f"        target = None\n"
        f"        if {attach_to_actor!r} is None:\n"
        f"            for _a in actors:\n"
        f"                if _a.get_class().get_name() in ('StaticMeshActor',\n"
        f"                                                      'BP_Sky_Sphere',\n"
        f"                                                      'DirectionalLight'):\n"
        f"                    target = _a\n"
        f"                    break\n"
        f"            if target is None and actors:\n"
        f"                target = actors[0]\n"
        f"        else:\n"
        f"            for _a in actors:\n"
        f"                if str(_a.get_name()) == {attach_to_actor!r}:\n"
        f"                    target = _a\n"
        f"                    break\n"
        f"        if target is None:\n"
        f"            OUT = {{'ok': False, 'set': False, 'known_parameters': known,\n"
        f"                  'error': 'no usable actor to host the effect'}}\n"
        f"        else:\n"
        f"            scenes = target.get_components_by_class(\n"
        f"                {UNREAL}.SceneComponent)\n"
        f"            comp = None\n"
        f"            if scenes:\n"
        f"                comp = {UNREAL}.NiagaraFunctionLibrary.spawn_system_attached(\n"
        f"                    sys, scenes[0], '', {UNREAL}.Vector(0.0, 0.0, 0.0),\n"
        f"                    {UNREAL}.Rotator(0.0, 0.0, 0.0),\n"
        f"                    {UNREAL}.AttachLocation.KEEP_RELATIVE_OFFSET,\n"
        f"                    True, True)\n"
        f"            if comp is None:\n"
        f"                OUT = {{'ok': False, 'set': False,\n"
        f"                      'known_parameters': known,\n"
        f"                      'error': 'actor has no SceneComponent to attach to'}}\n"
        f"            else:\n"
        f"                comp.set_auto_destroy(True)\n"
        f"                _v = {value!r}\n"
        f"                _kind = None\n"
        f"                if isinstance(_v, bool):\n"
        f"                    comp.set_bool_parameter({parameter_name!r}, _v)\n"
        f"                    _kind = 'bool'\n"
        f"                elif isinstance(_v, int):\n"
        f"                    comp.set_int_parameter({parameter_name!r}, _v)\n"
        f"                    _kind = 'int'\n"
        f"                elif isinstance(_v, (list, tuple)):\n"
        f"                    comp.set_vector_parameter(\n"
        f"                        {parameter_name!r},\n"
        f"                        {UNREAL}.Vector(float(_v[0]), float(_v[1]),\n"
        f"                                           float(_v[2])))\n"
        f"                    _kind = 'vector'\n"
        f"                else:\n"
        f"                    comp.set_float_parameter({parameter_name!r}, float(_v))\n"
        f"                    _kind = 'float'\n"
        f"                OUT = {{'ok': True, 'set': True, 'error': None,\n"
        f"                      'parameter': {parameter_name!r},\n"
        f"                      'value_kind': _kind,\n"
        f"                      'component': str(comp.get_path_name()),\n"
        f"                      'attached_to': str(target.get_name()),\n"
        f"                      'known_parameters': known}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("ok"):
        return {
            "success": False,
            "error": payload.get("error"),
            "set": False,
            "known_parameters": payload.get("known_parameters"),
        }
    return {
        "success": True,
        "system_path": system_path,
        "parameter": payload.get("parameter"),
        "value_kind": payload.get("value_kind"),
        "component": payload.get("component"),
        "attached_to": payload.get("attached_to"),
    }
