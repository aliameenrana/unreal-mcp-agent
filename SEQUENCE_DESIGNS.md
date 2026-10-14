# Sequence and Recipe Designs: automating game construction, not gameplay

Design document only. No code here; this is the paper spec reviewed before
anything gets built in `src/unreal_mcp/tools/` or in the Phase 2
orchestrator.

## What a sequence is, precisely

A **sequence** is a tool-call plan plus a prompt: a fixed (or lightly
branching) chain of MCP tool calls that an agent executes end-to-end to
*construct* something inside the editor — a Blueprint, an Animation
Blueprint state machine, a wired graph function, an attached prop. It is
**editor-time authoring**, not runtime gameplay. Nothing in a sequence
"plays" the game to produce its effect; it builds an asset that, when the
game is later played by a human or by a PIE-driven verification pass,
behaves as intended. The verification step at the end of every sequence
confirms the *construction* succeeded (compiled, wired, attached, variable
exists) — it does not simulate play to confirm behavior, except where a
short PIE pass is the only way to catch a specific silent failure (noted
per-sequence where that applies).

A **recipe** is a named bundle of several sequences plus the glue prompt
that orders them and states the shared inputs/outputs between them. "Build
a patrolling sword-wielding enemy" is a recipe; "wire the patrol-chase-
attack state machine" is one sequence inside it.

## Correction to the project's own prior scope baseline

`RECIPE_DESIGNS.md` (the prior design pass) stated "No Blueprint graph node
editing" and "No Animation Blueprint state machine authoring" as hard scope
limits, current at the time it was written. Both are now outdated:

- **Blueprint graph editing is built and live-verified** — 13 tools in
  `tools/blueprint_graph.py`, backed by `unreal.BlueprintGraphEditor`
  (`list_all_nodes`, `add_call_function_node`, `connect_blueprint_pins`,
  etc.). See PLAN.md's "Blueprint graph editing" section.
- **AnimBP state machine authoring is planned with real, named API backing**
  (`create_animation_blueprint`, `add_anim_state`, `add_anim_transition` via
  `AnimationStateMachineLibrary`) — not yet built, but confirmed buildable,
  not ruled out the way Niagara graph authoring or Landscape sculpting are.

This matters because the flagship ask — "enemy that patrols, chases,
attacks, loses sight" — requires exactly these two capabilities to be
authored as real engine logic (a real AnimBP state machine, a real Blueprint
function graph deciding transitions) rather than faked with instance-level
property pokes. The sequences below assume both are available; AnimBP
authoring sequences are flagged `NEEDS BUILD FIRST` until `add_anim_state`/
`add_anim_transition` are actually implemented and live-verified, per this
project's standing rule that a preset/sequence is only built on top of
already-verified primitives.

What is still correctly out of scope, unchanged from the prior document:

- **No GAS.** Still C++-only for setup/authoring. Anything calling itself an
  "ability" below is graph logic the MCP authors (a function graph checking
  cooldown, applying a health delta) — real Blueprint logic, just not GAS's
  specific framework.
- **No Niagara module/graph authoring.** Spawning and parameterizing
  existing Niagara systems is solid; writing new particle logic is not.
  Every VFX step below assumes a Niagara system asset already exists.
- **No Sequencer binding creation.** Confirmed to deadlock the editor.
  Reading existing bindings works; creating new ones does not.
