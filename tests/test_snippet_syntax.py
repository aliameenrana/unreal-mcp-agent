"""
Can't run a live Unreal Editor in this environment, so this is the next
best thing: intercept every snippet right before it would be sent over the
wire, and confirm it's at least syntactically valid Python. Catches f-string
mistakes in the tool modules without needing the editor open.

Separate from test_security.py, which tests the bouncer itself.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sys
import types
import warnings
from unittest import mock

import pytest

from unreal_mcp import bridge, remote_snippets, security
from unreal_mcp.remote_snippets import (
    actor_component,
    find_actor_by_name,
    guarded,
)
from unreal_mcp.tools import (
    blueprints,
    components,
    lighting,
    materials,
    meshes,
    presets,
    scene,
)


class _FakeBridge:
    """Captures the expression instead of sending it anywhere."""

    def __init__(self):
        self.last_expr: str | None = None
        self.last_timeout = None
        self.result = "fake-result"

    def run_python(self, expression: str, timeout=None):
        # Mirrors the real signature: tools that wait on a shader compile pass
        # timeout, and the fake has to accept it rather than TypeError.
        self.last_timeout = timeout
        self.last_expr = expression
        _assert_no_lambda_subscript(expression)
        ast.parse(expression)  # raises SyntaxError if the f-string building is broken
        return self.result


def _assert_no_lambda_subscript(expr: str) -> None:
    """
    Subscripting a lambda's result, as in `(lambda c: ...)[-1]`, compiles, but
    CPython emits "'function' object is not subscriptable; perhaps you missed
    a comma?" while compiling it. Unreal surfaces that warning in LogOutput and
    comes back with an empty ReturnValue, which bridge.run_python reports as a
    failure. Index the tuple inside the lambda body instead, the way
    lighting._apply_to_component does.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compile(expr, "<snippet>", "eval")
    offending = [str(w.message) for w in caught if "not subscriptable" in str(w.message)]
    assert not offending, f"subscripting a lambda result: {offending}"


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    fb = _FakeBridge()
    monkeypatch.setattr(bridge, "get_bridge", lambda: fb)
    # Each tools module imported get_bridge directly, so patch there too.
    monkeypatch.setattr(scene, "get_bridge", lambda: fb)
    monkeypatch.setattr(materials, "get_bridge", lambda: fb)
    monkeypatch.setattr(blueprints, "get_bridge", lambda: fb)
    monkeypatch.setattr(lighting, "get_bridge", lambda: fb)
    monkeypatch.setattr(components, "get_bridge", lambda: fb)
    monkeypatch.setattr(meshes, "get_bridge", lambda: fb)
    # presets calls the primitives in-process rather than the bridge itself, so
    # it has no get_bridge of its own to patch.
    return fb


def test_spawn_actor_snippet_is_valid(fake_bridge):
    scene.spawn_actor("/Script/Engine.StaticMeshActor", (1, 2, 3), (0, 90, 0))


def test_rotation_is_built_with_keywords(fake_bridge):
    """
    unreal.Rotator's positional constructor is (roll, pitch, yaw), not
    (pitch, yaw, roll), so a positional build tilts the actor instead of
    turning it and raises nothing. Confirmed live: asking for yaw=45 produced
    an actor reading yaw 0.
    """
    scene.spawn_actor("/Script/Engine.StaticMeshActor", (0, 0, 0), (10, 20, 30))
    expr = fake_bridge.last_expr
    assert "Rotator(pitch=10.0, yaw=20.0, roll=30.0)" in expr
    assert "Rotator(10, 20, 30)" not in expr
    assert "Rotator(10.0, 20.0, 30.0)" not in expr


def test_set_actor_transform_uses_keyword_rotator(fake_bridge):
    scene.set_actor_transform("Cube_0", rotation=(0, 45, 0))
    assert "Rotator(pitch=0.0, yaw=45.0, roll=0.0)" in fake_bridge.last_expr


def test_list_actors_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "run_python", lambda e: (ast.parse(e), [])[-1])
    scene.list_actors()


def test_set_actor_transform_snippet_is_valid(fake_bridge):
    scene.set_actor_transform("Cube_0", location=(1, 1, 1), rotation=(0, 0, 0))


def test_set_property_snippet_is_valid(fake_bridge):
    scene.set_property("Cube_0", "bHidden", True)


def test_delete_actor_snippet_is_valid(fake_bridge):
    scene.delete_actor("Cube_0", confirm=True)


def test_create_material_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result",
                        {"found": True, "error": None,
                         "material_path": "/Game/Materials/M_Test"})
    materials.create_material("/Game/Materials", "M_Test")


def test_create_material_instance_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result",
                        {"found": True, "error": None,
                         "instance_path": "/Game/Materials/MI_Test",
                         "parent": "/Game/Materials/M_Test.M_Test"})
    materials.create_material_instance("/Game/Materials", "MI_Test",
                                       "/Game/Materials/M_Test.M_Test")


