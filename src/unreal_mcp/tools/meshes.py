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

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, load_asset


def get_mesh_bounds(mesh_path: str) -> dict:
    """
    Reads a StaticMesh asset's bounding box in the asset's own local space, not
    world space, so it does not move when an actor using the mesh moves.
    extent is half the size; a box whose extent is 0 on an axis is flat on it.
    """
    security.enforce_tier("get_mesh_bounds")

    body = (
        f"m = {load_asset(mesh_path)}\n"
        f"b = m.get_bounding_box() if m is not None else None\n"
        f"OUT = {{'found': m is not None,\n"
        f"      'error': None if m is not None else 'Could not load ' + {mesh_path!r},\n"
        f"      'mesh': m.get_name() if m is not None else None,\n"
        f"      'lod_count': m.get_num_lods() if m is not None else None,\n"
        f"      'min': [b.min.x, b.min.y, b.min.z] if b is not None else None,\n"
        f"      'max': [b.max.x, b.max.y, b.max.z] if b is not None else None}}\n"
    )
    payload = get_bridge().run_python(guarded(body))

    if not payload.get("found"):
        return {"success": False, "mesh_path": mesh_path, "error": payload.get("error")}
    lo, hi = payload["min"], payload["max"]
    return {
        "success": True,
        "mesh_path": mesh_path,
        "mesh_name": payload["mesh"],
        "lod_count": payload["lod_count"],
        "min": lo,
        "max": hi,
        "size": [round(hi[i] - lo[i], 4) for i in range(3)],
        "extent": [round((hi[i] - lo[i]) / 2, 4) for i in range(3)],
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
        "lod_count": get_mesh_bounds(mesh_path)["lod_count"],
    }