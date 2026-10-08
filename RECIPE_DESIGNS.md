# Recipe Designs: flashy composite tools for live demos

Design document only. No code here; this is the paper spec a human reviews
before anything gets built in `src/unreal_mcp/tools/`.

## Scope and honesty baseline

Before designing anything "flashy," the honest limits that bound every
recipe below, established by this project's own research (see PLAN.md):

- **No Gameplay Ability System.** GAS is C++-only, not Python-scriptable.
  Anything described as a "spell" or "ability" in this document is a VFX +
  audio + maybe a timed state flag, never a real ability graph with cost,
  cooldown, or targeting logic.
- **No Blueprint graph node editing.** `add_blueprint_function_call_node` /
  `add_blueprint_event_node` are unbuilt and flagged in PLAN.md as likely
  needing the C++ escape hatch. Any recipe that would need to wire new
  Blueprint logic (a new overlap event, a new input binding) cannot do that
  part through this MCP today. Recipes route around this by using existing
  Blueprint structure, instance-level property writes, and spawning, never
  by authoring new graph logic.
- **No Animation Blueprint state machine authoring.** `add_anim_state` /
  `add_anim_transition` are catalog entries, not built. What *is* built and
  live-verified is instance-level animation control: `set_animation`,
  `set_animation_mode`, `play_animation`, `stop_animation`,
  `pause_animation`, `set_play_rate`, `get_animation_state`. Every
  animation-touching recipe below is scoped to what that instance-level API
  can actually do: swapping which AnimSequence plays in single-node mode,
  and changing play rate. It cannot add a new state to an existing Animation
  Blueprint's state machine, and it cannot blend two animations together
  (that's a blend space or state machine construct, authored, not read-write
  at the instance level).
- **No Niagara module/graph authoring.** Spawning existing Niagara systems
  and setting their exposed user parameters is solid and live-verified-ready
  (`spawn_niagara_system`, `set_niagara_parameter`,
  `get_niagara_user_parameters`, `add_niagara_emitter_from_template`). Writing
  a new particle effect from scratch is not. Every "spell"/"explosion"/"vfx"
  recipe below assumes a Niagara system asset already exists in the project
  (built by an artist, or picked from an engine template) and the recipe's
  job is to spawn and parameterize it, not invent it.
- **No Sequencer.** PLAN.md records that Sequencer binding writes
  (`add_possessable`) deadlock the editor intermittently and the tools were
  reverted. No recipe here depends on Sequencer.
- **No material-graph node authoring inside a recipe's hot path.** Graph
  authoring tools exist and are live-verified, but they're slow, multi-step,
  and risky to run inside a flashy one-call demo (a shading-model change
  mid-demo can wedge the Remote Control endpoint per PLAN.md's lighting
  notes). Recipes use existing materials/instances, not graph surgery.

Given that, every recipe below is a composition of: spawning actors,
attaching them to sockets, setting material/light/Niagara parameters,
reading back instance-level animation and component state, and (where UMG
is involved) creating and populating widget Blueprints with existing
`get_editor_property`/`set_editor_property` plumbing. That's a real, wide
set of moves. It's also specifically *not* "write new gameplay logic,"
which is the thing this MCP cannot yet do.

---

## Reusable building block: `attach_prop_to_socket`

Nearly every equip-style recipe below (gun, sword, bomb, spell-caster
effect) needs the same four-step sequence, so it's worth factoring out
first rather than letting each recipe reimplement it slightly differently.

**Pitch:** spawn an actor and rigidly attach it to a named socket on a
target skeletal mesh, verifying the socket exists and the attachment
actually took, rather than trusting the attach call's return value.

**Inputs:**
- `target_actor_name` (the character)
- `prop_class_path` (the weapon/prop Blueprint or static mesh actor class)
- `socket_name` (e.g. `"hand_r"`, `"weapon_r"`, `"spine_03"`)
- `attach_rule` (default `KEEP_WORLD`, per PLAN.md's finding on the two
  differently-named attach/detach enums)
- `relative_transform_offset` (optional fine-tune after attach, since a
  socket's native orientation rarely matches the prop's pivot)

**Outputs:** `{success, prop_actor_name, socket_name, verified_attached,
  attach_parent_socket_read_back, offset_applied}`

