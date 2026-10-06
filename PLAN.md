# Unreal Engine MCP — Project Plan

## Overview

Two-phase project:

1. **MCP (this phase):** gives an AI agent the ability to actually perform actions inside a running Unreal Engine 5 editor.
2. **Agentic layer (next phase):** takes a game prompt, plans it, generates assets, and builds the game using the MCP from phase 1.

This document covers phase 1 only: scope, MVP, extensions, and security.

---

## What is MCP

Model Context Protocol. An open standard that lets an LLM client (Claude Desktop, Claude Code, Cursor) discover and call external tools in one consistent way, instead of needing custom integration code per tool per client.

- Three primitives: **Tools** (functions the model calls), **Resources** (data it can read), **Prompts** (reusable templates). We mainly use Tools.
- Transport: stdio (local subprocess, pipes) or HTTP/SSE (remote). We use **stdio only, local**, for security reasons (see below).
- Protocol: JSON-RPC. Client sends a method call with arguments, server returns a result or an error.

## What our MCP does

Bridges an MCP client to a running Unreal Editor instance.

```
LLM client (Claude)  <--MCP (stdio)-->  Our MCP server  <--bridge-->  Unreal Editor
```

- The server is not the brain. It doesn't plan or decide what to build. It's pure capability: the hands, not the agent.
- Internally: tool call → our server → Python Remote Execution into the running editor (primary path) → C++ plugin escape hatch (only for what Python's API can't reach) → result returned.
- Design constraint: **general-purpose.** No hardcoded project paths or assumptions about one template project. Tools work against whatever project is currently open.

---

## Bare minimum (MVP)

The smallest set that makes this actually usable, not a toy demo.

- **Connection/discovery:** find and connect to a running Unreal Editor's Python Remote Execution endpoint.
- **`execute_python(code)`:** raw escape hatch for anything the named tools don't cover.
- **`spawn_actor(class_name, location, rotation)`**
- **`set_property(actor_id, property_name, value)`**
- **`list_actors()` / `get_scene_state()`:** read back what's actually in the level.
- **`create_material(...)` / `set_material_parameter(...)`**
- **`compile_blueprint(path)`:** returns real success/failure, not just "sent."
- **Every tool returns status + message.** Never fire-and-forget.

**The single most important MVP rule:** the agent must be able to read state back, and every action must report real success or failure. Without that the agent is blind, it can't verify its own work or recover from failure.

---

## Extensions (ranked, with verdict)

| Extension | Verdict |
|---|---|
| Viewport screenshot tool | Good, close to core. Lets the agent see the scene, not just query data. |
| Play In Editor (PIE) launch + log capture | Good, needed for phase 2's build-test-fix loop. Technically optional for phase 1, but build it, don't block phase 1 on it. |
| Undo/rollback (snapshot before risky edits) | Good, genuinely recommended. Safety net for a system with full RCE-level access. |
| C++ plugin for Blueprint graph node wiring | Good, but deferred. Only build once pure Python actually hits a wall. |
| Logging every tool call to a file | Good, cheap, and useful for debugging and write-ups. |
| Live dashboard showing agent activity | Nice for demo/portfolio value. Not technically necessary. |
| Exposing the MCP server beyond localhost | **Bad idea.** This is an RCE-equivalent channel. Keep it local, single-user, no exceptions. |
| Auth / access control on the server | Skip for a solo project. Worth naming as a known gap if productionizing later. |
| Batching multiple tool calls per round trip | Minor perf nice-to-have. Don't build until latency is actually a problem. |

---

## Security: the "bouncer" layer

Unreal's Python Remote Execution has **zero sandboxing** — it's a full interpreter with real OS access. Sandboxing has to happen in our MCP server, between "model decided to call a tool" and "command dispatched to Unreal." This layer must use deterministic static checks for hard blocks, not another LLM call, since an LLM-based check inherits the same prompt-injection surface it's meant to defend against.

### Threat scenarios to design against

- **Filesystem escape:** writing/deleting outside the project directory, path traversal, symlink tricks, reading secrets (`.env`, SSH keys, cloud credentials).
- **Version control / repo tampering:** `git push --force`, `git reset --hard`, deleting `.git`, rewriting history, committing/pushing without the user asking.
- **Scope mismatch:** user asks for a small change, agent does something destructive and unrelated ("recolor this wall" → agent also deletes an unrelated actor).
- **Arbitrary code execution beyond Unreal:** `subprocess`, `socket`, shelling out, network calls (exfiltration, downloading and running something).
- **Prompt injection via scene content:** text read from asset names, level data, or imported content containing instructions aimed at the agent.
- **Persistence/backdoors:** scripts that run on every build/launch, scheduled tasks, modified CI config — damage that outlives the session.
- **Resource exhaustion:** infinite loops, spawning massive numbers of actors, runaway asset creation.

### Bare minimum defenses (non-negotiable, deterministic)

- No `subprocess`, no shell, no raw sockets from the Unreal-side execution path. Asset downloads happen in the phase 2 orchestrator, never inside the Unreal execution context.
- Path allowlist: canonicalize every file-touching argument, check against the project root, reject anything outside it.
- Hard block on `.git/`, `.github/`, and build/CI config, no exceptions, no tier.
- AST check on every `execute_python` call before dispatch: parse with Python's `ast` module, reject code importing banned modules (`subprocess`, `socket`, `shutil`, `ctypes`, `importlib`) or calling destructive-looking functions (`delete_asset`, `delete_directory`, file removal).

### Stronger security (recommended additions)

