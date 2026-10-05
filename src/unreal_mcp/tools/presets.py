"""
Composite tools: each one is a pre-tested sequence of primitive tool calls
(scene.py, materials.py, blueprints.py) bundled as one named tool, so the
orchestrator doesn't have to rediscover the right call order itself. See
PLAN.md's "Presets" section for the rationale.
"""

from __future__ import annotations

import random

from .. import security
from . import lighting, materials, scene
from .assets import asset_exists


def light_scene_preset(mood: str) -> dict:
    """
    Applies a named lighting mood to the level's light, atmosphere, and fog
    actors (found by class, not by hardcoded name, so this works in any
    project). One of: 'daylight', 'golden_hour', 'interior_warm', 'horror'.

    Bundles set_light_properties + set_sky_atmosphere_params +
    set_exponential_fog_params, matching PLAN.md's preset spec, so a mood is
    one call instead of three. Returns the underlying getter results as
    read-back evidence alongside the applied values.
    """
    security.enforce_tier("light_scene_preset")

    presets = {
        "daylight": {
            "intensity": 10.0,
            "color": (1.0, 1.0, 1.0),
            "temperature": 6500.0,
            "sky_intensity": 1.0,
            "atmosphere_height": 60.0,
            "rayleigh_scattering": (5.5e-6, 13.0e-6, 22.4e-6),
            "fog_density": 0.02,
            "fog_height_falloff": 0.2,
            "volumetric_fog": False,
        },
        "golden_hour": {
            "intensity": 4.0,
            "color": (1.0, 0.65, 0.3),
            "temperature": 3200.0,
            "sky_intensity": 0.6,
            "atmosphere_height": 40.0,
            "rayleigh_scattering": (12.0e-6, 8.0e-6, 2.0e-6),
            "fog_density": 0.06,
            "fog_height_falloff": 0.15,
            "volumetric_fog": True,
        },
        "interior_warm": {
            "intensity": 2.0,
            "color": (1.0, 0.85, 0.7),
            "temperature": 2700.0,
            "sky_intensity": 0.15,
            "atmosphere_height": 15.0,
            "rayleigh_scattering": (2.0e-6, 2.0e-6, 2.0e-6),
            "fog_density": 0.09,
            "fog_height_falloff": 0.1,
            "volumetric_fog": True,
        },
        "horror": {
            "intensity": 0.3,
            "color": (0.4, 0.5, 0.7),
            "temperature": 9000.0,
            "sky_intensity": 0.05,
            "atmosphere_height": 8.0,
            "rayleigh_scattering": (1.0e-6, 1.5e-6, 2.5e-6),
            "fog_density": 0.14,
            "fog_height_falloff": 0.08,
            "volumetric_fog": True,
        },
    }
    if mood not in presets:
        return {"success": False, "error": f"Unknown mood {mood!r}. Choose from {list(presets)}."}

    p = presets[mood]

    # Read-back evidence comes from the lighting module's getters, which query
    # the components independently of the expressions the setters just used.
    directional = lighting.set_light_properties(
        intensity=p["intensity"], color=p["color"], temperature=p["temperature"]
    )
    skylight = lighting.set_light_properties(
        intensity=p["sky_intensity"], default_class="SkyLight"
    )
    atmosphere = lighting.set_sky_atmosphere_params(
        atmosphere_height=p["atmosphere_height"],
        rayleigh_scattering=p["rayleigh_scattering"],
    )
    fog = lighting.set_exponential_fog_params(
        fog_density=p["fog_density"],
        fog_height_falloff=p["fog_height_falloff"],
        volumetric_fog=p["volumetric_fog"],
    )

    steps = [directional, skylight, atmosphere, fog]
    failed = [s for s in steps if not s.get("success")]
    if failed:
        return {
            "success": False,
            "error": f"Mood {mood!r} applied partially; failed steps: {failed}",
        }

    return {
        "success": True,
        "mood": mood,
        "applied": p,
        "read_back": {
            "directional": lighting.get_light_properties(),
            "skylight": lighting.get_light_properties(default_class="SkyLight"),
            "atmosphere": lighting.get_sky_atmosphere_params(),
            "fog": lighting.get_exponential_fog_params(),
        },
    }


