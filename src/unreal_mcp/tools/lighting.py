"""
Lighting primitives: the DirectionalLight / SkyLight / SkyAtmosphere /
ExponentialHeightFog level singletons, plus a getter per setter.

The getters exist as separate tools rather than as an echo of the setter's own
return value on purpose. "The set call didn't throw" is not evidence a fog
density landed, and reading back through the same expression the setter used
proves nothing about editor state either; these re-query the component by an
independent path so the check can actually fail.

Verified against a live Unreal Editor 5.8 session. Two API facts confirmed by
probe and worth keeping in mind when extending this file:
  - plain `setattr` on a light/fog component property is rejected; every
    write here goes through `set_editor_property`.
  - `ground_albedo` on SkyAtmosphereComponent is an `unreal.Color`, whose
    positional fields are (b, g, r, a), while `rayleigh_scattering` and
    `mie_scattering` are `unreal.LinearColor` at (r, g, b, a). Same-named
    "color" argument, opposite channel order, hence the keyword arguments.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    actor_component,
    find_actor_by_class,
    find_actor_by_name,
    guarded,
    indent_block,
)


def _out(pairs: dict[str, str]) -> str:
    """
    Source text for an OUT dict whose values are already source expressions.

    Built with str.format-free string joining rather than an f-string, because a
    literal dict inside an f-string needs every brace doubled, and doubling the
    wrong one produces an f-string syntax error that names nothing useful.
    """
    return "OUT = {" + ", ".join(f"{k!r}: {v}" for k, v in pairs.items()) + "}"


def _resolve(actor_name: str | None, default_class: str) -> str:
    """
    Actor expression for a named actor, or for the level's single instance of
    a class. The lighting actors are singletons with UAID-suffixed object
    names, so class lookup is the only addressable form for them.

    Always optional: a missing actor yields None so the tool can report it
    rather than raising StopIteration inside the editor.
    """
    if actor_name is None:
        return find_actor_by_class(default_class)
    return find_actor_by_name(actor_name)


def _missing_message(actor_name: str | None, default_class: str) -> str:
    if actor_name is None:
        return f"No {default_class} in the current level"
    return f"No actor named {actor_name!r} in the current level"


def _body(actor_expr: str, actor_name: str | None, default_class: str,
          component_class: str, inner: str) -> str:
    """
    Wraps `inner`, which may assume both `a` (the actor) and `c` (the component)
    are bound, in a body that resolves them and reports a missing actor or a
    missing component as a plain error.

    Every tool here goes through this. Without it a missing actor raises
    StopIteration and a missing component raises AttributeError, and since these
    snippets are sent bare rather than through guarded(), either one arrives at the
    caller as RemoteCommandFailedError with an empty log and no indication of
    which light was missing.
    """
    actor_missing = _missing_message(actor_name, default_class)
    component_missing = (
        f"{actor_missing}, and it carries no {component_class}"
    )
    return (
        f"a = {actor_expr}\n"
        f"if a is None:\n"
        f"    " + _out({"found": "False", "error": repr(actor_missing)}) + "\n"
        f"else:\n"
        f"    c = a.get_component_by_class({UNREAL}.{component_class})\n"
        f"    if c is None:\n"
        f"        " + _out({"found": "False", "error": repr(component_missing)}) + "\n"
        f"    else:\n"
        f"{indent_block(inner, spaces=8)}"
    )


def _fail(actor_name: str | None, payload: dict, fallback: str) -> dict:
    return {"success": False, "actor_name": actor_name,
            "error": payload.get("error") or fallback}


def _ok(payload: dict, **fields) -> dict:
    return {"success": True,
            "actor_name": payload.get("actor_name"), **fields}


def _apply_to_component(component: str, parts: list[str]) -> str:
    """
    Evaluates each setter call in order against the resolved component, then
    yields None so the caller can seq a confirmation value after it.
    """
    return f"(lambda c: ([{', '.join(parts)}], None)[-1]) ({component})"


def _color_expr(r: float, g: float, b: float, a: float, cls: str) -> str:
    return f"{UNREAL}.{cls}(r={r!r}, g={g!r}, b={b!r}, a={a!r})"


def set_light_properties(
    actor_name: str | None = None,
    intensity: float | None = None,
    color: tuple[float, float, float] | None = None,
    temperature: float | None = None,
    default_class: str = "DirectionalLight",
) -> dict:
    """
    Sets intensity and/or color on one light actor. actor_name defaults to
    None, which targets the level's single instance of default_class (so
    default_class="SkyLight" reaches the skylight). color is (r, g, b) floats
    0..1. temperature is Kelvin and needs use_temperature=True; it only exists
    on LightComponent subclasses (point/spot/rect/directional), not SkyLight,
    so passing it for a skylight is an error rather than a silent no-op.
    """
    security.enforce_tier("set_light_properties")
    if temperature is not None and default_class == "SkyLight" and actor_name is None:
        return {
            "success": False,
            "error": "SkyLightComponent has no temperature control; "
            "use a DirectionalLight or a local light instead.",
        }

    parts: list[str] = []
    if intensity is not None:
        parts.append(f"c.set_editor_property('intensity', {float(intensity)!r})")
    if color is not None:
        r, g, b = color
        parts.append(
            f"c.set_editor_property('light_color', "
            f"{_color_expr(round(r * 255), round(g * 255), round(b * 255), 255, 'Color')})"
        )
    if temperature is not None:
        parts.append("c.set_editor_property('use_temperature', True)")
        parts.append(f"c.set_editor_property('temperature', {float(temperature)!r})")
    if not parts:
        return {
            "success": False,
            "error": "Nothing to do: pass intensity, color, and/or temperature.",
        }

    actor_expr = _resolve(actor_name, default_class)
    component = actor_component(actor_expr, "LightComponentBase")
    inner = (
        _apply_to_component(component, parts)
        + "\n"
        + _out({"found": "True", "error": "None",
                "actor_name": "a.get_name()"})
    )
    body = _body(actor_expr, actor_name, default_class, "LightComponentBase", inner)
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not set light properties")
    return _ok(payload,
               default_class=default_class if actor_name is None else None,
               intensity=intensity,
               color=color,
               temperature=temperature)


def get_light_properties(
    actor_name: str | None = None, default_class: str = "DirectionalLight"
) -> dict:
    """
    Reads a light actor's intensity and color straight off the component, plus
    whether that component exposes a temperature control at all.

    A missing actor, or an actor with no light component, comes back as
    {"success": False, ...} naming the problem.
    """
    security.enforce_tier("get_light_properties")
    actor_expr = _resolve(actor_name, default_class)
    inner = (
        "lc = c.get_editor_property('light_color')\n"
        "has_temp = hasattr(c, 'use_temperature')\n"
        + _out({
            "found": "True",
            "error": "None",
            "actor_name": "a.get_name()",
            "intensity": "c.get_editor_property('intensity')",
            "color": "[lc.r, lc.g, lc.b]",
            "has_temperature": "has_temp",
            "temperature": "c.get_editor_property('temperature') if has_temp else None",
        })
    )
    body = _body(actor_expr, actor_name, default_class, "LightComponentBase", inner)
    payload = get_bridge().run_python(guarded(body))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not read light properties")
    return {"success": True,
            "actor_name": payload.get("actor_name"),
            "intensity": payload.get("intensity"),
            "color": payload.get("color"),
            "has_temperature": payload.get("has_temperature"),
            "temperature": payload.get("temperature")}


def set_exponential_fog_params(
    actor_name: str | None = None,
    fog_density: float | None = None,
    fog_height_falloff: float | None = None,
    volumetric_fog: bool | None = None,
    fog_max_opacity: float | None = None,
) -> dict:
    """
    Sets height-fog parameters on the level's ExponentialHeightFog actor.
    Defaults to that actor by class, since its object name carries a UAID
    suffix. fog_density is a global multiplier; fog_max_opacity 0 disables
    fog contribution entirely, 1 is fully opaque at distance.
    """
    security.enforce_tier("set_exponential_fog_params")

    parts: list[str] = []
    if fog_density is not None:
        parts.append(f"c.set_editor_property('fog_density', {float(fog_density)!r})")
    if fog_height_falloff is not None:
        parts.append(
            f"c.set_editor_property('fog_height_falloff', {float(fog_height_falloff)!r})"
        )
    if volumetric_fog is not None:
        parts.append(f"c.set_editor_property('enable_volumetric_fog', {bool(volumetric_fog)!r})")
    if fog_max_opacity is not None:
        parts.append(
            f"c.set_editor_property('fog_max_opacity', {float(fog_max_opacity)!r})"
        )
    if not parts:
        return {
            "success": False,
            "error": "Nothing to do: pass at least one fog parameter.",
        }

    actor_expr = _resolve(actor_name, "ExponentialHeightFog")
    component = actor_component(actor_expr, "ExponentialHeightFogComponent")
    inner = (
        _apply_to_component(component, parts)
        + "\n"
        + _out({"found": "True", "error": "None", "actor_name": "a.get_name()"})
    )
    payload = get_bridge().run_python(guarded(
        _body(actor_expr, actor_name, "ExponentialHeightFog",
              "ExponentialHeightFogComponent", inner)
    ))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not set fog parameters")
    return _ok(payload,
               fog_density=fog_density,
               fog_height_falloff=fog_height_falloff,
               volumetric_fog=volumetric_fog,
               fog_max_opacity=fog_max_opacity)


def get_exponential_fog_params(actor_name: str | None = None) -> dict:
    """Reads the height-fog component's current parameters."""
    security.enforce_tier("get_exponential_fog_params")
    actor_expr = _resolve(actor_name, "ExponentialHeightFog")
    inner = _out({
        "found": "True",
        "error": "None",
        "actor_name": "a.get_name()",
        "fog_density": "c.get_editor_property('fog_density')",
        "fog_height_falloff": "c.get_editor_property('fog_height_falloff')",
        "volumetric_fog": "c.get_editor_property('enable_volumetric_fog')",
        "fog_max_opacity": "c.get_editor_property('fog_max_opacity')",
    })
    payload = get_bridge().run_python(guarded(
        _body(actor_expr, actor_name, "ExponentialHeightFog",
              "ExponentialHeightFogComponent", inner)
    ))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not read fog parameters")
    return {"success": True,
            "actor_name": payload.get("actor_name"),
            "fog_density": payload.get("fog_density"),
            "fog_height_falloff": payload.get("fog_height_falloff"),
            "volumetric_fog": payload.get("volumetric_fog"),
            "fog_max_opacity": payload.get("fog_max_opacity")}