- Risk tiering per tool call: read-only (always allowed) → constructive (allowed) → destructive (confirm or block) → system-level (blocked). Mirrors the same tiering model production AI agents use.
- Intent-scope matching: compare a pending destructive action against what the user actually asked this turn. Can reasonably use a lightweight model check, backed by the deterministic rules above as a floor.
- Snapshot before destructive actions, with undo. Prevention plus recovery.
- Immutable audit log stored outside the project directory (so the agent can't erase its own trail): every tool call, every raw Python string, timestamp, triggering instruction.
- Rate limits/caps: max actors per call, max loop iterations in generated scripts.

---

## Full tool catalog (target surface, not all built yet)

The MVP above is the floor. This is the ceiling: every domain a Phase 2
orchestrator would otherwise have to improvise against with raw
`execute_python` calls, which is exactly the try-and-fail loop we want to
avoid. A named, tested tool beats a generated snippet because the agent
gets a stable contract (fixed arguments, fixed return shape, pre-verified
against a live editor) instead of re-deriving the right API call, and
re-discovering its edge cases, every single time it needs the same
capability.

Each line names the tool and the underlying Unreal Python API it wraps.
Grouped by domain; order within a domain is rough build priority.

### Actors and scene graph

- `spawn_actor`, `delete_actor`, `list_actors`, `get_scene_state` — done (MVP).

**`unreal.Rotator` takes `(roll, pitch, yaw)` positionally, not
`(pitch, yaw, roll)`.** Every tool here documents its rotation argument as
`(pitch, yaw, roll)` and builds the Rotator by keyword via
`remote_snippets.rotator()`; a positional build tilts the actor instead of
turning it and raises nothing. This was live in `spawn_actor` and
`set_actor_transform` until it was caught, and it is the same failure mode as
the `unreal.Color` `(b, g, r, a)` case that TOOL_BUILDING_GUIDE.md documents.
Any struct built from a tool argument should be built by keyword for the same
reason.
- `set_actor_transform`, `set_property`, `get_property`, all done. Read-back is as
  important as write.
  `get_property` is the read side of that pair and returns non-scalar values
  too: enums as their name, `Vector` as `[x, y, z]`, `Rotator` and the color
  types as named-field dicts, object references as `{class, name}`. Two things
  it has to work around, both confirmed by probe:
  - Almost nothing Unreal returns is JSON-serializable. `json.dumps` on a
    `Vector` raises `TypeError`, and an enum stringifies to
    `"<NetDormancy.DORM_AWAKE: 1>"` instead of `DORM_AWAKE`, so a conversion
    layer (`remote_snippets.jsonable`) sits between the read and `json.dumps`.
  - `unreal.Array` is a `MutableSequence`, **not** a `list`. An
    `isinstance(v, (list, tuple))` check silently misses every
    `Array[Name]`/`Array[X]` property and returns a repr string as if it were
    the value.
  - An unknown property name raises rather than returning a sentinel, and
    `bridge.run_python` reports any raise as a `RemoteCommandFailedError` with
    an empty `LogOutput`, so the reason would otherwise be invisible. The call
    runs inside `remote_snippets.guarded()`, which turns the raise into
    `{"success": False, "error": ...}`.
  - Presence must **not** be checked with `dir(actor)`: `layers` and
    `replicate_movement` are both documented `Actor` properties that `dir()`
    omits and that `get_editor_property` reads fine.
- `duplicate_actor`, wrapping `EditorActorSubsystem.duplicate_actor`, done.
  Two things to know: a zero offset stacks the copy exactly on the original,
  so the tool defaults to a 100-unit X offset; and passing it a null actor
  takes the editor down rather than returning the documented "none if it
  didn't succeed", so the tool resolves the actor to None first and reports a
  missing actor as an error instead.
- `attach_actor` / `detach_actor`, done and live-verified. Two corrections:
  - The attached socket is read back with
    `Actor.get_attach_parent_socket_name()`. There is **no
    `Actor.get_attach_component`**, and the symptom is nasty: the
    `AttributeError` inside the snippet surfaced as a
    `RemoteCommandFailedError` with an empty log, so the cause was invisible
    until probed directly. `SceneComponent.get_attach_socket_name` exists and is
    the same idea one level down.
  - The two sides use **differently named enums**: `AttachmentRule` when
    attaching (`KEEP_RELATIVE`, `KEEP_WORLD`, `SNAP_TO_TARGET`), but
    `DetachmentRule` when detaching, and that one has **no `SNAP_TO_TARGET`**.
    Defaulting both tools to `KEEP_WORLD` means an attach or detach does not
    move the actor, which is almost always what you want.
- `set_actor_folder`, done and live-verified. **A folder path at the outliner
  root reads back as the literal string `'None'`, not `''`**: `get_folder_path`
  returns a null `Name` and `str()` of a null `Name` renders as `'None'`. Both
  `set_actor_folder` and `get_selected_actors` normalize it, because an agent
  comparing folder paths would otherwise never be able to match the root.
- `tag_actor` / `find_actors_by_tag`, done and live-verified. `Actor.tags`
  reads back as an **`Array` at runtime even though the stub types it
  `Set[str]`**, so the tool writes a sorted list back rather than a set.
  With no tag at all, `find_actors_by_tag` switches to listing the whole tag
  vocabulary with per-tag counts, which is the only way to discover what tags a
  level actually uses: **`EditorActorSubsystem` has no tag search method at
  all** in 5.8, so this filters `get_all_level_actors()`. (PLAN.md previously
  suggested filtering `get_selected_level_actors`, which would have searched
  only the viewport selection.)
- `select_actors` / `get_selected_actors`, done and live-verified.
  `set_selected_level_actors` **replaces** the entire selection, silently
  discarding whatever the user had selected, so the tool adds one actor at a
  time via `set_actor_selection_state` by default and only takes the replacing
  path with `replace=True` + `confirm=True`. `select_all` and
  `invert_selection` are never used: they act on whatever the user happened to
  have selected.
- `set_actor_parent_component` — **deliberately not built.** `attach_actor`
  covers actor-level parenting, which is what the scene graph needs; attaching
  to a specific *component* socket is `Actor.attach_to_component` and belongs
  with the component tools if it is ever needed.
- `group_actors` / `ungroup_actors` — **not available, and the stub is wrong
  about it.** The Python stub lists `EditorActorSubsystem.group_actors`
  (returning a `GroupActor`) and `.ungroup_actors`, but the **live subsystem
  object has neither**; a `dir()` sweep for group/select/tag/parent members
  returns selection methods only. This is the clearest instance so far of the
  stub being a **superset** of the running API, which is the opposite of the
  `dir()`-omits-properties case already documented above. Neither the docs nor
  the stub is sufficient on its own; check the live object.

### Components

All four done: `add_component`, `remove_component`, `list_components`,
`set_component_property`.

**Neither API named here exists in this engine version.** `Actor.add_component_by_class`
and `Actor.destroy_component` are both absent; `dir()` on a live Actor has no
member that adds a component at all, and the only `add_component` matches
anywhere in the Python API belong to ControlRig, AnimOptimus, and K2Node. What
works instead, confirmed live:

- add: `unreal.new_object(ComponentClass, actor, name)`, which attaches the
  component immediately (component count went up on the same call).
- remove: `ActorComponent.destroy_component(component)`, passing the component
  itself. Its own docstring warns against using it on an actor-owned component
  "unless the owning actor is calling the function", which is what happens here
  and it worked, but it is not the sanctioned path.

Both are the kind of call that reports success and leaves nothing behind, so
each was read back through `list_components` rather than trusted: the component
count and the surviving component names were compared against an independent
`get_all_level_actors_components()` query filtered by outer actor.

### Meshes and geometry

- `set_mesh_material_slot` / `get_mesh_material_slot`, done (see Components).
- `get_mesh_bounds`, done, and it now handles **both** mesh kinds. A
  `StaticMesh` has `get_bounding_box()` returning a Box of two corner Vectors.
  A `SkeletalMesh` has **no `get_bounding_box` at all**; it has `get_bounds()`
  returning a `BoxSphereBounds` with an `origin` and a `box_extent`. The tool
  normalizes both to min/max/size/extent and also reports `sphere_radius` for a
  SkeletalMesh, since that is the bound the engine culls against.
  Wraps `StaticMesh.get_bounding_box()`, which
  returns a `Box` of two corner `Vector`s, not an origin/extent pair. Local
  space, so it does not move when an actor using the mesh moves. Note
  `Vector` is not iterable in this Python build, so the corners have to be read
  as `.x` / `.y` / `.z`; a comprehension over one fails.
- `generate_lods`, **not buildable as named.** `EditorStaticMeshLibrary.generate_lods`
  does not exist. The real call is `set_lods(static_mesh, reduction_options)`,
  wrapped by `set_mesh_lods`, which takes `percent_triangles` as a list of
  **fractions 0.0-1.0**, one per generated LOD (0.5 and 0.2 gives two extra
  LODs at half and a fifth of the triangles). There is no
  `StaticMeshReductionOptions.percent_triangle_reduction` field; the
  percentage lives on `StaticMeshReductionSettings.percent_triangles`.
- `import_static_mesh` / `import_skeletal_mesh`, done and live-verified with a
  synthesized Wavefront OBJ, since the project had no FBX. Three things the
  docs and stub do not tell you:
  - `import_asset_tasks` returns `None` whether it worked or not. Success is
    only visible in the task's `imported_object_paths`, which is why every
    importer here reports paths rather than a boolean.
  - Those paths are **already full object paths** (`/Game/M/M.M`). Appending
    `"." + name` to them, which reads like an obvious normalization, produces
    `/Game/M/M.M.M`.
  - One file can import as several assets: an OBJ with two `o` groups produced
    two meshes. So the result is a list.
  - `import_skeletal_mesh` uses `FbxImportUI` (with `import_mesh`,
    `import_as_skeletal`, `import_animations`, `import_materials`,
    `import_textures`, `skeleton`). All of those properties were confirmed
    present. Given an OBJ it still succeeds, producing a Skeleton plus a mesh,
    so the pipeline is proven end to end; **what remains unverified is skeletal
    content** (bones, animation tracks, a real FBX), since no FBX was
    available.
- `set_collision_complexity`, **not buildable as named, and the correct API is
  now known.** Built instead as `get_mesh_collision_info` (read) and
  `set_mesh_collision_preset` (write), both live-verified. What the live
  `StaticMeshEditorSubsystem` actually has:
  - `get_collision_complexity(static_mesh)` exists and returns a
    **`CollisionTraceFlag`** (`CTF_USE_DEFAULT`, `CTF_USE_SIMPLE_AS_COMPLEX`,
    `CTF_USE_COMPLEX_AS_SIMPLE`, `CTF_USE_SIMPLE_AND_COMPLEX`), **not** a
    `CollisionComplexity`. No `CollisionComplexity` symbol exists anywhere.
  - There is **no setter**. The setting only changes as a side effect of
    rebuilding hulls, so the write side is a preset over three real calls:
    `remove_collisions` (always first), then either
    `set_convex_decomposition_collisions(mesh, hull_count, max_hull_verts,
    hull_precision)` or `add_simple_collisions(mesh, shape_type)` where
    shape_type is `BOX` / `SPHERE` / `CAPSULE`.
  - **`hull_count` is a ceiling, not a target.** On a convex solid every
    parameter combination yields exactly 1 hull, because one hull is already
    sufficient; on a concave mesh, 2 yields 2 hulls, 4 yields 4, and 8 yields 5
    because 5 is all the geometry needs. A convex test mesh therefore cannot
    tell you whether `hull_count` does anything at all.

### Materials and textures — this is a bigger domain than the MVP list suggests

Materials are the single area where an LLM is most likely to fall into a
guess-and-fail loop, because a material graph is a real node graph with
typed pins, not a flat property bag. The parameter-setting tools already
built only cover Material *Instances*; they say nothing about building the
underlying Material graph itself, which is where most of the actual
authoring work happens.

- `create_material`, `create_material_instance`, `set_material_scalar_parameter`, `set_material_vector_parameter` — done (MVP).

  **`create_material` never overwrites.** It fails if the name is taken; use a
  new one. This is not a limitation but a requirement: `AssetTools.create_asset`
  defaults `replace_existing` to True, which **pops an editor dialog asking
  whether to overwrite**, and the dialog blocks the Remote Control endpoint
  until a human clicks it, so the caller sees a timeout rather than a question
  and cannot answer it. The flag is passed as the 6th positional argument, since
  the 5th is `calling_context` (a Name) — passing False 5th raises
  `Cannot nativize 'bool' as 'Name'`.

  `create_material_instance` sets the parent explicitly and then **reads it
  back**, because it previously reported the parent it was asked for without
  checking and produced instances whose `parent` was null.
- `set_material_texture_parameter`, done. Wraps
  `MaterialEditingLibrary.set_material_instance_texture_parameter_value`,
  which takes a `MaterialParameterAssociation` defaulting to `GLOBAL_PARAMETER`.
- `get_material_parameter_list`, done. Wraps the four
  `MaterialEditingLibrary.get_*_parameter_names` calls (scalar, vector,
  texture, static switch) and reports `expression_count` alongside them, so a
  blank material reads as "exposes nothing, graph has 0 nodes" rather than as
  an empty success that could be mistaken for "no parameters exist".

  **Correction to TOOL_BUILDING_GUIDE.md:** the guide's Example B tells you to
  confirm a non-empty graph with
  `MaterialEditingLibrary.get_all_material_expressions`. That function does not
  exist in this build. The real names are `get_material_expressions` and
  `get_num_material_expressions` (no `all_`).
**Graph authoring is built and live-verified (17 tools, `tools/material_graph.py`).**
62 live checks build a material from empty to wired and compiling, each write
read back through a different call. What follows is what the live editor
actually does, which differs from the docs in six places.

- **Nodes have no identity but their title.** `MaterialExpression` exposes no
  id, name or guid; its `desc` (the editable node title) is the only handle that
  survives a recompile. Every tool takes a selector matched in a defined order —
  `Brightness` by title, `Multiply@120,80` by class and position, `Multiply` by
  class, `#2` by index — and **reports an error when a selector matches more than
  one node** instead of picking one. Wiring the wrong node still compiles and
  renders wrong, which is the worst failure this tool family has.
- **`get_inputs_for_material_expression` does NOT return pin names.** It returns
  the *expressions currently wired into* each input, so an unconnected pin
  stringifies to `'None'` and a connected one to an object repr. Its **length**
  is the pin count, which is useful; its contents are not names. A Multiply
  appears to have inputs `['None', 'None']`. **Input pin names are not readable
  from Python at all**, so node-to-node connections are attempted and then
  verified by read-back, and `list_material_expressions` reports
  `{count, wired, names_discoverable: false}` rather than inventing names.
  Output pin names *are* discoverable via
  `get_material_expression_output_names` (a Constant's is `''`; a TextureSample's
  are `RGB`, `R`, `G`, `B`, `A`, `RGBA`), so those are validated before calling.
- **Each node class holds its parameter default on a different property**, and
  `r` — the obvious guess — is right only for `MaterialExpressionConstant`.
  Measured by attempting each property on each class:

  | class | `r` | `default_value` | `parameter_name` | `texture` |
  |---|---|---|---|---|
  | `MaterialExpressionConstant` | yes | - | - | - |
  | `ScalarParameter` | - | yes (float) | yes | - |
  | `VectorParameter` | - | yes (**LinearColor**) | yes | - |
  | `StaticSwitchParameter` | - | yes (bool) | yes | - |
  | `TextureSampleParameter2D` | - | - | yes | yes |

  `MaterialExpressionConstant3Vector` and `Multiply` expose none of these; only
  `desc`.
- **A parameter node with an empty `parameter_name` is not a parameter.**
  Creating a `TextureSampleParameter2D` and leaving the name unset adds nothing
  to `get_texture_parameter_names()`: the node reads as a parameter in the graph
  while being invisible to every instance. Both `create_material_expression` and
  `create_material_parameter` set it, and read the name lists back.
- **A `VectorParameter`'s `default_value` is a `LinearColor`, not a `Vector`.**
  A 3-number default has to be widened with alpha or the editor raises
  `Cannot nativize 'Vector' as 'LinearColor'`.
- **`set_material_default_static_switch_parameter_value` does not exist.** Only
  the getter does, so PLAN.md's guess of a base-material switch setter was wrong
  by the same route as `generate_lods`. The default actually lives on the
  `MaterialExpressionStaticSwitchParameter` node's own `default_value`, so
  `set_material_static_switch_parameter` finds the node by name and writes it
  there, then reads back through the real getter.
- `connect_material_input` is a **separate call** from
  `connect_material_expressions`: the destination is a `MaterialProperty`
  (`MP_BASE_COLOR`, `MP_NORMAL`, ...), not a node pin. There is no overload
  accepting both.
- `get_child_instances` returns **`AssetData`, not loaded objects**, so
  `get_path_name()` does not exist on them; build the path from
  `AssetData.package_name` plus the object name.
- **`recompile_material` returns its shader errors rather than raising**, so a
  material can fail to compile while every tool reports success. Every mutating
  tool recompiles and returns `recompile_errors`. All of them take
  `recompile=False` to skip it: compiling a material whose domain or shading
  model just changed blocks the editor's main thread long enough that the Remote
  Control endpoint stops answering, so building 20 nodes with the recompile on
  by default will wedge the editor.
- `layout_material_graph` wraps `layout_material_expressions`, since an
  agent-built graph is otherwise all stacked at the origin and unreadable to a
  human.
- `set_material_domain_and_shading_model`, done and live-verified.
  `material_domain` is a plain settable property. **`shading_model` has no
  setter** and `shading_models` (the UE5 array that replaced it) is not exposed
  either, but `set_editor_property("shading_model", ...)` works. Read-back is a
  repr string (`"<MaterialDomain.MD_UI: 5>"`), so the tool compares on the enum
  *name* and fails if the value did not take. Destructive, because a domain
  change makes a material stop rendering on every mesh that uses it with no
  editor warning, and because saving recompiles the shader — that call needs a
  longer bridge timeout than the 8s default.
### Material functions (built and live-verified, 9 tools)

A MaterialFunction is a reusable subgraph. Its API is a *parallel* set of
methods, not the same ones with a different argument, and that is the whole
source of friction:

- `create_material_expression_in_function` exists, but there is **no
  `connect_material_expressions_in_function`**. Node-to-node wiring inside a
  function reuses the plain `connect_material_expressions` — except that the
  plain one resolves nodes with `get_material_expressions`, which **rejects a
  MaterialFunction outright** ("Cannot nativize 'MaterialFunction' as
  'Material', allowed Class type: 'Material'"). So
  `connect_material_function_expressions`, `set_material_function_expression_property`
  and `get_material_function_expression_property` exist as function-specific
  variants, because the material versions cannot be used there. Delete and
  layout do have `_in_function` variants.
- **A MaterialFunction has no readable `expressions` property** (the base
  Material does; on a function it raises). Read nodes with
  `get_material_function_expressions`, count with
  `get_num_material_expressions_in_function`.
- **The two ends take different enums.** `input_type` is a `FunctionInputType`
  member spelled `FUNCTION_INPUT_*`; the output type is a
  `CustomMaterialOutputType` member spelled `CMOT_*`. Passing the wrong one
  raises "Cannot nativize 'CustomMaterialOutputType' as 'FunctionInputType'".
- **There is no output_type property at all** on a FunctionOutput. Only
  `output_name` is settable; `CustomMaterialOutputType` exists as an enum but is
  unreachable from the node. So `create_material_function` has no output_type
  argument, and the return type follows from what is wired into the output pin.
- **A function needs a FunctionInput and a FunctionOutput to be usable at all**,
  so `create_material_function` makes one of each with names and types set.
- `delete_all_material_expressions_in_function` **leaves the FunctionOutput in
  place** (verified: 4 nodes in, 1 out, and it is the output), since a function
  with no output cannot return anything.

### Levels and world (built and live-verified, 5 tools)

Until these existed every tool was scoped to whatever level happened to be open,
and nothing could see what else existed.

- `list_levels`, `get_current_level`, `save_level`, `load_level`, `new_level`,
  all through `EditorLoadingAndSavingUtils` (`load_map`, `save_current_level`,
  `save_map`, `new_blank_map`, `get_dirty_map_packages`).
- **A level is a `.umap`, not a `.uasset`.** Filtering the asset list on
  `.uasset` finds no levels at all; skip directory entries (trailing slash) and
  sub-objects (the `:PersistentLevel.` form) and let `isinstance(obj, World)`
  decide instead.
- **`load_level` is destructive and unavoidably so.** It discards every unsaved
  change in the open level with no prompt; `load_map` does exactly what it says
  and there is no save-first option. So call `get_current_level` and check
  `is_dirty` first. The result reports `discarded_dirty_packages` after the fact.
- **A `World` exposes no `get_actors()` and no `get_levels()` from Python**, so
  the actor count comes from `EditorActorSubsystem.get_all_level_actors()`.
- **This project is World Partitioned, and `load_map` returns before its cells
  finish streaming.** The same level was observed reading 78 actors, then 142,
  then 138. `load_level` therefore polls from the client until two consecutive
  equal readings and reports `actor_count_samples` and `streaming_settled`. The
  polling cannot live in the snippet: that runs on the editor's main thread,
  where a loop cannot let the engine tick, and `SystemLibrary.delay` needs a
  `latent_info` a plain exec cannot supply.
- `new_level(save=False)` leaves an unsaved unnamed level; `save=True` writes it
  and refuses an existing name.

### Play-in-editor and console (built and live-verified, 7 tools)

- `get_play_state`, `start_play_in_editor`, `start_play_in_editor_simulate`,
  `stop_play_in_editor`, `wait_for_play_state`, `execute_console_command`,
  `get_console_variable`, via `LevelEditorSubsystem`.
- **A begin/end request lands on a later tick**, so the state immediately after
  a request is the state before it. `get_play_state` and the two request tools
  report the request and the observed state separately, and
  `wait_for_play_state` polls in separate round trips.
- **`unreal.WorldType` does not exist**, and `World` has no `get_world_type()`,
  so a world cannot be classified by type. `is_simulating` is derived from the
  game world's path, which is named `UEDPIE_0_<level>` in a PIE session.
- **`get_editor_world()` returns None while PIE is running**, because the editor
  world is replaced by the game world. So "no editor world" is a normal reading
  during play, and `execute_console_command` has to prefer the game world —
  requiring the editor world first made every command fail exactly when a
  command is most useful.
- `execute_console_command`'s 3rd parameter is `specific_player`, a
  PlayerController, **not a bool**; passing False raises "Cannot nativize 'bool'
  as 'SpecificPlayer'".
- **`get_console_variable_*` returns the type's default for an unknown name**
  rather than raising, and there is no `ConsoleManager` in the Python API, so
  there is no existence check. `get_console_variable` rejects a value equal to
  its type's default and reports the variable as missing. Caveat: a variable
  genuinely set to 0, False or "" is reported missing, and nothing better is
  available through this API.
- **Console commands return no output.** That is a property of the editor, not
  of the tool, and it is reported as `output: None` so nobody waits for it.

### Animation, instance level (built and live-verified, 7 tools)

`set_animation`, `set_animation_mode`, `play_animation`, `stop_animation`,
`pause_animation`, `set_play_rate`, `get_animation_state`. Not animation
*authoring*: no montage or Animation Blueprint graph tools.

A SkeletalMeshComponent has far less API than it looks:

- **No `get_animation()`.** The assigned asset lives on the
  `AnimSingleNodeInstance` returned by `get_anim_instance()`, read with
  `get_animation_asset()`. In blueprint mode there is no single-node instance,
  so the asset reads as None, which is correct.
- **No `get_playback_position()` and no `get_playback_length()`** on the
  component, and the anim instance's position and length getters are
  C++-protected and raise AttributeError. Length is therefore read from the
  `AnimSequence` itself, which does expose `get_play_length`. Position is not
  readable at all from Python and is reported as None.
- **No `pause()`.** `pause_animation` holds the frame by setting the play rate
  to 0, which is why it exists separately from `stop` (which would rewind).
- `play()` **recreates the anim instance**, and the replacement has no asset on
  it yet, so `play_animation` reads the asset and length *before* calling play.
- **Switching mode away from single_node clears the assigned asset.** Verified:
  set, switch to blueprint, switch back, and the animation reads as unassigned.
  After `set_animation_mode`, call `set_animation` again.
- Neither the asset nor the play rate is readable via `get_editor_property`; both
  raise. `get_play_rate()` and `is_playing()` do exist on the component.

### Collision and physics, instance level (built and live-verified, 7 tools)

`get_collision_state`, `set_collision_enabled`, `set_collision_profile`,
`set_collision_object_type`, `set_collision_response`, `set_simulate_physics`,
`apply_physics_impulse`. This is component-level, in the level; the mesh-asset
side is `set_mesh_collision_preset`.

- **Channels are `unreal.CollisionChannel` with `ECC_*` members**, and responses
  are `unreal.CollisionResponseType` with `ECR_*`. Neither `unreal.CollisionResponse`
  (a struct with no ECR_ members) nor bare module-level `ECC_Pawn` exists, and an
  int is rejected with "Failed to convert parameter 'channel'".
- **Object type and response are different things.** A component that is a Pawn
  but has Pawn set to Ignore passes through other pawns. `set_collision_enabled`
  only turns collision on or off wholesale, and there is deliberately no plain
  "on": whether something should block, overlap or merely be traceable is a real
  choice.
- **A named profile overwrites every channel at once**, so
  `set_collision_profile_name` undoes any per-channel work: profile first, then
  channels. It also moves the object type and the enabled state, so restoring in
  the reverse order leaves the profile reading back as "Custom".
- **`set_simulate_physics` reports whether it took effect.** A component with no
  physics body silently refuses to simulate, so the tool returns
  `took_effect: False` with an explanation rather than success for a change that
  never happened.
- **Physics does not run in the editor viewport**, and a PIE session only ticks
  while the game window has focus. Measured: an actor with simulation on reports
  a velocity of [0, 0, 0] indefinitely and an impulse changes nothing. These
  tools can configure and confirm simulation and can read velocity, but cannot
  observe motion on their own; anything needing frames to pass has to happen while
  someone is watching the game window.
- A PrimitiveComponent has **no `get_component_location()` and no
  `get_attach_location()`**; `get_world_location()` is the form that exists.
- `create_material_function` — reusable node subgraphs, `MaterialFunctionFactoryNew`, for anything built more than once.

**Texture creation and editing — yes, this is possible, with caveats:**

- `generate_texture_from_pixels` — `AssetToolsHelpers` + the Modeling Tools `CreateTextureObjectParams`/`UModelingObjectsCreationAPI.CreateTextureObject` path, builds a real `UTexture2D` asset from raw pixel data (an agent-generated gradient, noise pattern, flat color swatch, or a buffer handed in from an external image-gen step in Phase 2).
- `set_texture_properties` — compression settings, sRGB flag, mip gen settings, `Texture2D.set_editor_property`.
- `create_render_target` / `render_material_to_texture` — `MaterialEditingLibrary` has no direct bake-to-texture call in pure Python as of this writing; this likely needs `KismetRenderingLibrary.draw_material_to_render_target` plus a render target asset, confirm against a live editor before committing to the exact call shape.
- `import_texture` — `AssetImportTask` with `TextureFactory`, for anything coming from outside the engine (the far more common path than pixel-buffer generation).
- **What's realistically out of reach in pure Python:** procedural texture painting with brush strokes (that's the Texture Paint editor mode, mouse-driven); full node-graph texture generation tools like Substance-style graphs aren't natively in UE at all. Scope texture generation to "flat buffers in, `UTexture2D` out," not interactive painting.

### Lighting

- `set_light_properties` (intensity, color, temperature, source radius) — per light-component class — done, plus `get_light_properties`.
- `build_lighting` — `EditorLevelLibrary` lightmass build trigger + completion poll, since this is slow and the agent needs to know when it's actually done, not just dispatched.
- `set_sky_atmosphere_params` / `set_exponential_fog_params` — common "mood" controls for AI-driven scene dressing — done, plus `get_sky_atmosphere_params` / `get_exponential_fog_params`.

Two API facts the lighting tools depend on, both confirmed by probe against
the live 5.8 editor, because either one fails silently:

- `setattr` is rejected on light and fog component properties. Every write
  goes through `set_editor_property`; the dedicated `set_fog_density`-style
  methods work too.
- On `SkyAtmosphereComponent`, `ground_albedo` is an `unreal.Color` (whose
  positional fields are `b, g, r, a`) while `rayleigh_scattering` and
  `mie_scattering` are `unreal.LinearColor` (`r, g, b, a`). Two
  same-intent color arguments with opposite channel order, on one class.
  Both are built with keyword arguments in `tools/lighting.py`.

`unreal.LinearColor`'s field order is also now confirmed as `(r, g, b, a)`,
which closes the open question left in TOOL_BUILDING_GUIDE.md's gotcha 1.

### Levels and world

- `load_level` / `save_level` / `create_level` — `EditorLevelLibrary.load_level`, `save_current_level`, `new_level`.
- `stream_level` (add/remove a sub-level) — `EditorLevelUtils.add_level_to_world`.
- `set_world_partition_region_loaded` — for large open-world projects, load only the relevant cell instead of the whole map.
- `get_level_bounds` — spatial awareness before placing actors.

### Blueprints

- `compile_blueprint` — done (MVP).
- `create_blueprint` — `AssetToolsHelpers.get_asset_tools().create_asset(..., Blueprint, BlueprintFactory)`.
- `add_blueprint_variable` — `BlueprintEditorLibrary.add_member_variable`.
- `add_blueprint_function_call_node` / `add_blueprint_event_node` — graph editing via `K2Node` creation, the deferred "C++ plugin for graph wiring" extension from the original plan; likely the first thing that needs the C++ escape hatch since pure-Python Blueprint graph editing support is thin.
- `get_blueprint_compile_errors` — parse the compiler log, not just success/fail, so the agent gets an actual error message to react to.
- `set_blueprint_parent_class` — `BlueprintEditorLibrary.reparent_blueprint`.

### Animation

- `create_animation_blueprint` — `AssetToolsHelpers` + `AnimBlueprintFactory`.
- `add_anim_state` / `add_anim_transition` — `AnimationStateMachineLibrary` (available in recent UE Python API).
- `import_animation_sequence` — `AssetImportTask` with `FbxImportUI` animation settings.
- `set_skeletal_mesh_physics_asset` — `SkeletalMesh.set_editor_property('physics_asset', ...)`.
- `retarget_animation` — `AnimationLibrary`/IK Rig Python API (newer engine versions only; confirm availability before committing to this one).

### Physics and collision

- `set_simulate_physics` — `PrimitiveComponent.set_simulate_physics`.
- `set_collision_profile` — `PrimitiveComponent.set_collision_profile_name`.
- `set_collision_response` — per-channel overrides, `set_collision_response_to_channel`.
- `add_physics_constraint` — `PhysicsConstraintComponent` setup between two components.
- `create_physical_material` — `AssetToolsHelpers` + `PhysicalMaterialFactoryNew`, then friction/restitution properties.
- `simulate_physics_step` (PIE-only) — paired with the PIE tool below, lets the agent actually watch physics behavior instead of guessing.

### Niagara / VFX — mostly instance-level control, not graph authoring

Niagara's actual module/script graph (the thing you see when you double-click
a module inside the Niagara editor) is built on the same underlying node-graph
system as Blueprints and Materials, and like Blueprint graphs, Python
bindings for editing it node-by-node are not solidly documented. Scope the
tool set to what's reliably scriptable: spawning, parameterizing, and
assembling systems out of existing modules, not writing new HLSL-equivalent
particle logic from scratch.

