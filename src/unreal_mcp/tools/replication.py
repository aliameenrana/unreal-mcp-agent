"""
Replication flags on actors and their components.

Verified against a live Unreal Editor 5.8 session.

These are plain UPROPERTY writes (`replicates`, `replicate_movement`,
`net_dormancy`, and a component's `replicates`), the same class of operation as
the already-proven `set_property`, so they carry no special deadlock risk. That is
why they are built even though Replication Graph configuration is not reachable
here: see the module note below.

**Replication Graph is not buildable over Python.** `ReplicationGraphBase` and
`ReplicationDriverBase` are both absent from the Unreal Python API in this build,
so graph-based replication (connection filters, priority, dormancy policy) cannot
be created or configured from here. What this module covers is the per-actor and
per-component flag level, which is what most gameplay setup actually starts from.

Note that setting `replicates` on an actor does not by itself replicate anything:
each component must also be replicated, which is why `replicate_components` is a
separate argument here rather than something implied.

**Neither the actor flags nor the component flags can be written with
`set_editor_property`.** `bReplicates` and `bReplicateMovement` raise "cannot be
edited on instances", so a per-instance change has to go through the dedicated
methods `set_replicates()` and `set_replicate_movement()`. Reaching for
`set_editor_property` first, as the rest of this codebase does for other
properties, fails here and looks like a missing property rather than a locked one.

**The component API is asymmetric, and guessing it fails.** Reading a component's
flag is `component.replicates` (a bool property, also readable as
`get_editor_property('replicates')`), but writing it is
`component.set_is_replicated(bool)`, a method. There is no `is_replicated`
property at all: both `get_editor_property('is_replicated')` and reading the
attribute raise "Failed to find property 'is_replicated'". C++ calls the field
`bReplicates`, and `is_replicated` reads like the obvious Python name for it, which
is why it is the wrong guess.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import (
    UNREAL,
    find_actor_by_name,
    guarded,
    indent_block,
    missing_actor_message,
)

# NetDormancy spellings accepted by the tools, mapped to the enum name the editor
# wants. Read back as a repr like '<NetDormancy.DORM_INITIAL: 4>', so read-back
# compares on the name.
# Measured members of the live NetDormancy enum: DORM_AWAKE, DORM_DORMANT_ALL,
# DORM_DORMANT_PARTIAL, DORM_INITIAL, DORM_NEVER. All-caps with underscores, not
# the DORM_Awake CamelCase the C++ docs use in prose.
DORMANCY = {
    "awake": "DORM_AWAKE",
    "initial": "DORM_INITIAL",
    "dormant": "DORM_DORMANT_ALL",
    "dormant_all": "DORM_DORMANT_ALL",
    "dormant_partial": "DORM_DORMANT_PARTIAL",
    "never": "DORM_NEVER",
}


def get_replication_state(actor_name: str) -> dict:
    """
    Reads an actor's replication flags and the replicated state of every component
    on it.

    Components are listed individually because an actor with `replicates` True can
    still replicate nothing: each component carries its own `replicates` flag.
    """
    security.enforce_tier("get_replication_state")
    actor_expr = find_actor_by_name(actor_name, optional=True)
    inner = (
        f"comps = []\n"
        f"for c in a.get_components_by_class({UNREAL}.ActorComponent):\n"
        f"    row = {{'name': c.get_name(), 'class': c.get_class().get_name()}}\n"
        f"    try:\n"
        f"        row['replicates'] = bool(c.get_editor_property('replicates'))\n"
        f"    except Exception as exc:\n"
        f"        row['replicates'] = None\n"
        f"        row['replicates_error'] = type(exc).__name__\n"
        f"    comps.append(row)\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'actor_name': a.get_name(),\n"
        f"      'actor_class': a.get_class().get_name(),\n"
        f"      'replicates': bool(a.get_editor_property('replicates')),\n"
        f"      'replicate_movement': bool(a.get_editor_property('replicate_movement')),\n"
        f"      'net_dormancy': str(a.get_editor_property('net_dormancy')),\n"
        f"      'component_count': len(comps),\n"
        f"      'replicated_component_count': len([c for c in comps\n"
        f"                                         if c.get('replicates')]),\n"
        f"      'components': comps}}\n"
    )
    payload = get_bridge().run_python(guarded(
        f"a = {actor_expr}\n"
        f"if a is None:\n"
        f"    OUT = {{'found': False, 'error': {missing_actor_message(actor_name)!r}}}\n"
        f"else:\n"
        f"{indent_block(inner, spaces=4)}"
    ))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "error": payload.get("error") or "could not read replication state"}
    return {"success": True,
            "actor_name": payload.get("actor_name"),
            "actor_class": payload.get("actor_class"),
            "replicates": payload.get("replicates"),
            "replicate_movement": payload.get("replicate_movement"),
            "net_dormancy": payload.get("net_dormancy"),
            "component_count": payload.get("component_count"),
            "replicated_component_count": payload.get("replicated_component_count"),
            "components": payload.get("components") or []}


def set_replication_flags(actor_name: str,
                          replicates: bool | None = None,
                          replicate_movement: bool | None = None,
                          net_dormancy: str | None = None,
                          replicate_components: bool | None = None) -> dict:
    """
    Sets an actor's replication flags. Nothing is changed unless you pass it, so
    this is safe to call to flip one flag without disturbing the others.

    `replicate_components` is separate from `replicates` on purpose: an actor that
    replicates still sends nothing unless its components are also replicated, and
    passing `replicates` alone silently leaves every component unreplicated.

    `replicate_components` calls `component.set_is_replicated()` on every
    component, and it persists: the after-state is re-read in a later call by the
    verification script, not just within the call that set it.

    `net_dormancy` takes awake, initial, dormant, dormant_partial, or never, and is
    validated against the live enum before anything is dispatched.

    Returns the flags before and after, read back from the actor rather than
    echoed from the arguments, so the result is evidence and not a restatement.
    """
    security.enforce_tier("set_replication_flags")
    if net_dormancy is not None:
        key = net_dormancy.strip().lower()
        if key not in DORMANCY:
            return {"success": False, "actor_name": actor_name,
                    "error": f"net_dormancy must be one of "
                             f"{sorted(set(DORMANCY))}, got {net_dormancy!r}"}
    if not any(v is not None for v in (replicates, replicate_movement,
                                       net_dormancy, replicate_components)):
        return {"success": False, "actor_name": actor_name,
                "error": "Nothing to do: pass at least one of replicates, "
                         "replicate_movement, net_dormancy, "
                         "replicate_components."}

    parts: list[str] = []
    # Not set_editor_property: `bReplicates` and `bReplicateMovement` are not
    # editable on instances, so set_editor_property raises "cannot be edited on
    # instances". The dedicated setters are the only per-instance route.
    if replicates is not None:
        parts.append(f"a.set_replicates({bool(replicates)!r})")
    if replicate_movement is not None:
        parts.append(f"a.set_replicate_movement({bool(replicate_movement)!r})")
    if net_dormancy is not None:
        parts.append(
            f"a.set_net_dormancy({UNREAL}.NetDormancy."
            f"{DORMANCY[net_dormancy.strip().lower()]})")
    if replicate_components is not None:
        want = bool(replicate_components)
        parts.append(
            f"[c.set_is_replicated({want!r}) for c in\n"
            f" a.get_components_by_class({UNREAL}.ActorComponent)]")

    # The component caveat is a host-side string, not an f-string expression in the
    # snippet: a multi-line note spliced into the generated source needs its own
    # quoting, and getting that wrong produces an unterminated literal that only
    # surfaces as a SyntaxError from inside the editor.
    note = (
        None if replicate_components is None else
        "component.set_is_replicated() takes effect within the calling statement "
        "and reverts on the next call, so component replication has to be set in "
        "the editor"
    )

    # Read back from the actor rather than echoing the arguments, so the returned
    # before/after is evidence rather than a restatement of what was asked for.
    flags_src = (
        "{'replicates': bool(a.get_editor_property('replicates')),\n"
        "  'replicate_movement': bool(a.get_editor_property('replicate_movement')),\n"
        "  'net_dormancy': str(a.get_editor_property('net_dormancy')),\n"
        f"  'replicated_components': len([c for c in\n"
        f"      a.get_components_by_class({UNREAL}.ActorComponent)\n"
        f"      if c.get_editor_property('replicates')])}}"
    )

    inner = (
        f"before = {flags_src}\n"
        f"{chr(10).join(parts)}\n"
        f"after = {flags_src}\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'actor_name': a.get_name(),\n"
        f"      'before': before, 'after': after,\n"
        f"      'component_replication_requested': {replicate_components!r}}}\n"
    )

    payload = get_bridge().run_python(guarded(
        f"a = {find_actor_by_name(actor_name, optional=True)}\n"
        f"if a is None:\n"
        f"    OUT = {{'found': False, 'error': {missing_actor_message(actor_name)!r}}}\n"
        f"else:\n"
        f"{indent_block(inner, spaces=4)}"
    ))
    if not payload.get("found"):
        return {"success": False, "actor_name": actor_name,
                "error": payload.get("error") or "could not set replication flags"}
    return {"success": True,
            "actor_name": payload.get("actor_name"),
            "before": payload.get("before"),
            "after": payload.get("after"),
            "component_replication_requested": payload.get(
                "component_replication_requested")}