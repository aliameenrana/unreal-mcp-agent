"""
MCP server entrypoint. Registers every tool from unreal_mcp.tools with an
MCPServer instance and runs it over stdio.

Run with: python -m unreal_mcp.server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .security import SecurityViolation
from .tools import blueprints, materials, python_exec, scene

mcp = MCPServer(
    name="unreal-mcp",
    description="Bridges an MCP client to a running Unreal Engine 5 editor via Python Remote Execution.",
)


def _wrap(func):
    """Turns a SecurityViolation into a normal tool-result error instead of an
    unhandled exception, so the model sees why a call was refused."""

    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SecurityViolation as exc:
            return {"success": False, "blocked_by_security": True, "error": str(exc)}

    wrapped.__name__ = func.__name__
    wrapped.__doc__ = func.__doc__
    return wrapped


# Scene
mcp.add_tool(_wrap(scene.spawn_actor))
mcp.add_tool(_wrap(scene.list_actors))
mcp.add_tool(_wrap(scene.get_scene_state))
mcp.add_tool(_wrap(scene.set_actor_transform))
mcp.add_tool(_wrap(scene.set_property))
mcp.add_tool(_wrap(scene.delete_actor))

# Materials
mcp.add_tool(_wrap(materials.create_material))
mcp.add_tool(_wrap(materials.create_material_instance))
mcp.add_tool(_wrap(materials.set_material_scalar_parameter))
mcp.add_tool(_wrap(materials.set_material_vector_parameter))

# Blueprints
mcp.add_tool(_wrap(blueprints.compile_blueprint))

# Escape hatch
mcp.add_tool(_wrap(python_exec.execute_python))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
