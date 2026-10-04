"""
First live test of the bridge, bypassing MCP entirely. Run this with the
Unreal Editor already open (UnrealProject/MCPTestProject.uproject, or any
project with bRemoteExecution=True). Talks straight to unreal_mcp.bridge, no
MCP client involved, so failures here are about the Unreal connection, not
the protocol plumbing on top of it.

Usage:
    source .venv/bin/activate
    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys

from unreal_mcp.bridge import NoEditorFoundError, RemoteCommandFailedError, get_bridge
from unreal_mcp.tools import scene


def main() -> int:
    bridge = get_bridge()

    print("Connecting to a running Unreal Editor...")
    try:
        node_id = bridge.connect()
    except NoEditorFoundError as exc:
        print(f"FAILED to connect: {exc}")
        return 1
    print(f"Connected to node: {node_id}")

    print("\nCalling list_actors()...")
    try:
        result = scene.list_actors()
    except RemoteCommandFailedError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: {result['count']} actors in the level.")
    for actor in result["actors"][:10]:
        print(f"  - {actor['label']} ({actor['class']})")

    print("\nCalling spawn_actor() for a StaticMeshActor at (0, 0, 200)...")
    try:
        spawn_result = scene.spawn_actor("/Script/Engine.StaticMeshActor", (0, 0, 200))
    except RemoteCommandFailedError as exc:
        print(f"FAILED: {exc}")
        return 1
    actor_name = spawn_result["actor_name"]
    print(f"OK: spawned {actor_name}")

    print("\nConfirming it shows up in list_actors()...")
    result = scene.list_actors()
    found = any(a["name"] == actor_name for a in result["actors"])
    print("OK: found it." if found else "UNEXPECTED: not found in the list.")

    print(f"\nCalling set_actor_transform() to move {actor_name} to (100, 200, 300)...")
    try:
        transform_result = scene.set_actor_transform(actor_name, location=(100, 200, 300))
    except RemoteCommandFailedError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: {transform_result}")

    print(f"\nCalling set_property() to set custom_time_dilation on {actor_name}...")
    try:
        prop_result = scene.set_property(actor_name, "custom_time_dilation", 2.5)
    except RemoteCommandFailedError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"OK: {prop_result}")

    print(f"\nCleaning up: deleting {actor_name}...")
    delete_result = scene.delete_actor(actor_name, confirm=True)
    print(f"OK: {delete_result}")

    print("\nAll good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
