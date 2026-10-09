# Tool Building Guide

How to implement one tool from PLAN.md's "Full tool catalog" at a time, for
this project. Follow this mechanically. It exists because guessing at
Unreal's Python API, or treating "the call didn't throw" as proof of
correctness, has already produced wrong, silently-broken tools in this
project (see "Incidents" below). The fix is a fixed sequence of steps, in
order, every time, no shortcuts.

This document assumes you already know how to read `bridge.py`,
`remote_snippets.py`, `security.py`, and the existing `tools/*.py` files. If
you have not read those four files in this session, read them before
continuing. Everything below refers to patterns established in them.

---

## The one rule everything else serves

**Never guess the shape of an Unreal Python API call. Look it up in the
official Unreal Python API docs first, then confirm it live against the
running editor, before writing the tool.** "Shape" means: the exact method
or property name, the argument order, whether arguments are positional or
need keywords, and which of the three access patterns (`setattr`,
`set_editor_property`, or a dedicated setter method) actually works for a
given property. All four of those have already been guessed wrong at least
once in this project — and in every case, the mistake was one that reading
the published docs page would have caught before any code was written. A
live probe with no documented ground truth to check it against is still
trial and error; it only proves a guessed call didn't throw, not that the
guess was right (a wrong field order in a constructor call is valid syntax
and throws nothing). Docs lookup is step 2a below; the live check in 2b
exists only to catch drift between the docs and this exact installed
build, not to replace reading the docs. A wrong guess shipped as a tool
costs a human finding it by eye, later, after it has already been
"verified" by a check that wasn't actually checking the right thing.

**Every tool, no exceptions, gets an independent read-back after the live
test call, against real editor state, not the tool's own return value.**
"The call returned success" and "I read back what I just wrote and it
matches" are both necessary and both insufficient on their own. See
"What counts as sufficient read-back" below — it is not the same check for
every kind of tool.

**Screenshots and viewport inspection are out of scope right now.** Not
because they don't matter, but because this phase is about deterministic
tools with programmatically-verifiable state. Visual QA is Phase 2's job
(the orchestrator / agentic layer, per PLAN.md's "Play-in-editor and
verification" section and the MetaHuman section's note that those tools
"depend on phase 4's screenshot tooling"). If you find yourself wanting to
take a screenshot to confirm a tool worked, that's a sign the tool's
read-back check is incomplete, not a sign you need a screenshot — go back
and find the state query that would catch the same problem without eyes on
a viewport.

---

## Tool inventory: built, planned, and out of scope, in one place

This is the map to read before diving into process detail or any per-domain
section below. It answers one question per domain: what's already built
(✅, cross-checked directly against `grep -rn "^def [a-z]" src/unreal_mcp/tools/*.py`,
125 functions total), what PLAN.md's catalog names but nobody has built yet
(📋), and what's been confirmed not buildable in pure Python (🚫, with the
one-line reason and a pointer to the fuller writeup elsewhere in this
document or in RESEARCH_NEW_DOMAINS.md). Tool names only here — the "how to
build it" detail lives in the per-domain section the back-reference points
to, so this table doesn't duplicate it.

**A conflict surfaced while building this table, flagged rather than
silently resolved:** PLAN.md's prose (the "Materials and textures" section)
describes `create_material_function` as living alongside the other Material
*Library* calls, but the actual file is `create_material_function` in
`tools/material_graph.py` (confirmed by grep) — PLAN.md's prose is
describing the right API but the wrong file boundary; not a functional bug,
just a stale cross-reference if anyone goes looking for it by filename.
Separately, `create_material_function_call` (singular node-insertion,
distinct from `create_material_function` the asset-scaffolder) lives in
`tools/textures.py`, not `tools/material_graph.py` — grep is ground truth
here, trust the file, not PLAN.md's section grouping, if the two disagree
on where something lives.

### scene.py (actors and scene graph)

✅ `spawn_actor`, `list_actors`, `get_scene_state`, `set_actor_transform`,
`set_property`, `get_property`, `duplicate_actor`, `delete_actor`,
`set_mesh_material_slot`, `get_mesh_material_slot`, `attach_actor`,
`detach_actor`, `set_actor_folder`, `tag_actor`, `find_actors_by_tag`,
`select_actors`, `get_selected_actors`

