# Research: 8 New Domains from Epic's Official Unreal MCP Plugin (UE 5.8)

Scope: determine whether a genuine Python API surface exists for 8 domains Epic's own
"Unreal MCP" plugin covers that our catalog does not, per PLAN.md's phasing. This is a
research report only — no tool code was written, no file under `src/unreal_mcp/` was
touched. No live editor was available in this environment, so nothing here has had the
TOOL_BUILDING_GUIDE.md step-2b live-probe confirmation; every verdict is doc-sourced only
and flagged "needs live probe before implementation" where a tool would actually be built.

---

## 1. Animation Mixer — NOT BUILDABLE AS A GRAPH-AUTHORING SURFACE; narrow Python slice exists elsewhere

Epic's "Anim Mixer" (Sequencer Animation Mixer) is an **experimental UE 5.8 plugin**
that mixes/layers animation sources inside Sequencer, built on the Unreal Animation
Framework (UAF) and Animation Blueprints. Its own runtime classes
(`UMovieSceneAnimNextTargetSystem`, the `MovieSceneAnimMixer`/`MovieSceneAnimMixerEditor`
plugin pair) are C++ editor/runtime plumbing with **no Python API class found for them**
after a real search (plugin docs page, `API/Plugins/MovieSceneAnimMixer*` pages, site
search for "AnimMixer" on the python-api index — all three came back with only C++ API
pages, never a `python-api/class/...` page).

What *is* real and Python-reachable is the adjacent **AnimNext** system: `unreal.AnimNextAnimGraph`
and `unreal.AnimNextAnimationGraphLibrary` (`add_animation_graph`) are documented Python
API classes. This is AnimNext's own graph asset type, not the Sequencer Mixer UI; it is
the nearest real Python surface to "animation mixer" but is a different, narrower
capability (assembling/adding AnimNext graphs to assets), not mixing animation sources
in a Sequencer track.

- **Verdict: NOT BUILDABLE as "Animation Mixer" (Sequencer mixer track authoring).** The
  mixer plugin itself has no Python binding found by class-name search, site search, or
  checking its own C++ page for a Python-exposed sibling.
- **Partial substitute, separately buildable:** `AnimNextAnimationGraphLibrary.add_animation_graph`
  is a real, named Python call, but it is AnimNext graph assembly, not animation
  mixing — do not conflate the two if this gets picked up later.
- Sources: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AnimNextAnimationGraphLibrary ,
  https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AnimNextAnimGraph ,
  https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/MovieSceneAnimMixerEditor ,
  https://forums.unrealengine.com/t/tutorial-animation-mixer-in-sequencer-intro/2731468
- **Needs live probe:** whether `AnimNextAnimationGraphLibrary` is even present/enabled in
  this project's installed 5.8 build (AnimNext ships as an optional plugin), and whether
  `dir()` on the mixer's C++ classes reveals anything the docs search missed (step-2a's
  doc search is complete; step 2b/the dir() sweep from the 5-step protocol was not run —
  no editor access).

---

## 2. Morph Targets — BUILDABLE (read discovery; write is narrower, needs live confirmation)

