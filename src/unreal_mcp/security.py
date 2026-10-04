"""
The bouncer.

This module sits between "the model decided to call a tool" and "a command
gets dispatched to Unreal." It is deliberately plain, deterministic code,
not another model call: an LLM-based check would inherit the same
prompt-injection surface it's meant to defend against. Hard blocks here are
static analysis, not judgment calls.

This is a best-effort layer, not a sandbox. Unreal's Python Remote Execution
has no sandboxing of its own, so nothing downstream of this file can save you
if a dangerous snippet gets past it. Keep the banned lists conservative
(reject more than strictly necessary) rather than clever.
"""

from __future__ import annotations

import ast
import enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Hard blocks for raw execute_python() calls
# ---------------------------------------------------------------------------

# Importing any of these from agent-supplied code is rejected outright.
# These are the modules that let code escape "doing things in Unreal" and
# start doing things to the operating system, the network, or the filesystem
# at large.
BANNED_MODULES = {
    "subprocess",
    "socket",
    "ctypes",
    "importlib",
    "multiprocessing",
    "pty",
    "shutil",
}

# Specific attribute/method names that are destructive regardless of which
# module they're called through. Matched by attribute name only (we don't
# attempt full call-graph resolution), so this is deliberately broad.
BANNED_CALL_NAMES = {
    "system",       # os.system
    "popen",        # os.popen
    "remove",       # os.remove
    "unlink",       # os.unlink / pathlib unlink
    "rmdir",        # os.rmdir
    "rmtree",       # shutil.rmtree
    "execv",
    "execve",
    "spawnl",
    "spawnv",
    "delete_asset",        # unreal.EditorAssetLibrary.delete_asset
    "delete_directory",    # unreal.EditorAssetLibrary.delete_directory
    "delete_loaded_asset",
    "quit_editor",
}

# Substrings that are never allowed inside a string literal in agent code.
# This is a heuristic, not a guarantee, since a string can be built
# dynamically (e.g. via concatenation) to dodge a substring check. It catches
# the common, unsophisticated case and is cheap to run.
#
# Traversal is matched as "../" (and its Windows spelling) rather than a bare
# "..": a bare two-dot substring also rejects innocent strings like "0..10" or
# "Foo..bar", which are not paths and carry no traversal risk.
DANGEROUS_STRING_SUBSTRINGS = (
    ".git",
    ".github",
    "../",
    "..\\",
    ".ssh",
    ".env",
)


class SecurityViolation(Exception):
    """Raised when a tool call or a raw Python snippet trips a hard block."""


