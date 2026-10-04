"""
Material and material instance creation, confirmed buildable in Python via
unreal.MaterialEditingLibrary and the MaterialInstanceConstant factory
(see Epic's Python API docs for MaterialEditingLibrary / MaterialInstanceConstantFactoryNew).
Not yet exercised against a live editor.
"""

from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Domain / shading model
#
# `material_domain` is a plain settable property. `shading_model` is NOT: the
# live Material object exposes no setter for it, and `shading_models` (the UE5
# multi-model array that replaced it) is not exposed either. It is still
# readable with get_editor_property("shading_model"), and writable through
# set_editor_property under the same name, which is what this tool uses.
# ---------------------------------------------------------------------------

MATERIAL_DOMAINS = (
    "MD_SURFACE",
    "MD_DEFERRED_DECAL",
    "MD_LIGHT_FUNCTION",
    "MD_POST_PROCESS",
    "MD_VOLUME",
    "MD_UI",
)


def set_material_domain_and_shading_model(
    material_path: str,
    domain: str | None = None,
    shading_model: str | None = None,
    confirm: bool = False,
) -> dict:
    """
    Changes a material's domain and/or shading model and recompiles it.

    Destructive, and needs confirm=True, for a reason beyond habit: the domain
    decides which material slots will accept the material at all. Moving an
    existing material from MD_SURFACE to MD_UI or MD_POST_PROCESS makes it stop
    rendering on every mesh that currently uses it, and nothing in the editor
    warns about that before it happens.

    domain is one of MD_SURFACE, MD_DEFERRED_DECAL, MD_LIGHT_FUNCTION,
    MD_POST_PROCESS, MD_VOLUME, MD_UI. shading_model is any
    MaterialShadingModel member such as MSM_DEFAULT_LIT or MSM_UNLIT. Either
    argument may be None to change only the other.
    """
    security.enforce_tier("set_material_domain_and_shading_model", confirm=confirm)
    security.check_destination_path(material_path)

    if domain is None and shading_model is None:
        return {"success": False, "error": "Pass a domain, a shading_model, or both."}
    if domain is not None and domain not in MATERIAL_DOMAINS:
        return {
            "success": False,
            "error": f"Unknown domain {domain!r}. Choose one of {', '.join(MATERIAL_DOMAINS)}.",
        }

    body = (
        f"mat = {load_asset(material_path)}\n"
        f"if mat is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},\n"
        f"          'domain_before': None, 'shading_before': None,\n"
        f"          'domain_after': None, 'shading_after': None, 'compiled': False}}\n"
        f"else:\n"
        f"    dom_before = str(mat.get_editor_property('material_domain'))\n"
        f"    shd_before = str(mat.get_editor_property('shading_model'))\n"
        + (f"    mat.set_editor_property(\n"
           f"        'material_domain', {UNREAL}.MaterialDomain.{domain})\n"
           if domain else "")
        + (f"    mat.set_editor_property(\n"
           f"        'shading_model', {UNREAL}.MaterialShadingModel.{shading_model})\n"
           if shading_model else "")
        + f"    # Saving here is what triggers the shader recompile for the new\n"
        + f"    # domain, so this call is given a longer timeout by its caller.\n"
        + f"    {UNREAL}.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=True)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'domain_before': dom_before, 'shading_before': shd_before,\n"
        f"          'domain_after': str(mat.get_editor_property('material_domain')),\n"
        f"          'shading_after': str(mat.get_editor_property('shading_model')),\n"
        f"          'compiled': True}}\n"
    )
    payload = get_bridge().run_python(
        guarded(body),
        timeout=90.0,  # a domain change recompiles shaders on save
    )

    if not payload.get("found"):
        return {"success": False, "material_path": material_path, "error": payload.get("error")}

    # The enum read back as "<MaterialDomain.MD_UI: 4>", so compare on the name.
    def _name(text):
        return text.split(".")[1].split(":")[0] if text and "." in text else text

    ok = True
    if domain is not None:
        ok = ok and _name(payload.get("domain_after")) == domain
    if shading_model is not None:
        ok = ok and _name(payload.get("shading_after")) == shading_model

    return {
        "success": ok,
        "material_path": material_path,
        "domain_before": _name(payload.get("domain_before")),
        "domain_after": _name(payload.get("domain_after")),
        "shading_before": _name(payload.get("shading_before")),
        "shading_after": _name(payload.get("shading_after")),
        "error": None if ok else "Value did not read back as requested.",
    }