📋 `rename_asset`/`move_asset`/`duplicate_asset` (these are catalog-named
under "Asset management," not scene), generic `set_actor_parent_component`
(deliberately not built, see PLAN.md's own note), `group_actors`/`ungroup_actors`
(see 🚫)

🚫 `group_actors` / `ungroup_actors` — stub lists them on
`EditorActorSubsystem`, live object has neither. See PLAN.md's "Actors and
scene graph" section for the full finding.

📋 **`set_actor_label(actor_name, new_label)` — ready to build, no ambiguity.**
Found by RESEARCH_NEW_DOMAINS.md's catalog audit: every other actor-naming
surface in this project (`tag_actor`, `set_actor_folder`) has both a getter
and setter; labels only have `get_actor_label()`, read via `list_actors`/
`get_selected_actors`, with no matching setter anywhere in `scene.py`.

- **Exact call:** `AActor.set_actor_label(new_label, bMarkDirty=True)` —
  this is the direct Blueprint/Python-exposed counterpart to the already-used
  `get_actor_label()`, same class, same naming convention (both are plain
  methods on `Actor`, not `EditorActorSubsystem` calls, matching how
  `get_actor_label` is already called in `list_actors`).
- **Source:** `AActor::SetActorLabel` is documented C++/Blueprint API;
  confirm the Python-exposed signature (arg name/default for the dirty-flag
  parameter) via `dir(unreal.Actor)` before writing the tool — this project's
  own step-2a/2b discipline applies here same as anywhere else, this entry
  being "no ambiguity" means the target call and its purpose are unambiguous,
  not that the probe step is skippable.
- **No known gotcha.** Unlike `bHidden`/`hidden` (read-only trap) or
  `DirectionalLightComponent.intensity` (setattr-rejects, set_editor_property
  works), nothing in this project's prior research flags actor labels as
  having a read/write asymmetry at the API level — the asymmetry found here
  is purely "nobody built the setter tool yet," not an engine-side
  restriction.
- **Pattern to follow:** mirror `set_actor_folder`'s existing shape exactly
  (`find_actor_by_name` → call the setter → read back via `get_actor_label()`
  independently, not by trusting the setter's return) since it's the closest
  existing sibling tool for a single-string actor-naming property.
- **Security tier:** `CONSTRUCTIVE`, same tier as `set_actor_folder`/`tag_actor` — renaming
  a label is not destructive or irreversible.

✅ `get_skeletal_mesh_sockets` — built. See the verification caveat in its
docstring: both rejection paths and the read loop are confirmed live, but a
*populated* socket list is not, because this project has no SkeletalMesh asset
and none can be created from Python. `SkeletalMesh` has `num_sockets()` and
`get_socket_by_index(i)` but **no `get_all_socket_names`**, which is what
PLAN.md guessed, and the name lives on `socket_name`, not `name`.
`SkeletalMeshFromStaticMeshFactory` is the only conversion factory and has no
settable mesh attribute, so the populated case needs an .fbx the project lacks.

✅ `set_actor_label(actor_name, new_label)` — built and live-verified, closing
the asymmetry the catalog audit found (labels had a getter and no setter).
`Actor.set_actor_label(new_actor_label, mark_dirty=True)`; the first argument is
named `new_actor_label`, not `new_label`. The outliner label does not have to be
unique, and the Python-side object name is unaffected, which the tool reports as
`object_name_unchanged`.

### components.py

✅ `add_component`, `remove_component`, `list_components`,
`set_component_property`

(Built via `unreal.new_object`/`destroy_component`, not the
`add_component_by_class`/`destroy_component`-on-Actor PLAN.md originally
guessed — see PLAN.md's "Components" section for the corrected call shapes.)

### materials.py / material_graph.py / textures.py (materials, graph authoring, textures)

✅ Parameters (`materials.py`): `create_material`, `create_material_instance`,
`set_material_scalar_parameter`, `get_material_parameter_list`,
`set_material_texture_parameter`, `set_material_vector_parameter`,
`set_material_domain_and_shading_model`, `import_texture`

✅ Graph authoring (`material_graph.py`, 17 tools): `list_material_expressions`,
`get_material_inputs`, `get_material_graph_stats`, `get_material_used_textures`,
`find_material_parameter_usage`, `create_material_expression`,
`delete_material_expression`, `delete_unused_material_expressions`,
`layout_material_graph`, `connect_material_expressions`,
`connect_material_input`, `disconnect_material_input`,
`set_material_expression_property`, `get_material_expression_property`,
`recompile_material_graph`, `create_material_parameter`,
`set_material_static_switch_parameter`

✅ Material functions (`material_graph.py`, 9 tools): `create_material_function`,
`list_material_function_expressions`, `create_material_function_expression`,
`delete_material_function_expression`, `layout_material_function`,
`delete_all_material_function_expressions`,
`connect_material_function_expressions`,
`set_material_function_expression_property`,
`get_material_function_expression_property`

✅ Textures (`textures.py`): `generate_texture_from_pixels`,
`set_texture_properties`, `get_texture_info`, `create_material_function_call`

🚫 Material-to-texture baking (`render_material_to_texture` / `bake_material_to_texture`)
— `KismetRenderingLibrary` is absent, `CanvasRenderTarget2D` exposes no usable
draw call. See "Texture2D" section below.

📋 `create_render_target` — same blocker as above.

### components-adjacent: meshes.py

✅ `get_mesh_bounds`, `set_mesh_lods`, `get_mesh_collision_info`,
`set_mesh_collision_preset`, `import_static_mesh`, `import_skeletal_mesh`

📋 `generate_lods` as originally named — not buildable as named; built
instead as `set_mesh_lods`, see "Meshes and geometry" in PLAN.md.
📋 `set_collision_complexity` as originally named — not buildable as named;
built instead as `get_mesh_collision_info`/`set_mesh_collision_preset`.

### lighting.py

✅ `set_light_properties`, `get_light_properties`,
`set_exponential_fog_params`, `get_exponential_fog_params`,
`set_sky_atmosphere_params`, `get_sky_atmosphere_params`

📋 `build_lighting` — lightmass build trigger + completion poll, named in
PLAN.md, not built.

### levels.py

✅ `list_levels`, `get_current_level`, `save_level`, `load_level`, `new_level`

📋 `stream_level`, `set_world_partition_region_loaded`, `get_level_bounds`

### play.py

✅ `get_play_state`, `start_play_in_editor`, `start_play_in_editor_simulate`,
`stop_play_in_editor`, `wait_for_play_state`, `execute_console_command`,
`get_console_variable`

📋 `capture_viewport_screenshot`, `get_output_log`,
`run_automation_test` (see Automation Tests section below for the newer,
better-sourced version of this)

### animation.py

✅ `set_animation`, `set_animation_mode`, `play_animation`, `stop_animation`,
`pause_animation`, `set_play_rate`, `get_animation_state`

📋 `create_animation_blueprint`, `add_anim_state`/`add_anim_transition`,
`import_animation_sequence`, `set_skeletal_mesh_physics_asset`,
`retarget_animation`

📋 **Morph Targets** — see dedicated section below.
📋 **Control Rig** — see dedicated section below.
📋 **socket discovery (`get_skeletal_mesh_sockets`)** — see dedicated
subsection below; shared dependency for 4 recipes in RECIPE_DESIGNS.md.

### physics.py

✅ `get_collision_state`, `set_collision_enabled`, `set_collision_profile`,
`set_collision_object_type`, `set_collision_response`,
`set_simulate_physics`, `apply_physics_impulse`

📋 `add_physics_constraint`, `create_physical_material`,
`simulate_physics_step`

### replication.py

✅ `get_replication_state`, `set_replication_flags`

🚫 Replication Graph configuration — `ReplicationGraphBase`/`ReplicationDriverBase`
absent from the Python API entirely (stub has exactly one `Replication`-prefixed
class, `ReplicationSystem`). See "Deadlock triage" section's "Corrected
survey" below.

📋 `set_property_replication`, `add_rep_notify_function`,
`set_actor_network_relevancy`, `create_game_mode`/`set_default_game_mode`,
`verify_replication_setup`

### data_tables.py

✅ `create_data_table`, `get_data_table_info`, `list_data_table_rows`,
`export_data_table` — create/read only, by design.

🚫 Row authoring (`fill_data_table_from_json_string`/CSV twin) — deadlocks
the editor over Remote Control. See "The game-thread deadlock" section
below.

📋 `create_struct_asset`/`create_enum_asset`

### blueprints.py

✅ `create_blueprint`, `get_blueprint_info`, `list_blueprint_graphs`,
`list_blueprint_functions`, `list_blueprint_events`, `list_blueprint_variables`,
`list_blueprint_event_dispatchers`, `compile_blueprint`

🚫 Node-level reading/writing via `BlueprintEditorLibrary`/raw `EdGraph.Nodes`
— protected property, nodes not addressable by object path. See "Blueprints:
structure is readable, node contents are not" below. **Corrected by
RESEARCH_NEW_DOMAINS.md — see the dedicated "Blueprint graph editing" section
below, this is the single highest-priority item in this whole document.**

📋 `add_blueprint_variable`, `get_blueprint_compile_errors`,
`set_blueprint_parent_class`

### assets.py

✅ `asset_exists`, `delete_asset`, `save_asset`

📋 `import_asset` (generic dispatcher), `list_assets_in_path`,
`rename_asset`/`move_asset`/`duplicate_asset`,
`get_asset_references`/`get_asset_dependencies`, `fix_up_redirectors`

### presets.py

✅ `light_scene_preset`, `set_dressing_pass`, `apply_material_variant_set`

📋 `build_and_test_pie`, `create_pickup_item`, `setup_basic_multiplayer_actor`,
`spawn_vfx_with_sound`, `batch_import_asset_folder`, plus the 9 recipes from
RECIPE_DESIGNS.md and the 4-5 generic-combination recipes — all catalogued
in PLAN.md's "Presets" section.

### python_exec.py

✅ `execute_python` — the escape hatch, MVP.

### Niagara / VFX — instance level built, asset creation blocked

✅ `spawn_niagara_system`, `set_niagara_parameter`,
`get_niagara_user_parameters` — all three built and live-verified. Four
signature mistakes got through, all of which the compile-only snippet audit
passed:

- `spawn_system_attached` takes a **SceneComponent, not an Actor**, and also
  wants an attach point name plus an `AttachLocation` enum. Passing the actor
  gave `TypeError: Failed to convert parameter 'attach_to_component'`.
- `Actor` has **no `get_root_component`** in the Python API, so
  `get_components_by_class(SceneComponent)` is the only route to a component.
- `attach_point_name` rejects Python `None`; it needs `""`, so `socket_name`
  defaults to an empty string rather than None.
- The parameter setter is `NiagaraComponent.set_float_parameter` and siblings,
  **not** `set_variable_*`, which writes instance simulation state instead.

Setting an unexposed parameter is silently ignored by Niagara, so
`set_niagara_parameter` reads the exposed list first and fails loudly with
`known_parameters` attached. `get_niagara_user_parameters` had to reach through
the function library because `NiagaraSystem` exposes no parameter accessor at
all. Verified against `/Niagara/VectorFields/VectorFieldVisualizationSystem`,
which has 9 exposed parameters, covering the bool, int, float and vector setters.

🚫 `create_niagara_system_asset` — **not buildable.**
`NiagaraSystemFactoryNew` accepts `create_asset` and returns a `NiagaraSystem`,
but the package will not save and `load_asset` returns None afterwards. Engine
plugin content under `/Niagara` is the only usable source of systems.

🚫 `add_niagara_emitter_from_template` — not buildable. `NiagaraSystem` exposes
no emitter handle list or adder; `emitter_handles` is unreachable from Python,
and populating an emitter needs the graph authoring confirmed out of scope below.

🚫 `set_niagara_renderer_material` — not buildable. `NiagaraEmitter` has a
`renderer_bindings` property and no public accessor for the renderer list.

🚫 Niagara module/script graph authoring (node-by-node) — confirmed not
buildable, see "Niagara Graph-Level Authoring" below.

### Audio — no module yet, all planned

📋 `import_sound_wave`, `create_sound_cue`, `play_sound_at_location` (PIE-only),
`set_sound_attenuation_settings`

### UMG / UI — no module yet, all planned

📋 `create_widget_blueprint`, `add_widget_to_viewport` (PIE-only),
`set_widget_text`, `set_widget_visibility` — all three primitives
`spawn_hud` (RECIPE_DESIGNS.md) depends on; none built, widget-tree
child-resolution mechanics unconfirmed. See RECIPE_DESIGNS.md's own honesty
check on `spawn_hud`.

### Landscape and foliage — no module yet

📋 `add_foliage_type`/`paint_foliage_instances` buildable in part — see
"Foliage: the read side is reachable, creating a FoliageType is not" below.

🚫 `sculpt_landscape_heightmap`/`paint_landscape_layer` — Landscape editing
genuinely unavailable, no Subsystem/Library/Editor/Utils entry point exists
anywhere across all 44 live Landscape classes. See "Corrected survey" below.

### Sequencer / cinematics — no module yet

📋 `create_level_sequence`, `add_actor_to_sequence`/`add_camera_cut_track`,
`add_keyframe`, `render_sequence_to_movie` — tracks/sections/keyframing are
real and drafted; binding tools are not.

🚫 `add_possessable`/`add_spawnable_from_class` — hard-hangs the editor's
game thread, confirmed by repeated measurement. Never call. See
"add_possessable hard-hangs the editor" below.

### MetaHuman — no module yet

📋 Entire domain catalogued in PLAN.md, deliberately deferred until PIE
screenshot infrastructure exists.

### Control Rig — new domain, no module yet

📋 `unreal.ControlRigBlueprint`/`RigVMController`/`RigHierarchyController` —
BUILDABLE. See dedicated "Control Rig" section below.

### Automation Tests — new domain, no module yet

📋 `unreal.PythonTestRunner` — BUILDABLE. See dedicated "Automation Tests"
section below.

### GAS inspection — new domain, no module yet

📋 `get_ability_system_state` (read-only) — PARTIALLY BUILDABLE, narrow
scope only. See dedicated "GAS (read-only inspection)" section below.

### Blueprint graph editing (`unreal.BlueprintGraphEditor`) — correction, no module yet

📋 **Highest-priority item in this document.** See dedicated section below,
placed immediately after this inventory for prominence.

### Morph Targets — new domain, no module yet

📋 `unreal.MorphTarget` discovery — PARTIALLY BUILDABLE. See dedicated
"Morph Targets" section below.

### Confirmed out of scope, no further work planned

🚫 Animation Mixer (Sequencer mixer track authoring) — no Python binding
found. See "Animation Mixer" section below.
🚫 Niagara graph-level authoring — see "Niagara Graph-Level Authoring" below.
🚫 Gizmo control — no Python binding found. See "Gizmo Control" section
below.

---

## Blueprint graph editing: BUILDABLE. The "needs C++" verdict was wrong.

**This section replaces the earlier "correction" that said the domain was
unconfirmed. It is now confirmed working, and 13 tools are built and
live-verified in `src/unreal_mcp/tools/blueprint_graph.py`.**

The long-standing conclusion in both this guide and PLAN.md was that Blueprint
graph node wiring needs the C++ escape hatch, because `EdGraph.Nodes` is a
protected property and nodes are not addressable by object path. That reasoning
was correct *about the `BlueprintEditorLibrary` surface* and wrong about the
domain: `unreal.BlueprintGraphEditor` is a different class, it exposes a full
node-authoring API, and the node and pin handles it returns are ordinary Python
objects. The load-bearing probe, run before writing any tool:

1. `unreal.BlueprintGraphEditor` exists live (11240 symbols in `dir(unreal)`).
2. `get_graph_editor_by_name(bp, "EventGraph")` returns an editor object.
3. `list_all_nodes()` returns real `K2Node_*` handles (`K2Node_CustomEvent` in
   the probe), not opaque references.
4. `node.list_all_pins()` and `pin.get_pin_type_as_json_schema()` work, so pin
   identity, direction and type are all readable.

Handle quality was the open question and it is answered yes: the handles support
`find_execute_pin`, `find_data_input_pin`, `get_node_title`, `get_node_pos`,
`get_node_size`, `get_node_category` and `error_msg`.

### What is built

`list_blueprint_graph_nodes`, `list_blueprint_available_nodes`,
`get_blueprint_compile_errors`, `add_blueprint_event_node`,
`add_blueprint_call_function_node`, `add_blueprint_variable_node`,
`add_blueprint_branch_node`, `add_blueprint_comment`,
`add_blueprint_member_variable`, `create_blueprint_function_graph`,
`set_blueprint_node_position`, `connect_blueprint_pins`,
`delete_blueprint_nodes`. 20/20 live checks pass.

`list_blueprint_graphs` in `blueprints.py` still reports `nodes_readable: False`,
and that is now a *deliberate* statement about that tool's own code path rather
than a claim about the engine. It reads `EdGraph.Nodes`; `blueprint_graph.py`
uses the working path.

### Traps measured on this API, all of which cost a live probe

- **Three link-related calls wedge the Remote Control request** with an empty
  log and no ReturnValue: `BlueprintGraphPin.try_create_connection`,
  `BlueprintGraphPinLibrary.try_create_connection`, and
  `BlueprintGraphPinLibrary.list_connected_pins`. Wiring goes through
  `destination_pin.assign(source_pin)`, which works. There is no `is_linked`, so
  a connection cannot be read back directly at all; `connect_blueprint_pins`
  verifies by compiling and diffing the error-bearing node titles before and
  after, which is a different API path entirely.
- **`BlueprintGraphPin.get_pin_direction` cannot pythonize its return value**
  (`ByteProperty` -> enum). Pin direction is derived from whether a pin appears
  in `node.list_input_pins()` instead.
- **An exec pin's `Name` is null**, so it stringifies as the literal `'None'`
  and cannot be addressed by name at all. A selector token of `exec` (or
  `<exec>`) routes to `find_execute_pin()`; this is what makes exec wiring
  possible.
- **Node positions are `IntPoint`, not `Vector2D`.** `set_node_pos` rejects a
  Vector2D and a Vector both, with `Failed to convert parameter 'pos'`. Node
  *sizes* are a Vector2D. Comment boxes do take a Vector2D position.
- **`Blueprint.NewVariables` is protected** and raises, so a member variable
  cannot be read back that way. `BlueprintEditorLibrary.list_member_variable_names`
  and `add_member_variable` are the working pair. (`get_member_variable_type`
  is on the same library; a bare `EdGraphPinType()` reads back as an integer
  regardless of what was requested, so types resolve via
  `get_basic_type_by_name`.)
- **`BlueprintEditorSubsystem` does not exist in this build.** The entry point
  is the static `BlueprintGraphEditor.get_graph_editor_by_name(blueprint,
  graph_name)`. Separately, `get_graph_editor(graph)` takes the graph alone, not
  the Blueprint and the graph.
- **`list_available_nodes` returns about 40,000** pipe-separated
  `Category|Subcategory|Action` names. The originating class is not in the
  string, so filtering by a library name such as `KismetSystemLibrary` correctly
  returns nothing; filter on the action (`PrintString`).

### The generalisable lesson

This is the **fourth** time in this project that a "confirmed absent" verdict
came from checking one class instead of searching for the sibling that owns the
function. The earlier three were `generate_lods`, the foliage classes, and the
Niagara classes. The cost of each was the same: a capability declared impossible
that was in fact sitting in the API. Step 2's five-step protocol already demanded
the sibling check and it was still not run for this domain until the domain was
named as highest priority. Treat any new "not available in Python" claim as
provisional until a `dir(unreal)` sweep and a sibling-class search have both
come up empty.


---

## Control Rig — BUILDABLE, strong real surface, confirm before building

Source: https://dev.epicgames.com/documentation/unreal-engine/control-rig-python-scripting-in-unreal-engine ,
https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/RigVMController ,
https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/ControlRigBlueprint

**What's CONFIRMED (read directly off docs pages):**
- `unreal.ControlRigBlueprint` is the asset class. `get_controller()` returns
  the graph controller; `get_hierarchy_controller()` returns the hierarchy
  controller; `get_available_rig_units()` lists what can be added.
- `unreal.RigVMController` (module: ControlRigDeveloper) is the real
  graph-mutation object:
  - Node creation: `add_unit_node()`, `add_variable_node()`,
    `add_comment_node()`, `add_branch_node()`, `add_if_node()`,
    `add_array_node()`, `add_template_node()`, `add_function_reference_node()`.
  - Wiring: `add_link(output_pin_path, input_pin_path, setup_undo_redo=True, print_python_command=False)`,
    `break_link()`, `break_all_links()`. Pins are addressed by string path
    (e.g. `"NodeA.Translation.X"`), which is notably **more** reachable than
    Blueprint's `EdGraphPinType` (which exposes no readable fields at all —
    see the Blueprints section below).
  - Pin values: `set_pin_default_value()`, `get_pin_default_value()`,
    `add_array_pin()`, `remove_array_pin()`.
  - Removal: `remove_node()`, `remove_exposed_pin()`.
  - `print_python_command=True` on these calls echoes back the exact Python
    call that reproduces a hand-performed edit — genuinely useful for step-2b
    confirmation, since the editor can generate the ground-truth call shape
    for any edit made by hand first, rather than guessing it blind.
- `unreal.RigHierarchyController` (module: ControlRigDeveloper) —
  `add_bone(name, parent, transform)` and other hierarchy-side element
  creation, a separate object from the graph controller.

**What still needs a live probe before writing a single tool:**
- `ControlRigBlueprint.get_controller()`'s exact return-object identity —
  confirm it is actually the same `RigVMController` class documented above,
  not a differently-scoped wrapper.
- Whether this installed 5.8 build even has the Control Rig plugin enabled
  (it's optional).
- `add_unit_node`'s first argument shape — RESEARCH_NEW_DOMAINS.md found two
  sources disagreeing slightly on wording (`script_struct, method_name,
  position` per one source vs. the RigVMController page's own listing);
  resolve this against the actual page or a live `help()` call, not by
  guessing which source is right.

**How to build this:** confirm `get_controller()`'s return type and the
`add_unit_node` signature live first (both are cheap, single-call probes
given `print_python_command=True` can echo the correct call shape back after
one hand-performed edit in the editor). Once confirmed, this is comparable
in richness to the material-graph tooling already built in this project
(named Controller object, string-addressed pins, real node/link CRUD) and
arguably *easier* to verify than materials, because pins are
string-addressable instead of needing the node-selector-by-title workaround
material nodes needed. Treat it with the same per-tool workflow as any other
domain: docs first (done above), live probe to confirm, then write the tool,
one at a time.

---

## Automation Tests — NOT buildable in UE 5.8 (corrected)

`RESEARCH_NEW_DOMAINS.md` ranked `unreal.PythonTestRunner` first across all eight
domains it surveyed, on the strength of a docs page. **It does not exist in this
engine version.** Neither does the `UAutomationTestToolset` AICallable path.

Measured, by the "enumerate, never guess a class list" method rather than by
guessing names:

- `hasattr(unreal, "PythonTestRunner")` is False.
- `grep -c "^class.*TestRunner\|^class.*Toolset\|^class PythonTest"` over the
  installed stub returns **0**.
- There are 7 `Automation`-prefixed classes. Only two are libraries:
  `AutomationLibrary` and `AutomationUtilsBlueprintLibrary`.
- `AutomationLibrary`'s whole surface is `add_test_error`, `add_test_info`,
  `add_test_warning`, `add_test_telemetry_data`, `are_automated_tests_running`
  and `set_test_telemetry_storage`. It can annotate a test someone else is
  already running; it cannot start, list or filter one.
- `AutomationUtilsBlueprintLibrary` exposes no test methods at all.
- No `*Subsystem` in `dir(unreal)` matches Automation, Test or Script.

So the step-5 verification entry for `run_automation_test` has no call to wrap.
This is the same failure shape as the Blueprint graph verdict, in the opposite
direction: a docs page was treated as settled and the live check never ran. The
"ranked first" claim is the fourth time a curated list in this project has been
wrong, and the two most recent times were both "available" rather than
"unavailable".


---

## GAS (Gameplay Ability System) — read-only inspection ONLY, narrow scope

**This is not "GAS is now buildable."** GAS *setup* — wiring an
AttributeSet, authoring GameplayEffects, authoring the AbilitySystemComponent
onto an actor from scratch, granting or activating abilities — remains a C++
job, confirmed, not changed by this finding. RECIPE_DESIGNS.md's framing
holds: "Anything described as a 'spell' or 'ability' in this document is a
VFX + audio + maybe a timed state flag, never a real ability graph with
cost, cooldown, or targeting logic."

**What's CONFIRMED, with a caveat on source quality:**
`unreal.AbilitySystemComponent` is a real, documented Python API class,
constructible (`outer: Object | None = None, name: Name | str = 'None'`),
exposing at least one read-write editor property, `activatable_abilities`
(a `GameplayAbilitySpecContainer`). Source:
https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AbilitySystemComponent —
**caveat: the direct fetch of this page 404'd in the research session; the
class's existence and the `activatable_abilities` property are sourced from
a search-engine snippet of that page, not a direct read.** This is the
weakest-sourced finding in the entire research report and needs
re-confirmation, more than any other domain here, before even a read-only
tool is written.

No `give_ability`/`activate_ability`-shaped Python method was found on this
class. The C++ side has `GiveAbility`
(https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/GameplayAbilities/UAbilitySystemComponent/GiveAbility);
no Python counterpart surfaced.

**What this means for the one tool worth building:** `get_ability_system_state(actor_name)`
— read-only, wrapping `get_editor_property('activatable_abilities')` and
whatever attribute-set properties a live probe turns up, using the exact
same `get_editor_property` mechanism `scene.get_property` already uses. This
tool only works on an actor that **already has GAS set up** by some other
(C++ or hand-authored) path. It is not a GAS-authoring tool, not an
ability-granting tool, and should never be described as either in its own
docstring.

**How to build this:** before writing anything, retry the direct docs fetch
or run `dir(unreal.AbilitySystemComponent)` against the live editor — this
is a mandatory re-confirmation given the weak source, not an optional
nicety. Once confirmed, build the single read-only tool named above, scoped
explicitly to inspection of an already-set-up actor.

---

## Morph Targets — PARTIALLY BUILDABLE, cheap to confirm

Sources: https://docs.unrealengine.com/5.0/en-US/PythonAPI/class/MorphTarget.html ,
https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/USkeletalMesh/GetMorphTargets ,
https://docs.unrealengine.com/4.27/en-US/API/Runtime/Engine/Engine/USkeletalMesh/K2_GetAllMorphTargetNames/index.html

**What's CONFIRMED:** `unreal.MorphTarget` is a real, documented Python API
class (inherits `unreal.Object`). On the engine side,
`USkeletalMesh.GetMorphTargets()` and the Blueprint-exposed
`K2_GetAllMorphTargetNames()` are real, confirmed C++/BlueprintAPI calls.

**What is NOT confirmed, and must not be upgraded to a flat assertion:**
whether `unreal.SkeletalMesh` (the Python wrapper, not the C++ class) exposes
the same accessor under a `get_morph_targets`-shaped name, per this guide's
own gotcha #13 ("don't assume a struct has the members its C++ equivalent
has" — exactly the failure mode that hid `get_component_location` from
`PrimitiveComponent`). Driving a morph target's weight at runtime
(`SetMorphTarget`/`ClearMorphTargets` in Blueprint/C++) is also unconfirmed
on `unreal.SkeletalMeshComponent`. Only 2 of the 5-step search protocol were
run for this domain (class-name confirm, docs-page read) — no `dir()` sweep,
no sibling-Library check was possible without editor access. This domain
should NOT be marked "confirmed absent" or "confirmed present" on docs
alone.

**How to build this:** run `dir(unreal.SkeletalMesh)` and
`dir(unreal.SkeletalMeshComponent)` against the live 5.8 editor for the
exact getter/setter names — this is a cheap, single-session probe that
answers most of the open question at once. If a `get_morph_targets`-shaped
name turns up, build the discovery tool first (listing morph target names on
an asset); weight-setting is a separate, second probe on the Component side
and may turn out narrower.

---

## Confirmed out of scope: Animation Mixer

Epic's "Anim Mixer" (Sequencer Animation Mixer) is an experimental UE 5.8
plugin built on the Unreal Animation Framework and Animation Blueprints. Its
runtime classes (`UMovieSceneAnimNextTargetSystem`, the
`MovieSceneAnimMixer`/`MovieSceneAnimMixerEditor` plugin pair) have no
Python API class page found after a real search (plugin docs, site search
on the python-api index, all came back C++-only). A real, adjacent Python
surface exists — `unreal.AnimNextAnimationGraphLibrary.add_animation_graph`
— but it is AnimNext graph assembly, a different and narrower capability,
not Sequencer animation mixing; do not build it under the "Animation Mixer"
label if it's ever picked up. Source:
https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AnimNextAnimationGraphLibrary ,
https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/MovieSceneAnimMixerEditor

---

## Confirmed out of scope: Niagara Graph-Level Authoring

`unreal.NiagaraPythonModule` exposes exactly two methods (`GetObject()`,
`Init()`) and is a thin wrapper around one already-existing module instance
already placed in an emitter's stack — it cannot create a module, create a
graph node, or wire a connection. The graph-holding types
(`UNiagaraScriptSourceBase`/`UNiagaraScriptSource`) have no Python API class
page at all. This confirms, more strongly than before, PLAN.md's existing
scope decision: instance-level control (spawn/parameterize/assemble from
existing modules and templates) is the correct ceiling; graph-level
authoring needs the C++ escape hatch if ever required. Source:
https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/NiagaraEditor/UNiagaraPythonModule ,
https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/NiagaraScript ,
https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/Niagara/UNiagaraScriptSourceBase

---

## Confirmed out of scope: Gizmo Control

No Python binding surfaced for the viewport gizmo framework
(`UEditorInteractiveGizmoManager`/`UInteractiveGizmoManager`) across a real
multi-angle search (class-name search, full page read, sibling-Library
check all came back negative; only the live `dir()` sweep, step 3 of 5,
could not be run without editor access). The viewport-gizmo interaction
model is inherently mouse-drag-driven anyway and doesn't map cleanly onto a
scripted call; `set_actor_transform` already covers the same end-state need
without needing gizmo interaction at all. Source:
https://dev.epicgames.com/documentation/unreal-engine/API/Editor/EditorInteractiveToolsFramework/UEditorInteractiveGizmoManager ,
https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/InteractiveToolsFramework/UInteractiveGizmoManager

---

## Per-tool workflow

Work one tool at a time, start to finish, before moving to the next. Do not
batch-write several tools and then test them all at once — if something's
wrong, you want to know which tool broke it.

### 1. Read the spec and sibling conventions

- Find the tool's line in `PLAN.md`'s "Full tool catalog." Note the domain
  section it's in, the named Unreal API it's supposed to wrap, and any
  caveat already written next to it (several lines already say things like
  "confirm against a live editor before committing to the exact call
  shape" — treat that as a direct instruction, not a throwaway remark).
- Open the existing file in `src/unreal_mcp/tools/` for that domain (or the
  closest existing domain if there's no file yet). Read every function in
  it. Match: docstring style, how `security.enforce_tier` is called, how
  `remote_snippets.seq()` and `json_dumps()` are used to build one
  expression, the shape of the returned dict (`success` key always present,
  domain-specific keys alongside it, `error` key on failure), and how actors
  are resolved (`find_actor_by_name`, not a bare name string spliced into
  Unreal code).
- If the tool needs a helper that doesn't exist yet in `remote_snippets.py`
  (e.g. a new subsystem accessor like `actor_subsystem()`), plan to add it
  there, not inline in the tool file — that file exists precisely so these
  accessors are written once.

### 2. Look up the real API shape before writing anything — docs first, live probe to confirm

This is the step that gets skipped under time pressure and is the single
biggest source of the failures this guide exists to prevent. Do it before
step 3, not after. It has two parts, in order. Neither one alone is
enough — read why at the end of this section.

**2a. Read the official Unreal Python API reference first.** For every
method, property, or constructor the tool needs, look it up at
`dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/<ClassName>`
(e.g. `.../python-api/class/MaterialEditingLibrary`,
`.../python-api/class/Color`) **before** writing a single line of tool
code or guessing a call shape from memory or from how a similarly-named
API works elsewhere. Confirm, from the docs page itself:

- The exact method/property name as Python exposes it (not the C++
  UPROPERTY name — the docs page shows the actual Python signature).
- The full argument list, in order, with types — including whether a
  constructor's fields are documented in a different order than you'd
  guess (this is exactly what would have caught the `unreal.Color` BGRA
  case: the docs page lists its fields as `b, g, r, a`, in that order,
  plainly, for anyone who opens the page before writing the call).
- Whether a property is marked read-only in its docs entry. If it is, stop
  looking for a `setattr`/`set_editor_property` path for it and search the
  same class's docs page for a dedicated setter method instead
  (`set_<thing>`-shaped).
- Match the docs to the engine version this project targets
  (`UNREAL_ENGINE_ROOT` / README). The Python API has changed across
  versions; a method present in the docs for one version may not exist, or
  may take different arguments, in another. If the installed engine's
  major.minor doesn't match the docs page you're reading, find the docs
  for the installed version specifically before trusting the signature.

**If the class or method isn't where you first looked, that is not
permission to conclude it doesn't exist.** "I checked one URL and it
wasn't there" is not a finding, it's an unfinished search. Before writing
"not available in Python" anywhere — in a tool docstring, in a status
report, in a decision to reach for the C++ escape hatch — you must have
done all five of the following, in order, and be able to say which one
actually resolved it (or that all five failed):

1. **Confirm the class name itself, not just the method.** Use the site
   search at `dev.epicgames.com/documentation/en-us/unreal-engine/python-api/`
   (there is a search box on the Python API index page) for the class name
   alone, with no method appended. A class is sometimes named differently
   in Python than you'd guess from its C++/Blueprint name (e.g. Blueprint
   editor functionality is split across `BlueprintEditorLibrary` and other
   `*Library` classes, not one single obviously-named class) — confirm you
   have the right class page before concluding anything about a method on
   it.
2. **Read the whole class page, not just ctrl-F for the method name.**
   Python API docs pages list every exposed method and property for that
   class on one page. Scroll the entire page. The method may be there
   under a name you didn't expect (different verb, different word order,
   abbreviated differently than you guessed) — matching by eye against the
   full list catches this; a single exact-string search does not.
3. **Search the engine's actual installed Python module, not just the
   website.** Against the live editor, run `dir(unreal.TheClassName)` and
   read every name in the result. This is the ground truth for what this
   specific installed build exposes, independent of whether the website's
   docs happen to be indexed, current, or reachable. If the method is in
   `dir()` output under a name close to what you expected, that's your
   answer — use that name, and don't also conclude "not in the docs" just
   because the website search missed it.
4. **Check the containing module/namespace for a sibling class that owns
   the function instead.** Unreal's Python API frequently puts an
   operation on a `*Library` or `*Subsystem` class rather than on the
   "obvious" object class itself (e.g. a property-of-an-actor operation
   living on `EditorActorSubsystem`, not on `Actor`). If the method isn't
   on the class you first checked, check the `*Library`/`*Subsystem`
   classes for the same domain before concluding the capability doesn't
   exist at all. `remote_snippets.py`'s existing helpers
   (`actor_subsystem()`, `asset_tools()`) are exactly this pattern already
   applied — look at what subsystem/library class neighboring, already-working
   tools in this project use for the same domain.
5. **Only after all four of the above have genuinely failed** — confirmed
   class exists, confirmed you read its full page, confirmed `dir()`
   doesn't show it under any plausible name, confirmed no sibling
   Library/Subsystem class owns it — do you get to write down "not found
   in the Python API as of probing" as a real finding. At that point,
   state explicitly which of the four checks you ran and what each one
   showed (not just "I looked and it wasn't there"), so the next person
   doesn't have to redo the same search blind. Then proceed to "When a
   probe reveals the plan doesn't work" below for what to do about it.

**2b. Then, confirm the documented shape against the live, running
editor** — this step exists to catch the gap between "what the docs say"
and "what this exact installed build actually does" (version drift,
undocumented behavior, a property that's read-only in practice despite
docs not saying so), not to replace 2a with trial and error. Using
`execute_python` (or a raw `bridge.run_python()` call from a throwaway
script) against the live editor:

- Run the smallest possible call that exercises exactly the signature you
  just read in the docs, and confirm it behaves as documented — right
  argument count accepted, right field lands in the right place, no
  exception.
- For a constructor: build it with the keyword arguments named in the docs
  (e.g. `unreal.Color(r=10, g=20, b=200, a=255)`), then read each field
  back by name and confirm it matches what the docs said that keyword
  controls. This is a confirmation of the documented shape, not a blind
  search for the shape — if the docs are right, this call is a formality
  that costs one round trip; if the installed build disagrees with the
  docs, this is what catches that, and it's a notable finding worth
  recording (see below).
- For a property: if the docs say it's writable, confirm `setattr` or
  `set_editor_property` (whichever the docs/convention implies) actually
  writes it on a disposable actor/asset created for exactly this purpose.
  If it rejects the write despite docs saying it's writable, that's a
  version-drift finding, not a sign to start guessing alternate forms —
  recheck the docs for the installed version specifically before trying
  anything else.
- If this step needed to create something disposable (a scratch actor, a
  temp asset), delete it immediately after, before writing the tool.
  Don't let debris linger into step 5's test.
- Write down (a comment in the tool file, or your working notes for this
  session) what the docs said and whether the live check matched it,
  especially if it didn't. The next person implementing a neighboring tool
  benefits from knowing where this project's installed build disagrees
  with Epic's published docs.

**Why both steps, and in this order:** a live probe alone — calling
something and seeing what happens, with no documented ground truth to
check it against — is still trial and error; it tells you that one
guessed invocation didn't throw, not that you've found the real contract.
That gap is exactly what produced the `unreal.Color` incident: nothing
was tried in that case that would have raised an error, because passing
`(102, 127, 178)` positionally is perfectly valid syntax that silently
assigns to the wrong fields. The official docs page states the field
order plainly; reading it first would have caught the mistake before any
code was written, for free. The live check in 2b exists only to catch
the narrower case the docs can't: this specific installed build behaving
differently than what's published.

If the docs and the live check agree with what the tool needs: good,
proceed to step 3. If either one disagrees with your assumption, or the
API isn't in the docs at all: see "When a probe reveals the plan doesn't
work" below before writing any tool code.

### 3. Write the tool function

- Match existing conventions exactly, not approximately:
  - One remote expression per tool call wherever possible, built with
    `seq(...)` when you need to do several things and return a confirmation
    value, wrapped in `json_dumps(...)`, exactly like every function in
    `scene.py`/`materials.py`/`blueprints.py` does.
  - Resolve actors/assets through the existing helpers
    (`find_actor_by_name`, `load_asset`, `asset_tools()`,
    `actor_subsystem()`), adding new helpers to `remote_snippets.py` if a
    genuinely new resource type needs resolving, rather than inlining a
    one-off lookup expression in the tool file.
  - Return `{"success": True, ...domain keys...}` on the happy path. On a
    caller error you can detect before dispatch (bad argument, unknown
    enum value — see `light_scene_preset`'s mood check in `presets.py` for
    the pattern), return `{"success": False, "error": "..."}` without
    calling the bridge at all. Let real bridge/Unreal-side failures raise
    (`RemoteCommandFailedError` etc.) rather than swallowing them into a
    dict — that matches how every existing tool behaves; the MCP layer
    above these functions is responsible for turning exceptions into a
    tool-call error response.
  - If the tool is destructive (deletes or overwrites something
    irreversible), give it a `confirm: bool = False` parameter and call
    `security.enforce_tier("tool_name", confirm=confirm)`, matching
    `delete_actor`.
  - Keep docstrings short and informational: what the tool does, what any
    non-obvious argument means (units, coordinate order, what a string
    identifier refers to). Don't restate the signature in prose.
- If step 2's probe revealed a quirk (wrong positional order, read-only
  property, access-pattern mismatch), encode the fix directly in the code
  and leave a one-line comment explaining why the non-obvious form is
  there — see the existing comment in `presets.py` above the `color_expr`
  line and the one above the `set_editor_property` call for the exact tone
  and length to match.

### 4. Register it in `security.py`

- Add an entry to `TOOL_RISK_TIERS` in `src/unreal_mcp/security.py`. Pick
  the tier honestly:
  - `READ_ONLY` — pure queries, nothing mutates (`list_actors`-equivalent
    for the new domain).
  - `CONSTRUCTIVE` — creates or modifies state but isn't destroying
    anything irreversible (most tools land here).
  - `DESTRUCTIVE` — deletes or overwrites something that can't be trivially
    recreated (`delete_actor`, `delete_asset`-equivalents). Requires the
    `confirm=True` parameter from step 3.
  - `SYSTEM` — do not assign new tools here; this tier is for
    `execute_python` and the "unrecognized tool, blocked by default" case.
    If a tool you're building genuinely needs OS/filesystem/network access
    beyond what the path allowlist and AST checks already permit, that's a
    sign it's out of scope for a named tool at all — stop and reread
    PLAN.md's security section rather than forcing a `SYSTEM`-tier entry.
- Forgetting this step means the tool is silently blocked (unlisted tools
  default to `SYSTEM`, which is blocked). If your live test in step 5 fails
  with a `SecurityViolation` about an unrecognized tool, this is almost
  always why.

### 5. Run a live test against the running editor

- With the Unreal Editor open and the Remote Control API reachable (same
  setup `scripts/smoke_test.py` and the README assume), call the actual
  tool function you just wrote, not the raw probe snippet from step 2.
  Either add a temporary call to `scripts/smoke_test.py`, or run it ad hoc
  from a Python shell with the venv active — either is fine, this is a
  live-verification step, not a permanent automated test.
- Confirm it returns the success shape you expect and doesn't raise.

### 6. Independent read-back — confirm the actual resulting state

This is the step that is most often done wrong, not skipped. Doing it wrong
looks like: calling a `get_*` tool that happens to read the exact same cache
or the exact same leaf value the `set_*` tool just wrote, and treating that
as proof. That is a round-trip check on the write path, not an independent
observation of editor state. See "What counts as sufficient read-back"
below for the real bar, broken out by tool category. In every case:

- Make a **separate** call (a different query, ideally through a different
  Unreal API path than the one the tool itself used) against the live
  editor, after the tool call from step 5 has completed.
- The read-back must inspect the specific thing the tool claims to have
  changed — not a nearby proxy for it.
- If the effect depends on supporting structure (a graph connection, a
  parent/child reference, an enabled flag, a non-empty node graph), the
  read-back must also confirm that structure exists and is wired correctly,
  not just that the leaf value you set is sitting there unused. See
  incident 2 below for exactly what this catches.
- If the read-back doesn't match what the tool claims: the tool is wrong.
  Go back to step 2 — probe more, don't patch the tool function with
  another guess.

### 7. Clean up

- Delete any actors, assets, or files created purely for steps 2, 5, or 6.
  Leave the editor and project in the state they were in before you
  started, aside from anything the tool is actually meant to leave behind
  for the *user's* benefit (which, during tool development, should still be
  nothing — you're testing the tool, not using it).
- Confirm cleanup worked with another quick read-back (e.g. `list_actors()`
  no longer shows the test actor). Don't assume a delete call succeeded
  just because it didn't throw — the same rule applies to your own test
  debris as to the tool under test.

### 8. Mark it done

- Only after steps 1–7 **and** the "Code quality bar" section below are
  satisfied for this specific tool. Update PLAN.md's catalog entry or
  README's status section if that's the project's convention for tracking
  (check how `create_material` etc. are marked "done (MVP)" in PLAN.md and
  follow the same style).
- Move to the next tool. Do not write two tools and test them together.

---

## Code quality bar

A tool that works in isolation but is wired up wrong, inconsistent with its
siblings, or silently narrower than its own docstring claims is not done —
it's a new bug waiting to be found later, by someone who trusts it because
it passed steps 1–7. This section exists because a real review of this
project's own early preset tools found exactly these problems, all at once,
in code that had already been "live-verified": the tools worked when called
directly, and were still completely unreachable from the actual MCP server,
untested, and silently narrower than the spec they claimed to implement.
Being correct when poked directly and being done are not the same claim.
Check every item below before step 8.

**The tool must actually be reachable.** A tool living in
`src/unreal_mcp/tools/your_module.py` does nothing for an MCP client unless
`server.py` imports the module and registers the tool with
`mcp.add_tool(...)`, matching how every existing tool is wired up there. Open
`server.py` and confirm your new tool's name appears in both the import line
and an `add_tool` call. A tool that works when called directly from a Python
shell but was never added to `server.py` is not a smaller, acceptable version
of done — it's indistinguishable from not having built it at all, from the
one perspective that matters (an MCP client trying to call it).

**The tool's security tier must reference its own name, not a neighbor's.**
`security.enforce_tier("some_tool")` inside `some_tool`'s own function body
must pass the string `"some_tool"`, not the name of a similar existing tool
copy-pasted as a starting point. Two tools sharing a tier by accident today
(because both happen to be `CONSTRUCTIVE`) can silently diverge the moment
either one's tier changes later — the mismatch is invisible until then. Grep
your new tool's function body for `enforce_tier(` and check the string
inside it matches the function's own name, every time, even when copying an
existing function as a template.

**The tool must do what its own docstring says, completely, not partially.**
If a docstring says a preset bundles three primitive calls, it must call all
three — not one, with the other two silently dropped because they didn't
exist yet when the tool was written. If a docstring lists four named presets,
all four must be implemented, not three with the fourth quietly missing. If
PLAN.md specifies a tool's signature or behavior (several catalog entries
give an exact signature, like `set_dressing_pass(theme, area_bounds)`), the
shipped tool's actual signature and behavior must match it, or the deviation
must be stated plainly in the docstring and wherever the tool is marked done
(see "When a probe reveals the plan doesn't work" for the honest way to
narrow scope). A docstring that oversells what the function body actually
does is worse than no docstring, because it's the one place a future reader
(human or model) will trust without checking the implementation.

**New tools that depend on primitives which don't exist yet must not be
built as a workaround.** If a preset needs `set_light_properties` and that
primitive hasn't been built, the correct move is to build the primitive
first (it's earlier in PLAN.md's phasing for exactly this reason — see "What
this means for build order"), not to bake an equivalent raw snippet directly
into the preset as a substitute. A inlined workaround means the primitive,
when it's eventually built separately, has two independent implementations
of the same Unreal call that can drift apart, and the preset never actually
exercises the primitive it's supposed to be composing.

**The tool must be covered by the same test machinery as its siblings, not
silently excluded from it.** `tests/test_snippet_syntax.py` patches
`get_bridge` on each tools module it imports; if your new module isn't in
that list, its snippets are untested by the existing suite even though the
suite appears to pass. Check the test file's imports and patch list include
your new module, the same way `scene`, `materials`, and `blueprints` are
already covered there. A green test run that quietly skipped your module is
not evidence of anything.

**Docstrings and comments must describe the current state of the code, not
a past or aspirational one.** A docstring that says "this has not been
exercised against a live editor yet" on a tool that step 5 just live-tested
is actively misleading to the next reader — it directly contradicts
README's status section and makes the file untrustworthy as a source of
truth. Update a module's header docstring in the same change that verifies
its contents, not later, not never.

**Dead code and stale instructions must not accumulate.** If a refactor
(e.g. a transport change) makes a module unused, delete it — don't leave it
present but unimported, where it looks load-bearing to the next reader.
If a setup instruction in README references an environment variable, file,
or step that a later change made unnecessary, update or remove that
instruction in the same change, not as a someday cleanup. An unused file or
a stale instruction left behind by a change you are making is part of that
change, not a separate pass to do later.

**Security heuristics must be precise enough not to reject legitimate
input.** A substring or pattern check in `security.py` should be written to
catch what actually causes a security problem (e.g. real path traversal,
`../`) rather than the shortest pattern that correlates with it (a bare
`..`, which also rejects innocent, harmless strings like `"0..10"`). When
you add or touch a check in `security.py`, write down (in the comment above
it, matching the existing style) one legitimate input it does not
incorrectly reject and one attack it does catch — if you can't state the
legitimate example, the check is probably too broad.

**Self-check before calling step 8 done**, as a single pass over your new
code: open `server.py` and confirm the import and `add_tool` lines exist;
open `security.py` and confirm the tier entry's key string matches the
function's own `enforce_tier(...)` argument; open `tests/test_snippet_syntax.py`
and confirm your module is patched there; reread your own docstring next to
your own function body and confirm every claim in the docstring is actually
true of the code beneath it; reread the relevant PLAN.md catalog line next
to your signature and confirm they match or the deviation is stated. Five
checks, every tool, no exceptions — this is the same discipline as the
read-back step, applied to the surrounding wiring instead of to Unreal's
state.

---

## Worked examples: replaying the real incidents correctly

These are not hypotheticals. They already happened in this project. Each
one shows what step 2 and step 6 would have caught, concretely, if they'd
been followed.

### Example A: `unreal.Color` argument order (the cold-blue-renders-copper bug)

What happened: a tool needed to build an `unreal.Color` for a "cold blue"
lighting preset. The argument order was assumed to be `(r, g, b, a)` by
analogy with how color constructors work in most other graphics APIs. It
isn't — `unreal.Color` is `(b, g, r, a)`, because it mirrors the engine's
FColor byte layout. The tool shipped, reported success, and rendered a warm
copper tone instead of cold blue. It was caught only because a human looked
at the viewport — which is exactly the category of check this project has
decided not to rely on at this stage.

Correct process, replayed:

- Step 2a: before writing the lighting tool, open
  `dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/Color`
  and read the constructor's documented field order. It's listed as
  `b, g, r, a`, plainly, on the page. That one lookup is the entire fix —
  it costs nothing and requires no code, no editor, no guessing.
- Step 2b: confirm it against the live editor anyway, since this is a
  constructor — build `unreal.Color(10, 20, 200, 255)` (four clearly
  distinct values, not interchangeable ones) and read back `.r`, `.g`,
  `.b`, `.a` individually by name, confirming the live result matches what
  the docs said. This step would also have caught the bug on its own if
  2a had been skipped, but it should never be the *only* step — it's
  confirmation of a documented fact, not a substitute for reading the
  fact.
- This is exactly why `presets.py`'s actual `light_scene_preset` builds the
  color with keyword arguments (`unreal.Color(r=..., g=..., b=..., a=...)`)
  and leaves a comment calling out the BGRA trap by name — that tool was
  written with this already known. Use keyword arguments for every
  color/vector constructor you touch, every time, as standard practice, not
  only after being burned once.
- Step 6 read-back for this kind of tool: after calling the real tool,
  independently re-fetch the light component's color property
  (`get_editor_property('light_color')` or equivalent) and compare each
  channel by name against the input the tool was given — not against the
  raw constructor call the tool itself made internally. If the tool's
  internal logic has the channel order backwards, a read-back that just
  re-runs the same backwards logic to "confirm" it would agree with itself
  and still be wrong. The read-back has to check named channels against
  named intent, independent of how the tool's own code path got there.

### Example B: material parameter set on an empty graph (the blank-material bug)

What happened: `set_material_vector_parameter` was applied to a Material
Instance whose parent Material had zero nodes in its graph (freshly created
via `MaterialFactoryNew`, nothing wired to Base Color or anything else). The
tool was "verified" by setting the parameter, then calling
`get_vector_parameter_value` on the same parameter and confirming the
values matched. They did match — the write/read pipe for that one parameter
slot works fine. But the parameter had no node graph connection to anything
visible, so it could never have had any rendered effect no matter what
value was set. The round-trip check only proved the leaf value was stored;
it said nothing about whether that value did anything.

Correct process, replayed:

- The mistake was treating "set X, then get X, they match" as the complete
  read-back for a parameter *override* tool. A parameter override only has
  an effect if the parent Material actually exposes that parameter through
  a real graph connection (a Parameter node wired, directly or through
  other nodes, to a material output pin). That supporting structure is a
  separate fact from the override value and has to be checked separately.
- Correct step 6 read-back for any material-instance parameter tool:
  1. Confirm the leaf value, as before (`get_*_parameter_value` matches
     what was set) — necessary, not sufficient.
  2. Independently query the **parent Material's** parameter list via
     `MaterialEditingLibrary.get_scalar_parameter_names` /
     `get_vector_parameter_names` (the same discovery call PLAN.md names
     for `get_material_parameter_list`) and confirm the parameter name
     actually appears there. If it doesn't appear, the instance may accept
     the write into a non-exposed or stale slot that does nothing, and the
     leaf-value check alone would never have revealed that.
  3. If you want the stronger version of this check (recommended whenever
     you're building the material tools, not just this one incident):
     query whether that parameter node has any outgoing connection at all
     via `MaterialEditingLibrary.get_material_expression_outputs` or by
     inspecting the node's connections, and confirm the graph isn't empty
     (`MaterialEditingLibrary.get_num_material_expressions` returns more
     than zero). An empty graph is itself a meaningful finding worth
     asserting against, not just circumstantial.
- The general lesson: for anything that is an *override* or *parameter* on
  top of a *base* structure, the base structure's existence and wiring is
  part of what "setting the override" means. Read it back too.

### Example C (named in PLAN.md itself, not yet caused a bug, but same class of risk): guessing editor-side subsystem call shapes

A separate earlier session ran
`unreal.get_default_object(unreal.PythonScriptPluginSettings).remote_execution`
directly against the console without having confirmed that attribute
existed, and hit `AttributeError`/`TypeError`. No damage was done, but it
illustrates the same root cause as A and B: code was written from
assumption about what an API should look like, run directly as if it were
already confirmed, rather than probed first. Treat every unfamiliar
subsystem/class the same way: `dir()` it, or run the smallest possible call
against it, before writing it into a tool meant to be trusted.

---

## What counts as sufficient read-back, by tool category

"Read back the value" means something different depending on what the tool
actually did. Use this table as a floor, not a ceiling — if a tool's effect
has more than one dependency, check all of them.

**Plain scalar/vector property set on an actor or component**
(`set_property`, `set_actor_transform`, `set_light_properties`-style tools)
— Read back the exact property by name through an independent query (e.g.
`get_editor_property`, or a fresh `list_actors()`/inspection call, not a
variable still held in memory from the write call) and confirm it equals
the input. This is usually sufficient on its own *because* there's no
intermediate structure the value depends on — the property is the whole
effect.

**Parameter override on a Material Instance, or anything else that
overrides a value exposed by a separate base/parent asset**
(`set_material_scalar_parameter`, `set_material_texture_parameter`,
MetaHuman wardrobe/skin-tone overrides, Niagara user-parameter sets) — Leaf
value match is necessary but not sufficient (Example B above). Also
independently confirm: the parameter name actually exists on the parent's
exposed parameter list, and — where feasible — that the parameter has a
real graph/structural connection to something that matters (not an orphaned
node). For Niagara specifically, confirm the parameter name appears in
`get_niagara_user_parameters` output on the live system, not just that
`set_variable_*` didn't throw.

**Graph construction / node wiring** (`create_material_expression`,
`connect_material_expressions`, Blueprint graph node tools) — Confirm the
node actually exists in the asset's expression list after creation
(`get_num_material_expressions`, or list them with
`get_material_expressions`), and for a connection tool, confirm the specific
pin-to-pin link exists by querying the target node's inputs/the source node's
outputs, not just that the connect call returned
without error. A connect call that silently no-ops (wrong pin name, wrong
pin index) is exactly as dangerous as the blank-material case and exactly
as invisible to a bare success check.

**Blueprint compile** (`compile_blueprint`) — Don't rely solely on the
absence of a raised exception. Read back the Blueprint's actual compile
status/log after the call (Epic's Python API exposes compiler
results/log on the Blueprint object — confirm the exact accessor via a step
2 probe, don't assume the name) and confirm it reports success, not just
"the remote call didn't error." PLAN.md explicitly calls this out:
`get_blueprint_compile_errors` exists as its own planned tool precisely
because "success" and "didn't throw" aren't the same thing for a compile
step.

**Asset creation** (`create_material`, `create_blueprint`,
`create_data_table`, any `*FactoryNew`-based tool) — Confirm the asset
exists at the expected path via an independent query
(`unreal.EditorAssetLibrary.does_asset_exist` or `load_asset` succeeding
from a fresh call, not reusing the handle the creation call returned), and
confirm its class/type is what you expect, not just that *some* asset
landed at that path.

**Actor spawn/delete** (`spawn_actor`, `delete_actor`,
`duplicate_actor`) — Confirm via a fresh `list_actors()`-equivalent call
that the actor is present (spawn/duplicate) or absent (delete), by name,
not by trusting the name the spawn call happened to return. This is already
the pattern `scripts/smoke_test.py` follows for `spawn_actor` — match it.

**PIE-only tools and anything explicitly flagged as needing visual
confirmation in PLAN.md** (MetaHuman sculpting/rigging, Niagara
template-based VFX, viewport screenshot itself) — These are explicitly out
of scope for this phase's read-back discipline; see "Out of scope" below.
Do not attempt to fake a visual check with a non-visual proxy for these —
instead, build the narrowest possible non-visual check that's still
honest (e.g. confirm a MetaHuman auto-rig call reports a skeleton was
attached, without claiming that proves the rig deformed correctly) and
note plainly in the tool's docstring that full verification needs the
later screenshot/PIE infrastructure.

---

## Known Unreal Python API gotchas already found in this project

This list is not exhaustive. It exists so you don't have to rediscover
these five specific facts, not so you can stop checking the docs for
everything else. Anything not on this list gets the full step-2 (docs,
then live confirmation) treatment regardless of how simple it looks — and
note that every fact on this list is itself something the official docs
page states plainly; this list is a shortcut for these five, not a
replacement for reading the docs on the next one.

1. **`unreal.Color` positional arguments are `(b, g, r, a)`, not
   `(r, g, b, a)`.** Mirrors FColor's byte layout. Always construct with
   keyword arguments (`unreal.Color(r=.., g=.., b=.., a=..)`) to sidestep
   this entirely rather than remembering the order. `unreal.LinearColor`
   has not been confirmed either way — probe it before assuming it's
   `(r, g, b, a)` just because it's a different class.
2. **`setattr` vs `set_editor_property` are not interchangeable.** Some
   UPROPERTYs accept plain `setattr()`; others silently reject it or raise,
   and only accept the write through `set_editor_property()`. There is no
   way to know which one a given property needs without trying it (or
   checking the exact UE version's docs/source, which is slower than just
   trying it). `DirectionalLightComponent.intensity` is a confirmed
   `set_editor_property`-only case in this project; don't assume other
   light/component properties follow the same rule without checking.
3. **`bHidden` has no working snake_case settable alias.** `hidden` is a
   read-only accessor; there is no plain-attribute path to it at all. The
   actual mutator is the dedicated method `set_actor_hidden_in_game(...)`.
   This is the general pattern to watch for: some UPROPERTYs are exposed to
   Python only through a named method, never through either attribute-set
   path. If both `setattr` and `set_editor_property` fail or no-op on a
   property, search `dir()` for a `set_<name>`-shaped method before
   concluding the property can't be set at all.
4. **`set_property`'s existing scope limit**: it uses plain `setattr` only
   (see `scene.py`). That's a known, intentional limitation of that one
   MVP tool, not a bug to silently "fix" by rewriting it to try multiple
   access patterns — if a new tool needs to set something `set_property`
   can't reach, build that as its own tool (or extend `set_property`
   deliberately, as a scoped change you call out, not a quiet patch).
5. **The function named in Example B does not exist.** This guide said to
   confirm a non-empty material graph with
   `MaterialEditingLibrary.get_all_material_expressions`. Neither name has an
   `all_` in it on Unreal 5.8: the real calls are
   `get_num_material_expressions` (count) and `get_material_expressions` (the
   list). Corrected above, and recorded here because the wrong name is what
   you will reach for first.
6. **Structs that look like lists are not iterable.** `unreal.Vector` has no
   `__iter__` in this Python build, so `[v.x for v in some_vector]` raises
   `TypeError: 'Vector' object is not iterable`. Read `.x` / `.y` / `.z`
   explicitly. Same trap class as the positional constructors above: the
   obvious spelling is invalid, and a different obvious spelling silently
   yields a string instead of a value.
7. **Cleanup must delete an exact allowlist of the names you created, never a
   prefix or a class match.** This one cost a real actor. A verification script
   owned two scratch `StaticMeshActor`s and cleaned up with
   `[a for a in actors if a.name.startswith("StaticMeshActor_UAID_")]`. Every
   mesh actor in the level is named `StaticMeshActor_UAID_<guid>` — the
   generated prefix *is* the class name — so the sweep matched the level's own
   content and destroyed it along with the scratch actors. The actor count is a
   weak guard here: the baseline happened to move 138 to 137, a delta small
   enough to read as noise rather than as data loss.

   The rule: record every name you create in a list as you create it, and
   delete by membership in that list. A prefix, a class name, a tag, or a
   spatial query are all selection predicates that can match something you did
   not make. Verify the level is unchanged by comparing against a snapshot
   taken *before* the run, not against a count.
8. **A tool must never open a modal dialog.** `AssetTools.create_asset`
   defaults `replace_existing` to True, which pops an editor dialog asking
   whether to overwrite. The dialog blocks the Remote Control endpoint until a
   human clicks it, so the client sees a timeout rather than a question and
   cannot answer it — the run looks like a hang, not a prompt. Pass
   `replace_existing=False` explicitly and fail on a taken name, or use a fresh
   name. Note the flag is the **6th** positional argument; the 5th is
   `calling_context` (a Name), and passing a bool there raises
   `Cannot nativize 'bool' as 'Name'`.
9. **Recheck a value rather than echoing back what you were asked for.**
   `create_material_instance` reported the parent it was given without reading
   it back, and happily produced instances whose `parent` was null. Every write
   should be confirmed through a different call than the one that made it.
10. **`CommandResult` from `ExecutePythonCommandEx` arrives repr-wrapped.**
   Unreal `repr()`s the Python return value before putting it on the wire,
   so a string result comes back double-encoded. `bridge.py`'s
   `run_python()` already handles this (`ast.literal_eval` unwrap before
   `json.loads`) — you don't need to re-solve it, but it's worth
   understanding why that line exists, because the same
   "repr-then-serialize" quirk may resurface in some other form if you ever
   touch the transport layer or build a tool that bypasses `run_python()`
   for some reason.
11. **Splicing a multi-line snippet into an indented block.** A helper that
   emits at column 0 must land at the same depth as the statement it is spliced
   beside, or it produces an `IndentationError` that only surfaces inside
   `exec()` on the editor side, as a failure with an empty log and no clue.
   `indent_block()` handles this and **always appends a trailing newline**,
   because a block spliced before another line concatenates that line onto the
   last. Three separate helpers here (`indent_block`, `_compile_errors`,
   `_recompiled_result`) take an explicit indent for exactly this reason: the
   fix belongs in the helper, not in every call site.
12. **One f-string often emits the whole snippet, so its braces are format
   syntax.** A `{n}` placeholder meant for the *generated* code is evaluated
   while the snippet is being built, raising `NameError` before anything is
   sent. Comments inside the emitted text count too — a brace in a comment
   breaks the build the same way. Likewise a name computed on the caller's side
   has no binding on the editor's: interpolate it as `{label!r}`, never
   reference a bare `label`.
13. **Do not assume a struct or class has the members its C++ equivalent has.**
    `World` has no `get_actors()` or `get_levels()` from Python;
    `SkeletalMeshComponent` has no `get_animation()`, `pause()`,
    `get_playback_position()` or `get_playback_length()`; `PrimitiveComponent`
    has no `get_component_location()`. Check `dir()` on the live object and
    `hasattr` on a real instance, not the stub and not the docs — the Python
    API is a narrower slice than the class it wraps.
14. **A setter can succeed and change nothing.** `set_simulate_physics` on a
    component with no physics body returns normally and does not simulate;
    `execute_console_command` dispatches and returns no output; switching a
    component out of single-node animation mode drops the assigned asset. Read
    the value back and report `took_effect` rather than reporting the call.
15. **Asynchronous operations need polling in separate round trips.** A play-in-
    editor request lands on a later tick, and a World Partitioned level streams
    in after `load_map` returns. A loop *inside* the snippet cannot work: it
    runs on the editor's main thread, where the engine cannot tick. Poll from
    the client, and require more than one identical reading, since a pause can
    coincide with the middle of a stream.

---

## When a probe reveals the plan doesn't work

If step 2 shows the method named in PLAN.md doesn't exist, is read-only
when the tool needs to write, requires an engine version newer than what's
installed, or otherwise can't do what the catalog entry assumed:

1. **Do not force it.** Don't keep guessing alternate method names hoping
   one works, and don't fall back to a weaker check that avoids the
   problem instead of solving it.
2. **Do not silently narrow scope without saying so.** If the real API can
   do part of what was asked but not all of it, build the part that's real,
   and say explicitly (in the tool's docstring, and in whatever you report
   back) what was dropped and why — e.g. "`retarget_animation` only wraps
   the IK Rig retarget call confirmed present in this engine version; the
   animation-curve remapping PLAN.md mentions was not found on this API
   surface as of probing" is the right shape of note.
3. **Check whether it needs the C++ escape hatch instead.** PLAN.md already
   flags several domains (Blueprint graph node wiring, Niagara
   module/script graphs, Replication Graph configuration) as likely needing
   this. If your probe lands you in the same situation — the Python binding
   genuinely isn't there — that's confirmation the PLAN.md phasing was
   right to defer it, not a reason to hack around it with
   `execute_python` calling into something unsupported.
4. **If the engine version matters** (PLAN.md flags this for
   `retarget_animation` and the whole MetaHuman domain), check the actual
   installed engine version (`README.md`/`UNREAL_ENGINE_ROOT`) before
   concluding an API is missing — confirm absence on the real installed
   version, don't infer it from general Unreal documentation that may cover
   a different version.
5. **Report the deviation plainly** wherever you're tracking tool status
   (PLAN.md catalog entry, commit message, whatever this project uses) —
   "built as `X`, scoped to `Y` because `Z` was not available" — rather
   than marking it done as originally specified.

---

## Explicitly out of scope right now

- Viewport screenshots, or any check that requires a human (or a
  vision-capable step) to look at rendered output and judge whether it
  looks right. Tools are deterministic at this stage; PLAN.md's own
  phasing puts `capture_viewport_screenshot` and PIE infrastructure in a
  later phase, and the MetaHuman domain explicitly waits on that
  infrastructure before it's trustworthy to build. Don't take a screenshot
  as a substitute for finding the right state query — if you're tempted to,
  it usually means the real read-back hasn't been found yet.
- Building the orchestrator/agentic layer itself, or anything that chains
  multiple tool calls to "accomplish a goal" rather than exercising one
  named tool. That's Phase 2.
- Automation-test-suite style exhaustive coverage. One live-verified
  worked example per tool, following this guide's steps, is the bar — not
  a battery of edge cases. (`tests/` covers the security bouncer and
  snippet syntax with real pytest tests already; that's a different,
  narrower kind of test than the live-editor verification this guide
  describes, and both are needed, but don't conflate them.)

---

## Texture2D: what is and is not reachable from Python (measured, UE 5.8)

Four texture tools are live-verified (51/51 checks). Recording the API
deviations, because each one cost a live probe to discover and each one is
the kind of thing that is easy to guess wrong:

- **`platform_data`, `source`, `pixel_format` and `cached_num_mips` all raise
  on a Texture2D.** You cannot read a single pixel back through any of them,
  and you cannot confirm the pixel format of a texture you just wrote. So
  `get_texture_info` reports `pixel_format: None` and sets a `_readable: False`
  flag per property, rather than guessing the format from the compression
  setting. A generated texture is verifiable as *a real Texture2D at the right
  dimensions*; the pixel round trip is not verifiable at all, and claiming
  otherwise would be a lie.
- **`ModelingObjectsCreationAPI.create_texture_object` does not accept pixel
  bytes.** `CreateTextureObjectParams` wants an already-built transient
  texture object, which is the thing you do not have. Writing pixels therefore
  goes via a real PNG on disk plus `AssetImportTask` + `TextureFactory`, which
  does work: encode RGBA with `struct` + `zlib`, drop it under Saved, import it.
  That is what `generate_texture_from_pixels` does.
- **Never hardcode Unreal enum member names from the C++ header.** My first
  allowlists were wrong twice: there is no `TC_MASK` (it is `TC_MASKS`), and
  there are no `TF_SHARPEN*` members at all. A wrong name surfaces as an
  `AttributeError` from inside the editor that looks like an editor bug.
  `set_texture_properties` now validates against the live enum and returns the
  available members in the error.
- **`lod_bias` is set as a float but reads back as an int**, so 2.5 stores as 2
  and a fractional bias is silently truncated. Pass whole numbers.
- **Material-to-texture rendering is not buildable here.** `KismetRenderingLibrary`
  is absent, and `CanvasRenderTarget2D` exists but exposes no usable draw call,
  so there is no path from a material to a baked texture through Python. Don't
  plan a `bake_material_to_texture` tool around it without the C++ escape hatch.
- The material-function-call node is `MaterialExpressionMaterialFunctionCall`,
  not `MaterialExpressionFunctionCall`. Note that `create_material_function`
  returns a *package* path (`/Game/Path/Name`), so you must append
  `.{Name}` yourself before passing it to anything that loads an object.

---

## Blueprints: structure is readable, node contents are not (measured, UE 5.8)

Seven Blueprint tools live-verified (61/61 checks). The dividing line is hard
and worth knowing before you plan anything:

- **`EdGraph.Nodes` is a protected property.** Reading it raises "Property
  'Nodes' for attribute 'nodes' on 'EdGraph' is protected and cannot be read".
  Graph nodes are also *not* addressable by object path, so you cannot reach one
  even if you guessed its name. That means the node-level helpers that genuinely
  exist in `BlueprintEditorLibrary` — `get_node_title`, `get_node_pos`,
  `get_node_size`, `get_node_category`, `list_input_pins`, `list_output_pins`,
  `list_all_pins`, `get_nodes_in_comment` — are all unreachable, because every
  one of them needs a node handle you have no way to get. `list_blueprint_graphs`
  returns `nodes_readable: False` with the reason attached rather than letting
  anyone read it as a node listing. Enumerating nodes needs the C++ escape
  hatch.
- **`create_blueprint_asset_with_parent(asset_path, parent_class)` takes a
  package path, not an object path.** Pass `'/Game/MCPTest/BP_Thing'`. Pass
  `'/Game/MCPTest/BP_Thing.BP_Thing'` and it does not reject it, it sanitises the
  dot into the asset name and creates `BP_Thing_BP_Thing`. So `create_blueprint`
  refuses a dotted path outright rather than letting that happen.
- **Its return value is untrustworthy.** It returns `None` when the asset already
  exists, *and* it returns `None` in cases where it did create the asset anyway.
  Do not use the return to decide success. `create_blueprint` loads the asset
  back and uses that as the evidence, and reports `editor_returned_none` so the
  caller can see which happened.
- **`BlueprintFunctionInfo` fields are `name`, `description`,
  `is_implemented`** — not `function_name`, which is what the C++ suggests.
- **Descriptions come back as raw `NSLOCTEXT("NSLOCTEXT", "Key", "text")`**, and
  some run to 600+ characters. Cleaned to the tooltip text and truncated.
- **`EdGraphPinType` exposes no readable fields.** `str()` on one renders as
  `<Struct 'EdGraphPinType' (0x...) {}>` for *every* type, identical apart from
  the address, so it cannot be used as a type name. The only informative
  rendering is `pin_type_to_json_schema(pin_type, self_context)`, which
  `list_blueprint_variables` returns. Its limitation: it does not distinguish
  `int32` from `float`, both report `{"type": "integer"}`, so that caveat is
  returned on the result as `type_format`.
- **`EdGraphPinType()` has no settable `pin_category`** — the struct is empty to
  Python. For a typed variable use
  `BlueprintEditorLibrary.get_basic_type_by_name("float")`; a bare
  `EdGraphPinType()` produces a variable whose schema reads as `integer`.
- **`set_blueprint_variable_category` silently no-ops on a variable that does not
  exist**, returning None as if it had worked. Don't treat its return as proof.
- **`indent_block()` defaults to `spaces=0`.** Splicing its output under an
  `else:` needs `spaces=4`. Getting this wrong produces an
  `IndentationError: expected an indented block after 'else'`, which
  `guarded()` catches locally, so it fails fast rather than in the editor.

---

## Two bugs found by re-verifying old tools, and how they hid

Re-verifying lighting and presets after they had been untouched for a while found
two real defects. Both are the kind that pass every test that exists, because the
tests asserted the shape the buggy code already had.

### 1. The lighting tools sent bare snippets, so any error was opaque

All six lighting tools built a `json_dumps(seq(...))` expression and sent it
directly. `seq()` yields its last element, and the actor lookup was
`next(genexp)` without `optional=True`, so a missing actor raised StopIteration
inside the editor. Since the snippet was not wrapped in `guarded()`, the exception
surfaced at the caller as:

    RemoteCommandFailedError: Remote command failed: []

An empty log, no mention of which light was missing. Every other tool in the repo
returns `{"success": False, "error": ...}`. All six now build an `OUT` dict
through `guarded()`, resolve the actor optionally, and check the component for
`None` before reading from it. `lighting._body()` wraps that shape so it is
written once.

The lesson generalises: **a tool that does not use `guarded()` has no way to
report an in-editor error, it can only raise.** That is the whole reason guarded()
exists, and a module that predates it is the module most likely to still be
missing it.

The tests here had asserted the buggy shape. `test_read_back_tools_end_on_the_payload`
checked that a getter's snippet ends on a `lambda`, which was a proxy for "does not
return the actor name by mistake". It now asserts the stronger property that
actually guarantees it: the snippet assigns an `OUT` dict.

### 2. `apply_material_variant_set` reported phantom success

Pointed at a base material path that does not resolve,
`create_material_instance` returns a dict with a null `instance_path`. The function
appended that null to its results and returned:

    {"success": True, "count": 1, "instance_paths": [None]}

Nothing was created, and the caller was told one instance existed. It now checks
the base up front, refuses an empty base, and reports any variant that failed to
create by name instead of counting it.

Worth generalising: **a tool that appends a value to a results list without
checking it is truthy will report success for work it did not do.**
`create_material_instance` returning a null path in a success-shaped dict is the
upstream half of this; nothing downstream checked.

### Do not hand-write a dict literal inside an f-string

Both fixes went through several rounds of broken generated code because of this:
`f"OUT = {{'found': False}}"` needs every brace doubled, and doubling the wrong one
produces an f-string SyntaxError that names nothing useful. `lighting._out()`
builds the dict by joining `key: value` source pairs instead, with no f-string
involved, so there is nothing to double.

---

## The game-thread deadlock: what cannot be built over Remote Control, and why

Two calls wedged the editor during Data Table work, and the reason is structural.
It is worth writing down because it rules out a whole class of tools, and because
the obvious workarounds do not work.

`DataTableFunctionLibrary.fill_data_table_from_json_string` (and the CSV twin)
**deadlocks the editor** when invoked over Remote Control. The call arrives on the
game thread; the reimport it triggers wants the game thread; the handler blocks
waiting for a thread that is itself blocked. The editor stops answering entirely,
holding port 30010 open at high CPU, and every call then fails with
`NoEditorFoundError`. Recovery needs an editor restart.

Moving the same call onto a worker thread *inside* the editor does not help.
Unreal rejects it outright:

    RuntimeError: DataTableFunctionLibrary: Attempted to access Unreal API from
    outside the main game thread

So there is no in-language escape. `fill_data_table_*` is the only known member of
that family, and it is the whole of DataTable row authoring. Data Tables are
therefore built as **create-and-read only**: `create_data_table`,
`get_data_table_info`, `list_data_table_rows`, `export_data_table`. They carry
`rows_writable_from_bridge: False` so a caller is not misled, and the limit is
asserted against the editor in the verification script rather than taken on trust.

The general lesson, and it applies well beyond Data Tables:

- **Anything that triggers a reimport, a recompile, or a synchronous asset save
  from inside a Remote Control handler is a deadlock candidate.** Test such calls
  on a disposable project with a short client timeout, never as part of a longer
  chain, because the cost of getting it wrong is an editor restart.
- **When a call wedges the editor, bisect it before retrying it.** I assumed
  `AssetTools.create_asset` caused the first wedge and retried it; it was fine. The
  culprit was a later call in the same probe. Bisecting costs one cheap call and
  saves an editor session.
- **Unreal is single-threaded for its API from Python, and Remote Control's HTTP
  handler runs on that thread.** Any design that needs genuine concurrency has to
  move the work into C++ or into a deferred command the engine completes later.

### Related: `delete_asset` reports a false failure

`EditorAssetLibrary.delete_asset` returns True meaning the delete was *initiated*.
The asset does not disappear immediately, and the tool's `still_exists` check runs
in the same snippet, before the unload completes, so a successful delete came back
as `{"success": false, "deleted": true, "still_exists": true}` — three fields that
contradict each other. An asset with no references (a Data Table created and
dropped) deletes straight away; one still loaded, or held by an editor window,
does not, and closing all editors for it via
`AssetEditorSubsystem.close_all_editors_for_asset` was not enough either. Deleting
an asset and confirming it are two different operations and the tool collapses
them. Worth fixing before anyone relies on the return value.

### Related: a globally-supplied optional argument is a hazard

`tests/test_all_tool_snippets.py` fills required arguments from a shared table and
also offers optional ones by name. Adding `"offset": 0` for
`list_data_table_rows` silently broke `scene.duplicate_actor`, whose optional
`offset` is a 3-tuple it unpacks: it raised unpacking an int before it ever built a
snippet, so the tool stopped being covered at all and the only symptom was an
unrelated test failing. Arguments whose names are reused across tools with
different types now live in a per-tool table instead of the shared one.

---

## Deadlock triage, and the registry of what is known unsafe

Given how expensive a wrong guess is (an editor restart, and every verification
run in flight lost), the method is to triage in ascending order of what each stage
can cost.

**Stage 1, introspection. Free.** Enumerate with `dir()` and classify by name and
docstring. Unreal's naming is a decent signal: `import_*`, `*_factory_create_file`,
`fill_*`, `reimport*`, `request_*`, `regenerate*`, anything documented as
reimporting or recompiling. This costs nothing and narrows the field.

**Stage 2, off-thread probe. Survivable for the risky class.** Run the candidate on
a worker thread *inside* the editor and leave the game thread free, so the HTTP
handler always answers and the editor survives whatever the worker does. Outcomes:
an immediate `Attempted to access Unreal API from outside the main game thread`
means the call is game-thread-affine (safe to conclude, it was rejected, not
blocked); a clean return means it is not thread-affine; a worker still running
after the join means it blocks under any thread.

**Stage 3, game-thread probe. Only if 1 and 2 came back clean.** One call, nothing
else in the snippet, short client timeout. Then distinguish *slow* from *deadlock*
by polling liveness every 2s for ~20s afterwards: a slow call recovers (a material
domain change recompiling shaders does exactly this), a deadlock never does and
the port stays LISTENed at high CPU.

**Record every result.** A known-deadlocking call must never be retried. I lost an
editor session to a call I had *already* measured and not written down.

### Surveying the API surface: enumerate, never guess a class list

My first pass at the unbuilt categories was wrong in method, and worth recording
because it nearly produced false "not possible" verdicts.

I guessed roughly twenty plausible class names per category, looked each one up,
and called the category "dead" when they came back `ABSENT`. Concretely: I
concluded foliage and landscape were unreachable without ever checking
`ProceduralFoliageEditorLibrary` or `FoliageStatistics`, which both exist and do
exactly the job I said was impossible.

This is the same failure as hardcoding an enum list from the C++ header, and the
same failure as guessing a property name: **a name you invented is a hypothesis,
not a survey.** Every time in this project a curated list of names has been
wrong, it has been wrong in the same direction, towards concluding something is
unavailable.

The survey is cheap and complete, so there is no excuse for the guess. Compare a
prefix count against the stub, which lists everything the engine can expose:

    grep -c "^class Landscape" UnrealProject/Intermediate/PythonStub/unreal.py   # 44
    grep -c "^class Niagara"   UnrealProject/Intermediate/PythonStub/unreal.py   # 334
    grep -c "^class MovieScene" UnrealProject/Intermediate/PythonStub/unreal.py  # 317
    grep -c "^class Replication" UnrealProject/Intermediate/PythonStub/unreal.py # 1

and confirm the same prefixes are actually *live* in the editor with a
`dir(u)` scan. Those two checks together are the whole survey, and both were free.
An `ABSENT` from a guessed name means "I did not look", not "it does not exist".

### Corrected survey of the four unbuilt categories

- **Landscape editing: genuinely unavailable, now on real evidence.** All 44
  Landscape classes are live, but scanning all of them for any Subsystem,
  Library, Editor, Utils or Settings entry point turns up only
  `LandscapeGrassTypeFactory`, `LandscapeTargetLayerSettings` and some PCG
  settings. There is no landscape editing library. `Landscape`,
  `LandscapeProxy` and `LandscapeComponent` expose Actor and physics boilerplate
  only. `LandscapeEditLayer` and `LandscapeBlueprintCustomBrush` are data objects
  with no methods. `LandscapeBrushParameters` is a struct, so its fields are
  readable and settable via `import_text`, which is the one sliver of reachable
  landscape API. Sculpting and layer painting need the C++ escape hatch.
- **Foliage: available, and I had missed it.** `InstancedFoliageActor` with
  `add_instances`, `FoliageType` and `FoliageType_InstancedStaticMeshFactory` for
  the type asset, `FoliageType_ActorFactory`, `FoliageStatistics` with
  `foliage_overlapping_box_count` and
  `foliage_overlapping_box_transforms`, `InteractiveFoliageActor`,
  `ProceduralFoliageActor`, and `ProceduralFoliageEditorLibrary` for
  `resimulate_procedural_foliage_components`,
  `resimulate_procedural_foliage_volumes` and clearing procedural foliage.
- **Niagara: available, and far larger than my survey suggested.** 334 live
  classes including `NiagaraDataChannelLibrary`, `NiagaraBakerFunctionLibrary`,
  `NiagaraEditorPreviewActor`, `NiagaraEditorDataBase`, and a family of
  `...FactoryNew` classes (`NiagaraEffectTypeFactoryNew`,
  `NiagaraDataChannelAssetFactoryNew`). The instance-level surface I did find is
  real: `NiagaraFunctionLibrary.create_niagara_parameter_collection_instance` plus
  data-interface setters, and `NiagaraComponent` forces, impulses and overrides.
  Emitter *graph* editing is still unproven, and is the part most likely to need
  C++.
- **Multiplayer: Replication Graph genuinely unavailable.** This one holds, and
  the reason is now firmer than "I looked at two names": the stub contains
  **one** Replication-prefixed class, `ReplicationSystem`. There is no
  `ReplicationGraphBase`, no driver, no node. Per-actor and per-component flags
  are built and verified.
- **Sequencer: much larger than I implied.** 21 LevelSequence and 317 MovieScene
  classes live, 175 of them tracks and sections, including
  `MovieScene3DTransformTrack` and `MovieScene3DTransformSection` with
  `add_track`, `add_possessable`, `add_spawnable_from_class` and `remove_track` on
  `LevelSequence`/`MovieSceneSequence`. Keyframing looks buildable and is the most
  promising untested category. Untried, so treat that as a lead, not a promise.

### A correction worth recording: I nearly shipped a fabricated limitation

While building the replication tools I concluded that component-level replication
"does not persist", and wrote it into the module docstring, the tool's return
values, and a verification assertion. It was wrong. The probe that "showed" it
reverting selected its actor by class name and read back the *first* actor of that
class in the level, which was not the actor it had set the flag on. Component
replication persists perfectly well.

Three things went wrong at once, all worth naming because the first two are the
kind that survive review:

- The probe was ambiguous, and I did not notice.
- I wrote the conclusion into prose and tests before re-testing it, which made the
  wrong answer feel established and harder to revisit.
- The test then *asserted* the falsehood, so it would have defended itself forever.

The lesson is narrower than "test more": **do not write a limitation into a
docstring and a return field until it has been demonstrated twice, by two
different probes.** Everything here that reports a limitation should be reproducible
by a reader in a few lines. `data_tables` gets away with `rows_writable_from_bridge:
False` because the verification script re-derives the deadlock against the live
editor rather than trusting the constant.

### Replication API facts, all measured

- **Neither `bReplicates` nor `bReplicateMovement` can be set with
  `set_editor_property`** on an instance: both raise "cannot be edited on
  instances". Use `actor.set_replicates(bool)` and
  `actor.set_replicate_movement(bool)`. Both persist.
- **`net_dormancy` likewise**: `set_editor_property` appears to succeed but does not
  persist. `actor.set_net_dormancy(NetDormancy.X)` does persist.
- **A component's flag is `component.replicates` to read, but
  `component.set_is_replicated(bool)` to write.** There is no `is_replicated`
  property at all; reading the attribute or asking for the property both raise
  "Failed to find property 'is_replicated'". C++ calls it `bReplicates`, which
  makes `is_replicated` the natural wrong guess.
- **The NetDormancy enum is all-caps with underscores**: `DORM_AWAKE`,
  `DORM_DORMANT_ALL`, `DORM_DORMANT_PARTIAL`, `DORM_INITIAL`, `DORM_NEVER`. The
  CamelCase `DORM_Awake` looks right and raises AttributeError. This is the third
  time in this project a hardcoded enum list derived from the C++ header has been
  wrong; validate against the live enum instead, as the texture tools do.

---

## Foliage: the read side is reachable, creating a FoliageType is not (measured)

Foliage is buildable, with one specific hole that is worth knowing before anyone
plans around it. Measured against the live editor, not inferred.

**Reachable:**
- `InstancedFoliageActor.add_instances(world_context_object, foliage_type,
  transforms)` is exposed and callable.
- `FoliageStatistics.foliage_overlapping_box_count` and
  `foliage_overlapping_box_transforms` are exposed for queries.
- `ProceduralFoliageEditorLibrary` has
  `resimulate_procedural_foliage_components`,
  `resimulate_procedural_foliage_volumes`,
  `clear_procedural_foliage_components` and
  `clear_procedural_foliage_volumes`.
- `FoliageType`, `FoliageType_Actor`, `FoliageType_InstancedStaticMesh`,
  `InteractiveFoliageActor` and `ProceduralFoliageActor` are all live.

**Not reachable: creating the FoliageType asset a foliage actor needs.**
`add_instances` requires a `FoliageType`, and none can be made from Python:

- `unreal.new_object(unreal.FoliageType, ...)` raises "Class 'FoliageType' is
  abstract". It is the abstract base; the concrete subclass is
  `FoliageType_InstancedStaticMesh`.
- `FoliageType_InstancedStaticMeshFactory` has **no `static_mesh` setter**. Its
  only writable attributes are the inherited `asset_import_task`,
  `automated_import_data`, `formats`, `supported_class`, `text` and friends, and
  its only methods are `script_factory_can_import` and
  `script_factory_create_file`. It is a *file* import factory for `.ff` foliage
  type descriptions, not an in-project creator.

So `add_instances` is callable but untestable, because the argument cannot be
produced. The route that remains is to write a valid `.ff` file to disk and import
it through `script_factory_create_file`. That is the next thing to try, and it is
the only thing standing between the current state and a working
`add_foliage_instances` tool.

Note the asymmetry with Data Tables, where the *write* was blocked and reading
was fine. Here the *setup* is blocked: the asset creation, not the mutation. Worth
keeping the two apart, because the workaround differs, one needs C++ and the other
needs a file format.

---

## Sequencer: the API is there, but add_possessable is not reliable yet

Sequencer has the largest untouched surface: 21 `LevelSequence` classes and 317
`MovieScene` classes live, 175 of them tracks and sections. Measured, not guessed:

- `LevelSequenceFactoryNew()` creates a sequence asset. `create_asset` with it
  works and is immediate.
- `LevelSequence.add_track(track_class)` works and takes a UMovieSceneTrack class.
  A new track has no sections; `track.add_section()` then
  `section.set_range(start, end)` puts a key in, and both read back correctly
  through `get_sections()`.
- `LevelSequence.get_bindings()`, `get_tracks()`, `get_possessables()`,
  `get_spawnables()`, `find_binding_by_name()` and the range accessors all work.
- **Binding GUIDs are not introspectable.** A `MovieSceneBindingID` renders as
  `<Struct 'Guid' (0x...) {}>` and its `to_dict()` is empty. Bindings have to be
  identified by `get_display_name()`, which works.
- `add_possessable(actor)` and `add_spawnable_from_class(cls)` are the entry
  points for bindings, and **neither is reliable.** `add_possessable` worked on a
  fresh sequence several times, then began failing consistently with an empty
  ReturnValue and an empty log, i.e.
  `RemoteCommandFailedError: Remote command failed: []`, with no exception and no
  message. It was reproducible through both `load_asset` and `load_object`, before
  and after an explicit `save_asset`, so it is not a stale-asset or save-ordering
  problem. Something about repeated create/delete of sequences appears to leave
  the editor unable to bind.

That failure mode is the interesting part. It is indistinguishable from a deadlock
at the call site: an empty log and no ReturnValue, which is what a genuinely
wedged editor also looks like. The difference is that here the editor stays
responsive and later calls work.

**No Sequencer tools were committed.** Five were drafted (`create_level_sequence`,
`get_sequence_info`, `add_sequence_binding`, `add_sequence_track`,
`add_sequence_key`) and the three that do not touch bindings all behaved, but
nothing in the set could be verified end to end, and this repository's standard is
that a tool ships only once a live run has passed. A partially working binding
tool that silently fails on half its calls is worse than no tool.

To resume: verify `add_possessable` against a *single* long-lived sequence, with
no create/delete churn, and find what makes it fail. If it cannot be made
reliable, `create_level_sequence`, `get_sequence_info`, `add_sequence_track` and
`add_sequence_key` are all independently shippable and do not depend on it.

---

## add_possessable hard-hangs the editor. Never call it.

The earlier note on this page said `add_possessable` was "unreliable" and
sometimes returned an empty log. That was wrong, and the difference matters:
it is not flaky, it **deadlocks the editor's game thread.**

The evidence, in order:

1. `RemoteCommandFailedError: Remote command failed: []` — no ReturnValue, no
   log, no exception. Not a deadlocked-*looking* call.
2. `seq.add_track(...)` and `track.add_section()` + `section.set_range(...)` on a
   sequence **loaded from disk** both succeed. So the sequence object, the asset
   on disk, and the editor session are all healthy.
3. A call combining `add_possessable` with anything else ran past a 300 s timeout.
4. Immediately afterwards the editor answered no RPC *and* System Events
   returned no window list, while `ps` showed ~46% CPU. It was spinning, not
   idle and not waiting on a dialog.

So the earlier "empty log" results were a partially wedged editor recovering
sometimes, not an intermittent API. Nothing about the call pattern matters:
`LevelSequence.add_possessable`, `MovieSceneSequenceExtensions.add_possessable`
and `LevelSequenceEditorSubsystem.add_spawnable_from_class` all fail the same
way, on freshly created and on freshly loaded sequences, with and without an
intervening `save_asset`.

**Never call `add_possessable` or `add_spawnable_from_*` from this bridge.** There
is no safe timeout, no retry and no workaround; the only recovery is restarting
the editor. Any binding tool has to be built on something else or not at all.

What is genuinely usable, measured on a loaded sequence: `get_bindings()`,
`get_tracks()`, `get_possessables()`, `get_spawnables()`, `find_binding_by_name()`,
`add_track()`, `add_section()`, `set_range()`. So tracks and keyframes can be
built, but there is no way to attach them to an object, which is most of what a
sequencer tool is for.

### create_asset must pass bInteractive=False

`AssetTools.create_asset` defaults `bInteractive` to True and pops an overwrite
dialog. Under Remote Control that dialog blocks the endpoint until a human
clicks it, so an automated call sees a timeout instead of a question. Pass
`bInteractive=False` (6th argument, after `'None'` for calling_context) on **every**
call site, and keep the `does_asset_exist` pre-check as well: `delete_asset` is
asynchronous and can report a deletion that has not landed on disk, so the
pre-check alone is not sufficient. `create_data_table` was missing the flag.