def test_set_material_scalar_parameter_snippet_is_valid(fake_bridge):
    materials.set_material_scalar_parameter("/Game/Materials/MI_Test", "Roughness", 0.5)


def test_set_material_vector_parameter_snippet_is_valid(fake_bridge):
    materials.set_material_vector_parameter("/Game/Materials/MI_Test", "BaseColor", 1.0, 0.0, 0.0)


def test_compile_blueprint_snippet_is_valid(fake_bridge):
    blueprints.compile_blueprint("/Game/Blueprints/BP_Test")


def test_light_scene_preset_snippet_is_valid(monkeypatch, fake_bridge):
    # The preset's read-back getters unpack a dict, not a string.
    monkeypatch.setattr(fake_bridge, "result", {"intensity": 1.0})
    presets.light_scene_preset("golden_hour")


def test_light_scene_preset_rejects_unknown_mood_before_dispatch(fake_bridge):
    result = presets.light_scene_preset("not_a_mood")
    assert result["success"] is False
    assert "not_a_mood" in result["error"]
    assert fake_bridge.last_expr is None, "unknown mood must not reach the bridge"


def test_set_dressing_pass_snippet_is_valid(fake_bridge):
    presets.set_dressing_pass("/Script/Engine.StaticMeshActor", 2, (0, 0, 0), 100.0)


def test_apply_material_variant_set_snippet_is_valid(monkeypatch, fake_bridge):
    # This tool creates its instances, so it needs the same dict payload the new
    # create_material_instance returns.
    monkeypatch.setattr(fake_bridge, "result",
                        {"found": True, "error": None,
                         "instance_path": "/Game/Materials/MI_Red",
                         "parent": "/Game/Materials/M_Base.M_Base"})
    presets.apply_material_variant_set(
        "/Game/Materials",
        "/Game/Materials/M_Base",
        [{"name": "MI_Red", "scalar": {"Roughness": 0.2}, "vector": {"Tint": (1, 0, 0, 1)}}],
    )


# --- lighting primitives ---


def test_set_light_properties_snippet_is_valid(fake_bridge):
    lighting.set_light_properties(intensity=4.0, color=(1.0, 0.5, 0.2), temperature=3200.0)


def test_set_light_properties_rejects_empty_request(fake_bridge):
    result = lighting.set_light_properties("SomeLight")
    assert result["success"] is False
    assert fake_bridge.last_expr is None


def test_set_light_properties_rejects_temperature_on_skylight(fake_bridge):
    result = lighting.set_light_properties(temperature=3000.0, default_class="SkyLight")
    assert result["success"] is False
    assert "no temperature control" in result["error"]
    assert fake_bridge.last_expr is None


def test_get_light_properties_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"intensity": 1.0, "color": [1, 1, 1]})
    assert lighting.get_light_properties()["success"] is True


def test_set_exponential_fog_params_snippet_is_valid(fake_bridge):
    lighting.set_exponential_fog_params(fog_density=0.08, volumetric_fog=True)


def test_get_exponential_fog_params_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"fog_density": 0.0436})
    assert lighting.get_exponential_fog_params()["success"] is True


def test_set_sky_atmosphere_params_snippet_is_valid(fake_bridge):
    lighting.set_sky_atmosphere_params(
        atmosphere_height=60.0, ground_albedo=(0.2, 0.2, 0.2), rayleigh_scattering=(5e-6, 13e-6, 22e-6)
    )


def test_get_sky_atmosphere_params_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"atmosphere_height": 60.0})
    assert lighting.get_sky_atmosphere_params()["success"] is True


def test_light_color_uses_keyword_args_not_positional(fake_bridge):
    # Guards the BGRA trap: unreal.Color is (b, g, r, a) positionally, so a
    # positional build would silently swap red and blue.
    lighting.set_light_properties(color=(1.0, 0.0, 0.0))
    assert "Color(r=255, g=0, b=0, a=255)" in fake_bridge.last_expr
    assert "Color(255, 0, 0, 255)" not in fake_bridge.last_expr


# --- get_property / the snippet helpers it depends on ---


def _seq_last_element(expr: str) -> str:
    """Source of whatever seq() returns: its last tuple element, else the whole expr."""
    inner = expr[len("__import__('json').dumps(") : -1]
    node = ast.parse(inner, mode="eval").body
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Tuple):
        return ast.get_source_segment(inner, node.value.elts[-1]) or ""
    return inner


def test_get_property_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": True, "value": 1.0, "type": "float"})
    assert scene.get_property("Cube_0", "custom_time_dilation")["value"] == 1.0