def set_sky_atmosphere_params(
    actor_name: str | None = None,
    atmosphere_height: float | None = None,
    ground_albedo: tuple[float, float, float] | None = None,
    rayleigh_scattering: tuple[float, float, float] | None = None,
) -> dict:
    """
    Sets SkyAtmosphere parameters on the level's SkyAtmosphere actor.
    atmosphere_height is in kilometers. ground_albedo and rayleigh_scattering
    are (r, g, b) floats; see the module docstring for why the two take
    different color classes.
    """
    security.enforce_tier("set_sky_atmosphere_params")

    parts: list[str] = []
    if atmosphere_height is not None:
        parts.append(
            f"c.set_editor_property('atmosphere_height', {float(atmosphere_height)!r})"
        )
    if ground_albedo is not None:
        r, g, b = ground_albedo
        parts.append(
            f"c.set_editor_property('ground_albedo', "
            f"{_color_expr(r, g, b, 255, 'Color')})"
        )
    if rayleigh_scattering is not None:
        r, g, b = rayleigh_scattering
        parts.append(
            f"c.set_editor_property('rayleigh_scattering', "
            f"{_color_expr(r, g, b, 1.0, 'LinearColor')})"
        )
    if not parts:
        return {
            "success": False,
            "error": "Nothing to do: pass at least one atmosphere parameter.",
        }

    actor_expr = _resolve(actor_name, "SkyAtmosphere")
    component = actor_component(actor_expr, "SkyAtmosphereComponent")
    inner = (
        _apply_to_component(component, parts)
        + "\n"
        + _out({"found": "True", "error": "None", "actor_name": "a.get_name()"})
    )
    payload = get_bridge().run_python(guarded(
        _body(actor_expr, actor_name, "SkyAtmosphere", "SkyAtmosphereComponent", inner)
    ))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not set atmosphere parameters")
    return _ok(payload,
               atmosphere_height=atmosphere_height,
               ground_albedo=ground_albedo,
               rayleigh_scattering=rayleigh_scattering)


