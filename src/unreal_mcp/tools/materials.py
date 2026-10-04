"""
Material and material instance creation, confirmed buildable in Python via
unreal.MaterialEditingLibrary and the MaterialInstanceConstant factory
(see Epic's Python API docs for MaterialEditingLibrary / MaterialInstanceConstantFactoryNew).
Not yet exercised against a live editor.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    asset_tools,
    guarded,
    json_dumps,
    load_asset,
    seq,
)


def create_material(asset_path: str, asset_name: str) -> dict:
    """Creates a new, blank base Material asset at <asset_path>/<asset_name>."""
    security.enforce_tier("create_material")
    full_path = f"{asset_path}/{asset_name}"
    expr = json_dumps(
        seq(
            f"{asset_tools()}.create_asset({asset_name!r}, {asset_path!r}, "
            f"{UNREAL}.Material, {UNREAL}.MaterialFactoryNew())",
            repr(full_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "material_path": result}


def create_material_instance(asset_path: str, asset_name: str, parent_material_path: str) -> dict:
    """Creates a Material Instance Constant parented to an existing Material."""
    security.enforce_tier("create_material_instance")
    full_path = f"{asset_path}/{asset_name}"
    expr = json_dumps(
        seq(
            f"(lambda inst: ({UNREAL}.MaterialEditingLibrary.set_material_instance_parent("
            f"inst, {load_asset(parent_material_path)}), inst)[-1])"
            f"({asset_tools()}.create_asset({asset_name!r}, {asset_path!r}, "
            f"{UNREAL}.MaterialInstanceConstant, {UNREAL}.MaterialInstanceConstantFactoryNew()))",
            repr(full_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "instance_path": result, "parent": parent_material_path}


def set_material_scalar_parameter(instance_path: str, param_name: str, value: float) -> dict:
    security.enforce_tier("set_material_scalar_parameter")
    expr = json_dumps(
        seq(
            f"{UNREAL}.MaterialEditingLibrary.set_material_instance_scalar_parameter_value("
            f"{load_asset(instance_path)}, {param_name!r}, {float(value)!r})",
            repr(instance_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "instance_path": result, "param": param_name, "value": value}


def get_material_parameter_list(material_path: str) -> dict:
    """
    Lists the parameters a Material actually exposes, split by kind, plus the
    default value of each. Point this at a Material or a Material Instance.

    This is the discovery call that stops an agent guessing parameter names.
    An override written for a name the parent doesn't expose is stored in a
    slot nothing reads, so the write appears to succeed and changes nothing;
    see Example B in TOOL_BUILDING_GUIDE.md.

    An empty list is a real answer, not a failure: a freshly created Material
    has no expressions and therefore exposes no parameters.
    """
    security.enforce_tier("get_material_parameter_list")
    mel = f"{UNREAL}.MaterialEditingLibrary"

    body = (
        f"m = {load_asset(material_path)}\n"
        f"OUT = {{'found': m is not None,\n"
        f"      'error': None if m is not None else 'Could not load ' + {material_path!r},\n"
        f"      'class': type(m).__name__ if m is not None else None,\n"
        f"      'scalar': [str(n) for n in {mel}.get_scalar_parameter_names(m)],\n"
        f"      'vector': [str(n) for n in {mel}.get_vector_parameter_names(m)],\n"
        f"      'texture': [str(n) for n in {mel}.get_texture_parameter_names(m)],\n"
        f"      'static_switch': [str(n) for n in {mel}.get_static_switch_parameter_names(m)],\n"
        f"      'expression_count': {mel}.get_num_material_expressions(m)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}
    scalar = payload["scalar"]
    vector = payload["vector"]
    texture = payload["texture"]
    static_switch = payload["static_switch"]
    return {
        "success": True,
        "material_path": material_path,
        "material_class": payload["class"],
        "scalar": scalar,
        "vector": vector,
        "texture": texture,
        "static_switch": static_switch,
        "expression_count": payload["expression_count"],
        "total": len(scalar) + len(vector) + len(texture) + len(static_switch),
    }


def set_material_texture_parameter(
    instance_path: str,
    param_name: str,
    texture_path: str,
) -> dict:
    """
    Points a texture parameter on a Material Instance at a texture asset.
    texture_path is a full asset path, e.g.
    '/Engine/EngineResources/DefaultTexture.DefaultTexture'.

    The parameter must be exposed by the parent Material's graph or the write
    lands in a slot nothing reads. Use get_material_parameter_list to check
    first.
    """
    security.enforce_tier("set_material_texture_parameter")
    expr = json_dumps(
        seq(
            f"{UNREAL}.MaterialEditingLibrary.set_material_instance_texture_parameter_value("
            f"{load_asset(instance_path)}, {param_name!r}, {load_asset(texture_path)})",
            repr(instance_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {
        "success": True,
        "instance_path": result,
        "param": param_name,
        "texture_path": texture_path,
    }


def set_material_vector_parameter(
    instance_path: str, param_name: str, r: float, g: float, b: float, a: float = 1.0
) -> dict:
    security.enforce_tier("set_material_vector_parameter")
    expr = json_dumps(
        seq(
            f"{UNREAL}.MaterialEditingLibrary.set_material_instance_vector_parameter_value("
            f"{load_asset(instance_path)}, {param_name!r}, "
            f"{UNREAL}.LinearColor({r!r}, {g!r}, {b!r}, {a!r}))",
            repr(instance_path),
        )
    )
    result = get_bridge().run_python(expr)
    return {"success": True, "instance_path": result, "param": param_name, "rgba": (r, g, b, a)}
