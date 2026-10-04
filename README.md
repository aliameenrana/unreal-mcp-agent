# unreal-mcp

A general-purpose MCP server that bridges an MCP client (Claude Code, Claude
Desktop, Cursor) to a running Unreal Engine 5 editor, using Unreal's
Remote Control API (HTTP) to execute Python inside the editor.

See [PLAN.md](PLAN.md) for the full architecture, scope, and security design.

## Status

The full MVP tool set is confirmed working end to end against a live Unreal
Editor 5.8 session, each one exercised against a real, non-trivial level
(138 actors) and cleaned up afterward:

- `list_actors`, `spawn_actor`, `delete_actor`, `set_actor_transform`,
  `set_property` (`scripts/smoke_test.py` covers the first three plus
  transform and property mutation).
- `create_material`, `create_material_instance`,
  `set_material_scalar_parameter`, `set_material_vector_parameter`.
- `compile_blueprint`, against a real Blueprint asset created for the test.
- `set_mesh_material_slot` / `get_mesh_material_slot`, per-actor
  `MaterialInstanceDynamic` assignment, verified by reading the slot back
  through `get_material()` and confirming the instance's parent.
- `set_light_properties`, `set_sky_atmosphere_params`,
  `set_exponential_fog_params` and their three getters, verified by reading
  each component's properties back by name after the write.
- `light_scene_preset` (all four moods), `set_dressing_pass`,
  `apply_material_variant_set`.

23 tools are registered with the MCP server, each one carrying a risk tier in
`security.py`.

Three correctness notes from live testing, all of them silent failures rather
than errors:

- `set_property` sets a raw Python attribute via `setattr`, which only works
  for genuine UPROPERTY fields exposed under their Python (snake_case) name,
  e.g. `custom_time_dilation`. Some engine-side names look settable but are
  not (`hidden` is a read-only accessor backed by `bHidden`; use a dedicated
  method like `set_actor_hidden_in_game` for those).
- `setattr` is rejected outright on light and fog component properties. Those
  tools use `set_editor_property`.
- Subscripting a lambda's result, as in `(lambda c: ...)[-1]`, compiles but
  makes CPython emit a SyntaxWarning, which Unreal reports through
  `LogOutput` alongside an empty return value, so the remote call looks like
  it failed. Index the tuple inside the lambda body instead;
  `lighting._apply_to_component` is the pattern, and a test asserts no
  generated snippet does it.

`src/unreal_mcp/bridge.py` was originally written against Unreal's built-in
Python Remote Execution protocol (UDP multicast discovery + TCP). That
protocol's multicast discovery did not work in the environment this was
built in — `bRemoteExecution=True` was set and the plugin loaded, but the
editor never broadcast its presence, and no client-side fix changed that.
It now talks to the Remote Control API (HTTP) instead, per the setup steps
below. The tool modules in `src/unreal_mcp/tools/` were unaffected by the
switch, since they only call `bridge.run_python()`, never the transport
directly.

One correctness note from live testing: `set_property` sets a raw Python
attribute via `setattr`, which only works for genuine UPROPERTY fields
exposed under their Python (snake_case) name — e.g. `custom_time_dilation`.
Some engine-side names look settable but are not (`hidden` is a read-only
accessor backed by `bHidden`; use a dedicated method like
`set_actor_hidden_in_game` for those). This is normal Unreal Python API
behavior, not a bug in the tool, but it means callers need the real
property name, not the Blueprint-editor display name.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requires Unreal Engine 5 installed locally, with the editor running (the
server connects to its Remote Control HTTP endpoint on 127.0.0.1:30010). No
engine path needs configuring; the server talks to the editor over that one
local endpoint.

### Enable Remote Control API in your Unreal project

Every step below is done once per project, inside the Unreal Editor itself.
This repo doesn't ship an Unreal project (point the server at any project
of your own). For whichever project you use:

1. **Enable the plugin.** Edit → Plugins → search "Remote Control API" →
   enable → restart the editor when prompted. (Also confirm "Python Editor
   Script Plugin" is enabled the same way — needed to run Python at all.)

2. **Turn on Python execution and set the allowlist.** Edit → Project
   Settings → search "Remote Control" → Security section:
   - Check **Enable Remote Python Execution**.
     Greyed out until you check **Restrict Server Access** above it first.
   - Leave **Allow Any Remote Function Call** unchecked — that would let any
     HTTP client call *any* UFUNCTION in the project. Instead add one entry
     under **Custom Allowed Remote Function Calls** with Class Path:
     ```
     /Script/PythonScriptPlugin.PythonScriptLibrary
     ```
     This is the one class we need (it's what runs Python commands), nothing
     wider.
   - If **Enforce Passphrase for Remote Clients** is checked, add a
     passphrase under **Remote Control Passphrase** and set it in your
     environment (see below). Calls without the correct passphrase get
     rejected with HTTP 401.

   These settings live in `Config/DefaultRemoteControl.ini` under
   `[/Script/RemoteControlCommon.RemoteControlSettings]` if you'd rather
   edit the file directly and restart, instead of using the UI (the UI
   path takes effect immediately, no restart needed).

3. **Confirm the server is listening.** The Remote Control web server starts
   automatically once the plugin is enabled, on `127.0.0.1:30010` by
   default (configurable as `RemoteControlHttpServerPort`). Nothing is
   exposed outside the machine.

### Running it

1. Open your project in Unreal Editor (with the settings above applied) and
   leave it running.
2. If you set a passphrase, export it: `export UNREAL_RC_PASSPHRASE="..."`
3. In another terminal: `python -m unreal_mcp.server`
4. Point your MCP client at that command (stdio transport) to connect.

The server connects to the running editor's HTTP endpoint on first tool
call, not at startup, so it's fine to start the server before or after
opening the editor.

## Testing without Unreal open

```bash
source .venv/bin/activate
pip install pytest
python -m pytest tests/ -q
```

This covers the security bouncer and snippet-generation correctness. It
cannot cover whether Unreal actually accepts the generated calls, that needs
the editor running.

## Security

Every tool call passes through `src/unreal_mcp/security.py` before anything
is sent to Unreal: a path allowlist, a static AST check on raw Python (bans
`subprocess`/`socket`/`shutil`/etc., destructive calls like `delete_asset`,
and dangerous string patterns like `.git` or `..`), and a risk tier per tool
(destructive actions need `confirm=True`). Full rationale in
[PLAN.md](PLAN.md). The server only ever binds to localhost via stdio, never
expose it over a network.
