"""
Mesh tools operating on StaticMesh assets: bounds, LOD generation.

PLAN.md names two APIs that do not exist in this engine version, confirmed
against the installed 5.8 stub:

- `EditorStaticMeshLibrary.generate_lods` is not present. The real call is
  `set_lods(static_mesh, reduction_options)`, which returns the number of LODs
  it generated. `tools.set_mesh_lods` wraps that instead.
- There is no `set_collision_complexity` anywhere in the Python API, and no
  `CollisionComplexity` symbol either, so that one is not buildable as
  specified. Nearby real calls are `set_convex_decomposition_collisions` and
  `remove_collisions`.
"""

from __future__ import annotations

from pathlib import Path

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, asset_tools, guarded, indent_block, load_asset


def get_mesh_bounds(mesh_path: str) -> dict:
    """
    Reads a mesh asset's bounds in the asset's own local space, not world space,
    so it does not change when an actor using the mesh moves.

    Handles both mesh kinds, which do not agree on how to ask:
    - StaticMesh has get_bounding_box(), returning a Box of two corner Vectors.
    - SkeletalMesh has **no get_bounding_box at all**; it has get_bounds(),
      returning a BoxSphereBounds with an origin and a box_extent instead of
      corners.

    Both are normalized to min/max/size/extent here. extent is half the size; an
    extent of 0 on an axis means the mesh is flat on it. For a SkeletalMesh the
    sphere_radius is also reported, because that is the bound the engine
    actually culls against.
    """
    security.enforce_tier("get_mesh_bounds")

    body = (
        f"m = {load_asset(mesh_path)}\n"
        f"cls = m.get_class().get_name() if m is not None else None\n"
        f"lo = hi = None\n"
        f"sphere = None\n"
        f"if m is not None:\n"
        f"    if cls == 'SkeletalMesh':\n"
        f"        b = m.get_bounds()\n"
        f"        o, e = b.origin, b.box_extent\n"
        f"        lo = [o.x - e.x, o.y - e.y, o.z - e.z]\n"
        f"        hi = [o.x + e.x, o.y + e.y, o.z + e.z]\n"
        f"        sphere = b.sphere_radius\n"
        f"    else:\n"
        f"        b = m.get_bounding_box()\n"
        f"        lo = [b.min.x, b.min.y, b.min.z]\n"
        f"        hi = [b.max.x, b.max.y, b.max.z]\n"
        f"OUT = {{'found': m is not None,\n"
        f"      'error': None if m is not None else 'Could not load ' + {mesh_path!r},\n"
        f"      'mesh': m.get_name() if m is not None else None,\n"
        f"      'mesh_class': cls,\n"
        f"      'lod_count': (m.get_num_lods() if cls == 'StaticMesh'\n"
        f"                   else unreal.get_editor_subsystem(\n"
        f"                       unreal.SkeletalMeshEditorSubsystem).get_lod_count(m))\n"
        f"               if m is not None else None,\n"
        f"      'min': lo, 'max': hi, 'sphere_radius': sphere}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "mesh_path": mesh_path, "error": payload.get("error")}
    lo, hi = payload["min"], payload["max"]
    if lo is None or hi is None:
        return {
            "success": False,
            "mesh_path": mesh_path,
            "error": f"No bounds available for a {payload.get('mesh_class')}.",
        }
    return {
        "success": True,
        "mesh_path": mesh_path,
        "mesh_name": payload["mesh"],
        "mesh_class": payload.get("mesh_class"),
        "lod_count": payload.get("lod_count"),
        "min": [round(v, 4) for v in lo],
        "max": [round(v, 4) for v in hi],
        "size": [round(hi[i] - lo[i], 4) for i in range(3)],
        "extent": [round((hi[i] - lo[i]) / 2, 4) for i in range(3)],
        "sphere_radius": payload.get("sphere_radius"),
    }


def set_mesh_lods(
    mesh_path: str,
    percent_triangles: list[float] | None = None,
    auto_compute_lod_screen_size: bool = False,
    confirm: bool = False,
) -> dict:
    """
    Regenerates LODs on a StaticMesh asset via
    EditorStaticMeshLibrary.set_lods, which is the real call behind this
    (PLAN.md's `generate_lods` does not exist).

    percent_triangles is one fraction per generated LOD, 0.0 to 1.0, where 1.0
    keeps every triangle; UE's BasicShape Cube at [0.5, 0.2] gives two extra
    LODs at half and a fifth of the triangles. Left unset, only the existing
    LODs are rebuilt. Each entry becomes a StaticMeshReductionSettings.

    Overwrites the asset's LOD chain in place and is not undone by re-running
    with different values, so it takes confirm=True.
    """
    security.enforce_tier("set_mesh_lods", confirm=confirm)
    if percent_triangles and any(not 0.0 <= p <= 1.0 for p in percent_triangles):
        return {
            "success": False,
            "error": "percent_triangles entries are fractions 0.0-1.0, not percentages.",
        }

    settings = ", ".join(
        f"{UNREAL}.StaticMeshReductionSettings(percent_triangles={float(p)!r})"
        for p in percent_triangles or []
    )
    body = (
        f"m = {load_asset(mesh_path)}\n"
        f"OUT = {{'found': m is not None,\n"
        f"      'error': None if m is not None else 'Could not load ' + {mesh_path!r},\n"
        f"      'generated': 0}}\n"
        f"opts = {UNREAL}.StaticMeshReductionOptions(\n"
        f"    auto_compute_lod_screen_size={bool(auto_compute_lod_screen_size)!r},\n"
        f"    reduction_settings=[{settings}])\n"
        f"OUT = {{'found': True, 'error': None,"
        f" 'generated': {UNREAL}.EditorStaticMeshLibrary.set_lods(m, opts)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "mesh_path": mesh_path, "error": payload.get("error")}
    return {
        "success": True,
        "mesh_path": mesh_path,
        "lods_generated": payload["generated"],
        # A second round trip for the count, so it can come back unreadable even
        # though the write succeeded. Indexing it unconditionally raised KeyError
        # in that case; report the error instead.
        **_lod_count_after(mesh_path),
    }


def _lod_count_after(mesh_path: str) -> dict:
    """Reads the LOD count back, tolerating a mesh that will not load."""
    bounds = get_mesh_bounds(mesh_path)
    if not bounds.get("success") or "lod_count" not in bounds:
        return {"lod_count": None, "lod_count_error": bounds.get("error")}
    return {"lod_count": bounds["lod_count"]}

# ---------------------------------------------------------------------------
# Collision
#
# There is no `set_collision_complexity` in UE 5.8 and no `CollisionComplexity`
# enum. The live StaticMeshEditorSubsystem reads the current setting with
# `get_collision_complexity`, which returns a *CollisionTraceFlag*, and changes
# it only by rebuilding the hulls: set_convex_decomposition_collisions for a
# convex decomposition, add_simple_collisions for a simple primitive,
# remove_collisions to strip it. So the write side is expressed as a preset
# over those three rather than as a complexity assignment.
# ---------------------------------------------------------------------------

COLLISION_PRESETS = ("simple", "convex", "none")


def get_mesh_collision_info(mesh_path: str) -> dict:
    """
    Reports how a static mesh is set up for collision: the trace flag, and how
    many simple primitives and convex hulls it currently has.

    The trace flag is a CollisionTraceFlag (CTF_USE_DEFAULT,
    CTF_USE_SIMPLE_AS_COMPLEX, CTF_USE_COMPLEX_AS_SIMPLE,
    CTF_USE_SIMPLE_AND_COMPLEX), not a CollisionComplexity; the latter is not
    exposed to Python at all. A mesh with a simple count of 1 and no convex hulls
    is the ordinary default.
    """
    security.enforce_tier("get_mesh_collision_info")
    security.check_destination_path(mesh_path)

    body = (
        f"mesh = {load_asset(mesh_path)}\n"
        f"if mesh is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {mesh_path!r},"
        f" 'complexity': None, 'simple_count': None, 'convex_count': None}}\n"
        f"else:\n"
        f"    sms = {UNREAL}.get_editor_subsystem({UNREAL}.StaticMeshEditorSubsystem)\n"
        f"    OUT = {{'found': True, 'error': None,\n"
        f"          'complexity': str(sms.get_collision_complexity(mesh)),\n"
        f"          'simple_count': sms.get_simple_collision_count(mesh),\n"
        f"          'convex_count': sms.get_convex_collision_count(mesh)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    return {
        "success": bool(payload.get("found")),
        "mesh_path": mesh_path,
        "error": payload.get("error"),
        "complexity": payload.get("complexity"),
        "simple_count": payload.get("simple_count"),
        "convex_count": payload.get("convex_count"),
    }


def set_mesh_collision_preset(
    mesh_path: str,
    preset: str,
    shape_type: str = "BOX",
    hull_count: int = 8,
    max_hull_verts: int = 8,
    hull_precision: int = 100,
    confirm: bool = False,
) -> dict:
    """
    Rebuilds a mesh's collision as one of three presets:

    - "simple": strips existing collision, then adds one primitive of
      shape_type (BOX, SPHERE or CAPSULE) that bounds the mesh.
    - "convex": set_convex_decomposition_collisions with hull_count /
      max_hull_verts / hull_precision. This is the closest thing in 5.8 to
      "complex collision", and it *replaces* the simple primitives.
    - "none": removes all collision, leaving the mesh non-blocking.

    Destructive: every preset discards the mesh's existing collision first, so
    hand-built hulls are not preserved. Requires confirm=True. Read the current
    state with get_mesh_collision_info before overwriting an imported mesh.
    """
    security.enforce_tier("set_mesh_collision_preset", confirm=confirm)
    security.check_destination_path(mesh_path)

    if preset not in COLLISION_PRESETS:
        return {
            "success": False,
            "error": f"Unknown preset {preset!r}. Choose one of {', '.join(COLLISION_PRESETS)}.",
        }
    if shape_type not in ("BOX", "SPHERE", "CAPSULE"):
        return {
            "success": False,
            "error": f"Unknown shape_type {shape_type!r}. Choose BOX, SPHERE or CAPSULE.",
        }
    if hull_count < 1:
        return {"success": False, "error": "hull_count must be at least 1."}

    body = (
        f"mesh = {load_asset(mesh_path)}\n"
        f"if mesh is None:\n"
        f"    OUT = {{'found': False, 'error': 'Could not load ' + {mesh_path!r},\n"
        f"          'ok': False, 'simple_count': None, 'convex_count': None}}\n"
        f"else:\n"
        f"    sms = {UNREAL}.get_editor_subsystem({UNREAL}.StaticMeshEditorSubsystem)\n"
        f"    ok = sms.remove_collisions(mesh)\n"
        f"    if {preset!r} == 'convex':\n"
        f"        ok = sms.set_convex_decomposition_collisions(\n"
        f"            mesh, {int(hull_count)}, {int(max_hull_verts)}, {int(hull_precision)}) and ok\n"
        f"    elif {preset!r} == 'simple':\n"
        f"        sms.add_simple_collisions(\n"
        f"            mesh, {UNREAL}.ScriptCollisionShapeType.{shape_type})\n"
        f"    OUT = {{'found': True, 'error': None, 'ok': bool(ok),\n"
        f"          'complexity': str(sms.get_collision_complexity(mesh)),\n"
        f"          'simple_count': sms.get_simple_collision_count(mesh),\n"
        f"          'convex_count': sms.get_convex_collision_count(mesh)}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "mesh_path": mesh_path, "error": payload.get("error")}
    return {
        "success": bool(payload.get("ok")),
        "mesh_path": mesh_path,
        "preset": preset,
        "complexity": payload.get("complexity"),
        "simple_count": payload.get("simple_count"),
        "convex_count": payload.get("convex_count"),
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _run_import_task(
    source_file: str,
    destination_path: str,
    destination_name: str,
    replace_existing: bool,
    options_snippet: str | None,
) -> dict:
    """
    Shared body of import_static_mesh and import_skeletal_mesh. Both drive the
    same AssetImportTask; only the options object differs.

    Note AssetImportTask reports what it produced in `imported_object_paths`,
    which is the only reliable success signal: the call itself returns None, the
    same way duplicate_asset does. Those paths are already full object paths
    ("/Game/Foo.Foo"); they do not need a package path plus object name joined
    back together, and doing so yields "/Game/Foo.Foo.Foo".

    A single OBJ may import as several assets, one per `o` group, so this
    returns a list rather than a single path.
    """
    security.check_import_source(source_file)
    security.check_destination_path(destination_path)

    body = (
        f"import os\n"
        f"if not os.path.isfile({source_file!r}):\n"
        f"    OUT = {{'found': False, 'error': 'No such file: ' + {source_file!r},\n"
        f"          'imported_paths': [], 'asset_paths': []}}\n"
        f"else:\n"
        f"    task = {UNREAL}.AssetImportTask()\n"
        f"    task.filename = {source_file!r}\n"
        f"    task.destination_path = {destination_path!r}\n"
        f"    task.destination_name = {destination_name!r}\n"
        f"    task.automated = True\n"
        f"    task.replace_existing = {bool(replace_existing)!r}\n"
        f"    task.replace_existing_settings = {bool(replace_existing)!r}\n"
        f"    task.save = True\n"
        f"    task.async_ = False\n"
        + (indent_block(options_snippet, 4) + "\n    task.options = ui\n" if options_snippet else "")
        + f"    {asset_tools()}.import_asset_tasks([task])\n"
        f"    imported = [str(p) for p in task.imported_object_paths]\n"
        f"    OUT = {{'found': True, 'error': None, 'imported_paths': imported,\n"
        f"          'asset_paths': imported}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    imported = payload.get("imported_paths") or []
    return {
        "success": bool(payload.get("found")) and bool(imported),
        "source_file": source_file,
        "destination_path": destination_path,
        "destination_name": destination_name,
        "error": payload.get("error") or (None if imported else "Import produced no asset."),
        "imported_paths": imported,
        "asset_paths": payload.get("asset_paths") or [],
    }


def import_static_mesh(
    source_file: str,
    destination_path: str = "/Game/MCPTest",
    destination_name: str = "",
    replace_existing: bool = False,
) -> dict:
    """
    Imports a mesh file as a StaticMesh asset. Accepts .fbx, .obj, .dae, .abc,
    .usd and .gltf; the editor picks the pipeline from the extension.

    destination_name defaults to the file's stem. The result reports the created
    asset paths, because import_asset_tasks returns nothing on success and the
    task's imported_object_paths is the only success signal.

    Without replace_existing, importing onto an existing name fails rather than
    overwriting, so this will not silently clobber an existing asset.
    """
    security.enforce_tier("import_static_mesh")
    name = destination_name or Path(source_file).stem
    return _run_import_task(source_file, destination_path, name, replace_existing, None)


def import_skeletal_mesh(
    source_file: str,
    destination_path: str = "/Game/MCPTest",
    destination_name: str = "",
    replace_existing: bool = False,
    import_mesh: bool = True,
    import_animations: bool = True,
    import_materials: bool = True,
) -> dict:
    """
    Imports a skeletal mesh from a .fbx (or .gltf/.usd) using FbxImportUI, so
    that mesh, skeleton and animation tracks land as separate assets rather than
    being collapsed together.

    **Not yet live-verified**: no skeletal source file was available when this
    was written, so only the OBJ/static path above has actually been exercised
    against the editor. The FBX pipeline is written from the documented
    FbxImportUI shape and is the one thing here that still needs a real .fbx.
    """
    security.enforce_tier("import_skeletal_mesh")
    name = destination_name or Path(source_file).stem

    # Built unindented; _run_import_task runs it through indent_block so the
    # continuation lines stay inside the else: block they are spliced into.
    options = (
        f"ui = {UNREAL}.FbxImportUI()\n"
        f"ui.import_mesh = {bool(import_mesh)!r}\n"
        f"ui.import_as_skeletal = True\n"
        f"ui.import_animations = {bool(import_animations)!r}\n"
        f"ui.import_materials = {bool(import_materials)!r}\n"
        f"ui.import_textures = False\n"
        f"ui.skeleton = None\n"
    )
    return _run_import_task(source_file, destination_path, name, replace_existing, options)