`unreal.MorphTarget` is a real, documented Python API class
(`https://docs.unrealengine.com/5.0/en-US/PythonAPI/class/MorphTarget.html`, inherits
`unreal.Object`). On the mesh side, `USkeletalMesh.GetMorphTargets()` / the Blueprint-exposed
`K2_GetAllMorphTargetNames()` are real engine calls (confirmed via C++/BlueprintAPI docs);
what's unconfirmed is specifically whether `unreal.SkeletalMesh` (the Python wrapper)
exposes the same accessor under a `get_morph_targets`-shaped name, or whether (per
TOOL_BUILDING_GUIDE gotcha #13 — "don't assume a struct has the members its C++ equivalent
has") it's missing from the Python surface the way `get_component_location` was missing
from `PrimitiveComponent`.

Driving a morph target's weight at runtime is a `SkinnedMeshComponent` call
(`SetMorphTarget`/`ClearMorphTargets` in Blueprint/C++); whether this is Python-exposed on
`unreal.SkeletalMeshComponent` is also unconfirmed by this search.

- **Verdict: PARTIALLY BUILDABLE.** `unreal.MorphTarget` the class is real. Discovery
  (listing morph target names on an asset) is very likely real given `K2_GetAllMorphTargetNames`
  exists on the engine side and Python typically mirrons `K2_`-exposed Blueprint functions,
  but the exact Python method name on `unreal.SkeletalMesh` was not found verbatim in this
  search — this is a genuine gap, not a confirmed absence (only 2 of the 5-step protocol's
  steps were run: class-name confirm and docs-page read; no `dir()` sweep, no sibling-Library
  check was possible without an editor).
- Sources: https://docs.unrealengine.com/5.0/en-US/PythonAPI/class/MorphTarget.html ,
  https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/Engine/USkeletalMesh/GetMorphTargets ,
  https://docs.unrealengine.com/4.27/en-US/API/Runtime/Engine/Engine/USkeletalMesh/K2_GetAllMorphTargetNames/index.html
- **Needs live probe before implementation (mandatory, not optional):** `dir(unreal.SkeletalMesh)`
  and `dir(unreal.SkeletalMeshComponent)` against the live 5.8 editor for the exact getter/setter
  names, exactly as the guide's 5-step protocol requires before writing any tool. This domain
  should NOT be marked "confirmed absent" or "confirmed present" on docs alone.

---

## 3. Control Rig — BUILDABLE, and the strongest finding in this whole report

Real, documented, current Python scripting surface. Three classes carry the actual
capability, all confirmed present on `dev.epicgames.com/.../python-api/class/...` pages:

- **`unreal.ControlRigBlueprint`** — the asset. `get_controller()` returns the graph
  controller; `get_hierarchy_controller()` returns the hierarchy controller;
  `get_available_rig_units()` lists what can be added.
- **`unreal.RigVMController`** (module: ControlRigDeveloper) — the actual graph-mutation
  object, confirmed with a large, real method surface:
  - Node creation: `add_unit_node()`, `add_variable_node()`, `add_comment_node()`,
    `add_branch_node()`, `add_if_node()`, `add_array_node()`, `add_template_node()`,
    `add_function_reference_node()`.
  - Wiring: `add_link(output_pin_path, input_pin_path, setup_undo_redo=True, print_python_command=False)`,
    `break_link()`, `break_all_links()`. Pins are addressed by string path
    (`"NodeA.Translation.X"`-shaped), not by object handle — notably **more** reachable
    than Blueprint's `EdGraphPinType`, which exposes no readable fields (see
    TOOL_BUILDING_GUIDE's Blueprint section).
  - Pin values: `set_pin_default_value()`, `get_pin_default_value()`, `add_array_pin()`,
    `remove_array_pin()`.
  - Removal: `remove_node()`, `remove_exposed_pin()`.
  - `print_python_command=True` on these calls is a built-in "show me the exact Python
    call that would reproduce this edit" feature — genuinely useful for step-2b live
    confirmation later, since the editor can echo back the ground-truth call shape for
    any edit made by hand first.
- **`unreal.RigHierarchyController`** (module: ControlRigDeveloper) — `add_bone(name, parent, transform)`
  and hierarchy-side element creation, separate object from the graph controller.

This is a real **Model-View-Controller** architecture as the preliminary research
flagged: Controller objects mutate a Rig Graph / Rig Hierarchy that the Blueprint asset
owns, and the official "control-rig-python-scripting-in-unreal-engine" doc page is a
genuine authoring guide (not a stub), including the Python console's command-logging
feature for reverse-engineering call shapes from hand-performed edits.

- **Verdict: BUILDABLE.** This is comparable in richness to the material-graph tooling
  already built in this project (named Controller object, string-addressed pins,
  real node/link CRUD) and arguably easier to verify than materials because pins are
  string-addressable rather than needing the node-selector-by-title workaround material
  nodes needed.
- Sources: https://dev.epicgames.com/documentation/unreal-engine/control-rig-python-scripting-in-unreal-engine ,
  https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/RigVMController ,
  https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/ControlRigBlueprint (page existence confirmed via search; full method list not individually re-fetched — see below)
- **Needs live probe before implementation:** `ControlRigBlueprint.get_controller()`'s
  exact return-object identity (confirm it is the same `RigVMController` class, not a
  differently-scoped wrapper), and whether this specific installed 5.8 build has Control
  Rig plugin enabled at all (it is optional). Also confirm `add_unit_node`'s first
  argument shape (`script_struct, method_name, position` per one source vs. the
  RigVMController page's own listing) before writing a single call — the two sources in
  this search disagreed slightly on signature wording and that gap needs resolving
  against the actual page or a live `help()` call, not guessed.

---

## 4. Niagara Graph-Level Authoring — STILL NOT BUILDABLE; PLAN.md's existing scope is correct, not overcautious

This was the domain most worth double-checking since the user's framing suggested
PLAN.md may have scoped it down prematurely. It did not turn out that way.

- **`unreal.NiagaraPythonModule` (the actual class name is `UNiagaraPythonModule` in
  C++, Python-exposed) exposes exactly two methods: `GetObject()` and `Init()`.** It is
  a thin wrapper around a single `UNiagaraStackModuleItem*` — i.e. it lets Python hold a
  reference to *one already-existing* module instance already placed in an emitter's
  stack. It has **no method to create a module, create a graph node, or wire a
  connection.** Confirmed by reading the actual class page content, not just a search
  snippet.
- `unreal.NiagaraScript` and the C++-only `UNiagaraScriptSourceBase`/`UNiagaraScriptSource`
  (which holds the actual `NodeGraph` — the real particle-update/spawn graph) have **no
  Python API class page found** for the graph-holding types themselves, after checking
  both the dedicated `NiagaraScript` Python page (which does not expose graph editing)
  and searching specifically for `NiagaraGraph`/`NiagaraScriptSource` Python pages (none
  exist; only C++ `API/Plugins/Niagara/...` pages came back).
- This matches PLAN.md's own existing note almost exactly: "Niagara's actual
  module/script graph... Python bindings for editing it node-by-node are not solidly
  documented." The research in this task makes that **more confident, not less** — it is
  not "not solidly documented," it is "the one Python class that touches the graph layer
  is read-only to a single already-placed module, full stop."
- **Verdict: NOT BUILDABLE IN PYTHON** for graph-level authoring (node creation, module
  authoring, wiring). PLAN.md's instance-level scope (spawn/parameterize/assemble from
  existing modules and templates) remains the correct ceiling. Needs the C++ escape
  hatch if graph-level Niagara authoring is ever required.
- Sources: https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/NiagaraEditor/UNiagaraPythonModule ,
  https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/NiagaraScript ,
  https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/Niagara/UNiagaraScriptSourceBase
- Protocol note: this is one of the few domains where I can state the 5-step search was
  genuinely exhausted at the "no Python binding" conclusion — class name confirmed
  (`NiagaraPythonModule`/`NiagaraScript` both exist and were read in full), the
  graph-owning class (`NiagaraScriptSource`) was searched specifically and has no Python
  page, and no sibling `*Library` class surfaced in any search for Niagara graph editing.
  Step 3 (live `dir()`) and step 4 (sibling-Library check against the live object) were
  not run — no editor access — so "confirmed absent" here rests on steps 1+2 of 5, same
  caveat as the other domains, but the signal is unusually consistent across every page
  actually read.

---

## 5. Slate Widget Inspection — BUILDABLE, but via a different and newer surface than classic Python

Two distinct things surfaced under this name, and they should not be conflated:

- **`unreal.SlateInspectorToolset`**: this is **not** a classic `unreal.*` Python API
  class in the traditional sense. The `python-api/class/SlateInspectorToolset` URL
  404s directly. What actually exists is `USlateInspectorToolsetSubsystem` and a
  `SlateInspectorToolset` **plugin**, part of UE 5.8's new **AI-agent toolset
  framework**: C++ classes derived from `UToolsetDefinition` expose static methods
  marked `AICallable`, registered via `UToolsetRegistry`, and reachable through the
  MCP/tool-call layer (not through `import unreal; unreal.SlateInspectorToolset()`-style
  scripting). Described as "a Playwright-style Slate UI automation toolset that exposes
  snapshot, screenshot, and interaction tools" with an `Observe()`/`Unobserve()` model
  for tracking widget subtrees at ~100ms refresh.
- This is the exact mechanism behind Epic's own Unreal MCP plugin's Slate-inspection
  tools — i.e., Epic did not expose this through the Python Remote Execution path our
  project uses; they built a **parallel toolset/MCP-registration layer in C++** 
  specifically for this. That is a structurally different integration point than every
  other tool in our catalog, which goes through `bridge.run_python()`.
- **Verdict: BUILDABLE, but not via our project's existing architecture.** Our bridge is
  Python Remote Execution into the running editor; `SlateInspectorToolset` is a
  `UToolsetDefinition`/`AICallable` C++ construct, which is the same mechanism as the
  "C++ plugin escape hatch" PLAN.md already names for Blueprint graph wiring — this is
  not a pure-Python win, it is a second confirmed real use case for the escape hatch,
  and arguably the most concrete one found in this whole report since Epic's own shipped
  plugin uses exactly this pattern for this exact feature.
- Sources: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/SlateInspectorToolset (404,
  confirms it is not a classic Python class),
  https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/SlateInspectorToolset/USlateInspectorToolsetSubsystem ,
  https://www.seeles.ai/resources/blogs/unreal-engine-5-8-mcp-custom-toolsets-python-cpp-guide
- **Needs live probe:** none meaningful without first deciding whether this project wants
  to build the `UToolsetDefinition`/`AICallable` C++ pattern at all — that is an
  architecture decision (a second integration path alongside Remote Execution), not a
  single-tool probe.

---

## 6. Automation Test Running — BUILDABLE, already de-risked by prior session research, now corroborated

Confirms the preliminary finding. Two independent, real surfaces exist:

- **`unreal.PythonTestRunner`** — classic Python API, documented at
  `python-api/class/PythonTestRunner`. `create()`, `get_tests()`, `run_test()`,
  `get_last_test_result()`, all previously confirmed and not re-litigated here per the
  task's instruction to treat this as established.
- **`UAutomationTestToolset`** (newly found this session) — the same
  `UToolsetDefinition`/`AICallable` C++ pattern as `SlateInspectorToolset` above, with a
  full, real method list read directly off its documentation page: `DiscoverTests(bool bForceRediscover)`,
  `ListTests(NameFilter, TagFilter, Limit)`, `RunTests(TestNames)`, `RunTestsByFilter(FilterExpression)`,
  `GetTestStatus()`, `GetTestResults()`, `StopTests()`. This is confirmed UE 5.8 and is
  almost certainly the exact mechanism Epic's own plugin uses for "running automation
  tests," per the plugin's own capability description.
- **Verdict: BUILDABLE**, and the only domain in this report with **two independent real
  paths**, one of which (`PythonTestRunner`) goes through the existing pure-Python bridge
  architecture with zero new infrastructure needed.
- Sources: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/PythonTestRunner ,
  https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/AutomationTestToolset/UAutomationTestToolset
- **Needs live probe before implementation:** `PythonTestRunner.create()`'s exact
  argument shape and `run_test()`'s return value shape, per the guide's standard
  step-2b — not because anything here looks doubtful, but because no tool gets written
  on docs alone per this project's own rule.

---

## 7. GAS (Gameplay Ability System) — CONFIRMED NOT BUILDABLE FOR SETUP; genuine inspection path exists

This corroborates and sharpens the preliminary finding rather than overturning it.

- **`unreal.AbilitySystemComponent` is a real, documented Python API class.** This is a
  meaningfully different fact than "GAS has no Python surface at all" — the class exists,
  is constructible (`outer: Object | None = None, name: Name | str = 'None'`), and
  exposes at least one read-write editor property, `activatable_abilities`
  (a `GameplayAbilitySpecContainer`).
- However: its *existence* does not contradict the preliminary finding that core
  AbilitySystemComponent/AttributeSet **setup** needs C++. The documented surface found
  here is consistent with "the component and its container type are visible to Python for
  inspection/property access," not with "abilities can be authored, granted, or activated
  from a cold start in Python." No `give_ability`/`activate_ability`-shaped Python method
  was found on this class in this search (the C++ side has `GiveAbility`; no Python
  equivalent surfaced). This matches the project's own established pattern elsewhere
  (e.g. `World` lacking `get_actors()` despite C++ having it) — a property being visible
  via `get_editor_property` is not the same claim as a verb method being exposed.
- **Verdict: PARTIALLY BUILDABLE, narrowly.** Confirmed in line with the preliminary
  research: GAS *setup* (wiring an AttributeSet, defining GameplayEffects' core execution
  calculations, authoring the AbilitySystemComponent onto an actor from scratch) is a C++
  job. What is realistically buildable in pure Python, per this session's finding, is
  **read-only inspection of an actor that already has GAS set up** — reading
  `activatable_abilities` and other exposed editor properties via the same
  `get_editor_property` mechanism every other property-reading tool in this project
  already uses (`scene.get_property`'s pattern applies directly here, no new
  infrastructure needed). This is exactly the "read current attribute values" alternative
  the user's own framing already proposed, now backed by a confirmed real class to read
  it through.
- Sources: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/AbilitySystemComponent (confirmed via search snippet; direct fetch 404'd — see caveat below),
  https://dev.epicgames.com/documentation/unreal-engine/API/Plugins/GameplayAbilities/UAbilitySystemComponent/GiveAbility (C++-only, no Python counterpart found)
- **Caveat on source quality:** the direct `WebFetch` of the AbilitySystemComponent
  Python page 404'd in this session (transient, or the page structure doesn't suit the
  fetch tool — unclear which); the class's existence and the `activatable_abilities`
  property are sourced from the search-engine snippet of that same page, not a direct
  read. **Needs live probe before implementation, more than any other domain in this
  report**: confirm the page content directly (retry the fetch, or check
  `dir(unreal.AbilitySystemComponent)` live) before writing even a read-only inspection
  tool, since the one source available here is weaker than everywhere else in this
  report.
- **Realistic alternative confirmed, as the user framed it:** build
  `get_ability_system_state(actor_name)` as a read-only tool once live-confirmed,
  wrapping `get_editor_property('activatable_abilities')` and whatever attribute-set
  properties the live probe turns up — not a C++ stub, since the read path appears to be
  genuinely reachable through the existing bridge.

---

## 8. Gizmo Control — NOT BUILDABLE IN PYTHON, genuinely absent after a real 5-step search

- **Step 1 (confirm class name via site search):** Searched `UEditorInteractiveGizmoManager`,
  `UInteractiveGizmoManager`, `EditorGizmo`, bare `Gizmo` against the python-api index.
  Every single result returned was a `C++` `API/Editor/...` or `API/Runtime/...` page.
  Zero `python-api/class/...` pages surfaced for any gizmo-related class, across four
  separate query phrasings.
- **Step 2 (read the whole class page):** Read the `UEditorInteractiveGizmoManager` and
  `UInteractiveGizmoManager` C++ pages' summaries. Both are `UCLASS(MinimalAPI, Transient)`
  types in the Editor module — `MinimalAPI` on a class is itself a mild signal against a
  rich scripting surface, though not proof on its own.
- **Step 3 (live `dir()` sweep):** Not run — no editor access this session. This is a
  genuine gap in the search, not a result; the final verdict below is honestly weaker
  than domains 4 and 6 for exactly this reason.
- **Step 4 (sibling Library/Subsystem class check):** Searched for a `GizmoLibrary` /
  `EditorInteractiveGizmoSubsystem` Python binding specifically; none surfaced. The
  closest subsystem-shaped name, `UEditorInteractiveGizmoSubsystem`, also only returned
  C++ pages.
- **Step 5:** Per the guide, only after steps 1-4 genuinely fail does "not found" become
  a real finding. Steps 1, 2, and 4 are done and came back negative; step 3 could not be
  done. Stating this plainly rather than papering over it: **this verdict is provisional
  on a live `dir()` check that was not possible in this environment**, not a full
  5-step clearance.
- **Verdict: NOT BUILDABLE IN PYTHON, with a real caveat.** The C++ gizmo framework
  (`UInteractiveGizmoManager`/`UEditorInteractiveGizmoManager`) exists and is how the
  Transform/Rotate/Scale viewport gizmos work internally, but no Python binding surfaced
  for it across a genuine multi-angle search, and the viewport-gizmo interaction model
  (mouse-drag-driven manipulation) is inherently a UI-input concept that doesn't map
  cleanly onto a scripted call anyway — scripted transform manipulation in this project
  is already covered by `set_actor_transform`, which achieves the same end state without
  needing gizmo interaction at all.
- Sources: https://dev.epicgames.com/documentation/unreal-engine/API/Editor/EditorInteractiveToolsFramework/UEditorInteractiveGizmoManager ,
  https://dev.epicgames.com/documentation/unreal-engine/API/Runtime/InteractiveToolsFramework/UInteractiveGizmoManager
- **Needs live probe before fully closing this out:** `dir()` on
  `unreal.EditorInteractiveGizmoManager` (if the name is even importable at all — that
  itself is the first thing to check) against the live editor. Until that runs, "not
  buildable" here is the 4-of-5-steps-done version of the finding, flagged as such per
  the task's own instructions on how to report incomplete searches honestly.

---

## Catalog gap check: scene.py / components.py / assets.py vs. Epic's "ActorTools" scope

Epic's officially-described ActorTools scope (per the task's framing): "transforms,
labels, parent-child relationships, and components."