**Sequence:**
1. `list_components(target_actor_name)` or equivalent skeletal mesh
   component lookup, to confirm the target actually has a
   SkeletalMeshComponent before trying to attach to one of its sockets.
   **No primitive currently reads the socket list off a skeleton** — there
   is no `get_all_socket_names` wrapper in scene.py/components.py. This is
   a real gap: the recipe can attach to a socket name the caller supplies,
   but cannot independently confirm that name exists on this particular
   skeleton before trying. Flag: needs a new primitive,
   `get_skeletal_mesh_sockets(actor_name)`, wrapping
   `SkeletalMesh.get_all_socket_names()` or
   `SkeletalMeshComponent.does_socket_exist`, before this recipe can be
   fully self-verifying. Until then it attaches optimistically and relies
   on step 3's read-back to catch a bad name indirectly (a nonexistent
   socket typically attaches to the component root instead of raising).
2. `spawn_actor(prop_class_path, location=target's current location,
   rotation=(0,0,0))` — spawn at the target's location first, since
   attach will reposition it; spawning at the origin and attaching
   avoids an initial frame where the prop floats elsewhere.
3. `attach_actor(prop_actor_name, target_actor_name, socket_name,
   attach_rule)`.
4. **Read-back, not trust:** call `get_attach_parent_socket_name`
   (PLAN.md's documented correct getter, not the nonexistent
   `get_attach_component`) and compare it against the requested
   `socket_name`. If it doesn't match, the attach silently fell back to a
   different socket or the root, and the recipe reports
   `verified_attached: False` with the actual socket it landed on, rather
   than reporting success because the attach call itself didn't raise.
5. Optional `set_actor_transform` with `relative_transform_offset` for
   grip correction.

**Agentic part:** step 1's socket check, once built, lets this recipe
*ask the skeleton what it has* rather than assume `"hand_r"` exists — a
UE5 Mannequin uses that name, a custom rig might use `"weapon_socket"` or
nothing at all. Until that primitive exists, the judgment call is pushed
up to whatever recipe calls this one: it should try the most likely socket
name first and treat a mismatched read-back as a signal to retry with an
alternate name list, which is exactly the kind of branch a dumb linear
script wouldn't do.

**Honesty check:** fully achievable today for the attach/read-back
mechanics (every primitive it needs — `spawn_actor`, `attach_actor`,
`get_attach_parent_socket_name` — is built and live-verified per PLAN.md).
The one gap is socket *discovery*, which needs one new small primitive.
Not aspirational, just not quite complete.

---

## Recipe 1: `bring_character_to_life`

**Pitch:** take a static, idle-posed character actor already in the level
and give it the smallest set of visible signs of life a Python-only
toolchain can actually produce — walking the line between "technically
true" and "looks dead on camera" honestly.

**Inputs:** `actor_name`, `idle_animation_path` (an existing AnimSequence
asset), `breathing_play_rate` (default 1.0), `loop` (default True).

**Outputs:** `{success, actor_name, animation_assigned, animation_length,
  mode, is_playing, play_rate_applied}`

**Sequence:**
1. `get_animation_state(actor_name)` — read what's currently assigned
   (often nothing, or a wrong default) before changing anything.
2. `set_animation_mode(actor_name, mode="single_node")` — per PLAN.md,
   switching mode clears whatever asset was assigned, so this has to
   happen *before* `set_animation`, not after.
3. `set_animation(actor_name, idle_animation_path)`.
4. `play_animation(actor_name, loop=loop)`.
5. `set_play_rate(actor_name, breathing_play_rate)` — a play rate near
   1.0 with a subtly looping idle/breathing AnimSequence is the actual
   mechanism of "looks alive," not anything procedural.
6. **Read-back:** `get_animation_state(actor_name)` again, confirm
   `is_playing: True` and the assigned asset name matches what was
   requested. PLAN.md notes `play()` recreates the anim instance with no
   asset on it momentarily, so the read-back has to happen after play,
   not interleaved.

**What makes it agentic:** step 1's pre-read lets the recipe short-circuit
if the actor has no SkeletalMeshComponent at all (a StaticMeshActor can't
be brought to life this way, and the recipe should say so rather than
failing deep inside a snippet with an opaque error). It can also decide,
based on the pre-read, whether the actor is already in blueprint
animation mode (an Animation Blueprint reference) — forcing it to
single-node mode would silently discard that Animation Blueprint
assignment, which is destructive and arguably the wrong move for a
character that already has a real AnimBP. A careful version of this
recipe checks the current mode and, if it's already `blueprint` mode
with a valid AnimBP assigned, reports "already alive via AnimBP, no
change made" instead of overwriting it — because switching a
properly-rigged character to single-node idle playback is a downgrade,
not an upgrade.

