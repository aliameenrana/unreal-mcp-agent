"""
Thin wrapper around Epic's remote_execution.py, giving the rest of this
package a simple connect / run_python / disconnect surface.

Convention used throughout this project: every remote snippet we send ends
in an expression wrapped in `json.dumps(...)`, and we always run in
EVALUATE_STATEMENT mode. That means `run_python()` always gets back a JSON
string it can parse, regardless of whether the underlying Unreal value was a
string, a list, a bool, or whatever else. Avoids guessing at repr-vs-str
formatting differences between types.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from . import engine_locator


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
    One of these per MCP server process. Call connect() once at startup
    (or lazily on first tool call), then run_python() per tool call.
    """

    def __init__(self, discovery_timeout_seconds: float = 8.0) -> None:
        self._discovery_timeout = discovery_timeout_seconds
        self._remote_execution_module = None
        self._session = None
        self._connected_node_id: str | None = None

    def connect(self) -> str:
        """
        Starts discovery, waits for at least one Unreal Editor node to
        appear, and opens a command connection to it. Returns the node id
        connected to. Raises NoEditorFoundError if nothing answers in time.
        """
        if self._connected_node_id is not None:
            return self._connected_node_id

        remote_execution = engine_locator.load_remote_execution_module()
        self._remote_execution_module = remote_execution

        session = remote_execution.RemoteExecution()
        session.start()
        self._session = session

        deadline = time.monotonic() + self._discovery_timeout
        node_id = None
        while time.monotonic() < deadline:
            nodes = session.remote_nodes
            if nodes:
                node_id = nodes[0]["node_id"]
                break
            time.sleep(0.2)

        if node_id is None:
            session.stop()
            self._session = None
            raise NoEditorFoundError(
                "No running Unreal Editor instance answered the discovery "
                "broadcast within the timeout. Is the editor open, and is "
                "bRemoteExecution=True set in DefaultEngine.ini?"
            )

        session.open_command_connection(node_id)
        self._connected_node_id = node_id
        return node_id

    def disconnect(self) -> None:
        if self._session is not None:
            self._session.stop()
            self._session = None
        self._connected_node_id = None

    def run_raw(self, code: str, exec_mode: str | None = None) -> dict[str, Any]:
        """
        Runs arbitrary code (statement or multi-line file-style script) and
        returns the raw command_result dict, unparsed. Used only by the
        execute_python escape hatch, which can't assume the caller wrapped
        their code in json.dumps(...). Named tools should use run_python()
        instead, which enforces that convention.
        """
        self.connect()
        remote_execution = self._remote_execution_module
        assert self._session is not None

        mode = exec_mode or remote_execution.MODE_EXEC_FILE
        return self._session.run_command(code, unattended=True, exec_mode=mode)

    def run_python(self, expression: str) -> Any:
        """
        Evaluates a single Python expression inside the connected Unreal
        Editor and returns the decoded JSON value. The caller is responsible
        for wrapping the expression in json.dumps(...) on the remote side,
        e.g. run_python("__import__('json').dumps(1 + 1)") -> 2.
        """
        self.connect()
        remote_execution = self._remote_execution_module
        assert self._session is not None

        data = self._session.run_command(
            expression,
            unattended=True,
            exec_mode=remote_execution.MODE_EVAL_STATEMENT,
        )

        if not data.get("success"):
            raise RemoteCommandFailedError(
                f"Remote command failed: {data.get('result')!r}"
            )

        raw_result = data.get("result", "")
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