def test_get_property_failure_reports_no_value(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge,
        "result",
        {"found": False, "value": None, "type": None, "error": "no such property"},
    )
    result = scene.get_property("Cube_0", "nope")
    assert result["success"] is False
    assert result["value"] is None, "a failed read must not report a value"


def test_get_property_resolves_the_actor_optionally(monkeypatch, fake_bridge):
    # A missing actor must not reach next() unguarded: StopIteration's str() is
    # empty, so the caller would see a blank error.
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "x", "value": None})
    scene.get_property("Nope", "custom_time_dilation")
    assert find_actor_by_name("Nope", optional=True) in fake_bridge.last_expr


def _run_guarded(body: str) -> dict:
    """Evaluate a guarded() body locally, with a stub unreal module in place."""
    stub = types.ModuleType("unreal")
    with mock.patch.dict(sys.modules, {"unreal": stub}):
        return json.loads(eval(guarded(body)))  # noqa: S307 - guarded() builds this


def test_guarded_error_message_is_never_blank():
    # StopIteration stringifies to '', which is how a missing actor surfaces
    # from next(); guarded() prefixes the type so error is never empty.
    assert _run_guarded("raise StopIteration")["error"] == "StopIteration"


def test_guarded_carries_the_exception_message():
    assert _run_guarded("raise ValueError('x')")["error"] == "ValueError: x"


def test_actor_component_parenthesizes_the_actor_expression():
    # `next(a for a in xs if cond).method()` parses but binds method() into the
    # comprehension's condition, so it silently reads the wrong thing.
    assert actor_component("next(g)", "StaticMeshComponent").startswith("(next(g))")


@pytest.mark.parametrize(
    "call",
    [
        lambda: lighting.get_light_properties(),
        lambda: lighting.get_exponential_fog_params(),
        lambda: lighting.get_sky_atmosphere_params(),
        lambda: scene.get_mesh_material_slot("Cube_0"),
    ],
)
def test_read_back_tools_end_on_the_payload(monkeypatch, fake_bridge, call):
    """
    seq() returns its last element, so a getter that puts the actor name after
    the payload silently returns the name, and the tool then fails unpacking a
    string where it expected a dict.
    """
    monkeypatch.setattr(fake_bridge, "result", {"ok": True})
    call()
    assert "lambda" in _seq_last_element(fake_bridge.last_expr)


def test_set_mesh_material_slot_ends_on_the_assignment(fake_bridge):
    scene.set_mesh_material_slot(
        "Cube_0", "/Game/Materials/M_Base", scalar_params={"Roughness": 0.3}
    )
    assert _seq_last_element(fake_bridge.last_expr).startswith("(lambda a:")


# --- components ---


def test_list_components_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": True, "components": [], "root_component": None})
    assert components.list_components("Cube_0")["count"] == 0


def test_list_components_reports_a_missing_actor(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "gone", "components": []})
    result = components.list_components("Nope")
    assert result["success"] is False
    assert result["components"] == []


# --- meshes ---


def test_get_mesh_bounds_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge,
        "result",
        {"found": True, "mesh": "Cube", "lod_count": 1, "min": [-50.0] * 3, "max": [50.0] * 3},
    )
    result = meshes.get_mesh_bounds("/Engine/BasicShapes/Cube.Cube")
    assert result["size"] == [100.0] * 3
    assert result["extent"] == [50.0] * 3


def test_get_mesh_bounds_reports_a_missing_asset(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": False, "error": "nope"})
    assert meshes.get_mesh_bounds("/Game/Nope.Nope")["success"] is False


def test_set_mesh_lods_rejects_percentages(fake_bridge):
    # percent_triangles is a 0..1 fraction, not a percentage.
    result = meshes.set_mesh_lods("/Game/M.SM", [50.0], confirm=True)
    assert result["success"] is False
    assert fake_bridge.last_expr is None, "bad input must not reach the bridge"


def test_set_mesh_lods_requires_confirm(fake_bridge):
    try:
        meshes.set_mesh_lods("/Game/M.SM", [0.5])
        assert False, "expected SecurityViolation"
    except Exception as exc:
        assert type(exc).__name__ == "SecurityViolation"


# --- actor scene graph ---
#
# These lean on behaviour the fake bridge can actually observe (which snippet
# was built, what the tool returns, whether the bouncer refuses) rather than on
# grepping the implementation, except where a specific Unreal call is the whole
# point of the test.


def _sent_snippet(fake_bridge):
    """The last expression sent to Unreal, ready for ast.parse-style inspection."""
    return fake_bridge.last_expr


def test_attach_actor_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "attached": True, "parent_before": None, "parent_after": "P", "socket": "None"},
    )
    r = scene.attach_actor("Child", "Parent")
    assert r["success"] is True