- `spawn_niagara_system` — `NiagaraFunctionLibrary.spawn_system_at_location`.
- `set_niagara_parameter` — `NiagaraComponent.set_variable_float`/`set_variable_vec3`/`set_variable_linear_color`/`set_variable_int`, the most-used call by far once a system exists (color, spawn rate, lifetime, velocity knobs exposed as user parameters).
- `get_niagara_user_parameters` — discovery call, mirrors `get_material_parameter_list`'s role: list what a system actually exposes before guessing a name.
- `create_niagara_system_asset` — scaffolds a new empty system, `NiagaraSystemFactoryNew`.
- `add_niagara_emitter_from_template` — `NiagaraEditorModule`/`FNiagaraEditorUtilities` emitter templates (fountain, fire, smoke) are the realistic starting point for "build me an effect," since hand-assembling a particle system from bare modules is the single most repetitive, failure-prone task in Niagara even for human artists.
- `set_niagara_renderer_material` — swap the sprite/mesh renderer's material reference, `NiagaraRendererProperties`.
- **Explicitly out of scope for pure Python:** writing new Niagara modules/scripts, editing the particle update/spawn graphs node by node. If this is ever needed, it's a C++ plugin or Editor Utility Widget job, not a Python snippet.

### Audio

- `import_sound_wave` — `AssetImportTask` with `SoundFactory`.
- `create_sound_cue` — `AssetToolsHelpers` + `SoundCueFactoryNew`, wire in a wave player node.
- `play_sound_at_location` (PIE-only, for verification) — `GameplayStatics.play_sound_at_location`.
- `set_sound_attenuation_settings` — distance/falloff config on a `SoundAttenuation` asset.