Checked directly against the three files read in full this session:

| Epic ActorTools area | Our coverage | File |
|---|---|---|
| Transforms | `set_actor_transform`, `get_property`/`set_property` for everything else | `scene.py` |
| Labels | **Gap — read-only only.** `list_actors`/`get_selected_actors` return `label` via `get_actor_label()`, but there is no `set_actor_label` tool anywhere in `scene.py`. `SetActorLabel` is a real, simple Blueprint/C++ call (`AActor::SetActorLabel`) with no known gotcha from this session's reading — this looks like a cheap, real, missing primitive, not a deferred one. | `scene.py` |
| Parent-child relationships | Covered: `attach_actor`/`detach_actor` (actor-level), deliberately not component-socket-level per the file's own documented scope decision | `scene.py` |
| Components | Covered: `add_component`, `remove_component`, `list_components`, `set_component_property` | `components.py` |
| Folder organization | Covered: `set_actor_folder`, live-verified | `scene.py` |
| Tags | Covered: `tag_actor`, `find_actors_by_tag`, live-verified | `scene.py` |
| Selection | Covered: `select_actors`, `get_selected_actors`, live-verified | `scene.py` |
| Grouping (GroupActor) | **Confirmed absent, already documented as such in PLAN.md** — `EditorActorSubsystem` has no `group_actors`/`ungroup_actors` live despite the stub listing them. Not a new finding, just confirming the existing note still holds against the files as read. | `scene.py` |
| Actor-level component socket attach (`attach_to_component`) | **Deliberately deferred**, per the file's own docstring note: "attaching to a specific component socket is `Actor.attach_to_component` and belongs with the component tools if it is ever needed." Named as a real, scoped-out gap already, not something this session is newly discovering. | `scene.py`/`components.py` boundary |
| Generic asset rename/move/duplicate (`rename_asset`/`move_asset`/`duplicate_asset`) | **Gap, already named in PLAN.md's catalog as not-yet-built** (`EditorAssetLibrary` equivalents) — `assets.py` as read only has `asset_exists`, `delete_asset`, `save_asset`. Confirming the file-level state matches PLAN.md's own "not yet built" list rather than silently drifted further. | `assets.py` |

