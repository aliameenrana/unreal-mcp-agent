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

## Phase 2 preview (not building yet, just context)

- Planning step: turn a loose prompt into a scoped task list (GDD-lite), not a blind "make a game."
- Asset generation pipeline, separate from the MCP: image/3D/audio generation, headless Blender for mesh cleanup, feeding files into Unreal via the MCP's import tools.
- Play-test-and-fix loop: run via PIE, capture logs/screenshot, feed failures back for repair.
- Optional: local Kenney.nl asset mirror (CC0, free) as a fallback/supplement to generated assets.

---

## Next step

Start implementing phase 1: MCP server scaffold + Python Remote Execution bridge + the MVP tool list above.