### Asset management (general)

- Import sources are deliberately allowed to live **outside** the project, since
  a mesh or texture is normally exported from a DCC tool somewhere else, so
  `resolve_path_in_project` does not apply. What guards them instead is
  `security.check_import_source` (the file must exist and carry a suffix on an
  importable allowlist) and `security.check_destination_path` (must be under
  `/Game/`). **`/Engine` is refused on purpose**: a tool that can write there
  corrupts the install rather than the project. Plugin content is refused for
  the same reason. The prefix check includes the trailing slash, because
  `/Gameplay` passes a `startswith("/Game")` test and is not a content path.
- `save_asset`, done. Worth having for a non-obvious reason: setting a material
  instance's parent changes it in memory, and although `get_child_instances`
  does surface an unsaved instance, the parent link is not what registers it.
  Save before relying on reverse lookups across sessions.
- `import_texture`, done and live-verified against a synthesized PNG. **`srgb`
  and `compression_settings` are not on `TextureFactory`** (it exposes neither,
  and setting them raises `AttributeError`); they are properties of the
  imported `Texture2D`, so the tool sets them after the import and saves.


- `import_asset` — generic `AssetImportTask` dispatcher (FBX, textures, audio, CSV data tables) behind one interface, since the per-type tools above all reduce to this.
- `list_assets_in_path` — `EditorAssetLibrary.list_assets`.
- `rename_asset` / `move_asset` / `duplicate_asset` — `EditorAssetLibrary` equivalents, each going through the existing path-allowlist security check.
- `delete_asset` — already named as a destructive example in the security section; needs the same confirm-gate pattern as `delete_actor`.
- `get_asset_references` / `get_asset_dependencies` — `AssetToolsHelpers.get_asset_tools().find_references`/dependency graph walk, important before any delete so the agent can see blast radius first.
- `fix_up_redirectors` — `AssetToolsHelpers`, cleanup after moves/renames.

