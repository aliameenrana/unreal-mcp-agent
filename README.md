# unreal-mcp

A general-purpose MCP server that bridges an MCP client (Claude Code, Claude
Desktop, Cursor) to a running Unreal Engine 5 editor, using Unreal's
Remote Control API (HTTP) to execute Python inside the editor.

See [PLAN.md](PLAN.md) for the full architecture, scope, and security design.

## Status

Fully working end to end against a live Unreal Editor 5.8 session:
`list_actors`, `spawn_actor`, and `delete_actor` all confirmed via
`scripts/smoke_test.py` against a real, non-trivial level (138 actors). All
24 unit tests pass (security bouncer, snippet syntax).

`src/unreal_mcp/bridge.py` was originally written against Unreal's built-in
Python Remote Execution protocol (UDP multicast discovery + TCP). That
protocol's multicast discovery did not work in the environment this was
built in — `bRemoteExecution=True` was set and the plugin loaded, but the
editor never broadcast its presence, and no client-side fix changed that.
It now talks to the Remote Control API (HTTP) instead, per the setup steps
below. The tool modules in `src/unreal_mcp/tools/` were unaffected by the
switch, since they only call `bridge.run_python()`, never the transport
directly.

`compile_blueprint` (in `tools/blueprints.py`) has not been exercised
against a live editor yet and remains the least-verified call.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requires Unreal Engine 5 installed locally. The server finds it automatically
under `/Users/Shared/Epic Games/UE_*` (macOS) or `C:/Program Files/Epic
Games` (Windows). If yours is somewhere else, set:

```bash
export UNREAL_ENGINE_ROOT="/path/to/UE_5.8"
```

### Enable Remote Control API in your Unreal project

Every step below is done once per project, inside the Unreal Editor itself.
Already done for `UnrealProject/`, the throwaway test project in this repo.
For any other project:

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