- **Landscape sculpting has no Python path at all** (confirmed, not merely
  deferred — see PLAN.md's "Corrected survey"). The level-generation recipe
  below is honestly scoped around this wall.

## The zero-human-involvement constraint, stated precisely

Every sequence below must be completable by an autonomous Phase 2 agent with
no human clicking anything, **except where asset acquisition itself
genuinely requires it** — and even there, the design goal is to automate
that too wherever a real scriptable path exists. Research on exactly which
asset sources are genuinely automatable (Fab marketplace API, Mixamo,
alternatives) and which deep domains (vehicle tuning, VR controller
profiles) have a genuine no-human scripting path is in progress separately;
this document's asset-resolution and vehicle/VR sequences carry an explicit
`PENDING RESEARCH` flag until that lands, rather than asserting a
capability this project hasn't confirmed. Every other sequence — anything
that only touches Blueprint/AnimBP/graph authoring, data tables, or
primitives already built — requires no external asset decision and is
fully autonomous today, modulo build status of its component primitives.

---

## Recipe: Patrolling enemy (detect → chase → attack → lose sight)

The flagship recipe. Full build, in order:

### `seq_acquire_character_asset`

**Goal:** resolve a base skeletal mesh/character Blueprint to build the
enemy on.

1. `list_assets_in_path("/Game")` filtered to `SkeletalMesh`/`Blueprint`
   assets, matched against the requested character description (name/tag
   heuristics — "soldier," "goblin," whatever the prompt names).
2. If a match exists: use it directly, confirm it has a valid `Skeleton`
   reference (`get_property` on the mesh's `skeleton` field) before
   proceeding — an unskinned placeholder mesh fails every later step
   silently otherwise.
3. If no match exists: this is the asset-acquisition boundary — see
   `seq_resolve_asset_by_intent` below. `PENDING RESEARCH` on whether this
   step can complete with zero human involvement when nothing local
   matches.

**Verification:** `get_property` confirms the resolved asset is a
`SkeletalMesh` (not a `StaticMesh` mistakenly matched) and has a non-null
skeleton.

### `seq_discover_or_import_animations`

**Goal:** resolve Idle, Patrol (walk cycle), Chase (run cycle), Attack, and
Hit-reaction AnimSequences compatible with the resolved skeleton.

1. For each required animation role, search `/Game` for AnimSequence assets
   whose `skeleton` property matches the character's skeleton
   (`get_property` per candidate, not a name-guess match — a name match
   with an incompatible skeleton fails silently at assignment time
   otherwise).
2. Any role with no match: asset-acquisition boundary, same as above.
   `PENDING RESEARCH`.

**Verification:** each resolved AnimSequence's `skeleton` property equals
the character's skeleton, checked before any is wired into the state
machine — catching a skeleton mismatch here is far cheaper than catching it
after the AnimBP is built.

### `seq_build_anim_state_machine` — NEEDS BUILD FIRST (`add_anim_state`/`add_anim_transition` not yet implemented)

**Goal:** author a real AnimBP state machine with Idle/Patrol/Chase/Attack/
Flee states and the transitions between them.

1. `create_animation_blueprint(skeleton, name)`.
2. `add_anim_state` once per state (Idle, Patrol, Chase, Attack, Flee),
   each with its resolved AnimSequence as the state's output.
3. `add_anim_transition` for each edge: Idle→Patrol (always, on a short
   delay), Patrol→Chase (on `CanSeeTarget` becoming true), Chase→Attack (on
   `InAttackRange` becoming true), Attack→Chase (on attack animation
   finishing and target still in range), Chase→Patrol (on `CanSeeTarget`
   false for N seconds — the "lose sight" hysteresis, authored as a
   transition rule reading a timer variable, not an instant flip).
4. `get_blueprint_compile_errors` on the resulting AnimBP.

**Verification:** compile with zero errors, state count matches intent (5),
and each transition's condition variable name matches what
`seq_wire_perception_logic` (next) will actually set — a naming mismatch
between the two sequences is the single most likely silent-failure point,
so the two sequences must agree on variable names from a shared spec, not
each guess independently.

### `seq_wire_perception_logic`

**Goal:** author the Blueprint function graph that drives the AnimBP's
transition variables — range check and line-of-sight/occlusion check.

1. In the character's own Blueprint (parent of the AnimBP), use
   `blueprint_graph.py`'s tools to add a function graph:
   `create_blueprint_function_graph("UpdatePerception")`.
2. `add_blueprint_call_function_node` for a distance check between self and
   target (`GetDistanceTo` or a manual vector-subtract-length chain if that
   node doesn't exist — confirm via `list_blueprint_available_nodes` before
   assuming either shape).
3. `add_blueprint_call_function_node` for a line trace (`LineTraceSingle` or
   equivalent) from the enemy's eye location to the target, to compute
   occlusion.
4. `add_blueprint_branch_node` × 2 (range threshold, occlusion result),
   `connect_blueprint_pins` wiring trace result → branch → the exact
   `CanSeeTarget`/`InAttackRange` variable names the AnimBP transitions
   above read.
5. Wire this function to run on a Tick or a timer-driven event — authored
   as a real graph connection (an Event Tick node → this function call),
   not polled externally by the MCP.

**Verification:** `get_blueprint_compile_errors`, then
`list_blueprint_graph_nodes` to confirm the branch nodes and variable-set
nodes exist with the expected pin connections (a node existing but
unwired — a connection silently failing — is exactly the failure mode
`tools/blueprint_graph.py`'s own prior findings warn about, verified by
`list_blueprint_graph_nodes`'s read-back, not by trusting
`connect_blueprint_pins`'s return value).

### `seq_attach_weapon_for_attack_type`

**Goal:** resolve the enemy's declared attack type (sword, bow, unarmed,
etc.) to a weapon asset and socket, and physically attach it.

1. Attack-type → weapon-class + socket-name lookup table, carried in the
   recipe's own prompt (e.g. `{"sword": {"class": "...", "socket":
   "weapon_r"}, "bow": {"class": "...", "socket": "back_quiver"}}`), not
   guessed per call.
2. `get_skeletal_mesh_sockets(character_actor)` — confirm the target socket
   actually exists on this specific skeleton before attempting attach;
   different base characters may not share socket names.
3. `attach_prop_to_socket(character_actor, weapon_class, socket_name, ...)`.

**Verification:** `get_attach_parent_socket_name` on the spawned weapon
confirms it landed on the requested socket, not merely "attached to the
actor somewhere" — the specific failure mode this project's own prior
research flagged for every attach-based recipe.

### `seq_verify_enemy_construction`

**Goal:** the final cross-check tying every prior sequence's output
together.

1. `get_blueprint_compile_errors` on both the character Blueprint and the
   AnimBP — zero errors required on both.
2. Confirm the AnimBP is actually assigned to the character
   (`set_animation_mode` reads back as `blueprint`, not `single_node`).
3. Confirm the weapon attachment (already verified above) and the
   perception-graph variables (already verified above) use matching names —
   a final cross-sequence consistency check, since each prior sequence only
   verified its own output in isolation.
4. Optional, flagged as PIE-only and therefore not required for the
   sequence to report success: a short PIE pass placing the enemy and a
   dummy target actor at a known range, reading `CanSeeTarget`/
   `InAttackRange` back via `get_property` to confirm the graph actually
   evaluates as intended once ticking — this is the one place in the whole
   recipe where a short runtime pass catches something compile-time
   checking cannot (a logic error that compiles cleanly but evaluates
   wrong).

**This recipe's own prompt** ties the five sequences together: "Build a
sword-wielding patrol enemy from [character asset], chasing within 1500
units, losing the player after 3 seconds out of sight, attacking in melee
range." The prompt supplies the attack-type lookup, the range thresholds,
and the character description that seeds `seq_acquire_character_asset`.

---

## Recipe: Inventory system (authoring)

- **`seq_define_item_schema`** — `create_struct_asset` (fields: name, icon
  reference, stack size, item type enum) or `create_data_table` if a
  flat-row shape suffices; one row per item instance type.
- **`seq_build_inventory_component_blueprint`** — a Blueprint (Actor
  Component, not a full Actor, so it attaches to any Pawn) with an array
  variable (`add_blueprint_member_variable`, typed as the item struct), plus
  `AddItem`/`RemoveItem` function graphs (`create_blueprint_function_graph`,
  `add_blueprint_branch_node` for stack-size checks,
  `connect_blueprint_pins`).
- **`seq_build_pickup_actor`** — reuses the already-designed
  `create_pickup_item` preset: mesh + collision sphere + an overlap event
  wired to a "grant and destroy" sequence.
- **`seq_wire_overlap_to_inventory`** — graph-wires the pickup's overlap
  event to call the inventory component's `AddItem` function on the
  overlapping actor, resolved via `add_blueprint_call_function_node` with a
  cast-to-component step.

**Verification per sequence:** compile check after each graph edit;
`list_blueprint_variables` confirms the array variable's type matches the
struct; a final read of `list_blueprint_graph_nodes` on the pickup's event
graph confirms the overlap event is actually connected to the call node,
not merely present alongside it unwired.

---

## Recipe: Weapon system (authoring)

- **`seq_define_weapon_data_table`** — one row per weapon type (mesh path,
  socket name, damage value, attack animation reference, weapon category
  enum: melee/ranged/magic).
- **`seq_build_melee_weapon_actor`** — mesh + a trace-shaped collision
  component along the blade for hit detection (a thin box or capsule
  component, not the full mesh collision, to avoid false-positive overlaps
  with the wielder).
- **`seq_build_ranged_weapon_actor`** — mesh + muzzle socket (confirmed via
  `get_skeletal_mesh_sockets`/static mesh socket equivalent) + ammo count
  variable.
- **`seq_build_projectile_blueprint`** — separate Actor Blueprint: a
  physics-simulating or manually-ticked-movement component, collision, and
  a Niagara trail attached at its own root (assumes the Niagara system
  asset already exists per the standing out-of-scope rule).
- **`seq_wire_fire_function_graph`** — a callable function
  (`create_blueprint_function_graph("Fire")`) that spawns the projectile
  Blueprint at the muzzle socket's world transform
  (`add_blueprint_call_function_node` for `SpawnActor`), gated by a
  cooldown Branch + Delay node pair authored as real graph logic, not an
  MCP-side timer.
- **`seq_wire_projectile_impact_graph`** — on-hit event (collision overlap
  on the projectile) wired to: VFX spawn, a call into the health recipe's
  damage function (by reference — the two recipes must agree on that
  function's name and signature, carried in both recipes' prompts), and
  self-destroy.
- **`seq_wire_hitscan_variant`** — alternative fire-function graph using a
  line trace instead of spawning a projectile actor, for instant-hit
  weapon types; shares the cooldown-gate pattern with the projectile
  variant.
- **`seq_wire_attack_combo_graph`** (melee-specific) — chains 2-3 attack
  animations with input-buffered transitions: `add_anim_state` per combo
  step (NEEDS BUILD FIRST, same dependency as the enemy recipe above),
  gated on a combo-window boolean set by a Blueprint timer.
- **`seq_build_sheathe_drawn_toggle`** — a function graph swapping the
  weapon's attach socket between a sheath socket and a hand socket, driven
  by an `IsDrawn` boolean, reusing `attach_prop_to_socket`'s underlying
  attach/detach primitives inside the graph-authored function rather than
  as a direct MCP call (the toggle has to exist as real in-game logic the
  player's input triggers, not a one-time MCP-side attach).

**Verification:** compile checks after every graph edit (non-negotiable
given how many of these sequences chain multiple node-add calls in one
pass — a single dangling connection anywhere invalidates the whole weapon);
`get_attach_parent_socket_name` read-back on every socket attach step.

---

## Recipe: Magic / spell casting (authoring)

- **`seq_build_spell_data_table`** — one row per spell: cost, cooldown, VFX
  system path, sound cue path, effect type enum (damage/buff/debuff/
  summon), target mode enum (self/single-target/AoE).
- **`seq_build_ability_cast_graph`** — a function graph per spell (or one
  generic parameterized function reading the data table row, preferred —
  avoids one Blueprint-graph-authoring pass per spell): cast-time Delay
  node → VFX spawn at hand socket (reuses `cast_spell_effect`'s underlying
  primitives, authored into the graph) → resolve effect by branching on the
  effect-type enum → call the matching function (damage call into the
  health recipe, buff/debuff call into its own timer-gated revert function)
  → cooldown gate (Branch + Delay, same pattern as the ranged-weapon fire
  function).
- **`seq_wire_targeted_vs_aoe_branch`** — authored once as a reusable
  function both this recipe and any future ability-like system can call: a
  Branch on the target-mode enum, one side doing a line trace / nearest-
  actor lookup (single-target), the other an overlap-sphere query at the
  cast location (AoE), both resolving to the same "list of affected actors"
  output the effect-resolution step consumes uniformly.

**Verification:** compile check; a data-table-driven design means the
cast-graph only needs to be authored and verified once, with new spells
added purely as data-table rows afterward — call out this amortization
explicitly in the recipe's own report, since it changes the cost profile of
"add a tenth spell" from "author another graph" to "add another row."

---

## Recipe: Health, scoring, debuffs (authoring)

- **`seq_init_health_pool`** — `add_blueprint_member_variable` for
  current/max health (float or int, per the recipe's own convention) on the
  target Blueprint.
- **`seq_build_damage_function_graph`** — a callable function
  (`OnDamage(amount)`) that decrements the health variable, branches on
  threshold crossings (e.g. <50%, <20%) to call
  `damage_state_transition` (already built) for the visual material swap,
  and branches on health<=0 to call the death function below.
- **`seq_build_debuff_timer_graph`** — a function
  (`ApplyDebuff(type, duration)`) authored with a Delay node holding the
  effect (a timed `set_play_rate`/material swap/particle attach, applied
  via the matching already-built primitive called from inside the graph)
  and a matching revert call after the Delay fires — authored as real
  engine logic so the debuff genuinely expires in a running game, not
  something the MCP has to hold open across a session.
- **`seq_track_score`** — either a Data Table row counter or a simple
  Blueprint integer variable incremented by a function call wired into
  whatever event should award points (enemy-death, item-pickup — calls
  into those recipes' death/pickup functions by reference).
- **`seq_build_death_function_graph`** — `OnDeath()`: stop animation,
  disable collision, optional ragdoll (`set_simulate_physics` called from
  inside the graph), despawn after a Delay.

**Verification:** compile check per graph; `list_blueprint_variables`
confirms health/score variables exist with the expected type; a
cross-sequence check (same pattern as the enemy recipe's final step)
confirming the projectile-impact and melee-hit-detection graphs from the
weapon recipe actually call this recipe's `OnDamage` function by the exact
name/signature agreed between the two recipes' prompts.

---

## Recipe: UI / UMG (authoring)

- **`seq_build_hud_widget`** — `create_widget_blueprint`, add health bar /
  ammo counter / spell-cooldown child widgets, bind each to the relevant
  Blueprint variable via a graph-level "update on tick or on the variable's
  own change event" function (`add_blueprint_event_node` +
  `set_widget_text`/property binding).
- **`seq_build_inventory_ui_widget`** — a widget bound to the inventory
  recipe's array variable, with a list-view-shaped child widget per slot,
  refreshed on the inventory's `AddItem`/`RemoveItem` function completing
  (an event dispatcher wired from the inventory Blueprint to the widget, if
  `list_blueprint_event_dispatchers`-shaped wiring supports it — confirm
  the exact cross-Blueprint event-wiring call shape before committing,
  flagged as a live-probe item, not assumed).
- **`seq_build_interaction_prompt_widget`** — a context-sensitive "Press E
  to interact" widget shown/hidden by the same overlap events the
  inventory pickup sequence already wires.
- **`seq_wire_widget_to_viewport_lifecycle`** — add-to-viewport on
  `BeginPlay`, remove on death/menu-open, authored as graph nodes in the
  owning Blueprint (calls `add_widget_to_viewport`'s underlying primitive
  from inside the graph, not as a direct one-time MCP call, since the
  widget's lifecycle has to track the actual game session).

**Verification:** compile check; confirm `add_widget_to_viewport` is
actually buildable first — PLAN.md flags it `probe first — 0 hits in the
stub under that name`, so this whole recipe is gated on that primitive
clearing its own live probe before any UI sequence is attempted.

---

## Recipe: Vehicles — PARTIALLY SCRIPTABLE, confirmed

Research verdict: `unreal.ChaosWheeledVehicleMovementComponent` and
`unreal.ChaosVehicleMovementComponent` are real, confirmed-existing Python
API classes. Wheel setup (`FChaosWheelSetup`: bone name, wheel class,
offset), engine config (`FVehicleEngineConfig`: max RPM, torque curve
reference), and suspension values are ordinary UPROPERTY fields reachable
via `get_editor_property`/`set_editor_property`, the same pattern this
project uses for every other component type — high confidence, but exact
Python property names need a live probe before any tool is written, per the
standing step-2b rule (the official doc pages 302-redirected to 404 during
this research pass, a known doc-site quirk; existence was corroborated via
cached snippets and the parallel C++ API pages, not read live).

The one unconfirmed piece: torque/steering curves are `FRuntimeFloatCurve`
fields. These remain ordinary UPROPERTYs (settable via Python in principle,
not a bespoke editor-only widget), but Epic's docs didn't explicitly show
curve-key-set syntax from Python — flagged for live probe, not asserted
blocked. No documented one-click "Vehicle Template" factory was confirmed
either; "usable default vehicle with zero tuning" is unconfirmed, not
verified in either direction.

- **`seq_build_vehicle_actor`** — mesh import + Chaos Vehicle Movement
  Component setup via `add_component`/`set_component_property`, same
  pattern as every other component-adding sequence in this project.
  Confirmed scriptable in principle; needs the live-probe pass on exact
  property names before implementation.
- **`seq_wire_enter_exit_graph`** — overlap-triggered possess/un-possess
  function graph swapping control between the character Pawn and the
  vehicle Pawn, plus a seat-socket attach for the driver's visual placement
  when seated (reuses the socket-attach pattern throughout this document).
  No new unknowns; same `blueprint_graph.py` primitives as every other
  graph-authoring sequence.
- **`seq_wire_vehicle_input_bindings`** — maps Enhanced Input actions to
  the vehicle's throttle/steer/brake graph nodes. Confirmed scriptable
  (Enhanced Input asset creation is confirmed, see the VR section below for
  the sourcing).
- **`seq_apply_vehicle_default_tuning`** — applies a hand-authored default
  suspension/torque/wheel configuration this project owns (since no Epic
  one-click template was confirmed), setting `FRuntimeFloatCurve` keys
  programmatically pending the live-probe confirmation of that specific
  call shape. States its ceiling honestly in its own output: a working,
  drivable vehicle with sane, untuned handling — not competitive-quality
  physics tuning, which stays a human/hand-tuning task this project does
  not attempt to automate away.

**Build this after the live-probe session confirms exact property names**
on `ChaosWheeledVehicleMovementComponent` and curve-key-set syntax; nothing
here is blocked the way Fab/Mixamo or Landscape sculpting are, it's simply
unconfirmed in its exact shape.

---

## Recipe: VR authoring — PARTIALLY SCRIPTABLE, one confirmed hard wall

Research verdict, split cleanly into a scriptable half and a blocked half:

**Scriptable, confirmed:** `unreal.InputAction`/`unreal.InputMappingContext`
are both DataAssets creatable via the standard
`AssetTools.create_asset()` + factory pattern
(`unreal.InputAction_Factory` confirmed to exist).
`InputMappingContext.map_key(action, key)` is a confirmed Python method for
binding a generic key/button to an action.
`unreal.MotionControllerComponent` is a confirmed Python class with
standard settable properties (hand/motion source, display model, transform
flags). So: defining input actions, generic key mappings, and instantiating
motion controllers with sane defaults is all genuinely automatable —
`seq_build_vr_pawn` and the input-definition half of `seq_wire_vr_grab_logic`
rest on solid ground.

**Not scriptable, confirmed:** the actual per-headset OpenXR
interaction-profile-to-button binding has no Python-exposed path. UE5's
OpenXR plugin doesn't expose an `OpenXRActionSet` asset type the way the
older SteamVR plugin did (`unreal.SteamVRActionSet` exists; there's no
OpenXR equivalent). This binding happens through Project Settings > Input,
mapping Enhanced Input keys to OpenXR interaction-profile-specific button
paths — a Project Settings editor panel, not an asset with a Python
factory. The one Python hook found,
`unreal.OpenXRInputFunctionLibrary.begin_xr_session()`, attaches an
already-built mapping context to a running session; it does not create or
edit headset-specific button bindings themselves.

**The practical way around the wall, confirmed real:** Epic's own VR
Template ships a working default VRPawn with teleport locomotion already
wired, instantiable via Python like any other Blueprint/Pawn asset. Since
its input bindings were authored once by Epic and ship with the template,
a sequence that **starts from the VR Template and only customizes what's
exposed as ordinary properties** (which hand, which mesh, locomotion speed)
sidesteps the OpenXR-binding wall entirely rather than fighting it.
Customizing per-headset button mapping beyond the template's shipped
defaults stays editor-UI-only and out of scope.

- **`seq_build_vr_pawn`** — instantiate the VR Template's Pawn (not build
  one from scratch), customize camera/motion-controller properties via
  `set_editor_property`. Confirmed scriptable end to end.
- **`seq_wire_vr_grab_logic`** — overlap + input-action-triggered
  attach/detach graph function, reusing the socket-attach pattern, driven
  by motion controller transforms. The input-action definition half is
  confirmed scriptable; it binds to the template's existing default key
  mappings rather than authoring new per-headset button bindings.
- **`seq_wire_teleport_locomotion_graph`** — reuses the VR Template's
  already-wired teleport locomotion rather than authoring a new
  trace-from-controller + blink/fade graph from scratch, since the
  template ships this and re-deriving it is pure duplicated effort against
  a wall that doesn't need fighting.

**Scope stated honestly, per this project's standing discipline:** this
recipe automates "assemble a working VR pawn with grab and teleport
locomotion, starting from Epic's template." It does not automate
"configure custom OpenXR controller-button mappings for a bespoke input
scheme" — that step is confirmed editor-UI-only, not a research gap that
further probing would close.

---

## Recipe: Level generation (landscape + dressing)

Unlike every recipe above, this one is genuinely bounded by a confirmed,
permanent wall rather than a pending research question: **Landscape
sculpting has no Python entry point at all** (PLAN.md's "Corrected survey"
— all 44 Landscape classes are live, none exposes a Subsystem/Library/
Editor/Utils entry point). This recipe is honestly scoped around that wall,
not pretending it can be engineered away.

- **`seq_author_landscape_prompt`** — not a tool call: a structured prompt
  template (size, biome, elevation profile, erosion style) for an external
  heightmap generator. Which external generator is actually automatable
  with zero human involvement is itself `PENDING RESEARCH` (same bucket as
  the asset-acquisition question above — an external heightmap tool is an
  asset source, same as a mesh or animation source).
- **`seq_import_heightmap`** — imports the generated heightmap as a texture/
  raw file via `import_asset`. Getting it to actually build a Landscape
  inside Unreal from that import is the part with no Python path; this
  step is honestly scoped to "the file is in the project," not "the
  Landscape exists."
- **`seq_populate_foliage`** — blocked today on `FoliageType` asset
  creation having no Python path (confirmed, PLAN.md's Foliage findings);
  `paint_foliage_instances` itself is buildable once an existing
  `FoliageType` asset can be supplied (e.g. imported from a `.ff` file
  rather than created fresh).
- **`seq_scatter_set_dressing`** — reuses the already-built
  `set_dressing_pass` preset. Fully buildable today, no gaps.
- **`seq_place_lighting_mood`** — reuses the already-built
  `light_scene_preset` preset. Fully buildable today, no gaps.

---

## Cross-cutting sequence: asset resolution by intent

**`seq_resolve_asset_by_intent(description, asset_type, compatibility_check)`**

The generalized form of "find the right mesh/animation from the project or
an external source" that every character/weapon/VFX sequence above calls
rather than reimplementing per domain.

1. Search project content first: `list_assets_in_path` filtered by
   `asset_type`, matched against `description` by name/tag heuristics.
2. Run `compatibility_check` against each candidate (skeleton match for
   animations, socket existence for attachables, material domain match for
   VFX-adjacent materials) — reject a name-match that fails compatibility
   rather than using it anyway, since a plausible-sounding wrong asset is a
   worse failure than reporting "none found."
3. If nothing passes: fall back to an external source.

**Step 3's research is in: mostly a wall, one real door.**

- **Fab marketplace: NOT SCRIPTABLE.** Epic's own Fab docs list exactly four
  access methods (website, Fab-in-Launcher, UE Integration, UEFN
  Integration), all human-interactive. No REST/GraphQL API, no CLI, no
  developer portal exists. Unofficial reverse-engineered tools
  (`egs-api`, `UEVaultManager`) can download assets already in an account's
  library, but **cannot add a new free asset to that library** — library
  addition is itself a web/launcher UI action with no documented API
  workaround. This path is closed for true zero-human-involvement use; any
  use of the unofficial tools also carries ToS risk and no stability
  guarantee.
- **Mixamo: NOT SCRIPTABLE**, browser-only. Adobe's own community staff
  confirm no API exists. Existing "automation" (console injection,
  Playwright-driven MCP servers) scripts the live website, not a documented
  interface — fragile, breaks on any frontend change, and Adobe's ToS on
  this was not checked. Not a real door for this project's asset-resolution
  fallback.
- **Sketchfab: CONFIRMED SCRIPTABLE — the one real door.** Sketchfab has an
  official, documented Download API supporting programmatic search (by
  tag/category/polygon count/license) and download of CC-licensed free
  models, authenticated via a service-account OAuth token set up once
  (not per-call, not per-human-click). Constraint: only glTF/GLB/USDZ are
  downloadable through the API, not FBX/OBJ — so an import step converting
  glTF into something Unreal's importers accept (or using glTF directly, if
  UE5's glTF import path is solid) becomes part of this sequence. No
  animation coverage — Sketchfab is a mesh/model source, not an animation
  source, so it does not substitute for the Mixamo gap.
- **Kenney.nl: PARTIALLY SCRIPTABLE.** CC0 asset packs are static zips at
  stable URLs — direct HTTP download is scriptable with zero auth and zero
  clicking — but there is no search/discovery API, only a hand-maintained
  manifest or site-scraping would locate "a sword mesh" specifically. Usable
  as a bulk pre-staged local library (matching the "Local asset library
  only" option this session already chose as one source), not as an
  on-demand search-by-intent source.

**Revised design for `seq_resolve_asset_by_intent`'s fallback step:**

1. Search the pre-staged local library first (Kenney-style packs, or
   anything manually added to the project) — this was already one of the
   two sourcing options chosen for this project and remains the cheapest,
   most reliable path.
2. If nothing matches and the asset is a static mesh (not an animation):
   fall back to Sketchfab's Download API, filtered by CC0/permissive
   license, converted from glTF through Unreal's import pipeline. This is
   the one genuinely automatable on-demand fallback found.
3. **Animations have no automatable fallback.** If a required AnimSequence
   role (Idle, Patrol, Chase, Attack, Hit-reaction) has no local match, the
   honest behavior is to report the gap plainly rather than claim a
   fallback that doesn't exist — per the "fall back to placeholder and flag"
   option already chosen as a design principle. A sequence should substitute
   a neutral placeholder (T-pose hold, or the nearest available animation
   even if imperfect) and report exactly which role is unfilled, rather than
   silently proceeding as if fully resolved or blocking entirely.
4. Fab is not part of this design. Do not build against it; revisit only if
   Epic ever ships a real developer API.

This changes every recipe above in one specific way, worth stating plainly:
**animation acquisition is the one genuine human-involvement gap this
project cannot currently engineer away.** Mesh acquisition has a real
automated door (local library, then Sketchfab); animation acquisition does
not, short of fragile, ToS-risky browser automation against Mixamo, which
this project should not build against given the stated preference for
reliability over cleverness. If animation coverage becomes the actual
blocker in practice, the honest next options are: (a) a small, deliberately
curated local animation library covering the common roles (idle/walk/run/
attack/hit/death) per common skeleton rig, built once by a human and reused
forever — the cheapest real fix — or (b) revisit Mixamo automation
explicitly as a scoped, flagged-fragile exception if (a) proves
insufficient.

---

## Build order recommendation

1. **Unblock the two gating primitives first:** `add_anim_state`/
   `add_anim_transition` (needed by the enemy recipe and the melee combo
   sequence) and `add_widget_to_viewport` (needed by the entire UI recipe,
   currently flagged "probe first, 0 hits in the stub"). Nothing patrol/
   combat/UI-shaped ships without these.
2. **Ship recipes with zero new primitives needed, today:** Level
   generation's `seq_scatter_set_dressing`/`seq_place_lighting_mood` (reuse
   already-built presets), and any `blueprint_graph.py`-only sequence that
   doesn't touch AnimBP or UMG (e.g. the inventory recipe's component/
   function graphs, the health recipe's damage/debuff/death graphs).
3. **Then, once the gating primitives land:** the full enemy-patrol recipe,
   the weapon recipe's combo/sheathe sequences, the full UI recipe.
4. **Resolve PENDING RESEARCH before touching:** the asset-resolution
   fallback step (gates how "autonomous" every character/weapon/VFX
   sequence really is end-to-end), Vehicles, VR, and the landscape-
   generation external-source step.
5. **Permanently deferred, not phased:** Landscape sculpting itself (no
   Python path exists; only the import-adjacent steps around it are
   buildable), full competitive vehicle tuning (sane-defaults ceiling
   stated honestly, not solved further).
