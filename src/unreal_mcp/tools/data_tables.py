"""
Data Tables: create the asset and read its structure and contents.

Verified against a live Unreal Editor 5.8 session.

**Rows cannot be written through this bridge, and that is a hard limit rather than
an unfinished feature.** `DataTableFunctionLibrary.fill_data_table_from_json_string`
(and the CSV twin) deadlocks the editor when called over Remote Control: the call
is dispatched on the game thread, and the reimport it triggers wants the game
thread too, so the editor wedges and never answers. Moving the same call onto a
worker thread inside the editor does not help, because Unreal rejects it outright:

    RuntimeError: DataTableFunctionLibrary: Attempted to access Unreal API from
    outside the main game thread

So a Data Table can be created and read from here, but a row can only be added by
the editor's own import UI or by hand. Everything below is therefore built to be
useful without row writes: create the structure, inspect what is in it, and export
it for external processing. Authoring rows is left to the editor.
"""

from __future__ import annotations

from .. import security
from ..bridge import get_bridge
from ..remote_snippets import UNREAL, guarded, indent_block, load_asset

# Row writes are unreachable from here; see the module docstring. Tools that would
# need them are not built rather than built to fail confusingly.
ROW_WRITES_SUPPORTED = False


def _load_table(table_path: str) -> str:
    """Source for loading a DataTable and refusing anything else, as `dt`."""
    return (
        f"dt = {load_asset(table_path)}\n"
        f"if dt is None:\n"
        f"    OUT = {{'found': False, 'error': {table_path!r} + ' did not load'}}\n"
        f"elif not isinstance(dt, {UNREAL}.DataTable):\n"
        f"    OUT = {{'found': False, 'error': 'Not a DataTable: ' + type(dt).__name__}}\n"
    )


def _result(payload: dict, table_path: str, fallback: str, **fields) -> dict:
    """
    Builds a tool return from an editor payload.

    Every reading tool routes through this so `success` cannot drift from what the
    editor actually reported. It previously did: `_read` returned an error dict for
    a missing table, and each caller then rebuilt its return with a hardcoded
    `success: True`, so a nonexistent table read as a success with every field
    None. One place decides success, from `found`.
    """
    if not payload.get("found"):
        return {"success": False, "table_path": table_path,
                "error": payload.get("error") or fallback}
    return {"success": True, "table_path": payload.get("table_path"), **fields}


def _read(table_path: str, inner: str) -> dict:
    """
    Runs `inner` with `dt` loaded, inside a guarded body. `inner` is indented under
    the success branch and is responsible for assigning OUT.
    """
    script = (
        f"{_load_table(table_path)}"
        f"else:\n"
        f"{indent_block(inner, spaces=4)}"
    )
    return get_bridge().run_python(guarded(script))