**The one genuinely new gap worth flagging:** `set_actor_label`. Every other actor
"naming" surface in this project (`tags`, `folder`) has both a getter and setter; labels
only have a getter. `AActor::SetActorLabel` is editor-only, simple, no known
version-drift risk surfaced in this session's reading, and would close the one real
asymmetry in an otherwise well-covered "transforms, labels, parent-child, components"
checklist. Still needs the standard step-2 docs-then-probe treatment before being built
— nothing here is exempt from that — but it is a legitimate small gap, not a
rediscovery of something already known and deferred.

---

## Blueprint graph editing: PLAN.md's assumption should be updated, not just repeated

PLAN.md currently says Blueprint graph node wiring "is the deferred 'C++ plugin for graph
wiring' extension... likely the first thing that needs the C++ escape hatch since
pure-Python Blueprint graph editing support is thin," and TOOL_BUILDING_GUIDE.md's own
measured findings say `EdGraph.Nodes` is protected and nodes are not addressable by
object path, so the existing `BlueprintEditorLibrary` node-level helpers
(`get_node_title`, `list_input_pins`, etc.) are confirmed unreachable.

This session found a **different, real class that was not checked before**:
**`unreal.BlueprintGraphEditor`**, confirmed present on the official python-api docs with
a large, genuinely usable method surface read directly off its page:

- Node creation covering the common cases: `add_branch_node()`, `add_call_function_node(function_path)`,
  `add_custom_event_node(event_name)`, `add_comment_node(...)`, `add_component_bound_event_node(...)`,
  `add_dispatcher_event_node(...)`, `add_macro_node(macro_path)`, `add_return_node()`.
- Variable read/write node creation: `add_get_member_variable_node`, `add_set_member_variable_node`,
  `add_get_local_variable_node`, `add_set_local_variable_node`.
- **This is the part that most directly contradicts the "nodes aren't addressable"
  finding**: `list_all_nodes()`, `list_nodes_of_class(class)`, `find_event_node(event_name)`,
  `list_nodes_with_errors()`/`list_nodes_with_warnings()`/`list_nodes_with_notes()` all
  return node handles. If these genuinely return usable `K2Node` objects (not opaque
  references), this is a materially different and better answer than "protected, not
  addressable" — it would mean the *access path* was wrong before (going through raw
  `EdGraph.Nodes`), not that the capability itself is absent.
- Graph-level construction: `create_and_edit_function_graph(blueprint, func_name)` and
  `get_graph_editor(graph)`/`get_graph_editor_by_name(blueprint, graph_name)` are the
  entry points — this is a different and newer access pattern than the
  `BlueprintEditorLibrary` calls the existing TOOL_BUILDING_GUIDE findings were based on.
