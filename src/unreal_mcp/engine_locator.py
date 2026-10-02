"""
Finds a local Unreal Engine install and loads Epic's own `remote_execution.py`
client directly from it.

We deliberately do not vendor a copy of that file in this repo: it ships as
part of the Unreal Engine install and is covered by Epic's EULA, not ours to
redistribute. Loading it from the user's own install, at runtime, is the
correct way to depend on it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

_RELATIVE_MODULE_PATH = (
    "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py"
)

# Common install locations to try before giving up and asking the user.
_CANDIDATE_ROOTS = [
    "/Users/Shared/Epic Games",
    os.path.expanduser("~/Epic Games"),
    "C:/Program Files/Epic Games",
]


class EngineNotFoundError(RuntimeError):
    pass


def _find_engine_root() -> Path:
    """
    Returns the root of an installed UE5 engine (the directory containing
    `Engine/`), preferring UNREAL_ENGINE_ROOT if set.
    """
    env_override = os.environ.get("UNREAL_ENGINE_ROOT")
    if env_override:
        candidate = Path(env_override)
        if (candidate / _RELATIVE_MODULE_PATH).is_file():
            return candidate
        raise EngineNotFoundError(
            f"UNREAL_ENGINE_ROOT={env_override!r} does not contain a valid engine "
            f"(expected {_RELATIVE_MODULE_PATH} under it)."
        )

    for root in _CANDIDATE_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        # e.g. /Users/Shared/Epic Games/UE_5.8
        for child in sorted(root_path.glob("UE_*"), reverse=True):
            if (child / _RELATIVE_MODULE_PATH).is_file():
                return child

    raise EngineNotFoundError(
        "Could not find an Unreal Engine install with the Python Script Plugin. "
        "Set the UNREAL_ENGINE_ROOT environment variable to your engine root "
        "(the folder containing 'Engine/'), e.g. '/Users/Shared/Epic Games/UE_5.8'."
    )


_loaded_module: types.ModuleType | None = None


def load_remote_execution_module() -> types.ModuleType:
    """
    Dynamically imports Epic's remote_execution.py from the local engine
    install and returns it as a module object. Cached after first call.
    """
    global _loaded_module
    if _loaded_module is not None:
        return _loaded_module

    engine_root = _find_engine_root()
    module_path = engine_root / _RELATIVE_MODULE_PATH

    spec = importlib.util.spec_from_file_location("ue_remote_execution", module_path)
    if spec is None or spec.loader is None:
        raise EngineNotFoundError(f"Found {module_path} but could not load it as a module.")

    module = importlib.util.module_from_spec(spec)
    sys.modules["ue_remote_execution"] = module
    spec.loader.exec_module(module)

    _loaded_module = module
    return module
