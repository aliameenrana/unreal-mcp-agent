"""
MCP server entrypoint. Registers every tool from unreal_mcp.tools with an
MCPServer instance and runs it over stdio.

Run with: python -m unreal_mcp.server
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .security import SecurityViolation
from .tools import (
    animation,
    assets,
    blueprints,
    components,
    levels,
    material_graph,
    physics,
    play,
    textures,
    lighting,
    materials,
    meshes,
    presets,
    python_exec,
    scene,
)

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
mcp.add_tool(_wrap(scene.get_property))
mcp.add_tool(_wrap(scene.duplicate_actor))
mcp.add_tool(_wrap(scene.set_mesh_material_slot))
mcp.add_tool(_wrap(scene.get_mesh_material_slot))
mcp.add_tool(_wrap(scene.delete_actor))

# Materials
mcp.add_tool(_wrap(materials.create_material))
mcp.add_tool(_wrap(materials.create_material_instance))
mcp.add_tool(_wrap(materials.set_material_scalar_parameter))
mcp.add_tool(_wrap(materials.set_material_vector_parameter))
mcp.add_tool(_wrap(materials.set_material_texture_parameter))
mcp.add_tool(_wrap(materials.set_material_domain_and_shading_model))
mcp.add_tool(_wrap(materials.import_texture))

# Material graph authoring
for _fn in (
    material_graph.list_material_expressions,
    material_graph.get_material_inputs,
    material_graph.get_material_graph_stats,
    material_graph.get_material_used_textures,
    material_graph.find_material_parameter_usage,
    material_graph.create_material_expression,
    material_graph.create_material_parameter,
    material_graph.connect_material_expressions,
    material_graph.connect_material_input,
    material_graph.disconnect_material_input,
    material_graph.set_material_expression_property,
    material_graph.get_material_expression_property,
    material_graph.delete_material_expression,
    material_graph.delete_unused_material_expressions,
    material_graph.layout_material_graph,
    material_graph.recompile_material_graph,
    material_graph.create_material_function,
    material_graph.list_material_function_expressions,
    material_graph.create_material_function_expression,
    material_graph.connect_material_function_expressions,
    material_graph.set_material_function_expression_property,
    material_graph.get_material_function_expression_property,
    material_graph.layout_material_function,
    material_graph.delete_material_function_expression,
    material_graph.delete_all_material_function_expressions,
    material_graph.set_material_static_switch_parameter,
):
    mcp.add_tool(_wrap(_fn))
mcp.add_tool(_wrap(materials.get_material_parameter_list))

# Blueprints
mcp.add_tool(_wrap(blueprints.compile_blueprint))

# Components
mcp.add_tool(_wrap(components.add_component))
mcp.add_tool(_wrap(components.remove_component))
mcp.add_tool(_wrap(components.set_component_property))
mcp.add_tool(_wrap(components.list_components))

# Textures and material function calls
mcp.add_tool(_wrap(textures.generate_texture_from_pixels))
mcp.add_tool(_wrap(textures.set_texture_properties))
mcp.add_tool(_wrap(textures.get_texture_info))
mcp.add_tool(_wrap(textures.create_material_function_call))

# Collision and physics
mcp.add_tool(_wrap(physics.get_collision_state))
mcp.add_tool(_wrap(physics.set_collision_enabled))
mcp.add_tool(_wrap(physics.set_collision_profile))
mcp.add_tool(_wrap(physics.set_collision_object_type))
mcp.add_tool(_wrap(physics.set_collision_response))
mcp.add_tool(_wrap(physics.set_simulate_physics))
mcp.add_tool(_wrap(physics.apply_physics_impulse))

# Animation (instance level)
mcp.add_tool(_wrap(animation.set_animation))
mcp.add_tool(_wrap(animation.set_animation_mode))
mcp.add_tool(_wrap(animation.play_animation))
mcp.add_tool(_wrap(animation.stop_animation))
mcp.add_tool(_wrap(animation.pause_animation))
mcp.add_tool(_wrap(animation.set_play_rate))
mcp.add_tool(_wrap(animation.get_animation_state))

# Play-in-editor and console
mcp.add_tool(_wrap(play.get_play_state))
mcp.add_tool(_wrap(play.start_play_in_editor))
mcp.add_tool(_wrap(play.start_play_in_editor_simulate))
mcp.add_tool(_wrap(play.stop_play_in_editor))
mcp.add_tool(_wrap(play.wait_for_play_state))
mcp.add_tool(_wrap(play.execute_console_command))
mcp.add_tool(_wrap(play.get_console_variable))

# Levels and world
mcp.add_tool(_wrap(levels.list_levels))
mcp.add_tool(_wrap(levels.get_current_level))
mcp.add_tool(_wrap(levels.save_level))
mcp.add_tool(_wrap(levels.load_level))
mcp.add_tool(_wrap(levels.new_level))

# Scene graph / selection
mcp.add_tool(_wrap(scene.attach_actor))
mcp.add_tool(_wrap(scene.detach_actor))
mcp.add_tool(_wrap(scene.set_actor_folder))
mcp.add_tool(_wrap(scene.tag_actor))
mcp.add_tool(_wrap(scene.find_actors_by_tag))
mcp.add_tool(_wrap(scene.select_actors))
mcp.add_tool(_wrap(scene.get_selected_actors))

# Assets
mcp.add_tool(_wrap(assets.asset_exists))
mcp.add_tool(_wrap(assets.delete_asset))
mcp.add_tool(_wrap(assets.save_asset))

# Meshes
mcp.add_tool(_wrap(meshes.get_mesh_bounds))
mcp.add_tool(_wrap(meshes.set_mesh_lods))
mcp.add_tool(_wrap(meshes.get_mesh_collision_info))
mcp.add_tool(_wrap(meshes.set_mesh_collision_preset))
mcp.add_tool(_wrap(meshes.import_static_mesh))
mcp.add_tool(_wrap(meshes.import_skeletal_mesh))

# Lighting
mcp.add_tool(_wrap(lighting.set_light_properties))
mcp.add_tool(_wrap(lighting.set_sky_atmosphere_params))
mcp.add_tool(_wrap(lighting.set_exponential_fog_params))
mcp.add_tool(_wrap(lighting.get_light_properties))
mcp.add_tool(_wrap(lighting.get_sky_atmosphere_params))
mcp.add_tool(_wrap(lighting.get_exponential_fog_params))

# Presets (composite tools that call the primitives above in-process)
mcp.add_tool(_wrap(presets.light_scene_preset))
mcp.add_tool(_wrap(presets.set_dressing_pass))
mcp.add_tool(_wrap(presets.apply_material_variant_set))

# Escape hatch
mcp.add_tool(_wrap(python_exec.execute_python))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
