from __future__ import annotations

from unittest.mock import patch

from unreal_mcp.tools import data_tables


def _found(**fields):
    return {"found": True, "error": None, **fields}


def _guard_failure(message):
    return {"found": False, "error": message}


def test_row_writes_are_declared_unsupported():
    # Not pessimism: measured. fill_data_table_from_json_string deadlocks the
    # editor when called on the game thread, and Unreal rejects it outright from a
    # worker thread. verify_data_tables.py step 8 checks this against the editor.
    assert data_tables.ROW_WRITES_SUPPORTED is False


def test_reading_a_missing_table_reports_the_failure():
    # Regression-shaped: _read returned the guard's error dict, and each caller
    # then rebuilt its return with a hardcoded `success: True`, so a nonexistent
    # table read as a success with every field None.
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _guard_failure(
            "/Game/MCPTest/ZZNoSuchTable did not load")
        result = data_tables.get_data_table_info("/Game/MCPTest/ZZNoSuchTable")
    assert result["success"] is False
    assert "did not load" in result["error"]


def test_reading_a_non_datatable_names_what_it_actually_was():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _guard_failure(
            "Not a DataTable: StaticMesh")
        result = data_tables.get_data_table_info("/Game/MCPTest/Red")
    assert result["success"] is False
    assert "StaticMesh" in result["error"]


def test_info_reports_both_column_spellings():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT",
            row_struct="/Script/GameplayTags.GameplayTagTableRow",
            row_count=0,
            row_names=[],
            columns=["Tag", "DevComment"],
            export_names=["Tag", "DevComment"],
            rows_writable_from_bridge=False,
        )
        result = data_tables.get_data_table_info("/Game/MCPTest/DT")
    assert result["success"] is True
    assert result["columns"] == result["export_names"] == ["Tag", "DevComment"]
    assert result["rows_writable_from_bridge"] is False


def test_rows_are_flagged_as_strings():
    # Cells come from get_data_table_column_as_string, so a caller must not assume
    # typed values.
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT", total_rows=1, offset=0, limit=None,
            columns=["Tag"], count=1,
            rows=[{"name": "R1", "cells": {"Tag": "T.A"}}],
        )
        result = data_tables.list_data_table_rows("/Game/MCPTest/DT")
    assert result["cells_are_strings"] is True
    assert result["rows"][0]["cells"] == {"Tag": "T.A"}


def test_paging_arguments_are_validated_before_dispatch():
    with patch.object(data_tables, "get_bridge") as bridge:
        assert data_tables.list_data_table_rows(
            "/Game/MCPTest/DT", limit=0)["success"] is False
        assert data_tables.list_data_table_rows(
            "/Game/MCPTest/DT", offset=-1)["success"] is False
        bridge.assert_not_called()


def test_paging_bounds_are_decided_on_the_host():
    # The snippet must not emit `if 2 is not None`, which compiles but warns.
    # Slicing is chosen here instead.
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT", total_rows=0, offset=5, limit=3,
            columns=[], count=0, rows=[])
        data_tables.list_data_table_rows("/Game/MCPTest/DT", limit=3, offset=5)
    body = bridge.return_value.run_python.call_args[0][0]
    assert "all_rows[5:8]" in body
    assert "is not None" not in body


def test_unbounded_paging_omits_the_end_index():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT", total_rows=0, offset=2, limit=None,
            columns=[], count=0, rows=[])
        data_tables.list_data_table_rows("/Game/MCPTest/DT", offset=2)
    body = bridge.return_value.run_python.call_args[0][0]
    assert "all_rows[2:]" in body


def test_export_rejects_an_unsupported_format_before_dispatch():
    with patch.object(data_tables, "get_bridge") as bridge:
        result = data_tables.export_data_table("/Game/MCPTest/DT", fmt="xml")
        bridge.assert_not_called()
    assert result["success"] is False
    assert "csv" in result["error"]


def test_export_truncation_is_decided_on_the_host():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT", format="csv", total_rows=100,
            truncated=True, text="a,b,c")
        result = data_tables.export_data_table("/Game/MCPTest/DT", max_rows=5)
    body = bridge.return_value.run_python.call_args[0][0]
    assert "len(all_rows) > 5" in body
    assert "is not None" not in body
    # A partial export must be reported as one.
    assert result["truncated"] is True
    assert result["total_rows"] == 100


def test_create_refuses_an_empty_name_before_dispatch():
    with patch.object(data_tables, "get_bridge") as bridge:
        assert data_tables.create_data_table(
            "/Game/MCPTest", "", "/Script/X.Y")["success"] is False
        bridge.assert_not_called()


def test_create_refuses_an_existing_table():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _guard_failure(
            "an asset already exists at /Game/MCPTest/DT; use a new name")
        result = data_tables.create_data_table(
            "/Game/MCPTest", "DT", "/Script/X.Y")
    assert result["success"] is False
    assert "new name" in result["error"]


def test_create_reports_row_writes_as_unavailable_even_on_success():
    with patch.object(data_tables, "get_bridge") as bridge:
        bridge.return_value.run_python.return_value = _found(
            table_path="/Game/MCPTest/DT.DT",
            row_struct="/Script/GameplayTags.GameplayTagTableRow",
            row_count=0, rows_writable_from_bridge=False)
        result = data_tables.create_data_table(
            "/Game/MCPTest", "DT", "/Script/GameplayTags.GameplayTagTableRow")
    assert result["success"] is True
    assert result["rows_writable_from_bridge"] is False