def get_sky_atmosphere_params(actor_name: str | None = None) -> dict:
    """Reads the SkyAtmosphere component's current parameters, channels by name."""
    security.enforce_tier("get_sky_atmosphere_params")
    actor_expr = _resolve(actor_name, "SkyAtmosphere")
    inner = (
        "ga = c.get_editor_property('ground_albedo')\n"
        "rs = c.get_editor_property('rayleigh_scattering')\n"
        + _out({
            "found": "True",
            "error": "None",
            "actor_name": "a.get_name()",
            "atmosphere_height": "c.get_editor_property('atmosphere_height')",
            "ground_albedo": "[ga.r, ga.g, ga.b]",
            "rayleigh_scattering": "[rs.r, rs.g, rs.b]",
        })
    )
    payload = get_bridge().run_python(guarded(
        _body(actor_expr, actor_name, "SkyAtmosphere", "SkyAtmosphereComponent", inner)
    ))
    if not payload.get("found"):
        return _fail(actor_name, payload, "could not read atmosphere parameters")
    return {"success": True,
            "actor_name": payload.get("actor_name"),
            "atmosphere_height": payload.get("atmosphere_height"),
            "ground_albedo": payload.get("ground_albedo"),
            "rayleigh_scattering": payload.get("rayleigh_scattering")}