def create_data_table(package_path: str, table_name: str,
                      row_struct_class: str) -> dict:
    """
    Creates an empty Data Table asset. `row_struct_class` is a full ScriptStruct
    path, e.g. '/Script/GameplayTags.GameplayTagTableRow'.

    The table starts with no rows: see the module docstring for why row writes are
    not reachable through this bridge. This is safe to call; it does not block.
    """
    security.enforce_tier("create_data_table")
    if not table_name.strip():
        return {"success": False, "error": "table_name must not be empty."}
    package = f"{package_path.rstrip('/')}/{table_name}"
    object_path = f"{package}.{table_name}"

    # 'None', False are calling_context and bInteractive. bInteractive must be
    # False explicitly: create_asset defaults it to True and pops an overwrite
    # dialog, which blocks the Remote Control endpoint until a human clicks it,
    # so the caller sees a timeout rather than a question. The does_asset_exist
    # check above is not a substitute, because delete_asset is asynchronous and
    # can report a deletion that has not landed on disk yet.
    script = (
        f"pkg = {package!r}\n"
        f"struct_path = {row_struct_class!r}\n"
        f"if {UNREAL}.EditorAssetLibrary.does_asset_exist(pkg):\n"
        f"    OUT = {{'found': False,\n"
        f"          'error': 'an asset already exists at ' + pkg + '; use a new name'}}\n"
        f"else:\n"
        f"    ss = {UNREAL}.load_object(None, struct_path)\n"
        f"    if not isinstance(ss, {UNREAL}.ScriptStruct):\n"
        f"        OUT = {{'found': False,\n"
        f"              'error': 'not a ScriptStruct: ' + struct_path + ' (got '\n"
        f"                      + type(ss).__name__ + ')'}}\n"
        f"    else:\n"
        f"        fac = {UNREAL}.DataTableFactory()\n"
        f"        fac.struct = ss\n"
        f"        dt = {UNREAL}.AssetToolsHelpers.get_asset_tools().create_asset(\n"
        f"            {table_name!r}, {package_path!r}, {UNREAL}.DataTable, fac,\n"
        f"            'None', False)\n"
        f"        if dt is None:\n"
        f"            OUT = {{'found': False,\n"
        f"                  'error': 'the editor created nothing at ' + pkg}}\n"
        f"        else:\n"
        f"            OUT = {{'found': True, 'error': None,\n"
        f"                  'table_path': dt.get_path_name(),\n"
        f"                  'row_struct': ss.get_path_name(),\n"
        f"                  'row_count': len(list(dt.get_row_names())),\n"
        f"                  'rows_writable_from_bridge': False}}\n"
    )
    payload = get_bridge().run_python(guarded(script))
    if not payload.get("found"):
        return {"success": False, "table_path": None,
                "error": payload.get("error") or "could not create data table"}
    return {"success": True,
            "table_path": payload.get("table_path"),
            "row_struct": payload.get("row_struct"),
            "row_count": payload.get("row_count"),
            "rows_writable_from_bridge": False}


def get_data_table_info(table_path: str) -> dict:
    """
    Summarises a Data Table: its row struct, row count, row names, column names,
    and whether rows can be written from here.

    Column names come in two spellings, both reported: the internal names used by
    `get_data_table_column_names` and the export names a CSV or JSON import
    expects. Both are returned because Unreal allows them to differ, but they were
    measured identical for every struct tried (GameplayTagTableRow gives
    ['Tag', 'DevComment'] from both), so do not rely on either spelling differing
    from the other; the pair is reported for callers that need to be exact.
    """
    security.enforce_tier("get_data_table_info")
    inner = (
        f"F = {UNREAL}.DataTableFunctionLibrary\n"
        f"rs = dt.get_row_struct()\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'table_path': dt.get_path_name(),\n"
        f"      'row_struct': rs.get_path_name() if rs is not None else None,\n"
        f"      'row_count': len(list(dt.get_row_names())),\n"
        f"      'rows_writable_from_bridge': {ROW_WRITES_SUPPORTED!r},\n"
        f"      'row_names': [str(n) for n in dt.get_row_names()],\n"
        f"      'columns': [str(n) for n in F.get_data_table_column_names(dt)],\n"
        f"      'export_names': [str(n)\n"
        f"                     for n in F.get_data_table_column_export_names(dt)]}}\n"
    )
    payload = _read(table_path, inner)
    return _result(payload, table_path, "could not read data table",
                   row_struct=payload.get("row_struct"),
                   row_count=payload.get("row_count"),
                   row_names=payload.get("row_names") or [],
                   columns=payload.get("columns") or [],
                   export_names=payload.get("export_names") or [],
                   rows_writable_from_bridge=payload.get("rows_writable_from_bridge"))


