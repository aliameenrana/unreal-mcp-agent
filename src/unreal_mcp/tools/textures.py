"""
Texture creation from a pixel buffer, texture properties, and calling a material
function from a material.

**How pixels become a texture.** There is no writable pixel-data property on
`Texture2D` from Python: `source` and `platform_data` both raise, so there is no
way to fill an in-memory texture. The route that does work is to encode the
buffer as a PNG in-process and import it, which is what
`generate_texture_from_pixels` does. It is not a workaround in the sense of being
a lesser result: the result is a real `Texture2D` asset with the pixels in it,
and PNG encoding is lossless for the 8-bit RGBA this takes.

`ModelingObjectsCreationAPI.create_texture_object` was considered and is a dead
end: `CreateTextureObjectParams` does not take pixels, it takes a
`generated_transient_texture` that you must already have built. Since building
one is the thing that is not possible, it cannot be the first step.

**Rendering a material into a texture is not buildable.** There is no
`KismetRenderingLibrary` in the Python API of this build, so there is no
`draw_material_to_render_target` and no `create_render_target2d`.
`CanvasRenderTarget2D` exists as an asset type but exposes only
`on_canvas_render_target_update` / `update_resource` / `receive_update` with no
way to issue a draw into it. Baking a material to a texture needs one of those
calls, so it is out of reach from Python in 5.8.
"""

from __future__ import annotations

import struct
import zlib

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    asset_tools,
    guarded,
    indent_block,
    load_asset,
)


# ---------------------------------------------------------------------------
# Pixel encoding
# ---------------------------------------------------------------------------