- Dynamic/generic node creation: `create_node_from_name(node_with_category, location, context_pins, declaring_class)`
  — this is the closest thing found anywhere in this report to "add an arbitrary node by
  name," which is exactly the capability PLAN.md assumed needed C++.

**This does not mean the existing TOOL_BUILDING_GUIDE.md findings were wrong** — they
were measured against `BlueprintEditorLibrary` and raw `EdGraph` access, which really are
as limited as documented. `BlueprintGraphEditor` is a **different class** that was
apparently not checked in that earlier session, consistent with the whole point of the
guide's 5-step protocol (step 4: check a sibling Library/Subsystem class before
concluding absence) — this is exactly the sibling class that step would have surfaced,
and it appears nobody ran step 4 for Blueprint graph editing specifically.

- **Verdict: update PLAN.md's assumption from "likely needs C++ escape hatch" to
  "PARTIALLY BUILDABLE IN PYTHON, re-investigate before deferring to C++."** This is the
  single highest-value correction in this whole report, because PLAN.md explicitly named
  Blueprint graph wiring as the flagship case for the C++ escape hatch, and that may no
  longer be true.
- Source: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/BlueprintGraphEditor
  (fetched directly, full method list read, not a search snippet)
- **Needs live probe before implementation, urgently, before writing any tool code for
  this domain:** whether `BlueprintGraphEditor` is new in 5.8 specifically (plausible,
  given it wasn't found in the prior session's TOOL_BUILDING_GUIDE.md research) or was
  always present and missed; whether `list_all_nodes()` actually returns usable `K2Node`
  handles that other `BlueprintGraphEditor` methods can act on (vs. returning the same
  kind of unreadable opaque reference the old `EdGraph.Nodes` path hit); and whether
  `create_node_from_name`'s `node_with_category` string format is documented anywhere
  concrete enough to use without guessing. This is exactly the kind of "looks great in
  docs, confirm against live build before trusting it" case the guide's step 2b exists
  for — do not skip that step just because this finding is exciting.