def list_data_table_rows(table_path: str, limit: int | None = None,
                         offset: int = 0) -> dict:
    """
    Reads rows from a Data Table as cell-name to cell-string pairs.

    Cells come back through `get_data_table_column_as_string`, so they are
    formatted for display rather than typed: a float reads as its printed form
    and a struct as its bracketed summary. That is enough to inspect or diff a
    table, but not to read exact values back with their types, because a DataTable
    cell cannot be read into a typed Python value through any exposed API.

    `limit` and `offset` page through a large table without pulling every cell.
    """
    security.enforce_tier("list_data_table_rows")
    if limit is not None and limit <= 0:
        return {"success": False, "table_path": table_path,
                "error": "limit must be positive, or omitted for every row."}
    if offset < 0:
        return {"success": False, "table_path": table_path,
                "error": "offset must not be negative."}

    # The slice bounds are decided here rather than in the snippet. Emitting
    # `if 2 is not None` instead compiles but raises a SyntaxWarning on the way
    # past, and a tool that generates warnings is a tool nobody trusts.
    if limit is None:
        window_src = f"all_rows[{offset}:]"
    else:
        window_src = f"all_rows[{offset}:{offset + limit}]"

    inner = (
        f"F = {UNREAL}.DataTableFunctionLibrary\n"
        f"cols = [str(c) for c in F.get_data_table_column_names(dt)]\n"
        f"all_rows = [str(n) for n in dt.get_row_names()]\n"
        f"window = {window_src}\n"
        f"rows = []\n"
        f"for name in window:\n"
        f"    cells = {{}}\n"
        f"    for col in cols:\n"
        f"        try:\n"
        f"            cells[col] = F.get_data_table_column_as_string(dt, name, col)\n"
        f"        except Exception as exc:\n"
        f"            cells[col] = None\n"
        f"            cells[col + '_error'] = type(exc).__name__\n"
        f"    rows.append({{'name': name, 'cells': cells}})\n"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'table_path': dt.get_path_name(),\n"
        f"      'total_rows': len(all_rows),\n"
        f"      'offset': {offset!r},\n"
        f"      'limit': {limit!r},\n"
        f"      'columns': cols,\n"
        f"      'cells_are_strings': True,\n"
        f"      'count': len(rows), 'rows': rows}}\n"
    )
    payload = _read(table_path, inner)
    return _result(payload, table_path, "could not list rows",
                   total_rows=payload.get("total_rows"),
                   offset=payload.get("offset"),
                   limit=payload.get("limit"),
                   columns=payload.get("columns") or [],
                   cells_are_strings=True,
                   count=payload.get("count"),
                   rows=payload.get("rows") or [])


def export_data_table(table_path: str, fmt: str = "csv",
                      max_rows: int | None = None) -> dict:
    """
    Exports a Data Table's contents as text, in `fmt` of 'csv' or 'json'.

    Useful for handing a table to external tooling, or for diffing two tables.
    `max_rows` truncates the export, which matters because a large table exports to
    a very large string. Truncation is reported as `truncated` with the row count
    actually included, so a caller never mistakes a partial export for the whole
    table.
    """
    security.enforce_tier("export_data_table")
    fmt = fmt.strip().lower()
    if fmt not in ("csv", "json"):
        return {"success": False, "table_path": table_path,
                "error": f"fmt must be 'csv' or 'json', got {fmt!r}"}

    # Truncation is decided here for the same reason as the paging bounds above.
    if max_rows is None:
        truncate_src = "truncated = False\n"
    else:
        truncate_src = (
            f"truncated = len(all_rows) > {max_rows!r}\n"
            f"if truncated:\n"
            f"    lines = text.splitlines()\n"
            f"    # Keep the header plus the first max_rows data lines.\n"
            f"    text = chr(10).join(lines[:{max_rows!r} + 1])\n"
        )

    inner = (
        f"all_rows = [str(n) for n in dt.get_row_names()]\n"
        f"text = dt.export_to_{fmt}_string()\n"
        f"{truncate_src}"
        f"OUT = {{'found': True, 'error': None,\n"
        f"      'table_path': dt.get_path_name(),\n"
        f"      'format': {fmt!r},\n"
        f"      'total_rows': len(all_rows),\n"
        f"      'truncated': truncated,\n"
        f"      'text': text}}\n"
    )
    payload = _read(table_path, inner)
    return _result(payload, table_path, "could not export data table",
                   format=payload.get("format"),
                   total_rows=payload.get("total_rows"),
                   truncated=payload.get("truncated"),
                   text=payload.get("text") or "")