def test_attach_actor_reports_a_missing_child_as_an_error(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": False, "error": "no actor named ZZ", "attached": False},
    )
    r = scene.attach_actor("ZZ", "Parent")
    assert r["success"] is False
    assert "ZZ" in r["error"]


def test_attach_actor_uses_the_socket_name_reader_that_exists(fake_bridge):
    """
    Actor has no get_attach_component in 5.8. Reading the socket back through
    one produced an AttributeError that surfaced as an empty-log
    RemoteCommandFailedError rather than a readable failure.
    """
    monkeypatch_result = {"found": True, "attached": True, "parent_after": "P", "socket": "None"}
    fake_bridge.result = monkeypatch_result
    scene.attach_actor("Child", "Parent")
    snippet = _sent_snippet(fake_bridge)
    assert "get_attach_parent_socket_name" in snippet
    assert "get_attach_component" not in snippet


def test_attach_actor_builds_the_rules_from_the_attachment_enum(fake_bridge):
    fake_bridge.result = {"found": True, "attached": True, "parent_after": "P", "socket": "None"}
    scene.attach_actor("Child", "Parent", location_rule="SNAP_TO_TARGET")
    assert "unreal.AttachmentRule.SNAP_TO_TARGET" in _sent_snippet(fake_bridge)


def test_detach_actor_uses_the_detachment_enum_not_the_attachment_one(fake_bridge):
    """
    The two enums have different names on purpose, and only DetachmentRule
    exists on the detach side; there is no SNAP_TO_TARGET there at all.
    """
    fake_bridge.result = {"found": True, "had_parent": True, "parent_before": "P", "parent_after": None}
    scene.detach_actor("Child")
    snippet = _sent_snippet(fake_bridge)
    assert "unreal.DetachmentRule.KEEP_WORLD" in snippet
    assert "unreal.AttachmentRule" not in snippet


def test_detach_actor_succeeds_only_once_the_parent_is_gone(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "had_parent": True, "parent_before": "P", "parent_after": "P"},
    )
    assert scene.detach_actor("Child")["success"] is False


def test_set_actor_folder_round_trips_an_empty_path_as_empty_string(monkeypatch, fake_bridge):
    """
    An actor at the outliner root has a null folder Name, and str() of a null
    Name is the literal text 'None'. Reporting that verbatim would make the root
    folder unreachable by comparison.
    """
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "folder_before": "", "folder_after": ""},
    )
    assert scene.set_actor_folder("A", "")["folder_after"] == ""


def test_set_actor_folder_normalizes_a_literal_none_string(monkeypatch, fake_bridge):
    """Guards the normalization itself: the editor reports 'None', not ''."""
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "folder_before": "", "folder_after": ""},
    )
    scene.set_actor_folder("A", "")
    snippet = _sent_snippet(fake_bridge)
    assert "== 'None'" in snippet


def test_tag_actor_assigns_a_sorted_list_not_a_python_set(fake_bridge):
    """
    Actor.tags reads back as an Array at runtime even though the stub types it
    as Set[str], so handing it a set is the wrong shape to write back.
    """
    fake_bridge.result = {"found": True, "tags_before": [], "tags_after": ["A", "B"]}
    scene.tag_actor("A", ["B", "A"])
    assert "a.tags = sorted(current)" in _sent_snippet(fake_bridge)


def test_tag_actor_dedupes_the_add_and_remove_lists(fake_bridge):
    fake_bridge.result = {"found": True, "tags_before": [], "tags_after": ["A"]}
    r = scene.tag_actor("A", ["X", "X", "X"], remove=["Y", "Y"])
    assert r["added"] == ["X"]
    assert r["removed"] == ["Y"]


def test_find_actors_by_tag_with_no_tag_lists_the_vocabulary(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mode": "all_tags", "tags": [("Prop", 2)], "actors": []},
    )
    r = scene.find_actors_by_tag("")
    assert r["mode"] == "all_tags"
    assert r["count"] is None


def test_find_actors_by_tag_counts_hits(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mode": "by_tag", "tags": [],
         "actors": [{"name": "A"}, {"name": "B"}]},
    )
    assert scene.find_actors_by_tag("Prop")["count"] == 2


def test_find_actors_by_tag_substring_mode_is_case_insensitive(fake_bridge):
    fake_bridge.result = {"found": True, "mode": "by_tag", "tags": [], "actors": []}
    scene.find_actors_by_tag("prop", exact=False)
    assert "low = 'prop'.lower()" in _sent_snippet(fake_bridge)


def test_select_actors_replaces_nothing_by_default(fake_bridge):
    """
    Both branches live in the snippet; `replace` is substituted as a literal and
    decides which one runs, so that is what to assert on.
    """
    fake_bridge.result = {"found": True, "missing": [], "selected_now": ["A"], "count": 1}
    scene.select_actors(["A"])
    snippet = _sent_snippet(fake_bridge)
    assert "if False:" in snippet      # the replacing branch is dead
    assert "if True:" not in snippet