def import_texture(
    source_file: str,
    destination_path: str = "/Game/MCPTest",
    destination_name: str = "",
    replace_existing: bool = False,
    srgb: bool = True,
    compression: str | None = None,
) -> dict:
    """
    Imports an image file as a Texture2D asset. Accepts png, jpg, tga, bmp,
    tif, exr, hdr, dds and psd.

    srgb and compression are applied to the imported asset after the fact, not
    to the factory: `TextureFactory` exposes no `srgb` or `compression_settings`
    at all, so setting them there raises AttributeError. Both are properties of
    the resulting Texture2D instead.

    srgb should be True for colour data and False for masks, roughness or
    normal maps, where the file holds linear data and sRGB conversion would
    corrupt it. Unreal normally infers this from how the texture is used;
    forcing it is for when that guess is wrong. compression names a
    TextureCompressionSettings member such as TC_DEFAULT, TC_NORMALMAP or
    TC_MASK; left as None to keep Unreal's choice.

    Like the mesh importers, success is reported from the task's
    imported_object_paths, because import_asset_tasks returns None either way.
    """
    security.enforce_tier("import_texture")
    name = destination_name or Path(source_file).stem
    return _run_texture_import(source_file, destination_path, name, replace_existing, srgb, compression)


def _run_texture_import(
    source_file: str,
    destination_path: str,
    destination_name: str,
    replace_existing: bool,
    srgb: bool,
    compression: str | None,
) -> dict:
    security.check_import_source(source_file)
    security.check_destination_path(destination_path)

    compression_line = (
        f"    tex.set_editor_property(\n"
        f"        'compression_settings', {UNREAL}.TextureCompressionSettings.{compression})\n"
        if compression else ""
    )

    body = (
        f"import os\n"
        f"if not os.path.isfile({source_file!r}):\n"
        f"    OUT = {{'found': False, 'error': 'No such file: ' + {source_file!r},\n"
        f"          'asset_path': None, 'size_x': None, 'size_y': None,\n"
        f"          'srgb': None, 'compression': None}}\n"
        f"else:\n"
        f"    task = {UNREAL}.AssetImportTask()\n"
        f"    task.filename = {source_file!r}\n"
        f"    task.destination_path = {destination_path!r}\n"
        f"    task.destination_name = {destination_name!r}\n"
        f"    task.factory = {UNREAL}.TextureFactory()\n"
        f"    task.automated = True\n"
        f"    task.replace_existing = {bool(replace_existing)!r}\n"
        f"    task.replace_existing_settings = {bool(replace_existing)!r}\n"
        f"    task.save = True\n"
        f"    task.async_ = False\n"
        f"    {asset_tools()}.import_asset_tasks([task])\n"
        f"    imported = [str(p) for p in task.imported_object_paths]\n"
        f"    tex = None\n"
        f"    if imported:\n"
        f"        tex = {UNREAL}.load_asset(imported[0])\n"
        f"    if tex is not None:\n"
        f"        tex.set_editor_property('srgb', {bool(srgb)!r})\n"
        + compression_line +
        f"        {UNREAL}.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'asset_path': imported[0] if imported else None,\n"
        f"          'size_x': tex.blueprint_get_size_x() if tex else None,\n"
        f"          'size_y': tex.blueprint_get_size_y() if tex else None,\n"
        f"          'srgb': str(tex.get_editor_property('srgb')) if tex else None,\n"
        f"          'compression': (str(tex.get_editor_property('compression_settings'))\n"
        f"                        if tex else None)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    return {
        "success": bool(payload.get("found")) and bool(payload.get("asset_path")),
        "source_file": source_file,
        "destination_path": destination_path,
        "destination_name": destination_name,
        "asset_path": payload.get("asset_path"),
        "size_x": payload.get("size_x"),
        "size_y": payload.get("size_y"),
        "srgb": payload.get("srgb"),
        "compression": payload.get("compression"),
        "error": payload.get("error") or (None if payload.get("asset_path") else "Import produced no asset."),
    }
