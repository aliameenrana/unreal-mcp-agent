"""
Material and material instance creation, confirmed buildable in Python via
unreal.MaterialEditingLibrary and the MaterialInstanceConstant factory
(see Epic's Python API docs for MaterialEditingLibrary / MaterialInstanceConstantFactoryNew).
Not yet exercised against a live editor.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, asset_tools, json_dumps, load_asset, seq


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
