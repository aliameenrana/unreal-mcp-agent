"""
The raw escape hatch. Every call here goes through the bouncer first
(security.check_python_code). This is the one tool where "general purpose"
and "dangerous" overlap most directly, so prefer the named tools wherever
one exists and reserve this for gaps.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge


def execute_python(code: str) -> dict:
    """
    Runs a Python script inside the Unreal Editor and returns whatever it
    printed plus whether it succeeded. Unlike the named tools, this does not
    assume your code returns JSON, since it may be a multi-statement script
    rather than a single expression.
    """
    security.check_python_code(code)
    raw = get_bridge().run_raw(code)
    return {
        "success": bool(raw.get("success")),
        "result": raw.get("result"),
        "output": raw.get("output"),
    }
