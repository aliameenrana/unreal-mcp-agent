from __future__ import annotations

from unittest.mock import patch

from unreal_mcp.tools import blueprints


def test_create_rejects_empty_path_without_touching_the_editor():
    with patch.object(blueprints, "get_bridge") as bridge:
        assert blueprints.create_blueprint("")["success"] is False
        bridge.assert_not_called()


def test_create_rejects_a_dotted_path_and_explains_why():
    # The editor silently turns the dot into an underscore in the asset name, so
    # the tool refuses rather than creating BP_Thing_BP_Thing.
    with patch.object(blueprints, "get_bridge") as bridge:
        result = blueprints.create_blueprint("/Game/MCPTest/BP_Thing.BP_Thing")
        bridge.assert_not_called()
    assert result["success"] is False
    assert "underscore" in result["error"]


def test_create_defaults_to_the_actor_parent_class():
    with patch.object(blueprints, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": True,
            "blueprint_path": "/Game/MCPTest/BP_Thing.BP_Thing",
            "parent_class": "Actor",
            "editor_returned_none": False,
        }
        result = blueprints.create_blueprint("/Game/MCPTest/BP_Thing")
    assert result["success"] is True
    assert result["parent_class"] == "Actor"
    script = bridge.return_value.run_python.call_args[0][0]
    assert "/Script/Engine.Actor" in script


def test_create_reports_success_even_when_the_editor_returned_none():
    # Measured: create_blueprint_asset_with_parent returns None in cases where it
    # did create the asset, so a None return is not treated as failure.
    with patch.object(blueprints, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": True,
            "blueprint_path": "/Game/MCPTest/BP_Thing.BP_Thing",
            "parent_class": "Actor",
            "editor_returned_none": True,
        }
        result = blueprints.create_blueprint("/Game/MCPTest/BP_Thing")
    assert result["success"] is True
    assert result["editor_returned_none"] is True


def test_reading_a_non_blueprint_reports_what_it_actually_was():
    with patch.object(blueprints, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": False,
            "error": "Not a Blueprint: StaticMesh",
        }
        result = blueprints.get_blueprint_info("/Game/MCPTest/Red")
    assert result["success"] is False
    assert "StaticMesh" in result["error"]


def test_graph_listing_always_declares_nodes_unreadable():
    # A caller must never mistake the graph list for a node list.
    with patch.object(blueprints, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": True,
            "blueprint_path": "/Game/MCPTest/BP_Thing.BP_Thing",
            "count": 2,
            "graphs": [
                {"name": "EventGraph", "object_path": "a:EventGraph",
                 "is_event_graph": True},
                {"name": "UserConstructionScript", "object_path": "a:UCS",
                 "is_event_graph": False},
            ],
            "nodes_readable": False,
            "nodes_unreadable_reason": "EdGraph.Nodes is a protected property",
        }
        result = blueprints.list_blueprint_graphs("/Game/MCPTest/BP_Thing")
    assert result["success"] is True
    assert result["nodes_readable"] is False
    assert "protected" in result["nodes_unreadable_reason"]
    assert result["count"] == len(result["graphs"]) == 2
    assert [g["name"] for g in result["graphs"] if g["is_event_graph"]] == ["EventGraph"]


def test_generated_snippet_declares_the_variables_type_caveat():
    # The type column is a JSON schema, and the int32/float ambiguity has to be
    # stated on the result, not only in the docstring.
    with patch.object(blueprints, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = {
            "found": True,
            "blueprint_path": "/Game/MCPTest/BP_Thing.BP_Thing",
            "include_inherited": False,
            "type_format": "int32 and float both read as integer",
            "count": 1,
            "variables": [{"name": "Speed", "type": '{ "type": "number" }'}],
        }
        result = blueprints.list_blueprint_variables("/Game/MCPTest/BP_Thing")
    assert "int32" in result["type_format"]
    assert result["include_inherited"] is False