def check_python_code(code: str) -> None:
    """
    Statically inspects a Python snippet before it is sent to Unreal.
    Raises SecurityViolation on the first thing it doesn't like.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SecurityViolation(f"Code does not parse: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in BANNED_MODULES:
                    raise SecurityViolation(f"Import of banned module '{alias.name}'.")

        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in BANNED_MODULES:
                raise SecurityViolation(f"Import from banned module '{node.module}'.")

        elif isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in BANNED_CALL_NAMES:
                raise SecurityViolation(f"Call to banned function/method '{name}'.")

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for bad in DANGEROUS_STRING_SUBSTRINGS:
                if bad in lowered:
                    raise SecurityViolation(
                        f"String literal contains disallowed pattern '{bad}': {node.value!r}"
                    )


# ---------------------------------------------------------------------------
# Filesystem scope enforcement for tools that take a path argument
# ---------------------------------------------------------------------------


def resolve_path_in_project(path_str: str, project_root: Path) -> Path:
    """
    Canonicalizes `path_str` and verifies it resolves inside `project_root`.
    Raises SecurityViolation otherwise. Use this for every tool argument that
    is, or contains, a filesystem path.
    """
    project_root = project_root.resolve()
    candidate = (project_root / path_str).resolve() if not Path(path_str).is_absolute() else Path(path_str).resolve()

    try:
        candidate.relative_to(project_root)
    except ValueError:
        raise SecurityViolation(
            f"Path '{path_str}' resolves to '{candidate}', which is outside the "
            f"project root '{project_root}'."
        ) from None

    return candidate


# ---------------------------------------------------------------------------
# Risk tiering for named tools
# ---------------------------------------------------------------------------


class RiskTier(enum.Enum):
    READ_ONLY = "read_only"       # always allowed
    CONSTRUCTIVE = "constructive"  # creates/modifies, allowed by default
    DESTRUCTIVE = "destructive"    # deletes/overwrites, requires confirm=True
    SYSTEM = "system"              # touches the OS/filesystem/network directly, blocked


# Known tools and their tier. Anything not listed here defaults to SYSTEM
# (i.e. blocked) out of caution, rather than silently allowed.
TOOL_RISK_TIERS: dict[str, RiskTier] = {
    "list_actors": RiskTier.READ_ONLY,
    "get_scene_state": RiskTier.READ_ONLY,
    "spawn_actor": RiskTier.CONSTRUCTIVE,
    "set_actor_transform": RiskTier.CONSTRUCTIVE,
    "set_property": RiskTier.CONSTRUCTIVE,
    "get_property": RiskTier.READ_ONLY,
    "asset_exists": RiskTier.READ_ONLY,
    "get_material_parameter_list": RiskTier.READ_ONLY,
    "set_material_texture_parameter": RiskTier.CONSTRUCTIVE,
    "add_component": RiskTier.CONSTRUCTIVE,
    "remove_component": RiskTier.DESTRUCTIVE,
    "set_component_property": RiskTier.CONSTRUCTIVE,
    "list_components": RiskTier.READ_ONLY,
    "get_mesh_bounds": RiskTier.READ_ONLY,
    "set_mesh_lods": RiskTier.DESTRUCTIVE,
    "duplicate_actor": RiskTier.CONSTRUCTIVE,
    "set_mesh_material_slot": RiskTier.CONSTRUCTIVE,
    "get_mesh_material_slot": RiskTier.READ_ONLY,
    "create_material": RiskTier.CONSTRUCTIVE,
    "create_material_instance": RiskTier.CONSTRUCTIVE,
    "set_material_scalar_parameter": RiskTier.CONSTRUCTIVE,
    "set_material_vector_parameter": RiskTier.CONSTRUCTIVE,
    "compile_blueprint": RiskTier.CONSTRUCTIVE,
    "set_light_properties": RiskTier.CONSTRUCTIVE,
    "set_sky_atmosphere_params": RiskTier.CONSTRUCTIVE,
    "set_exponential_fog_params": RiskTier.CONSTRUCTIVE,
    "get_light_properties": RiskTier.READ_ONLY,
    "get_sky_atmosphere_params": RiskTier.READ_ONLY,
    "get_exponential_fog_params": RiskTier.READ_ONLY,
    "light_scene_preset": RiskTier.CONSTRUCTIVE,
    "set_dressing_pass": RiskTier.CONSTRUCTIVE,
    "apply_material_variant_set": RiskTier.CONSTRUCTIVE,
    "delete_actor": RiskTier.DESTRUCTIVE,
    "delete_asset": RiskTier.DESTRUCTIVE,
    "execute_python": RiskTier.SYSTEM,  # gated separately via check_python_code, not blocked outright
}


def classify_tool(tool_name: str) -> RiskTier:
    return TOOL_RISK_TIERS.get(tool_name, RiskTier.SYSTEM)


def enforce_tier(tool_name: str, confirm: bool = False) -> None:
    """
    Call at the top of every tool function. Raises SecurityViolation if the
    call isn't allowed to proceed.
    """
    tier = classify_tool(tool_name)
    if tier == RiskTier.DESTRUCTIVE and not confirm:
        raise SecurityViolation(
            f"'{tool_name}' is a destructive action and requires confirm=True."
        )
    if tier == RiskTier.SYSTEM and tool_name != "execute_python":
        raise SecurityViolation(f"'{tool_name}' is not a recognized tool and is blocked by default.")