**Honesty check:** fully achievable, and it is exactly as much "alive" as
instance-level animation control can deliver: assigning and looping one
AnimSequence with a chosen play rate. That is not procedural breathing,
not a blend between idle states, and not a real Animation Blueprint
state machine. If the character has no suitable AnimSequence asset in the
project, this recipe cannot make one; it can only assign what exists.

---

## Recipe 2: `equip_weapon`

**Pitch:** hand a character a gun or sword, attached and oriented
correctly at the hand, in one call — the single most visually legible
"the AI did something to the character" demo moment there is.

**Inputs:** `actor_name`, `weapon_class_path`, `weapon_type`
(`"melee"` | `"ranged"`, used only to pick a sane default socket/offset),
`socket_name` (optional override), `hand` (`"right"` | `"left"`, default
right).

**Outputs:** `{success, weapon_actor_name, socket_used, verified_attached,
  weapon_type}`

**Sequence:** this recipe *is* `attach_prop_to_socket` with weapon-specific
socket defaults layered on top:
1. Resolve a candidate socket name: `"hand_r"` for right/melee,
   `"weapon_r"` as a fallback try, per the UE5 Mannequin convention — but
   see the agentic note below.
2. Call `attach_prop_to_socket(actor_name, weapon_class_path,
   candidate_socket, attach_rule=KEEP_WORLD)`.
