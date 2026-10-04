"""Every graph tool's generated snippet must compile locally.

Cheaper and more reliable than discovering the problem at the editor, where a
malformed body arrives as an empty-log RemoteCommandFailedError. No editor
needed: stub the bridge result and let guarded() do its compile check.
"""
import pytest

from unreal_mcp import security
from unreal_mcp.tools import material_graph as mg

MAT = "/Game/MCPTest/SnippetProbe.M"
OK = {"found": True, "error": None}


@pytest.fixture
def stubbed(monkeypatch):
    """Capture the body and return a benign payload without touching an editor."""
    cap = {}

    def fake_guarded(body):
        cap["body"] = body
        # Compiling here is the assertion: guarded() raises SyntaxError itself.
        return "built"

    def fake_run_python(expression, timeout=None):
        return dict(OK)

    monkeypatch.setattr(mg, "guarded", fake_guarded)
    monkeypatch.setattr(mg, "get_bridge", lambda: type(
        "B", (), {"run_python": staticmethod(fake_run_python)})())
    return cap


def _builds(fn, *args, **kwargs):
    """Call fn and return True if its snippet compiles."""
    try:
        fn(*args, **kwargs)
        return True, ""
    except SyntaxError as exc:
        return False, str(exc)


READ_CALLS = [
    ("list_material_expressions", (MAT,)),
    ("get_material_inputs", (MAT,)),
    ("get_material_graph_stats", (MAT,)),
    ("get_material_used_textures", (MAT,)),
    ("find_material_parameter_usage", (MAT,)),
    ("recompile_material_graph", (MAT,)),
    ("get_material_expression_property", (MAT, "Brightness", "r")),
]

WRITE_CALLS = [
    ("create_material_expression", (MAT, "MaterialExpressionConstant")),
    ("create_material_expression",
     (MAT, "MaterialExpressionConstant", 0, 0, "Label", {"r": 0.5})),
    ("create_material_parameter", (MAT, "scalar", "Gloss", 0.5, "Surface")),
    ("create_material_parameter", (MAT, "vector", "Tint", (1.0, 0.0, 0.0))),
    ("create_material_parameter", (MAT, "static_switch", "Flag", True)),
    ("connect_material_expressions", (MAT, "Brightness", "", "Multiply", "A")),
    ("connect_material_input", (MAT, "Multiply", "", "base_color")),
    ("set_material_expression_property", (MAT, "Brightness", "r", 0.25)),
    ("layout_material_graph", (MAT,)),
]

CONFIRMED_CALLS = [
    ("delete_material_expression", (MAT, "Brightness"), {"confirm": True}),
    ("delete_unused_material_expressions", (MAT,), {"confirm": True}),
    ("disconnect_material_input", (MAT, "Multiply", "A"), {"confirm": True}),
    ("set_material_static_switch_parameter", (MAT, "Flag", True), {"confirm": True}),
]


@pytest.mark.parametrize("name,args", READ_CALLS)
def test_read_tool_snippet_compiles(stubbed, name, args):
    ok, err = _builds(getattr(mg, name), *args)
    assert ok, f"{name}: {err}"


@pytest.mark.parametrize("name,args", WRITE_CALLS)
def test_write_tool_snippet_compiles(stubbed, name, args):
    ok, err = _builds(getattr(mg, name), *args)
    assert ok, f"{name}: {err}"


@pytest.mark.parametrize("name,args,kwargs", CONFIRMED_CALLS)
def test_confirmed_tool_snippet_compiles(stubbed, name, args, kwargs):
    ok, err = _builds(getattr(mg, name), *args, **kwargs)
    assert ok, f"{name}: {err}"


@pytest.mark.parametrize("selector", ["Brightness", "Multiply", "Multiply@0,0", "#2"])
def test_every_selector_shape_builds(stubbed, selector):
    ok, err = _builds(mg.get_material_expression_property, MAT, selector, "r")
    assert ok, f"selector {selector!r}: {err}"


@pytest.mark.parametrize("recompile", [True, False])
def test_recompile_flag_changes_only_the_compile_step(stubbed, recompile):
    mg.create_material_expression(MAT, "MaterialExpressionConstant", recompile=recompile)
    body = stubbed["body"]
    if recompile:
        assert "recompile_material" in body
    else:
        assert "recompile_material" not in body
        assert "errors = None" in body


def test_resolution_error_never_raises_on_a_bad_selector(stubbed):
    """
    The ambiguity message is built with concatenation, not .format(): the {n}/{d}
    placeholders live inside the generated snippet, so an f-string evaluated them
    here and raised NameError before anything reached the editor.
    """
    ok, err = _builds(mg.get_material_expression_property, MAT, "Nope", "r")
    assert ok, err
    assert "{n}" not in stubbed["body"]


def test_value_type_inference_covers_the_common_shapes():
    assert "Vector" in mg._value_snippet([1.0, 2.0, 3.0], "auto")
    assert "LinearColor" in mg._value_snippet([1.0, 2.0, 3.0, 1.0], "auto")
    assert "float(" in mg._value_snippet(0.5, "auto")
    assert "int(" in mg._value_snippet(3, "auto")
    assert mg._value_snippet(True, "auto") in ("True", "bool(True)")
    assert "load_asset" in mg._value_snippet("/Game/T.T", "texture")
    with pytest.raises(ValueError):
        mg._value_snippet(1.0, "quaternion")


def test_parameter_classes_are_real_expression_classes():
    for friendly, cls in mg.PARAMETER_CLASSES.items():
        assert cls.startswith("MaterialExpression"), friendly


def test_material_input_map_is_non_empty_and_names_are_upper():
    assert mg.MATERIAL_INPUTS
    for friendly, enum in mg.MATERIAL_INPUTS.items():
        assert enum.startswith("MP_"), friendly
        assert friendly.islower(), friendly


def test_every_graph_tool_has_a_tier():
    for name in (
        "list_material_expressions", "get_material_inputs", "get_material_graph_stats",
        "get_material_used_textures", "find_material_parameter_usage",
        "create_material_expression", "create_material_parameter",
        "connect_material_expressions", "connect_material_input",
        "disconnect_material_input", "set_material_expression_property",
        "get_material_expression_property", "delete_material_expression",
        "delete_unused_material_expressions", "layout_material_graph",
        "recompile_material_graph", "set_material_static_switch_parameter",
    ):
        assert name in security.TOOL_RISK_TIERS, name


def test_destructive_graph_tools_require_confirm():
    for name in ("delete_material_expression", "delete_unused_material_expressions",
                 "disconnect_material_input", "set_material_static_switch_parameter"):
        assert security.TOOL_RISK_TIERS[name] is security.RiskTier.DESTRUCTIVE, name