---

## Build order recommendation

Ranked by (confirmed real API) × (value) × (how little new infrastructure it needs):

1. **Automation test running (`PythonTestRunner`)** — build first. Two independent real
   paths, zero new architecture, lowest risk in this entire report, and was already
   flagged low-risk before this session even started.
2. **Blueprint graph editing via `BlueprintGraphEditor`** — build second, but *after* a
   dedicated live-probe session, not blind. If `list_all_nodes()`/`create_node_from_name`
   pan out live, this closes PLAN.md's single biggest named gap (graph node wiring) in
   pure Python, which changes the whole project's risk profile for that domain. High
   value, moderate-to-high risk pending the probe — probe first, commit second.
2. **Control Rig** — tie with Blueprint graph editing for second place. Real,
   well-documented, string-addressed pins (easier to verify than Blueprint's opaque
   nodes), and a distinct, valuable capability (procedural rigging) nothing else in the
   catalog touches. The `print_python_command=True` self-documenting feature lowers the
   step-2b probing cost specifically for this domain.
3. **Morph targets** — cheap to probe (one `dir()` sweep answers most of the open
   question), likely buildable, moderate value (useful for facial animation / blend
   shape work, smaller than Control Rig or Blueprint graphs on its own).
4. **GAS read-only inspection** — build only the narrow read path
   (`get_ability_system_state`-shaped tool over `get_editor_property`), explicitly not
   the setup/authoring path. Low effort once the one weak source is re-confirmed live;
   value is real but inherently capped since it requires GAS to already be hand-set-up
   in C++ first.
