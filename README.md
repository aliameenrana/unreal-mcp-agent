# unreal-mcp

A general-purpose MCP server that bridges an MCP client (Claude Code, Claude
Desktop, Cursor) to a running Unreal Engine 5 editor, using Unreal's built-in
Python Remote Execution protocol.

See [PLAN.md](PLAN.md) for the full architecture, scope, and security design.

## Status

MVP tool set is implemented and unit-tested (bouncer logic, and every tool's
generated snippet checked for syntactically valid Python). **Not yet tested
against a live editor** — this was built without GUI access to Unreal in
the environment that wrote it. The API calls (`EditorActorSubsystem`,
`MaterialEditingLibrary`, `BlueprintEditorLibrary.compile_blueprint`) are
taken from Epic's documented Python API, but exact behavior needs confirming
on first real run. If something throws, the error message from Unreal will
say what; expect to adjust a snippet or two in `src/unreal_mcp/tools/`.

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

### Enable Python Remote Execution in your project

Already done for `UnrealProject/`, the throwaway test project in this repo
(`Config/DefaultEngine.ini` sets `bRemoteExecution=True`). For any other
project, add the same block to its `Config/DefaultEngine.ini`:

```ini
[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
bRemoteExecution=True
bDeveloperMode=True
```

### Running it

1. Open `UnrealProject/MCPTestProject.uproject` in Unreal Editor (or your own
   project with remote execution enabled) and leave it running.
2. In another terminal: `python -m unreal_mcp.server`
3. Point your MCP client at that command (stdio transport) to connect.

The server discovers the open editor over UDP multicast on first tool call,
not at startup, so it's fine to start the server before or after opening the
editor.

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