def test_select_actors_default_never_uses_select_all_or_invert(fake_bridge):
    """select_all and invert_selection act on the user's existing selection."""
    fake_bridge.result = {"found": True, "missing": [], "selected_now": ["A"], "count": 1}
    scene.select_actors(["A"], replace=True, confirm=True)
    snippet = _sent_snippet(fake_bridge)
    assert ".select_all(" not in snippet
    assert "invert_selection" not in snippet


def test_select_actors_needs_confirm_before_clobbering_the_selection(fake_bridge):
    """replace=True discards whatever the user had selected, so it is gated."""
    with pytest.raises(security.SecurityViolation):
        scene.select_actors(["A"], replace=True)


def test_select_actors_allows_additive_selection_without_confirm(fake_bridge):
    fake_bridge.result = {"found": True, "missing": [], "selected_now": ["A"], "count": 1}
    assert scene.select_actors(["A"])["success"] is True


def test_select_actors_replace_takes_the_replacing_branch(fake_bridge):
    fake_bridge.result = {"found": True, "missing": [], "selected_now": ["A"], "count": 1}
    scene.select_actors(["A"], replace=True, confirm=True)
    assert "if True:" in _sent_snippet(fake_bridge)


def test_select_actors_dedupes_and_reports_missing_names(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "missing": ["ZZ"], "selected_now": ["A"], "count": 1},
    )
    r = scene.select_actors(["A", "A", "ZZ"])
    assert r["requested"] == ["A", "ZZ"]
    assert r["missing"] == ["ZZ"]
    assert r["success"] is False


def test_get_selected_actors_snippet_is_valid(monkeypatch, fake_bridge):
    monkeypatch.setattr(fake_bridge, "result", {"found": True, "count": 0, "actors": []})
    assert scene.get_selected_actors()["count"] == 0


def test_every_scene_graph_tool_has_a_risk_tier():
    """A tool missing from the registry defaults to blocked, not to allowed."""
    for name in (
        "attach_actor", "detach_actor", "set_actor_folder", "tag_actor",
        "find_actors_by_tag", "select_actors", "get_selected_actors",
    ):
        assert name in security.TOOL_RISK_TIERS, name


# --- snippet self-validation -------------------------------------------------
#
# guarded() embeds the body as a string literal inside exec(), so parsing the
# outer expression never compiles it. A malformed body used to reach the editor
# and fail there at compile time, arriving as RemoteCommandFailedError with an
# empty log. These lock in the local check that replaced that.


def test_guarded_rejects_a_body_that_does_not_compile():
    """A dedented line inside an if-block is the exact failure that cost hours."""
    body = "if True:\n    x = 1\ny = 2\n    z = 3"
    with pytest.raises(SyntaxError) as excinfo:
        remote_snippets.guarded(body)
    assert "does not compile" in str(excinfo.value)


def test_guarded_error_points_at_indent_block():
    """The message should name the usual cause, not just the symptom."""
    body = "if True:\n    x = 1\ny = 2\n    z = 3"
    with pytest.raises(SyntaxError) as excinfo:
        remote_snippets.guarded(body)
    assert "indent_block" in str(excinfo.value)


def test_guarded_accepts_a_well_formed_multiline_body():
    body = "a = None\nif a is not None:\n    b = 1\nelse:\n    b = 2\nOUT = {'b': b}"
    assert remote_snippets.guarded(body)


def test_indent_block_indents_every_nonblank_line():
    out = remote_snippets.indent_block("a = 1\nb = 2\n\nc = 3", 4)
    assert out == "    a = 1\n    b = 2\n\n    c = 3\n"


def test_indent_block_with_zero_spaces_is_identity():
    assert remote_snippets.indent_block("a = 1\nb = 2", 0) == "a = 1\nb = 2\n"


def test_indent_block_always_ends_in_a_newline():
    """
    Every call site got this wrong individually: splicing a block that had no
    trailing newline concatenated the next line onto the last, giving an
    IndentationError inside the generated snippet.
    """
    assert remote_snippets.indent_block("a = 1", 4).endswith("\n")
    assert remote_snippets.indent_block("a = 1\nb = 2", 8).endswith("\n")


def test_indent_block_of_a_spliced_block_keeps_the_next_line_separate():
    """The regression this exists for, stated as a test."""
    block = remote_snippets.indent_block("x = 1\ny = 2", 4)
    combined = f"if True:\n{block}    z = 3\n"
    compile(combined, "<test>", "exec")


# --- collision ---------------------------------------------------------------