3. If `verified_attached` is False (the socket didn't match), retry with
   the next candidate in the fallback list (`"hand_l"`, `"spine_01"`,
   root) before giving up and reporting which sockets were tried.
4. For a ranged weapon specifically, also set collision off on the weapon
   actor (`set_collision_enabled`) so it doesn't physically shove the
   character on attach — a known gotcha class (physics bodies colliding on
   spawn) worth guarding against explicitly.

**What makes it agentic:** the retry-across-candidate-sockets loop in
step 3 is the whole point — it's exactly the "find the hand socket by
name, don't assume one" requirement from the brief, implemented as an
actual branch on real read-back state (the mismatched socket name coming
back from `get_attach_parent_socket_name`), not a hardcoded single
attempt. A dumb script would call `attach_actor(actor, weapon, "hand_r")`
once and declare victory; this recipe only declares victory once the
read-back independently confirms it landed where intended.

**Honesty check:** fully achievable using only built, live-verified
primitives (`spawn_actor`, `attach_actor`, `get_attach_parent_socket_name`,
`set_collision_enabled`), modulo the same socket-discovery gap noted in
the shared building block — without `get_skeletal_mesh_sockets`, the
fallback list is a guess informed by convention, not a verified catalog of
what the rig actually has.

---

## Recipe 3: `plant_bomb`

**Pitch:** spawn a bomb prop at a location (or attached to the character's
back/belt), with a visible fuse/light effect and a one-shot "beep"
attenuated sound, as a placeable, interactable-looking object rather than
a bare static mesh.

**Inputs:** `location` (or `attach_to_actor` + `socket_name` for
carrying it), `bomb_blueprint_path`, `fuse_niagara_system_path` (optional),
`tick_sound_cue_path` (optional).

**Outputs:** `{success, bomb_actor_name, placement_mode, vfx_attached,
  sound_played}`

**Sequence:**
1. If `attach_to_actor` is given: `attach_prop_to_socket(...)` with a
   belt/back socket (`"spine_03"`, `"pelvis"`); otherwise
   `spawn_actor(bomb_blueprint_path, location, rotation=(0,0,0))`.
2. If `fuse_niagara_system_path` given: `spawn_niagara_system` attached at
   the bomb's fuse point (location = bomb's location + a small Z offset,
   since there's no component-socket spawn-attach for Niagara in the
   catalog — it spawns at a world location, not parented to a socket,
   unless the recipe also calls `attach_actor` on the returned Niagara
   component's owning actor, which is a real extra step worth calling out
   rather than assuming it's parented for free).
3. `set_niagara_parameter` for fuse color/intensity if the system exposes
   one (read via `get_niagara_user_parameters` first — never guess a
   parameter name blind, matching the project's materials-domain
   discipline of listing before setting).
4. If `tick_sound_cue_path` given: this is PIE-only per the Audio catalog
   entry (`play_sound_at_location`), so this step only fires anything
   audible during a Play In Editor session, not in the bare editor
   viewport. The recipe should say so in its output rather than silently
   doing nothing.
5. Read back: `list_components(bomb_actor_name)` to confirm the Niagara
   component is actually present on the actor, not just that the spawn
   call returned success.

**What makes it agentic:** deciding whether the bomb is "placed" (world
location) or "carried" (attached to character) based on which input was
supplied is already a branch; more importantly, this recipe has to
recognize that sound only has effect under PIE and report that
constraint rather than claim a sound played in a static editor view,
which would be a lie a human reviewer would catch instantly on camera.

**Honesty check:** placement, VFX spawn, and parameterization are fully
achievable with built/catalog-listed primitives
(`spawn_actor`/`attach_actor`, `spawn_niagara_system`,
`set_niagara_parameter`). The sound step is real but conditionally
inert outside PIE, which is a scope limit worth stating up front, not
discovering live during a demo.

---

## Recipe 4: `cast_spell_effect`

**Pitch:** the most important scope-correction in this document. "Give
him a spell" cannot mean a gameplay ability — no cost, no cooldown, no
targeting, no damage application, because GAS is C++-only. What it
*can* mean, honestly: spawn a Niagara VFX at or attached to a hand
socket, play a one-shot sound cue, and optionally pulse a light or an
emissive material parameter for a camera-friendly flash — a presentation
effect with zero actual gameplay behind it.

**Inputs:** `actor_name`, `hand_socket_name` (e.g. `"hand_r"`),
`spell_niagara_system_path`, `cast_sound_cue_path` (optional),
`flash_light_actor_name` (optional, for a nearby point light to pulse).

**Outputs:** `{success, vfx_spawned, vfx_attached_socket, sound_note,
  flash_applied}`

**Sequence:**
1. `spawn_niagara_system(spell_niagara_system_path, location=actor's
   hand location)`.
2. `attach_actor` the returned Niagara system's owning actor to
   `actor_name` at `hand_socket_name`, same read-back-verified pattern as
   `attach_prop_to_socket` — a spell effect that spawns at the world
   origin instead of the caster's hand is a dead giveaway of a broken
   recipe.
3. `get_niagara_user_parameters` then `set_niagara_parameter` for color/
   scale if exposed (e.g. tint the effect to a requested "fire" vs "ice"
   palette, if the underlying Niagara system was authored with a color
   parameter — this recipe cannot invent that parameter if the system
   doesn't expose one, only use it if it does).
4. Sound: same PIE-only caveat as the bomb recipe.
5. If `flash_light_actor_name` given: `set_light_properties` with a
   brief high intensity value as a "cast flash" — there's no timeline
   primitive to animate this back down automatically, so a true pulse
   (bright, then fade) is **not buildable as a single call**; this recipe
   can only set the light to a flash value and stop there, leaving any
   fade-back to a separate, later call or to PIE-driven Blueprint logic
   this MCP cannot author.

**What makes it agentic:** choosing whether to request a color override
depends on reading `get_niagara_user_parameters` first; a recipe that
just always tried to set a `"Color"` parameter would fail silently or
error on systems that expose it under a different name, which is exactly
the discover-before-guess discipline PLAN.md's materials section already
established as mandatory.

**Honesty check:** VFX spawn/attach/parameterize is fully achievable with
built/catalog primitives. The "light pulse" half is only partially
achievable: a one-shot step up is buildable, a return-to-normal fade
is not, without either a second scheduled call or logic this MCP cannot
author. State clearly to any reviewer: this recipe delivers a VFX+sound
*presentation* of a spell, not a spell.

---

## Recipe 5: `afflict_with_limp`

**Pitch:** make a character visibly, audibly sick or injured while
walking — the most mechanically interesting recipe in this set, because
it's explicitly *not* new animation, it's parameter manipulation on
what's already assigned.

**Inputs:** `actor_name`, `severity` (`"slight"` | `"heavy"`, maps to a
play-rate-and-locomotion-asset combination), `limp_animation_path`
(optional override — an existing limping/injured walk AnimSequence, if
the project has one).

**Outputs:** `{success, mechanism_used, play_rate_applied,
  animation_swapped, warning}`

**Sequence, branching on what's actually assignable:**
1. `get_animation_state(actor_name)` — read current mode. If the
   character is in `single_node` mode with a plain walk cycle assigned,
   the realistic mechanism is: **(a)** if `limp_animation_path` is
   supplied, `set_animation_mode` (re-confirm, don't clear and forget) →
   `set_animation(limp_animation_path)` → `play_animation` — a genuine
   limp cycle, because an actual limping AnimSequence asset exists and
   this recipe swapped to it; **(b)** if no limp asset is supplied, the
   only remaining lever is `set_play_rate` with an irregular-feeling
   value (e.g. 0.6 for heavy, 0.85 for slight) — a uniform rate change
   reads as "slow," not as "limping," and this recipe should say exactly
   that in its output rather than imply a slowed walk cycle looks like an
   injury.
2. If the character is in `blueprint` mode (a real Animation Blueprint
   driving locomotion, likely a Blend Space keyed on speed), **this
   recipe cannot reach in and add a limp blend or state** — that's
   authoring the AnimBP's state machine, which PLAN.md explicitly flags
   as not buildable from Python (`add_anim_state`/`add_anim_transition`
   are catalog-only, unbuilt, and the C++/thin-coverage risk tier). The
   honest fallback here is to expose *existing* AnimBP instance
   variables if any are exposed for exactly this purpose (a
   `LimpBlendAlpha` float the AnimBP's own author wired up) via
   `set_property`/`set_editor_property` on the anim instance — but that
   only works if the AnimBP already has that variable built in; this
   recipe cannot create it. The output should report which path was
   taken (`"play_rate_only"`, `"animation_swapped"`, or
   `"animbp_variable_set"`) so a caller/reviewer isn't misled about how
   "real" the limp is.
3. Read-back via `get_animation_state(actor_name)` to confirm whichever
   path was taken actually applied.

**What makes it agentic:** the entire recipe is a judgment call tree
driven by what mode the character is actually in and what assets/
variables actually exist on it — there is no universal "make it limp"
call, because the right mechanism depends entirely on what's already
been authored for this specific character. That's the clearest example
in this document of "read real state, then branch" rather than "run a
fixed sequence."

**Honesty check:** the single-node play-rate path is fully achievable
today and is honestly described as "slower, not injured-looking." The
animation-swap path is fully achievable *if* a limping AnimSequence
asset already exists in the project; this recipe cannot generate one.
The AnimBP-variable path is achievable only if the Animation Blueprint
already exposes a relevant variable; otherwise it's a dead end this
recipe should report rather than paper over. No path here involves
building a new animation state, which remains out of reach.

---

## Recipe 6: `spawn_hud`

**Pitch:** stand up a minimal on-screen UI (health bar, ammo counter, or
objective text) and get it actually visible in a running PIE session,
closing the loop from "a widget asset exists" to "it's rendering on
screen" in one call — the UI equivalent of the other recipes' "attach and
verify" pattern.

**Inputs:** `widget_blueprint_path` (existing or newly created),
`text_fields` (dict of widget element name → string, e.g.
`{"HealthText": "100", "ObjectiveText": "Find the gun"}`),
`create_if_missing` (bool).

**Outputs:** `{success, widget_created, fields_set, added_to_viewport,
  pie_required}`

**Sequence:**
1. If `create_if_missing` and the asset doesn't exist:
   `create_widget_blueprint(widget_blueprint_path)` — catalog entry,
   unbuilt as of PLAN.md, wraps `WidgetBlueprintFactory`. **Flag: this
   primitive does not exist yet.**
2. For each entry in `text_fields`: `set_widget_text(widget_path,
   element_name, value)` — also catalog-only/unbuilt, described in
   PLAN.md as "generic `get_editor_property`/`set_editor_property` on
   resolved widget references." The open question this recipe inherits:
   resolving a named child widget *inside* a WidgetBlueprint from Python
   is the same "node has no stable identity but its title" problem
   PLAN.md documents for material graph nodes — UMG widget trees may have
   the same issue, unconfirmed, and should be probed against a live
   editor before this recipe is built, not assumed to work by analogy.
3. `add_widget_to_viewport(widget_path)` — PIE-only per the catalog, since
   a UMG widget only actually renders on screen during Play, not in the
   bare editor viewport. This recipe should make that constraint explicit
   in its output, exactly like the bomb/spell recipes' sound caveat.
4. Read-back: there's no catalog entry for "is this widget currently
   visible/on screen," so verification here is weaker than the actor-based
   recipes above — the best available check is confirming the
   `create`/`add_to_viewport` calls didn't error, not confirming pixels
   on screen. **Flag: closing this gap for real would need
   `capture_viewport_screenshot`** (ranked "good, close to core" in
   PLAN.md, build-order phase 4), used the way `build_and_test_pie` uses
   it — screenshot after adding to viewport, and either visually confirm
   or at minimum confirm the call sequence didn't throw during a live PIE
   session.

**What makes it agentic:** very little, honestly, until the widget-field
resolution question above is answered — right now this recipe is close
to a dumb linear script because none of its steps have a meaningful
branch point. The one judgment call available is deciding whether to
create the widget Blueprint or reuse an existing one, based on an asset
existence check (`asset_exists`, already a helper used by
`apply_material_variant_set`).

**Honesty check:** aspirational as named. Every primitive this recipe
needs (`create_widget_blueprint`, `set_widget_text`,
`add_widget_to_viewport`) is catalog-only, not built, and the widget-tree
child-resolution mechanics are unconfirmed against a live editor. This is
the recipe in this document with the least existing foundation under it;
it should not be built until the UMG primitives it depends on are built
and live-verified individually, per PLAN.md's own stated rule that a
preset built on an unverified primitive "just compounds the untested
surface instead of reducing it."

---

## Three additional recipes, same spirit

### `damage_state_transition`

**Pitch:** flip a character/prop visually from "healthy" to "damaged" or
"destroyed" in one call — cracked material, tilted mesh, sparks — the
kind of instant, legible state change that reads well on camera and is a
natural extension of `apply_material_variant_set`, which already exists.

**Inputs:** `actor_name`, `damage_material_instance_path` (ideally
produced ahead of time by `apply_material_variant_set`'s damage-state
variant list), `spark_niagara_system_path` (optional), `tilt_degrees`
(optional, small rotation for a "staggering" look).

**Sequence:** `set_mesh_material_slot(actor_name,
damage_material_instance_path)` → optional
`set_actor_transform(actor_name, rotation_delta=tilt_degrees)` → optional
`spawn_niagara_system` attached at an impact socket, same
`attach_prop_to_socket` pattern as the weapon recipes. Read-back via
`get_mesh_material_slot` to confirm the swap actually took.

**Agentic part:** choosing the tilt angle and whether to spawn sparks can
reasonably be scaled by a `damage_severity` input rather than fixed, and
the material swap should be checked against `get_mesh_material_slot`
before declaring success, not assumed from the setter's return.

**Honesty check:** fully achievable; every primitive is built and
live-verified. This is the cheapest, most reliable recipe in the whole
document to actually build next.

### `scene_reveal_sequence`

**Pitch:** the "theatrical unveiling" demo moment — lights down, fog in,
then a character or prop actor fades up via a sudden light-properties
flip plus a Niagara "reveal" puff, timed as a scripted beat rather than
an instant state change. Genuinely impressive on camera because it
reads as directed, not just configured.

**Inputs:** `focus_actor_name`, `mood_before` (e.g. `"horror"`),
`mood_after` (e.g. `"golden_hour"`), `reveal_niagara_system_path`
(optional).

**Sequence:** `light_scene_preset(mood_before)` (already built) →
spawn/attach the reveal Niagara puff at `focus_actor_name`'s location →
`light_scene_preset(mood_after)`. This is a straight composition of two
already-shipped presets plus one VFX spawn, no new primitives needed at
all.

**Agentic part:** limited — the main judgment call is validating that
`focus_actor_name` actually exists and has a sensible location before
spending a Niagara spawn on it, via `get_scene_state`/`list_actors`
first. Mostly this recipe's value is sequencing two existing presets in
a meaningful dramatic order, which is itself worth naming once if it's
going to be requested repeatedly (per PLAN.md's own test for what
qualifies as a preset: "a sequence a human would actually do by hand,
repeatedly, in roughly the same shape every time").

**Honesty check:** fully achievable today, since it only composes
already-live-verified presets and primitives (`light_scene_preset`,
`spawn_niagara_system`). The lowest-risk recipe to ship, alongside
`damage_state_transition`.

### `squad_formation_spawn`

**Pitch:** spawn N enemy/ally actors in a tactical formation (line,
wedge, circle) around a point, each facing a shared target direction, in
one call — a crowd/army moment that is visually dense and instantly
reads as "the AI just populated a scene with intent," distinct from
`set_dressing_pass`'s randomized scatter because the placement is
geometrically deliberate, not jittered.

**Inputs:** `class_path`, `count`, `center`, `formation`
(`"line"` | `"wedge"` | `"circle"`), `facing_target` (a location or actor
name every spawned unit should face), `spacing`.

**Outputs:** `{success, count, actor_names, formation_used}`

**Sequence:** compute N positions deterministically from `formation` +
`spacing` + `center` (pure math, no Unreal call needed for the layout
itself) → `spawn_actor` per position with rotation computed to face
`facing_target` → read back via `list_actors`/`get_scene_state` to
confirm the expected count actually landed, since a batch spawn failing
partway through should be caught, not silently under-reported.

**Agentic part:** resolving `facing_target` when it's given as an actor
name rather than a raw location requires a `get_property`/scene-state
read to find that actor's current location first — a live lookup, not a
hardcoded coordinate — and the facing-rotation math has to be computed
per spawn point relative to that live position, not a single shared
rotation value, which is the detail that separates "formation" from
"pile of identical actors."

**Honesty check:** fully achievable with only built primitives
(`spawn_actor`, `list_actors`/`get_scene_state`, `get_property` for
resolving a named facing target). No catalog gaps.

---

## Composability summary

- **`attach_prop_to_socket`** is the clear reusable building block.
  `equip_weapon`, `plant_bomb` (carried mode), `cast_spell_effect`, and
  `damage_state_transition`'s spark spawn all call it, or should, rather
  than each reimplementing attach-then-verify independently. It is the
  single highest-leverage small recipe to build first, before any of the
  flashier ones, for the same reason PLAN.md gives for presets generally:
  write the sequence once, pre-tested, instead of letting four other
  recipes each get the attach/read-back logic slightly wrong in their own
  way.
- **`light_scene_preset`** (already shipped) is reused wholesale by
  `scene_reveal_sequence`, which is really just "call it twice with a
  VFX beat in between" — this is the cleanest evidence that the preset
  layer composes the way PLAN.md's design intends.
- **A socket-discovery primitive** (`get_skeletal_mesh_sockets` or
  equivalent) does not exist yet and is a dependency shared by every
  socket-attach recipe in this document (`attach_prop_to_socket`,
  `equip_weapon`, `cast_spell_effect`). Building this one primitive first
  upgrades four recipes at once from "attach optimistically, verify after
  the fact" to "verify the target first, then attach with confidence" —
  worth prioritizing precisely because of that fan-out.
- **UMG primitives** (`create_widget_blueprint`, `set_widget_text`,
  `add_widget_to_viewport`) are a dependency only `spawn_hud` needs among
  the recipes here, so there's no cross-recipe sharing pressure to build
  them early the way there is for the socket-discovery gap — they can
  wait until UI work is actually prioritized without blocking any other
  recipe in this document.
- **Discover-before-set** is a repeated pattern, not a reusable call, but
  worth naming: `get_niagara_user_parameters` before
  `set_niagara_parameter` appears in `plant_bomb`, `cast_spell_effect`,
  and implicitly in `damage_state_transition`'s spark step. It mirrors
  the materials domain's `get_material_parameter_list`-before-set
  discipline already established in PLAN.md, applied to a second domain.

## Build-order recommendation

Given the above, in order of foundation-readiness, not demo flashiness:

1. `attach_prop_to_socket` (needs: nothing new except ideally the socket
   discovery primitive, which can also follow later as a hardening pass).
2. `damage_state_transition` and `scene_reveal_sequence` (fully buildable
   today on existing, shipped presets and primitives; zero new gaps).
3. `equip_weapon`, `plant_bomb`, `squad_formation_spawn` (buildable today,
   depend only on `attach_prop_to_socket` plus built primitives).
4. `bring_character_to_life`, `afflict_with_limp` (buildable today at the
   instance-level-animation scope described; ship with explicit
   documentation of what mechanism was used, since both have an honest
   "lesser" fallback path that must not be reported as equivalent to the
   fuller one).
5. `cast_spell_effect` (buildable for the VFX/sound half; ship with the
   light-pulse limitation stated plainly, not quietly dropped).
6. `spawn_hud` (blocked on three unbuilt UMG primitives and one
   unconfirmed widget-tree-resolution question; build and live-verify
   those primitives individually first, per PLAN.md's standing rule
   against building a preset on an unverified primitive).