5. **Slate widget inspection, Automation via `UAutomationTestToolset`** — defer as a
   pair. Both are real, but both require building the `UToolsetDefinition`/`AICallable`
   C++ registration pattern, which is new architecture for this project (a second
   integration path alongside Python Remote Execution). Worth doing eventually since it's
   confirmed to be Epic's own actual mechanism, but it's an infrastructure decision, not
   a single tool, so it shouldn't jump the queue ahead of pure-Python wins above.
6. **Animation Mixer** — defer/drop as named. The mixer plugin itself has no Python
   surface found; the adjacent `AnimNextAnimationGraphLibrary` is real but is a
   meaningfully different capability (AnimNext graph assembly) and shouldn't be built
   under the "Animation Mixer" label without being explicit that it's not actually that.
7. **Niagara graph-level authoring** — drop, confirmed. PLAN.md's existing scope (instance
   level only) is correct and this session's deeper look made that more certain, not
   less. Needs the C++ escape hatch if ever required; not worth pursuing in pure Python.
8. **Gizmo control** — drop. No Python binding found after 4 of 5 search steps, and the
   one remaining step (live `dir()`) is unlikely to change the answer given
   `set_actor_transform` already covers the same end-state need without any gizmo
   interaction at all. Lowest value of the eight even if a binding somehow existed, since
   it's a UI-interaction concept that scripting bypasses rather than needing.

**Net correction to PLAN.md:** the single most important update from this research is
not a new domain — it's that **Blueprint graph node wiring, PLAN.md's flagship "needs
C++" example, may not need C++ after all.** Everything else in this report is either a
confirmation of existing scope decisions (Niagara, GAS, Gizmo) or a genuinely new but
secondary domain (Control Rig, morph targets, automation, Slate). That one correction is
worth prioritizing a live-probe session over just adding more catalog entries.