### Data assets and structs

- `create_data_table` — `AssetToolsHelpers` + `DataTableFactory`, from a CSV or JSON source.
- `get_data_table_row` / `set_data_table_row` — `DataTableFunctionLibrary`.
- `create_struct_asset` / `create_enum_asset` — `UserDefinedStructFactory`/`UserDefinedEnumFactory`, useful for agent-authored gameplay data.

### Multiplayer and networking — mostly property flags, not a dedicated API

There's no special "networking API" to wrap; replication is configured
through ordinary UPROPERTY/UFUNCTION metadata that's visible and settable
through the same `get_editor_property`/`set_editor_property` mechanism
already used elsewhere. The value of naming these as tools isn't API
novelty, it's that replication setup is extremely easy to get subtly wrong
(forgetting to mark a function a RepNotify callback, mismatching a
replication condition with how the property is actually used), so a tested
tool that sets the whole correct bundle in one call is worth far more here
than in domains where the raw API is already simple.

- `set_actor_replicates` — `Actor.set_editor_property('replicates', True)` + `set_replicate_movement`, the two flags that are easy to set independently and forget one of.
- `set_property_replication` — configures a Blueprint variable's replication condition (`None`/`OwnerOnly`/`SkipOwner`/etc.) via the Blueprint variable metadata API, since this is per-variable, not a single actor-level flag.
- `add_rep_notify_function` — scaffolds the paired "mark variable ReplicatedUsing, create the matching `OnRep_<Name>` function stub" combination, because doing only half of this is a common, silent bug (the callback is referenced by name as a string internally; a typo doesn't error at compile time the way a C++ mismatch would).
- `set_actor_network_relevancy` — `always_relevant`, `net_cull_distance_squared`, `min_net_update_frequency`, the "why isn't this actor showing up on remote clients" knobs.
- `create_game_mode` / `set_default_game_mode` — `AssetToolsHelpers` + Blueprint parented to `GameModeBase`, then `WorldSettings.set_editor_property('default_game_mode', ...)`, since a multiplayer test is dead on arrival without this wired up first.
- `verify_replication_setup` (read-only sanity check) — walks a Blueprint's variables/functions and flags properties that look like gameplay state (health, position, inventory count) but aren't marked replicated, and RepNotify functions whose paired variable isn't actually set to `ReplicatedUsing`. Not a guarantee, a lint pass; genuinely useful because this class of bug is invisible in a single-player PIE session and only shows up under a real network test.
- **Out of scope / needs the C++ escape hatch:** Replication Graph configuration, custom `NetSerialize` implementations, anything below the Blueprint/property layer. Pure Python has no reach into the low-level replication driver.

### MetaHuman — real, documented, and recent (MetaHuman 5.7 / Nov 2025)

As of MetaHuman 5.7, shipped alongside Unreal Engine 5.7, Epic ships an
actual Python/Blueprint API (`MetaHumanCharacterEditorSubsystem`) covering
sculpting, conforming, wardrobe, rigging, and texture/assembly operations
for batch automation — this isn't a guess or an extrapolation from
unrelated APIs, it's the documented feature set. Requires UE 5.8+ and the
MetaHuman Character plugin enabled. The common pattern across nearly every
non-trivial call: register the character with the subsystem for editing,
perform edits, commit each change (so it's reflected in the live viewport
and serialized to the asset), then remove the character from the subsystem
when done — a sequence worth wrapping once as a context-manager-style
helper rather than repeating in every tool.

- `create_metahuman_character` — instantiate a new `MetaHumanCharacter` asset from a preset/template.
- `set_metahuman_body_type` / `sculpt_metahuman_body` — body conform and blend-space sculpting calls on the editor subsystem.
- `set_metahuman_face_landmarks` — face sculpting via landmark manipulation, the programmatic equivalent of dragging control points in MetaHuman Creator.
- `set_metahuman_skin_tone` / `set_metahuman_wardrobe_item` — material/wardrobe assembly calls.
- `auto_rig_metahuman` — triggers the auto-rig request through the subsystem, this is the step that turns a sculpted character into something animatable.
- `export_metahuman_to_level` — assembles and places the finished character as an actor, the natural hand-off point back into the rest of this tool set (it's now just an actor `set_actor_transform` etc. can operate on).
- `batch_generate_metahuman_variants` — the actually-impressive one: given a base template and a list of parameter deltas (skin tone, body type, wardrobe), spit out N distinct characters in one call. This is squarely what the API was built for (batch automation) and is the kind of task that's brutally repetitive by hand.
- Build this domain after the PIE/verification infrastructure, not before: a MetaHuman tool that silently produces a broken character (bad topology, missing rig) is worse than not having the tool, and the only way to catch that is rendering a viewport screenshot back for inspection.

### Landscape and foliage

- `sculpt_landscape_heightmap` — `LandscapeEditorObject`/`EditorLevelLibrary` heightmap import, or direct height data edits.
- `paint_landscape_layer` — layer weight painting via `LandscapeProxy` API.
- `add_foliage_type` / `paint_foliage_instances` — `InstancedFoliageActor`/`FoliageEditorSubsystem` (API coverage here is spotty; verify before committing to full scope).

### Sequencer / cinematics

- `create_level_sequence` — `AssetToolsHelpers` + `LevelSequenceFactoryNew`.
- `add_actor_to_sequence` / `add_camera_cut_track` — `LevelSequenceEditorBlueprintLibrary`.
- `add_keyframe` — `MovieSceneSequence` track/section API, for simple agent-driven cutscenes.
- `render_sequence_to_movie` — `MoviePipelineQueueSubsystem`, genuinely useful output artifact for a build-and-show loop.

### UMG / UI

- `create_widget_blueprint` — `AssetToolsHelpers` + `WidgetBlueprintFactory`.
- `add_widget_to_viewport` (PIE-only) — `WidgetBlueprintLibrary.create`.
- `set_widget_text` / `set_widget_visibility` — generic `get_editor_property`/`set_editor_property` on resolved widget references.

### Play-in-editor and verification (ties everything above together)

- `launch_pie` / `stop_pie` — `EditorLevelLibrary.editor_play_simulate` or `UnrealEditorSubsystem` PIE control.
- `capture_viewport_screenshot` — `AutomationLibrary.take_high_res_screenshot`, the already-ranked "good, close to core" extension.
- `get_output_log` (filtered by severity/time window since last call) — makes the PIE loop actually useful: run, then read back what happened, not just that it launched.
- `run_automation_test` — `AutomationController` Python bindings, if the project already has functional tests defined.

### Project and build

- `get_project_settings` / `set_project_setting` — narrow, allowlisted subset only (this is explicitly a security-sensitive surface, needs its own tier).
- `package_project` — `subprocess`-free trigger via `AutomationTool` Python bindings if available, otherwise explicitly out of scope per the no-subprocess security rule.
- `get_compile_errors` (C++ side, if the project has C++ modules) — likely requires the C++ plugin escape hatch, not pure Python.

---

## Presets: composite tools, not more primitives

Everything above is a primitive: one tool, one Unreal API call (or a small
cluster of calls needed to do one coherent thing). But a lot of real work is
the *same sequence* of primitives, every time, with different parameters.
If the orchestrator has to rediscover that sequence by chaining primitives
itself on every run, we've just moved the try-and-fail loop up one layer
instead of removing it. A preset is a single named tool, pre-written and
pre-tested, that internally calls several primitives in the right order
with the right error-handling between steps — the same reason named tools
beat raw `execute_python` in the first place, applied recursively.

Rule for what qualifies as a preset: it must be a sequence a human would
actually do by hand, repeatedly, in roughly the same shape every time.
Not "spawn 3 arbitrary actors," that's not a real recurring task. Things
like these are:

- `set_dressing_pass(theme, area_bounds)` — spawn + place + material-tint a
  themed batch of scene objects (e.g. "clutter this room like a workshop"):
  `spawn_actor` × N, `set_actor_transform` with randomized jitter within
  bounds, `set_material_scalar_parameter` for variation. The single
  highest-value preset for "make a small game" style prompts, since set
  dressing is pure repetition by hand. Built as
  `set_dressing_pass(class_path, count, center, radius, material_path=...)`:
  the material-tint step is there, but driven by `set_mesh_material_slot`
  (per-actor `MaterialInstanceDynamic`) rather than by
  `set_material_scalar_parameter`, which writes to a shared instance asset and
  so cannot vary one actor from another. There is no `theme` lookup table yet;
  the caller passes the class and material paths directly.
- `build_and_test_pie(level, duration_seconds)` — `launch_pie`,
  `get_output_log` filtered to warnings/errors only, `capture_viewport_screenshot`,
  `stop_pie`, bundled as one call returning a pass/fail verdict plus the
  evidence. This is the actual build-test-fix loop Phase 2 needs, expressed
  as a single tool instead of four calls the orchestrator has to sequence
  and get the timing right on every time.
- `create_pickup_item(mesh_path, blueprint_name, variable_name)` — a genuinely
  common beginner-tutorial pattern: `create_blueprint` parented to Actor,
  `add_component` (static mesh + collision sphere), `add_blueprint_variable`,
  wire an overlap event to a "grant and destroy" sequence. Doing this by
  hand in the editor is a 15-step tutorial; as a preset it's one call.
- `setup_basic_multiplayer_actor(blueprint_name, replicated_properties)` —
  `create_blueprint`, `set_actor_replicates`, `set_property_replication` for
  each listed property, `add_rep_notify_function` where appropriate, then
  `verify_replication_setup` as a built-in self-check before returning
  success. Bundles the "easy to forget one flag" problem named above into
  one call that can't partially forget a step.
- `apply_material_variant_set(base_material, variants)` — given one base
  Material and a list of `{name, scalar_overrides, vector_overrides,
  texture_overrides}` dicts, creates one Material Instance per variant in a
  single call. Common for anything with team colors, rarity tiers, or
  damage-state skins — a flat list-comprehension-shaped task by hand that
  shouldn't cost N separate tool round-trips.
- `light_scene_preset(mood)` — named presets (`"daylight"`, `"interior_warm"`,
  `"horror"`, `"golden_hour"`) that bundle `set_light_properties` +
  `set_sky_atmosphere_params` + `set_exponential_fog_params` into one
  defensible starting point, rather than the agent guessing intensity/color
  values from scratch on every scene and iterating by trial and error.
- `spawn_vfx_with_sound(niagara_system, sound_cue, location)` — the
  "explosion," "pickup sparkle," "footstep dust" pattern: one Niagara spawn
  plus one attenuated sound play, together, since these two are requested
  together in practice far more often than either is requested alone.
- `batch_import_asset_folder(folder_path, asset_type)` — walks a folder of
  source files (FBX/PNG/WAV) and runs the right `import_asset` call on each
  with sensible default import settings, instead of N individual import
  calls for what is, by far, usually a "bring in this whole folder of art"
  request rather than a one-off.

### Where presets live in the architecture

Presets are not a new transport or a new security tier; they're ordinary
MCP tools in `src/unreal_mcp/tools/`, each one calling into the existing
primitive functions directly (Python function calls within the server
process) rather than round-tripping through `bridge.run_python()` per step
unless a step genuinely needs fresh state read back from Unreal first. Each
preset still goes through `security.enforce_tier()` same as any other tool,
tiered at least as strict as its most destructive step. Build presets only
after the primitives they depend on are live-verified individually; a
preset built on an unverified primitive just compounds the untested surface
instead of reducing it.

### What this means for build order

Not all of this gets built at once. Rough phasing, each phase live-verified
before moving to the next, same discipline as the MVP:

1. **Already done:** actors/scene graph core, materials core (instance parameters only), `compile_blueprint`.
2. **Next, highest value per tool:** components, asset management (list/move/rename/delete with the dependency-check tool), lighting basics, mesh import, material graph authoring (`create_material_expression`/`connect_material_expressions`), replication flag tools.
3. **Then:** physics/collision, landscape/foliage, data tables, Niagara instance-level control (spawn/parameter/template), multiplayer presets (`setup_basic_multiplayer_actor`).
4. **Then, needs PIE infrastructure first:** play-in-editor launch/stop, screenshot capture, filtered log readback, texture generation (needs a viewport/asset check to confirm it actually looks right). This unlocks the actual build-test-fix loop Phase 2 depends on, so it should land before Phase 2 starts in earnest, not after.
5. **Then, once primitives from phases 2–4 are live-verified:** the preset layer (`set_dressing_pass`, `build_and_test_pie`, `create_pickup_item`, `apply_material_variant_set`, `light_scene_preset`, etc.), since presets compound whatever primitives they call and are only worth building on a tested foundation.

   Three of these were built ahead of that order, because every primitive
   they call was already live-verified in phase 1: `light_scene_preset`,
   `set_dressing_pass`, and `apply_material_variant_set`. `light_scene_preset`
   needed the phase-2 lighting primitives, which were built first for exactly
   that reason. The remaining presets still wait on phases 2–4.
   `light_scene_preset` returns each primitive's getter output as read-back
   evidence rather than asserting success from the setter's return value.
6. **Last, and likely needs the C++ escape hatch or has thin Python coverage:** Blueprint graph node wiring, Niagara module/script graph editing, Sequencer keyframing, Animation Blueprint state machine editing, Replication Graph configuration.
7. **MetaHuman, as its own track once PIE verification exists:** the API is real and documented (MetaHuman 5.7+, UE 5.8+) but every operation needs visual confirmation to catch silent failures (bad topology, missing rig), so it depends on phase 4's screenshot tooling rather than slotting in by API-maturity alone.

## Phase 2 preview (not building yet, just context)

- Planning step: turn a loose prompt into a scoped task list (GDD-lite), not a blind "make a game."
- Asset generation pipeline, separate from the MCP: image/3D/audio generation, headless Blender for mesh cleanup, feeding files into Unreal via the MCP's import tools.
- Play-test-and-fix loop: run via PIE, capture logs/screenshot, feed failures back for repair.
- Optional: local Kenney.nl asset mirror (CC0, free) as a fallback/supplement to generated assets.

---

## Next step

Start implementing phase 1: MCP server scaffold + Python Remote Execution bridge + the MVP tool list above.

### Materials, domain and shading (verified)

`set_material_domain_and_shading_model` is built and live-verified. Notes are
in the materials section above; the short version is that `material_domain` is
a settable property, `shading_model` has no setter but responds to
`set_editor_property`, and read-back is a repr string that has to be compared by
enum name.

### Textures and material-function calls (verified)

Four tools live-verified, 51/51 checks: `generate_texture_from_pixels`,
`set_texture_properties`, `get_texture_info`, `create_material_function_call`.
Plus 6 offline unit tests for the PNG encoder.

Pixel read-back is impossible on a Texture2D in this build (`platform_data`,
`source`, `pixel_format`, `cached_num_mips` all raise), so a generated texture
is verified as a real Texture2D at the right dimensions, not as a verified pixel
round trip. Material-to-texture baking is not buildable through Python at all:
no `KismetRenderingLibrary`, no usable draw on `CanvasRenderTarget2D`.

### Blueprint structure reading (verified)

Seven tools live-verified, 61/61 checks: `create_blueprint`,
`get_blueprint_info`, `list_blueprint_graphs`, `list_blueprint_functions`,
`list_blueprint_events`, `list_blueprint_variables`,
`list_blueprint_event_dispatchers`. Plus 7 offline unit tests.

Blueprint *structure* is fully readable: parent class, graphs, functions, events,
event dispatchers and member variables. Blueprint *node contents* are not
readable at all: `EdGraph.Nodes` is protected and nodes are not addressable by
object path, so the node-level helpers in `BlueprintEditorLibrary` are
unreachable. Node reading still needs the C++ escape hatch. The tools say so
explicitly (`nodes_readable: False`) rather than implying coverage they do not
have.

### Whole-surface snippet audit, and two bugs it led to (verified)

`tests/test_all_tool_snippets.py` walks server.py's registrations and
compile-checks the snippet every tool hands to the bridge, with `run_python` and
`run_raw` stubbed so nothing reaches the editor. 86 tools, 155 snippets, 0
failures. This closes the gap where the earlier audit skipped 18 tools because its
dummy-argument table did not cover their parameters; a tool with an unmapped
argument now fails the suite instead of being silently skipped.

Re-verifying lighting and presets live (73/73 checks) found two real bugs:

- All six lighting tools sent bare `json_dumps` snippets with no `guarded()`
  wrapper and a non-optional actor lookup, so a missing actor raised StopIteration
  in the editor and came back as `RemoteCommandFailedError: Remote command failed:
  []` with an empty log. They now return a structured error. Regression test added.
- `apply_material_variant_set` reported `{"success": True, "count": 1,
  "instance_paths": [None]}` for a base material that does not exist, creating
  nothing. It now validates the base and reports failed variants by name.

Both bugs passed the existing tests because the tests asserted the buggy shape.

### Data Tables: create and read only (verified, row writes blocked)

Four tools live-verified, 54/54 checks: `create_data_table`,
`get_data_table_info`, `list_data_table_rows`, `export_data_table`. Plus 13
offline unit tests.

Row authoring is **not buildable** over Remote Control.
`DataTableFunctionLibrary.fill_data_table_from_json_string` deadlocks the editor:
it is dispatched on the game thread and its reimport wants the game thread too.
Running it on a worker thread inside the editor fails differently, with
"Attempted to access Unreal API from outside the main game thread". Both are
measured, not inferred, and the verification script asserts the limit against the
editor. The tools report `rows_writable_from_bridge: False` so a caller is never
told a row can be added when it cannot.

Cells read back through `get_data_table_column_as_string`, so they are formatted
strings rather than typed values; a DataTable cell cannot be read into a typed
Python value through any exposed API. Column internal and export names are both
returned; measured identical for every struct tried.

This closes the Data Tables category only in its read-only form. Landscape and
foliage, Niagara, multiplayer and the remaining presets are still unbuilt, and
each involves asset creation or world mutation, which is the class of operation
that just proved capable of wedging the editor.

### Multiplayer: replication flags (verified)

Two tools live-verified, 42/42 checks: `get_replication_state`,
`set_replication_flags`. Plus 9 offline unit tests.

Covers the per-actor flags (`replicates`, `replicate_movement`, `net_dormancy`)
and per-component replication. **Replication Graph is not buildable:**
`ReplicationGraphBase` and `ReplicationDriverBase` are both absent from the Python
API, so connection filters, priority and dormancy policy cannot be configured from
here at all.

Every one of these writes needs a dedicated method rather than
`set_editor_property`: the actor flags raise "cannot be edited on instances", and
`net_dormancy` silently does not persist. A component reads `replicates` but writes
via `set_is_replicated()`, and there is no `is_replicated` property. The return
carries before and after read back from the actor, so it is evidence rather than a
restatement of the arguments.

### Corrected survey: two earlier "not possible" verdicts were wrong

The first pass over the unbuilt categories guessed class names instead of
surveying the API, and concluded foliage and Niagara were largely unreachable.
That was wrong: `ProceduralFoliageEditorLibrary`, `FoliageStatistics`,
`InstancedFoliageActor.add_instances` and 334 Niagara classes are all live.

Only two verdicts survive a proper survey. **Landscape editing** is genuinely
unavailable: all 44 Landscape classes are live but none exposes a Subsystem,
Library, Editor or Utils entry point. **Replication Graph** is genuinely
unavailable: the stub contains exactly one Replication class, `ReplicationSystem`.

Foliage, Niagara and Sequencer are all buildable and remain unbuilt. Sequencer
looks the most promising, with 175 track and section classes live. Recorded in
TOOL_BUILDING_GUIDE.md so the guessed-name method is not repeated.