def set_dressing_pass(
    class_path: str,
    count: int,
    center: tuple[float, float, float],
    radius: float,
    material_path: str | None = None,
    scalar_params: dict[str, float] | None = None,
    vector_params: dict[str, tuple[float, float, float, float]] | None = None,
    color_jitter: float = 0.0,
) -> dict:
    """
    Spawns `count` actors of `class_path` scattered randomly within `radius` of
    `center`, with randomized yaw. When material_path is given, each spawned
    actor also gets its own dynamic material instance carrying the given
    parameter overrides; color_jitter then offsets those vector parameters per
    channel per actor so a batch doesn't look uniform.

    The parent material must expose each parameter name in its graph for the
    tint to render at all.
    """
    security.enforce_tier("set_dressing_pass")
    if count < 0:
        return {"success": False, "error": "count must be zero or positive."}
    if radius < 0:
        return {"success": False, "error": "radius must be zero or positive."}

    cx, cy, cz = center
    spawned = []
    tinted = []

    for _ in range(count):
        result = scene.spawn_actor(
            class_path,
            (
                cx + random.uniform(-radius, radius),
                cy + random.uniform(-radius, radius),
                cz,
            ),
            (0.0, random.uniform(0, 360), 0.0),
        )
        name = result["actor_name"]
        spawned.append(name)

        if material_path is None:
            continue

        vectors = {
            key: tuple(
                max(0.0, min(1.0, channel + random.uniform(-color_jitter, color_jitter)))
                if color_jitter
                else channel
                for channel in rgba
            )
            for key, rgba in (vector_params or {}).items()
        }
        scene.set_mesh_material_slot(
            name,
            material_path,
            scalar_params=scalar_params,
            vector_params=vectors or None,
        )
        tinted.append(name)

    return {
        "success": True,
        "count": len(spawned),
        "actor_names": spawned,
        "tinted_count": len(tinted),
    }


def apply_material_variant_set(
    asset_path: str,
    base_material_path: str,
    variants: list[dict],
) -> dict:
    """
    Creates one Material Instance per entry in `variants`, each parented to
    base_material_path. Each variant dict looks like:
        {"name": "Red", "scalar": {"Roughness": 0.2}, "vector": {"Tint": (1,0,0,1)}}
    "scalar"/"vector" keys are optional per-variant. Returns the list of
    created instance paths. Common for team colors, rarity tiers, damage
    states: anything that is "the same material, N color variations."

    `base_material_path` is checked first. It used to be taken on trust, and a
    path that does not resolve made `create_material_instance` return a dict with
    a null instance_path, which this function then appended to its results: the
    call reported {"success": True, "count": 1, "instance_paths": [None]} and
    created nothing. A missing base is now an error, and a variant that fails to
    create is reported by name instead of being counted.
    """
    security.enforce_tier("apply_material_variant_set")
    if not base_material_path.strip():
        return {"success": False, "error": "base_material_path must not be empty."}
    if not asset_exists(base_material_path)["exists"]:
        return {"success": False,
                "error": f"base material not found: {base_material_path}",
                "count": 0, "instance_paths": []}

    created: list[str] = []
    failed: list[dict] = []
    for variant in variants:
        name = variant["name"]
        instance = materials.create_material_instance(
            asset_path, name, base_material_path
        )
        instance_path = instance.get("instance_path")
        if not instance.get("success") or not instance_path:
            failed.append({"name": name, "error": instance.get("error")
                           or "the editor returned no instance path"})
            continue

        for param_name, value in variant.get("scalar", {}).items():
            materials.set_material_scalar_parameter(instance_path, param_name, value)

        for param_name, rgba in variant.get("vector", {}).items():
            r, g, b, *a = rgba
            materials.set_material_vector_parameter(
                instance_path, param_name, r, g, b, a[0] if a else 1.0
            )

        created.append(instance_path)

    return {
        "success": not failed,
        "count": len(created),
        "requested": len(variants),
        "instance_paths": created,
        "failed": failed,
        **({"error": f"could not create: {failed}"} if failed else {}),
    }