def test_get_mesh_collision_info_reads_the_trace_flag_not_a_complexity(fake_bridge):
    fake_bridge.result = {
        "found": True, "complexity": "<CollisionTraceFlag.CTF_USE_DEFAULT: 0>",
        "simple_count": 1, "convex_count": 0,
    }
    r = meshes.get_mesh_collision_info("/Game/M/C.M")
    assert r["success"] is True
    assert "CTF_USE_DEFAULT" in r["complexity"]


def test_get_mesh_collision_info_reports_a_missing_asset(fake_bridge):
    fake_bridge.result = {"found": False, "error": "Could not load", "complexity": None,
                          "simple_count": None, "convex_count": None}
    assert meshes.get_mesh_collision_info("/Game/Nope.Nope")["success"] is False


@pytest.mark.parametrize("preset", ["spheroid", "", "CONVEX", None])
def test_collision_preset_rejects_an_unknown_name(fake_bridge, preset):
    r = meshes.set_mesh_collision_preset("/Game/M/C.C", preset, confirm=True)
    assert r["success"] is False
    assert "preset" in r["error"].lower()


def test_collision_preset_rejects_an_unknown_shape(fake_bridge):
    r = meshes.set_mesh_collision_preset("/Game/M/C.C", "simple", shape_type="DONUT", confirm=True)
    assert r["success"] is False
    assert "shape_type" in r["error"]


def test_collision_preset_rejects_a_zero_hull_count(fake_bridge):
    assert meshes.set_mesh_collision_preset("/Game/M/C.C", "convex", hull_count=0, confirm=True)["success"] is False


def test_collision_preset_needs_confirm(fake_bridge):
    with pytest.raises(security.SecurityViolation):
        meshes.set_mesh_collision_preset("/Game/M/C.C", "none")


def test_collision_preset_validates_before_dispatch(fake_bridge):
    """A bad preset must not reach the editor at all."""
    meshes.set_mesh_collision_preset("/Game/M/C.C", "nope", confirm=True)
    assert fake_bridge.last_expr is None


def test_convex_preset_uses_the_decomposition_call(fake_bridge):
    fake_bridge.result = {"found": True, "ok": True, "complexity": "CTF_USE_DEFAULT",
                          "simple_count": 0, "convex_count": 4}
    meshes.set_mesh_collision_preset("/Game/M/C.C", "convex", hull_count=4, confirm=True)
    snippet = fake_bridge.last_expr
    assert "set_convex_decomposition_collisions" in snippet
    assert "StaticMeshEditorSubsystem" in snippet


def test_simple_preset_uses_the_shape_enum(fake_bridge):
    fake_bridge.result = {"found": True, "ok": True, "complexity": "x",
                          "simple_count": 1, "convex_count": 0}
    meshes.set_mesh_collision_preset("/Game/M/C.C", "simple", shape_type="SPHERE", confirm=True)
    assert "ScriptCollisionShapeType.SPHERE" in fake_bridge.last_expr


# --- import guards -----------------------------------------------------------


def test_import_refuses_a_missing_source_file(fake_bridge):
    with pytest.raises(security.SecurityViolation):
        meshes.import_static_mesh("/tmp/definitely_not_here.obj")


def test_import_refuses_a_non_importable_suffix(tmp_path):
    bad = tmp_path / "payload.py"
    bad.write_text("x = 1\n")
    with pytest.raises(security.SecurityViolation):
        meshes.import_static_mesh(str(bad))


def test_import_refuses_an_engine_destination(tmp_path):
    """
    /Engine is engine content. A tool that can write there corrupts the install
    rather than the project.
    """
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    with pytest.raises(security.SecurityViolation):
        meshes.import_static_mesh(str(obj), "/Engine/Somewhere")


def test_import_allows_a_game_destination(tmp_path, fake_bridge):
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    fake_bridge.result = {"found": True, "error": None,
                          "imported_paths": ["/Game/M/M.M"], "asset_paths": ["/Game/M/M.M"]}
    assert meshes.import_static_mesh(str(obj), "/Game/MCPTest")["success"] is True


def test_import_reports_failure_when_nothing_was_produced(fake_bridge, tmp_path):
    """
    import_asset_tasks returns None either way, so an empty
    imported_object_paths is the only failure signal available.
    """
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    fake_bridge.result = {"found": True, "error": None, "imported_paths": [], "asset_paths": []}
    r = meshes.import_static_mesh(str(obj))
    assert r["success"] is False
    assert "no asset" in r["error"].lower()


def test_import_does_not_double_the_object_path(tmp_path, fake_bridge):
    """imported_object_paths are already full object paths."""
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    fake_bridge.result = {
        "found": True, "error": None,
        "imported_paths": ["/Game/M/M.M"], "asset_paths": ["/Game/M/M.M"],
    }
    r = meshes.import_static_mesh(str(obj))
    assert r["asset_paths"] == ["/Game/M/M.M"]
    assert ".M.M.M" not in r["asset_paths"][0]