def _encode_png(width: int, height: int, pixels: list[int]) -> bytes:
    """
    Encodes 8-bit RGBA pixels as a PNG, in-process.

    `pixels` is a flat list of width * height * 4 values, row-major, top row
    first. Returns the PNG bytes. Pure stdlib (zlib + struct), so it costs
    nothing to ship and needs no temporary file.

    A PNG scanline is a filter byte followed by the row's bytes; filter 0 means
    "none", which is all that is needed for correctness, just not for file size.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"expected a positive size, got {width}x{height}")
    expected = width * height * 4
    if len(pixels) != expected:
        raise ValueError(
            f"expected {expected} values for {width}x{height} RGBA "
            f"({width}*{height}*4), got {len(pixels)}"
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        start = y * width * 4
        raw.extend(pixels[start:start + width * 4])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 6 = RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def generate_texture_from_pixels(
    destination_path: str,
    texture_name: str,
    width: int,
    height: int,
    pixels: list[int],
    srgb: bool = True,
    compression: str = "TC_DEFAULT",
    overwrite: bool = False,
) -> dict:
    """
    Creates a Texture2D asset from a raw RGBA pixel buffer.

    width and height must be > 0, and pixels must hold exactly width*height*4
    values in 0-255, row-major from the top-left. The buffer is encoded to PNG
    and imported, which is the only way to get pixels into a texture from Python
    (see the module docstring for why the in-memory route does not exist).

    srgb should be True for colour and False for data: masks, roughness, normal
    maps. Imported data is usually linear, and sRGB conversion would corrupt it.
    compression is a TextureCompressionSettings member name.

    overwrite=False refuses rather than replacing an existing asset, and never
    prompts: see create_material for why a modal is unacceptable in a tool.
    """
    security.enforce_tier("generate_texture_from_pixels")
    security.check_destination_path(destination_path)

    if width <= 0 or height <= 0:
        return {"success": False,
                "error": f"width and height must be positive, got {width}x{height}."}

    try:
        png = _encode_png(width, height, [max(0, min(255, int(v))) for v in pixels])
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    except (TypeError, IndexError) as exc:
        return {"success": False,
                "error": f"pixels must be a flat list of integers: {exc}"}

    full_path = f"{destination_path}/{texture_name}"
    body = (
        f"import os\n"
        f"png = {png!r}\n"
        f"full = {full_path!r}\n"
        f"if {UNREAL}.EditorAssetLibrary.does_asset_exist(full) and not {bool(overwrite)!r}:\n"
        f"    OUT = {{'found': False, 'error': 'An asset already exists at ' + full\n"
        f"          + '; pass overwrite=True to replace it',\n"
        f"          'asset_path': None}}\n"
        f"else:\n"
        f"    tmp = os.path.join(\n"
        f"        {UNREAL}.Paths.project_saved_dir(), 'mcp_textures',\n"
        f"        {texture_name!r} + '.png')\n"
        f"    os.makedirs(os.path.dirname(tmp), exist_ok=True)\n"
        f"    with open(tmp, 'wb') as _fh:\n"
        f"        _fh.write(png)\n"
        f"    task = {UNREAL}.AssetImportTask()\n"
        f"    task.filename = tmp\n"
        f"    task.destination_path = {destination_path!r}\n"
        f"    task.destination_name = {texture_name!r}\n"
        f"    task.factory = {UNREAL}.TextureFactory()\n"
        f"    task.automated = True\n"
        f"    task.replace_existing = {bool(overwrite)!r}\n"
        f"    task.replace_existing_settings = {bool(overwrite)!r}\n"
        f"    task.save = True\n"
        f"    task.async_ = False\n"
        f"    {asset_tools()}.import_asset_tasks([task])\n"
        f"    imported = [str(p) for p in task.imported_object_paths]\n"
        f"    if imported:\n"
        f"        tex = {UNREAL}.load_asset(imported[0])\n"
        f"        if tex is not None:\n"
        f"            tex.set_editor_property('srgb', {bool(srgb)!r})\n"
        f"            tex.set_editor_property(\n"
        f"                'compression_settings',\n"
        f"                {UNREAL}.TextureCompressionSettings.{compression})\n"
        f"            {UNREAL}.EditorAssetLibrary.save_loaded_asset(\n"
        f"                tex, only_if_is_dirty=False)\n"
        f"        OUT = {{'found': True, 'error': None, 'asset_path': imported[0],\n"
        f"              'size_x': tex.blueprint_get_size_x(),\n"
        f"              'size_y': tex.blueprint_get_size_y()}}\n"
        f"    else:\n"
        f"        OUT = {{'found': False, 'error': 'import produced no asset',\n"
        f"              'asset_path': None}}\n"
        f"    # The staging file is ours; remove it either way.\n"
        f"    if os.path.exists(tmp):\n"
        f"        os.remove(tmp)\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("found"):
        return {"success": False, "asset_path": payload.get("asset_path"),
                "error": payload.get("error")}
    return {
        "success": True,
        "asset_path": payload["asset_path"],
        "texture_name": texture_name,
        "width": width,
        "height": height,
        "size_x": payload.get("size_x"),
        "size_y": payload.get("size_y"),
        "srgb": srgb,
        "compression": compression,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

# The settings that are readable and writable on a Texture2D in this build.
# Verified by reading each one: srgb, compression_settings and mip_gen_settings
# are all settable. lod_bias is set as a float but reads back as an int, so a
# fractional bias is silently truncated: 2.5 stores as 2. Pass whole numbers.
#
# The enum member names are NOT hardcoded here. A hardcoded list derived from the
# C++ names was wrong in two places (there is no TC_MASK, it is TC_MASKS, and
# there are no TF_SHARPEN* members at all), and a wrong name surfaces as an
# AttributeError from inside the editor with no hint that the list was the
# problem. set_texture_properties checks the live enum instead.
TEXTURE_ENUMS = {
    "compression_settings": "TextureCompressionSettings",
    "mip_gen_settings": "TextureMipGenSettings",
    "filter": "TextureFilter",
    "address_x": "TextureAddress",
    "address_y": "TextureAddress",
}


def _validate_enum_member(enum_name: str, member: str) -> str | None:
    """
    Returns an error string if `member` is not a member of `enum_name` on the live
    Unreal object, else None. Checked at build time via the editor, because a
    hardcoded list of Unreal enum members is a guess that will be wrong.
    """
    body = (
        f"e = getattr({UNREAL}, {enum_name!r}, None)\n"
        f"if e is None:\n"
        f"    OUT = {{'ok': False, 'error': 'no such enum ' + {enum_name!r}}}\n"
        f"elif hasattr(e, {member!r}):\n"
        f"    OUT = {{'ok': True, 'error': None}}\n"
        f"else:\n"
        f"    _mem = sorted(n for n in dir(e) if not n.startswith('_')\n"
        f"                 and not callable(getattr(e, n, None)))\n"
        f"    OUT = {{'ok': False, 'error': {member!r} + ' is not a member of '\n"
        f"              + {enum_name!r} + '; available: ' + repr(_mem[:10])}}\n"
    )
    payload = get_bridge().run_python(guarded(body))
    return None if payload.get("ok") else (payload.get("error") or "unknown enum error")


def set_texture_properties(
    texture_path: str,
    srgb: bool | None = None,
    compression: str | None = None,
    mip_gen_settings: str | None = None,
    lod_bias: float | None = None,
    filter_mode: str | None = None,
    address_x: str | None = None,
    address_y: str | None = None,
) -> dict:
    """
    Sets properties on an existing Texture2D. Every argument is optional and only
    the ones passed are changed.

    srgb False for data textures (masks, roughness, normal maps), True for
    colour. Getting this wrong is the most common texture mistake and it is
    silent: the texture still works, the values are just wrong.

    Values are read back and returned, and a mismatch is reported as a failure,
    because a setter that appears to accept a value is not proof the value stuck.
    """
    security.enforce_tier("set_texture_properties")
    security.check_destination_path(texture_path)

    for label, value, enum_name in (
        ("compression", compression, "TextureCompressionSettings"),
        ("mip_gen_settings", mip_gen_settings, "TextureMipGenSettings"),
        ("filter_mode", filter_mode, "TextureFilter"),
        ("address_x", address_x, "TextureAddress"),
        ("address_y", address_y, "TextureAddress"),
    ):
        if value is None:
            continue
        problem = _validate_enum_member(enum_name, value)
        if problem:
            return {"success": False,
                    "error": f"Invalid {label} {value!r}: {problem}"}

    body = (
        f"tex = {load_asset(texture_path)}\n"
        f"if tex is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {texture_path!r},\n"
        f"          'before': None, 'after': None}}\n"
        f"else:\n"
        f"    _before = {{}}\n"
        f"    for _k in ('srgb', 'compression_settings', 'mip_gen_settings', 'lod_bias'):\n"
        f"        try:\n"
        f"            _before[_k] = str(tex.get_editor_property(_k))\n"
        f"        except Exception as exc:\n"
        f"            _before[_k] = 'ERR ' + type(exc).__name__\n"
        + "".join(
            f"    if {value is not None!r} and tex is not None:\n"
            + (f"        tex.set_editor_property('{prop}', {value!r})\n"
               if isinstance(value, (bool, int, float))
               else f"        tex.set_editor_property(\n"
                    f"            '{prop}', {UNREAL}.{enum}.{value})\n")
            for prop, enum, value in (
                ("srgb", None, srgb),
                ("compression_settings", "TextureCompressionSettings", compression),
                ("mip_gen_settings", "TextureMipGenSettings", mip_gen_settings),
                ("filter", "TextureFilter", filter_mode),
                ("address_x", "TextureAddress", address_x),
                ("address_y", "TextureAddress", address_y),
            )
            if value is not None
        )
        + (f"    if tex is not None:\n"
           f"        tex.set_editor_property('lod_bias', float({float(lod_bias)!r}))\n"
           if lod_bias is not None else "")
        + f"    {UNREAL}.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=True)\n"
        f"    _after = {{}}\n"
        f"    for _k in ('srgb', 'compression_settings', 'mip_gen_settings', 'lod_bias',\n"
        f"                'filter', 'address_x', 'address_y'):\n"
        f"        try:\n"
        f"            _after[_k] = str(tex.get_editor_property(_k))\n"
        f"        except Exception as exc:\n"
        f"            _after[_k] = 'ERR ' + type(exc).__name__\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'before': _before, 'after': _after}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("found"):
        return {"success": False, "texture_path": texture_path,
                "error": payload.get("error")}
    return {
        "success": True,
        "texture_path": texture_path,
        "before": payload.get("before") or {},
        "after": payload.get("after") or {},
        "error": None,
    }


def get_texture_info(texture_path: str) -> dict:
    """
    Describes a Texture2D: dimensions and the settings that decide cost and
    correctness.

    The size read back is the *imported* size, so it reflects mip generation and
    any texture group limit rather than what was uploaded, which is the number
    that matters for memory.

    **`pixel_format` is always None** and this is not a bug in the tool: a
    Texture2D exposes neither `pixel_format`, `source` nor `platform_data` to
    Python in this build, all three raise. So the pixel format cannot be read
    back, and neither can the pixel contents. `properties` reports a
    `_readable` flag for each of the three so a caller can tell that the absence
    is the API's, not the query's.
    """
    security.enforce_tier("get_texture_info")
    security.check_destination_path(texture_path)

    body = (
        f"tex = {load_asset(texture_path)}\n"
        f"if tex is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {texture_path!r}}}\n"
        f"else:\n"
        f"    rows = {{}}\n"
        f"    for _k in ('srgb', 'compression_settings', 'mip_gen_settings', 'lod_bias',\n"
        f"                'filter', 'address_x', 'address_y', 'never_stream', 'power_of_two_mode'):\n"
        f"        try:\n"
        f"            rows[_k] = str(tex.get_editor_property(_k))\n"
        f"        except Exception as exc:\n"
        f"            rows[_k] = 'ERR ' + type(exc).__name__\n"
        # pixel_format, source and platform_data are all unreadable on a
        # Texture2D in this build, so the format is reported as unknown rather
        # than guessed from the compression setting.
        f"    _format = None\n"
        f"    for _cand in ('pixel_format', 'source', 'platform_data'):\n"
        f"        try:\n"
        f"            tex.get_editor_property(_cand)\n"
        f"            rows[_cand + '_readable'] = True\n"
        f"        except Exception:\n"
        f"            rows[_cand + '_readable'] = False\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'size_x': tex.blueprint_get_size_x(),\n"
        f"          'size_y': tex.blueprint_get_size_y(),\n"
        f"          'pixel_format': _format, 'properties': rows}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=120.0)
    if not payload.get("found"):
        return {"success": False, "texture_path": texture_path,
                "error": payload.get("error")}
    return {
        "success": True,
        "texture_path": texture_path,
        "size_x": payload.get("size_x"),
        "size_y": payload.get("size_y"),
        "pixel_format": payload.get("pixel_format"),
        "properties": payload.get("properties") or {},
    }


# ---------------------------------------------------------------------------
# Calling a material function
# ---------------------------------------------------------------------------


def create_material_function_call(
    material_path: str,
    function_path: str,
    node_x: int = 0,
    node_y: int = 0,
    description: str = "",
    recompile: bool = False,
) -> dict:
    """
    Adds a `MaterialExpressionMaterialFunctionCall` node to a material, calling
    the given MaterialFunction.

    **The node class is `MaterialExpressionMaterialFunctionCall`**, not
    `MaterialExpressionFunctionCall`, which does not exist. That extra
    `Material` is the whole naming trap here.

    The node's own pins are named after the function's FunctionInput and
    FunctionOutput nodes, so the pin names to wire against come from
    `list_material_function_expressions` rather than from a fixed list.

    Does not recompile by default: a material recompile is the slowest thing in
    this server, and one is enough at the end of a build.
    """
    security.enforce_tier("create_material_function_call")
    security.check_destination_path(function_path)

    body = (
        f"mf = {load_asset(function_path)}\n"
        f"mat = {load_asset(material_path)}\n"
        f"if mat is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {material_path!r},\n"
        f"          'selector': None}}\n"
        f"elif mf is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {function_path!r},\n"
        f"          'selector': None}}\n"
        f"elif not isinstance(mf, {UNREAL}.MaterialFunction):\n"
        f"    OUT = {{'found': False, 'selector': None,\n"
        f"          'error': 'Not a MaterialFunction: ' + mf.get_class().get_name()}}\n"
        f"else:\n"
        f"    mel = {UNREAL}.MaterialEditingLibrary\n"
        f"    _cls = getattr({UNREAL}, 'MaterialExpressionMaterialFunctionCall', None)\n"
        f"    if _cls is None:\n"
        f"        OUT = {{'found': False, 'selector': None,\n"
        f"              'error': 'this build has no MaterialExpressionMaterialFunctionCall'}}\n"
        f"    else:\n"
        f"        _expr = mel.create_material_expression(\n"
        f"            mat, _cls, {int(node_x)}, {int(node_y)})\n"
        + (f"        _expr.set_editor_property('desc', {description!r})\n"
           if description else "")
        + f"        _set = None\n"
        f"        for _prop in ('material_function', 'function', 'material_function_asset'):\n"
        f"            try:\n"
        f"                _expr.set_editor_property(_prop, mf)\n"
        f"                _set = _prop\n"
        f"                break\n"
        f"            except Exception:\n"
        f"                continue\n"
        f"        if _set is None:\n"
        f"            mel.delete_material_expression(mat, _expr)\n"
        f"            OUT = {{'found': False, 'selector': None,\n"
        f"                  'error': 'the node accepts no material_function property'}}\n"
        f"        else:\n"
        f"            _x, _y = mel.get_material_expression_node_position(_expr)\n"
        f"            _d = str(_expr.get_editor_property('desc') or '')\n"
        f"            _sel = ((_d + ' ') if _d else '') + _expr.get_class().get_name()\n"
        f"            _sel = _sel + '@' + str(_x) + ',' + str(_y)\n"
        + (f"            errors = [str(e) for e in mel.recompile_material(mat)]\n"
           if recompile else "            errors = None\n")
        + f"            OUT = {{'found': True, 'error': None, 'selector': _sel,\n"
        f"                  'class': _expr.get_class().get_name(), 'desc': _d,\n"
        f"                  'x': _x, 'y': _y, 'set_via': _set,\n"
        f"                  'function': str(mf.get_path_name()),\n"
        f"                  'recompile_errors': errors,\n"
        f"                  'expression_count':\n"
        f"                      mel.get_num_material_expressions(mat)}}\n"
    )
    payload = get_bridge().run_python(guarded(body), timeout=180.0)
    if not payload.get("found"):
        return {"success": False, "material_path": material_path,
                "function_path": function_path, "error": payload.get("error")}
    return {
        "success": True,
        "material_path": material_path,
        "function_path": function_path,
        "selector": payload.get("selector"),
        "expression_class": payload.get("class"),
        "description": payload.get("desc"),
        "position": [payload.get("x"), payload.get("y")],
        # Which property actually took the function, since it is guessed.
        "set_via": payload.get("set_via"),
        "expression_count": payload.get("expression_count"),
        "recompile_errors": payload.get("recompile_errors"),
        "error": None,
    }
