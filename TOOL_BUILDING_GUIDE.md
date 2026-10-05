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