def test_import_names_the_asset_after_the_file_stem(tmp_path, fake_bridge):
    obj = tmp_path / "MyMesh.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    fake_bridge.result = {"found": True, "error": None,
                          "imported_paths": ["/Game/M/MyMesh.MyMesh"],
                          "asset_paths": ["/Game/M/MyMesh.MyMesh"]}
    assert meshes.import_static_mesh(str(obj))["destination_name"] == "MyMesh"


def test_skeletal_import_sets_the_skeletal_flag(tmp_path, fake_bridge):
    fbx = tmp_path / "rig.fbx"
    fbx.write_bytes(b"Kaydara FBX Binary  \x00")
    fake_bridge.result = {"found": True, "error": None,
                          "imported_paths": ["/Game/M/rig.rig"], "asset_paths": ["/Game/M/rig.rig"]}
    meshes.import_skeletal_mesh(str(fbx))
    assert "import_as_skeletal = True" in fake_bridge.last_expr


# --- material domain ---------------------------------------------------------


def test_material_domain_change_needs_confirm(fake_bridge):
    with pytest.raises(security.SecurityViolation):
        materials.set_material_domain_and_shading_model("/Game/M/M.M", domain="MD_UI")


def test_material_domain_requires_at_least_one_argument(fake_bridge):
    r = materials.set_material_domain_and_shading_model("/Game/M/M.M", confirm=True)
    assert r["success"] is False


def test_material_domain_rejects_an_unknown_domain(fake_bridge):
    r = materials.set_material_domain_and_shading_model("/Game/M/M.M", domain="MD_NOPE", confirm=True)
    assert r["success"] is False
    assert "MD_NOPE" in r["error"]


def test_material_domain_builds_only_the_enum_it_is_given(fake_bridge):
    """
    Regression: interpolating both enum members unconditionally emitted
    `MaterialShadingModel.None` when only the domain was being changed, which
    does not compile and used to surface as an empty-log editor failure.
    """
    fake_bridge.result = {"found": True, "domain_before": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_before": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>",
                          "domain_after": "<MaterialDomain.MD_UI: 5>",
                          "shading_after": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>"}
    materials.set_material_domain_and_shading_model("/Game/M/M.M", domain="MD_UI", confirm=True)
    snippet = fake_bridge.last_expr
    assert "MaterialDomain.MD_UI" in snippet
    assert "MaterialShadingModel" not in snippet


def test_material_shading_model_alone_omits_the_domain_enum(fake_bridge):
    fake_bridge.result = {"found": True, "domain_before": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_before": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>",
                          "domain_after": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_after": "<MaterialShadingModel.MSM_UNLIT: 0>"}
    materials.set_material_domain_and_shading_model("/Game/M/M.M", shading_model="MSM_UNLIT", confirm=True)
    snippet = fake_bridge.last_expr
    assert "MaterialShadingModel.MSM_UNLIT" in snippet
    assert "set_editor_property" in snippet


def test_material_domain_strips_the_enum_repr_before_comparing(fake_bridge):
    """The read-back is '<MaterialDomain.MD_UI: 5>', not 'MD_UI'."""
    fake_bridge.result = {"found": True, "domain_before": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_before": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>",
                          "domain_after": "<MaterialDomain.MD_UI: 5>",
                          "shading_after": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>"}
    r = materials.set_material_domain_and_shading_model("/Game/M/M.M", domain="MD_UI", confirm=True)
    assert r["success"] is True
    assert r["domain_after"] == "MD_UI"


def test_material_domain_reports_a_read_back_that_did_not_take(fake_bridge):
    """If the value did not stick, that is a failure, not a success."""
    fake_bridge.result = {"found": True, "domain_before": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_before": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>",
                          "domain_after": "<MaterialDomain.MD_SURFACE: 0>",
                          "shading_after": "<MaterialShadingModel.MSM_DEFAULT_LIT: 1>"}
    r = materials.set_material_domain_and_shading_model("/Game/M/M.M", domain="MD_UI", confirm=True)
    assert r["success"] is False


def test_texture_import_sets_srgb_on_the_asset_not_the_factory(tmp_path, fake_bridge):
    """
    TextureFactory exposes no `srgb`, so setting it there raised AttributeError.
    The property belongs to the imported Texture2D.
    """
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake_bridge.result = {"found": True, "error": None,
                          "asset_path": "/Game/M/s.s", "size_x": 4, "size_y": 4,
                          "srgb": "True", "compression": "TC_DEFAULT"}
    r = materials.import_texture(str(png), srgb=False)
    assert r["success"] is True
    snippet = fake_bridge.last_expr
    assert "tex.set_editor_property('srgb', False)" in snippet
    assert "fact.srgb" not in snippet


def test_every_new_tool_has_a_risk_tier():
    for name in (
        "get_mesh_collision_info", "set_mesh_collision_preset",
        "import_static_mesh", "import_skeletal_mesh", "import_texture",
        "set_material_domain_and_shading_model",
    ):
        assert name in security.TOOL_RISK_TIERS, name


def test_run_python_accepts_a_per_call_timeout():
    """Domain changes recompile shaders and overrun the default 8s."""
    import inspect
    sig = inspect.signature(bridge.UnrealBridge.run_python)
    assert "timeout" in sig.parameters


# --- skeletal mesh support ---------------------------------------------------


def test_mesh_bounds_asks_skeletal_meshes_a_different_question(monkeypatch, fake_bridge):
    """
    SkeletalMesh has no get_bounding_box; it has get_bounds returning a
    BoxSphereBounds. Asking the StaticMesh way raised AttributeError.
    """
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mesh": "SK", "mesh_class": "SkeletalMesh", "lod_count": 1,
         "min": [-1, -1, -1], "max": [1, 1, 1], "sphere_radius": 1.73},
    )
    r = meshes.get_mesh_bounds("/Game/M/SK.SK")
    assert r["success"] is True
    assert r["mesh_class"] == "SkeletalMesh"
    assert r["sphere_radius"] == 1.73


def test_mesh_bounds_uses_the_static_path_for_static_meshes(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mesh": "SM", "mesh_class": "StaticMesh", "lod_count": 2,
         "min": [-50, -50, -50], "max": [50, 50, 50], "sphere_radius": None},
    )
    r = meshes.get_mesh_bounds("/Game/M/SM.SM")
    assert r["size"] == [100, 100, 100]
    assert r["extent"] == [50, 50, 50]
    assert r["sphere_radius"] is None


