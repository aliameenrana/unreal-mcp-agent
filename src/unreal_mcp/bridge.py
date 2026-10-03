"""
Thin wrapper around Unreal's Remote Control API (HTTP), giving the rest of
this package a simple connect / run_python / disconnect surface.

Originally written against Unreal's built-in Python Remote Execution
protocol (UDP multicast discovery + TCP). That protocol's multicast
discovery did not work reliably (see README) even with the editor-side
setting correctly enabled, so this talks to the Remote Control API instead:
a plain HTTP server the editor exposes on localhost, calling
PythonScriptLibrary.ExecutePythonCommandEx by object path. Requires the
editor-side setup documented in README.md (Remote Control API plugin,
Enable Remote Python Execution, PythonScriptLibrary on the allowlist).

Convention used throughout this project: every remote snippet we send ends
in an expression wrapped in `json.dumps(...)`, and we always run in
EvaluateStatement mode. That means `run_python()` always gets back a JSON
string it can parse, regardless of whether the underlying Unreal value was a
string, a list, a bool, or whatever else. Avoids guessing at repr-vs-str
formatting differences between types.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from typing import Any

import requests

_PYTHON_LIBRARY_OBJECT_PATH = "/Script/PythonScriptPlugin.Default__PythonScriptLibrary"


class BridgeError(RuntimeError):
    pass


class NoEditorFoundError(BridgeError):
    pass


class RemoteCommandFailedError(BridgeError):
    pass


@dataclass
class CommandResult:
    success: bool
    raw_result: str
    output: list[dict[str, Any]]


class UnrealBridge:
    """
    One of these per MCP server process. Connection is just an HTTP base
    URL; there's no persistent session to hold open, so connect() only
    does a reachability check.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 30010,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._base_url = f"http://{host}:{port}"
        self._timeout = timeout_seconds
        self._connected = False

    def connect(self) -> str:
        """
        Confirms the editor's Remote Control web server is reachable.
        Returns the base URL connected to. Raises NoEditorFoundError if
        nothing answers.
        """
        if self._connected:
            return self._base_url

        try:
            # Any 2xx/4xx response means something is listening; a 404 on
            # the bare root is expected and still proves the server is up.
            requests.get(self._base_url, timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            raise NoEditorFoundError(
                "No running Unreal Editor instance answered on "
                f"{self._base_url}. Is the editor open, and is the Remote "
                "Control API plugin enabled? See README.md setup steps."
            ) from exc

        self._connected = True
        return self._base_url

    def disconnect(self) -> None:
        self._connected = False

    def _call_execute_python_command_ex(self, code: str) -> dict[str, Any]:
        self.connect()
        payload: dict[str, Any] = {
            "objectPath": _PYTHON_LIBRARY_OBJECT_PATH,
            "functionName": "ExecutePythonCommandEx",
            "parameters": {
                "PythonCommand": code,
                "ExecutionMode": "EvaluateStatement",
            },
            "generateTransaction": False,
        }
        passphrase = os.environ.get("UNREAL_RC_PASSPHRASE")
        if passphrase:
            payload["Passphrase"] = passphrase

        response = requests.put(
            f"{self._base_url}/remote/object/call",
            json=payload,
            timeout=self._timeout,
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise RemoteCommandFailedError(
                f"Non-JSON response from Unreal (HTTP {response.status_code}): "
                f"{response.text!r}"
            ) from exc

        if response.status_code != 200:
            raise RemoteCommandFailedError(
                f"Remote Control call failed (HTTP {response.status_code}): "
                f"{data.get('errorMessage', data)!r}"
            )
        return data

    def run_raw(self, code: str, exec_mode: str | None = None) -> dict[str, Any]:
        """
        Runs arbitrary code (statement or multi-line file-style script) and
        returns the raw ExecutePythonCommandEx result dict, unparsed. Used
        only by the execute_python escape hatch, which can't assume the
        caller wrapped their code in json.dumps(...). Named tools should use
        run_python() instead, which enforces that convention.

        exec_mode is accepted for interface compatibility with the old
        remote_execution-based bridge but ignored: ExecuteStatement handles
        both single statements and multi-line scripts.
        """
        return self._call_execute_python_command_ex(code)

    def run_python(self, expression: str) -> Any:
        """
        Evaluates a single Python expression inside the connected Unreal
        Editor and returns the decoded JSON value. The caller is responsible
        for wrapping the expression in json.dumps(...) on the remote side,
        e.g. run_python("__import__('json').dumps(1 + 1)") -> 2.
        """
        data = self._call_execute_python_command_ex(expression)

        if not data.get("ReturnValue"):
            log = data.get("LogOutput", [])
            raise RemoteCommandFailedError(f"Remote command failed: {log!r}")

        raw_result = data.get("CommandResult", "")
        # CommandResult is Unreal's repr() of the Python return value, so a
        # string result arrives as e.g. "'[1, 2, 3]'" (repr-quoted), not raw
        # JSON. Unwrap that one repr layer before parsing.
        try:
            raw_result = ast.literal_eval(raw_result)
        except (ValueError, SyntaxError):
            pass
        try:
            return json.loads(raw_result)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RemoteCommandFailedError(
                f"Expected JSON back from Unreal, got: {raw_result!r}"
            ) from exc


# Module-level singleton. One editor connection per MCP server process is
# the right model here: the server is local, stdio-only, single user.
_bridge: UnrealBridge | None = None


def get_bridge() -> UnrealBridge:
    global _bridge
    if _bridge is None:
        _bridge = UnrealBridge()
    return _bridge