def test_mesh_bounds_normalizes_skeletal_origin_extent_into_min_max(monkeypatch, fake_bridge):
    """A SkeletalMesh reports a centre and a half-size, not two corners."""
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mesh": "SK", "mesh_class": "SkeletalMesh", "lod_count": 1,
         "min": [0.0, 0.0, 0.0], "max": [2.0, 2.0, 2.0], "sphere_radius": 1.73},
    )
    r = meshes.get_mesh_bounds("/Game/M/SK.SK")
    assert r["min"] == [0.0, 0.0, 0.0]
    assert r["max"] == [2.0, 2.0, 2.0]
    assert r["extent"] == [1.0, 1.0, 1.0]


def test_mesh_bounds_reports_a_mesh_with_no_bounds_instead_of_crashing(monkeypatch, fake_bridge):
    monkeypatch.setattr(
        fake_bridge, "result",
        {"found": True, "mesh": "M", "mesh_class": "Mystery", "lod_count": 1,
         "min": None, "max": None, "sphere_radius": None},
    )
    r = meshes.get_mesh_bounds("/Game/M/M.M")
    assert r["success"] is False
    assert "Mystery" in r["error"]


# --- create_material must never open a modal -------------------------------
#
# AssetTools.create_asset defaults to replace_existing=True, which pops an editor
# dialog. The dialog blocks the Remote Control endpoint, so the caller sees a
# timeout rather than a question and cannot answer it.


def test_create_material_refuses_to_overwrite_by_default(fake_bridge):
    fake_bridge.result = {
        "found": False,
        "error": "An asset already exists at /Game/M; pass replace_existing=True"
                 " (and confirm=True) to overwrite",
        "material_path": "/Game/M",
    }
    r = materials.create_material("/Game/Mats", "M")
    assert r["success"] is False


def test_create_material_never_overwrites(fake_bridge):
    """
    Creating a material with a name that is taken must fail, not overwrite and
    not prompt. Use a new name instead; overwriting is a separate act.
    """
    assert "replace_existing" not in inspect.signature(materials.create_material).parameters
    fake_bridge.result = {
        "found": False,
        "error": "An asset already exists at /Game/M; create_material does not"
                 " overwrite, so use a new name",
        "material_path": "/Game/M",
    }
    r = materials.create_material("/Game/Mats", "M")
    assert r["success"] is False
    assert "does not overwrite" in r["error"]


def test_create_material_passes_the_overwrite_flag_positionally(fake_bridge):
    """
    create_asset's 5th positional arg is calling_context (a Name), so the False
    that suppresses the overwrite dialog is passed 6th. Leaving it 5th raises
    "Cannot nativize 'bool' as 'Name'", and omitting it entirely pops the dialog.
    """
    fake_bridge.result = {"found": True, "error": None, "material_path": "/Game/M"}
    materials.create_material("/Game/Mats", "M")
    snippet = fake_bridge.last_expr
    # UNREAL expands to __import__('unreal'), so match on the tail of the call.
    assert "MaterialFactoryNew(), 'None', False)" in snippet
    assert "MaterialFactoryNew())" not in snippet
