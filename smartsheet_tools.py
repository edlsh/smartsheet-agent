#!/usr/bin/env python3
"""
Smartsheet tools for SmartSheetBot.

This module provides READ-ONLY tools for interacting with Smartsheet data.
These tools are simple Python functions that can be used with Agno agents.

CONSOLIDATED TOOLS (28 total, reduced from 49):

    Core (5 tools - unchanged):
    - list_sheets: List all accessible Smartsheets
    - get_sheet: Get detailed data from a specific sheet
    - get_row: Get information about a specific row
    - filter_rows: Filter rows based on column values
    - count_rows_by_column: Count rows grouped by column values

    Unified Resource Tools (7 tools - consolidated from 14):
    - workspace: List all or get specific workspace
    - folder: List all or get specific folder
    - sight: List all or get specific sight/dashboard
    - report: List all or get specific report
    - webhook: List all or get specific webhook
    - group: List all or get specific group
    - user: List all or get specific user (Admin)

    Unified Scope Tools (2 tools - consolidated from 5):
    - attachment: Get attachments at sheet/row/specific level
    - discussion: Get discussions at sheet/row level

    Unified Search (1 tool - consolidated from 2):
    - search: Search globally or within specific sheet

    Unified Navigation (1 tool - consolidated from 3):
    - navigation: Access home, favorites, or templates

    Unified Sheet Metadata (1 tool - consolidated from 5):
    - sheet_metadata: Get automation, shares, publish status, proofs, or cross-references

    Unified Sheet Info (1 tool - consolidated from 4):
    - sheet_info: Get columns, stats, summary_fields, or specific columns

    Unified Update Requests (1 tool - consolidated from 2):
    - update_requests: Get pending or sent update requests

    Standalone Tools (9 tools - unchanged):
    - compare_sheets: Compare two sheets by key column
    - get_cell_history: Get revision history for a specific cell
    - get_sheet_version: Get sheet version information
    - get_events: Get recent audit events
    - get_current_user: Get current user profile
    - get_contacts: Get personal contacts
    - get_server_info: Get server information
    - list_org_sheets: List all organization sheets (Admin)
    - get_image_urls: Get image URLs from cells

Sheet Scoping:
    Set these environment variables to restrict access to specific sheets:
    - ALLOWED_SHEET_IDS: Comma-separated list of sheet IDs
    - ALLOWED_SHEET_NAMES: Comma-separated list of sheet names
"""

import os
from typing import Optional, Literal
from datetime import datetime, timedelta
from functools import lru_cache
import threading
import time
import smartsheet


# =============================================================================
# CACHING & PERFORMANCE CONFIGURATION
# =============================================================================

# Cache TTL in seconds (5 minutes default)
CACHE_TTL_SECONDS = int(os.getenv("SMARTSHEET_CACHE_TTL", "300"))

# Global cache storage with timestamps
_cache = {
    "client": None,
    "client_created_at": 0,
    "sheets_list": None,
    "sheets_list_fetched_at": 0,
    "sheet_name_to_id": {},  # name.lower() -> (id, name, fetched_at)
}
_cache_lock = threading.Lock()


def _is_cache_valid(fetched_at: float) -> bool:
    """Check if cached data is still valid based on TTL."""
    return time.time() - fetched_at < CACHE_TTL_SECONDS


def clear_cache():
    """Clear all cached data. Call this if you need fresh data."""
    global _cache
    with _cache_lock:
        _cache = {
            "client": None,
            "client_created_at": 0,
            "sheets_list": None,
            "sheets_list_fetched_at": 0,
            "sheet_name_to_id": {},
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

@lru_cache(maxsize=1)
def _get_allowed_sheet_ids() -> frozenset[int]:
    """Get set of allowed sheet IDs from environment (cached)."""
    ids_str = os.getenv("ALLOWED_SHEET_IDS", "").strip()
    if not ids_str:
        return frozenset()
    return frozenset(int(id.strip()) for id in ids_str.split(",") if id.strip().isdigit())


@lru_cache(maxsize=1)
def _get_allowed_sheet_names() -> frozenset[str]:
    """Get set of allowed sheet names from environment (cached, lowercase for comparison)."""
    names_str = os.getenv("ALLOWED_SHEET_NAMES", "").strip()
    if not names_str:
        return frozenset()
    return frozenset(name.strip().lower() for name in names_str.split(",") if name.strip())


def _is_sheet_allowed(sheet_id: int = None, sheet_name: str = None) -> bool:
    """Check if a sheet is in the allowed list."""
    allowed_ids = _get_allowed_sheet_ids()
    allowed_names = _get_allowed_sheet_names()

    if not allowed_ids and not allowed_names:
        return True

    if sheet_id and sheet_id in allowed_ids:
        return True

    if sheet_name and sheet_name.lower() in allowed_names:
        return True

    return False


def get_smartsheet_client() -> smartsheet.Smartsheet:
    """
    Get an authenticated Smartsheet client (singleton with TTL).
    
    The client is cached and reused for CACHE_TTL_SECONDS to reduce
    connection overhead. Thread-safe.
    """
    global _cache
    
    with _cache_lock:
        # Return cached client if still valid
        if _cache["client"] and _is_cache_valid(_cache["client_created_at"]):
            return _cache["client"]
        
        # Create new client
        token = os.getenv("SMARTSHEET_ACCESS_TOKEN")
        if not token:
            raise ValueError("SMARTSHEET_ACCESS_TOKEN environment variable is not set")
        
        client = smartsheet.Smartsheet(token)
        client.errors_as_exceptions(True)
        
        # Cache the client
        _cache["client"] = client
        _cache["client_created_at"] = time.time()
        
        return client


def _get_sheets_list_cached(client) -> list:
    """
    Get list of sheets with caching.
    
    Caches the sheets list for CACHE_TTL_SECONDS to avoid repeated API calls
    when resolving sheet names or listing sheets.
    """
    global _cache
    
    with _cache_lock:
        if _cache["sheets_list"] and _is_cache_valid(_cache["sheets_list_fetched_at"]):
            return _cache["sheets_list"]
    
    # Fetch fresh data (outside lock to avoid blocking)
    response = client.Sheets.list_sheets(include_all=True)
    sheets_list = list(response.data)
    
    with _cache_lock:
        _cache["sheets_list"] = sheets_list
        _cache["sheets_list_fetched_at"] = time.time()
        
        # Also populate the name->id cache
        for sheet in sheets_list:
            _cache["sheet_name_to_id"][sheet.name.lower()] = (
                sheet.id, 
                sheet.name, 
                time.time()
            )
    
    return sheets_list


def _resolve_sheet_id(client, sheet_id: str) -> tuple[int, str]:
    """
    Resolve sheet ID from name if needed. Returns (id, name).
    
    Uses caching to avoid repeated API calls for name resolution.
    """
    # If it's already a numeric ID, return it
    if str(sheet_id).isdigit():
        return int(sheet_id), None
    
    sheet_name_lower = sheet_id.lower()
    
    # Check cache first
    with _cache_lock:
        if sheet_name_lower in _cache["sheet_name_to_id"]:
            cached_id, cached_name, fetched_at = _cache["sheet_name_to_id"][sheet_name_lower]
            if _is_cache_valid(fetched_at):
                return cached_id, cached_name
    
    # Fetch sheets list (uses its own cache)
    sheets_list = _get_sheets_list_cached(client)
    
    for sheet in sheets_list:
        if sheet.name.lower() == sheet_name_lower:
            return sheet.id, sheet.name
    
    return None, None


# =============================================================================
# CORE TOOLS (5 tools - unchanged)
# =============================================================================

def list_sheets(use_cache: bool = True) -> str:
    """
    List all Smartsheet sheets accessible to the user.

    Args:
        use_cache: If True (default), use cached sheet list. Set False to force fresh fetch.

    Returns a formatted list of all available sheets with their IDs and access levels.
    Results are filtered by allowed sheets if ALLOWED_SHEET_IDS or ALLOWED_SHEET_NAMES
    environment variables are configured.
    """
    try:
        client = get_smartsheet_client()
        
        # Use cached list or fetch fresh
        if use_cache:
            all_sheets = _get_sheets_list_cached(client)
        else:
            clear_cache()  # Clear cache if explicitly requesting fresh data
            response = client.Sheets.list_sheets(include_all=True)
            all_sheets = response.data

        sheets = []
        for sheet in all_sheets:
            if not _is_sheet_allowed(sheet.id, sheet.name):
                continue
            sheets.append({
                "id": sheet.id,
                "name": sheet.name,
                "access_level": sheet.access_level,
                "created_at": str(sheet.created_at) if sheet.created_at else None,
                "modified_at": str(sheet.modified_at) if sheet.modified_at else None,
            })

        if not sheets:
            return "No sheets available in the configured scope."

        result = f"Found {len(sheets)} sheets:\n"
        for s in sheets:
            result += f"- {s['name']} (ID: {s['id']}, Access: {s['access_level']})\n"

        return result
    except Exception as e:
        return f"Error listing sheets: {str(e)}"


def get_sheet(sheet_id: str) -> str:
    """
    Get detailed data from a specific Smartsheet.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name to retrieve.

    Returns all columns and rows from the sheet in a formatted text output.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id, page_size=5000)
        columns = {col.id: col.title for col in sheet.columns}
        column_list = [col.title for col in sheet.columns]

        rows_data = []
        for row in sheet.rows:
            row_dict = {"row_id": row.id, "row_number": row.row_number}
            for cell in row.cells:
                col_name = columns.get(cell.column_id, f"Column_{cell.column_id}")
                row_dict[col_name] = cell.display_value or cell.value
            rows_data.append(row_dict)

        text_output = f"Sheet: {sheet.name}\n"
        text_output += f"Total Rows: {len(rows_data)}\n"
        text_output += f"Columns: {', '.join(column_list)}\n\n"

        if rows_data:
            text_output += "Data:\n"
            for row in rows_data:
                row_str = " | ".join(
                    f"{k}: {v}" for k, v in row.items()
                    if k not in ("row_id", "row_number") and v is not None
                )
                text_output += f"  Row {row['row_number']}: {row_str}\n"

        return text_output
    except Exception as e:
        return f"Error getting sheet: {str(e)}"


def get_row(sheet_id: str, row_id: str) -> str:
    """
    Get detailed information about a specific row in a Smartsheet.

    Args:
        sheet_id: The ID of the sheet containing the row.
        row_id: The ID of the row to retrieve.

    Returns formatted row data with all cell values.
    """
    if not sheet_id or not row_id:
        return "Error: Both sheet_id and row_id parameters are required"

    try:
        client = get_smartsheet_client()
        sheet = client.Sheets.get_sheet(int(sheet_id))

        if not _is_sheet_allowed(sheet.id, sheet.name):
            return f"Error: Access to sheet '{sheet.name}' is not permitted."

        columns = {col.id: col.title for col in sheet.columns}
        row = client.Sheets.get_row(int(sheet_id), int(row_id))

        text_output = f"Row {row.row_number} from sheet '{sheet.name}':\n"
        for cell in row.cells:
            col_name = columns.get(cell.column_id, f"Column_{cell.column_id}")
            value = cell.display_value or cell.value
            if value is not None:
                text_output += f"  {col_name}: {value}\n"

        return text_output
    except Exception as e:
        return f"Error getting row: {str(e)}"


def filter_rows(sheet_id: str, column_name: str, filter_value: str, match_type: str = "contains") -> str:
    """
    Filter rows in a Smartsheet based on column values.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name to filter.
        column_name: The name of the column to filter on.
        filter_value: The value to filter for.
        match_type: Type of match - "contains", "equals", "starts_with", "ends_with" (default: "contains")

    Returns formatted list of rows matching the filter criteria.
    """
    if not sheet_id or not column_name or not filter_value:
        return "Error: sheet_id, column_name, and filter_value parameters are required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id, page_size=5000)

        target_column_id = None
        columns_map = {col.id: col.title for col in sheet.columns}
        for col in sheet.columns:
            if col.title.lower() == column_name.lower():
                target_column_id = col.id
                break

        if not target_column_id:
            available_cols = ", ".join([col.title for col in sheet.columns])
            return f"Error: Column '{column_name}' not found. Available columns: {available_cols}"

        matching_rows = []
        filter_value_lower = str(filter_value).lower()

        for row in sheet.rows:
            for cell in row.cells:
                if cell.column_id == target_column_id:
                    cell_value = str(cell.display_value or cell.value or "").lower()

                    match = False
                    if match_type == "equals":
                        match = cell_value == filter_value_lower
                    elif match_type == "starts_with":
                        match = cell_value.startswith(filter_value_lower)
                    elif match_type == "ends_with":
                        match = cell_value.endswith(filter_value_lower)
                    else:
                        match = filter_value_lower in cell_value

                    if match:
                        row_data = {"row_number": row.row_number, "row_id": row.id}
                        for c in row.cells:
                            col_name = columns_map.get(c.column_id, f"Col_{c.column_id}")
                            row_data[col_name] = c.display_value or c.value
                        matching_rows.append(row_data)
                    break

        text_output = f"Filter results for '{sheet.name}'\n"
        text_output += f"Filter: {column_name} {match_type} '{filter_value}'\n"
        text_output += f"Found: {len(matching_rows)} matching rows\n\n"

        if matching_rows:
            for row in matching_rows[:50]:
                row_str = " | ".join(
                    f"{k}: {v}" for k, v in row.items()
                    if k not in ("row_id",) and v is not None
                )
                text_output += f"  {row_str}\n"

            if len(matching_rows) > 50:
                text_output += f"\n  ... and {len(matching_rows) - 50} more rows"
        else:
            text_output += "  No matching rows found."

        return text_output
    except Exception as e:
        return f"Error filtering rows: {str(e)}"


def count_rows_by_column(sheet_id: str, column_name: str) -> str:
    """
    Count rows grouped by values in a specific column.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        column_name: The column to group and count by.

    Returns a breakdown showing count of rows for each unique value in the column.
    Useful for status breakdowns and analytics.
    """
    if not sheet_id or not column_name:
        return "Error: sheet_id and column_name parameters are required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id, page_size=5000)

        target_col_id = None
        for col in sheet.columns:
            if col.title.lower() == column_name.lower():
                target_col_id = col.id
                break

        if not target_col_id:
            available = ", ".join([col.title for col in sheet.columns])
            return f"Error: Column '{column_name}' not found. Available: {available}"

        counts = {}
        for row in sheet.rows:
            for cell in row.cells:
                if cell.column_id == target_col_id:
                    value = str(cell.display_value or cell.value or "(empty)")
                    counts[value] = counts.get(value, 0) + 1
                    break

        text_output = f"Row Count by '{column_name}' in '{sheet.name}'\n"
        text_output += "=" * 50 + "\n\n"
        text_output += f"Total rows: {len(sheet.rows)}\n\n"

        for value, count in sorted(counts.items(), key=lambda x: -x[1]):
            pct = (count / len(sheet.rows)) * 100 if sheet.rows else 0
            bar = "█" * int(pct / 5)
            text_output += f"  {value}: {count} ({pct:.1f}%) {bar}\n"

        return text_output
    except Exception as e:
        return f"Error counting rows: {str(e)}"


# =============================================================================
# UNIFIED RESOURCE TOOLS (7 tools - consolidated from 14 list/get pairs)
# =============================================================================

def workspace(workspace_id: str = None) -> str:
    """
    Get workspace(s). Lists all workspaces if no ID provided, or gets details for a specific workspace.

    Args:
        workspace_id: Optional workspace ID. If not provided, lists all workspaces.
                     If provided, returns details including sheets, folders, and reports in that workspace.

    Returns formatted workspace information.
    """
    try:
        client = get_smartsheet_client()

        if workspace_id:
            # Get specific workspace
            ws = client.Workspaces.get_workspace(int(workspace_id))

            text_output = f"Workspace: {ws.name}\n"
            text_output += "=" * 50 + "\n\n"

            if hasattr(ws, 'access_level'):
                text_output += f"Access Level: {ws.access_level}\n"
            if hasattr(ws, 'permalink') and ws.permalink:
                text_output += f"Permalink: {ws.permalink}\n"

            if hasattr(ws, 'sheets') and ws.sheets:
                allowed_sheets = [s for s in ws.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed_sheets:
                    text_output += f"\n**Sheets ({len(allowed_sheets)}):**\n"
                    for sheet in allowed_sheets:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"

            if hasattr(ws, 'folders') and ws.folders:
                text_output += f"\n**Folders ({len(ws.folders)}):**\n"
                for folder in ws.folders:
                    text_output += f"  - {folder.name} (ID: {folder.id})\n"

            if hasattr(ws, 'reports') and ws.reports:
                text_output += f"\n**Reports ({len(ws.reports)}):**\n"
                for report in ws.reports:
                    text_output += f"  - {report.name} (ID: {report.id})\n"

            return text_output
        else:
            # List all workspaces
            response = client.Workspaces.list_workspaces(include_all=True)

            if not response.data:
                return "No workspaces available."

            text_output = f"Found {len(response.data)} workspace(s):\n\n"
            for ws in response.data:
                text_output += f"- {ws.name} (ID: {ws.id}, Access: {getattr(ws, 'access_level', 'Unknown')})\n"

            return text_output
    except Exception as e:
        return f"Error with workspace: {str(e)}"


def folder(folder_id: str = None) -> str:
    """
    Get folder(s). Lists home-level folders if no ID provided, or gets details for a specific folder.

    Args:
        folder_id: Optional folder ID. If not provided, lists all home-level folders.
                  If provided, returns details including sheets, subfolders, and reports.

    Returns formatted folder information.
    """
    try:
        client = get_smartsheet_client()

        if folder_id:
            # Get specific folder
            f = client.Folders.get_folder(int(folder_id))

            text_output = f"Folder: {f.name}\n"
            text_output += "=" * 50 + "\n\n"

            if hasattr(f, 'permalink') and f.permalink:
                text_output += f"Permalink: {f.permalink}\n"

            if hasattr(f, 'sheets') and f.sheets:
                allowed_sheets = [s for s in f.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed_sheets:
                    text_output += f"\n**Sheets ({len(allowed_sheets)}):**\n"
                    for sheet in allowed_sheets:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"

            if hasattr(f, 'folders') and f.folders:
                text_output += f"\n**Subfolders ({len(f.folders)}):**\n"
                for subfolder in f.folders:
                    text_output += f"  - {subfolder.name} (ID: {subfolder.id})\n"

            if hasattr(f, 'reports') and f.reports:
                text_output += f"\n**Reports ({len(f.reports)}):**\n"
                for report in f.reports:
                    text_output += f"  - {report.name} (ID: {report.id})\n"

            return text_output
        else:
            # List home-level folders
            response = client.Home.list_folders(include_all=True)

            if not response.data:
                return "No folders available at home level."

            text_output = f"Found {len(response.data)} folder(s):\n\n"
            for f in response.data:
                text_output += f"- {f.name} (ID: {f.id})\n"

            return text_output
    except Exception as e:
        return f"Error with folder: {str(e)}"


def sight(sight_id: str = None) -> str:
    """
    Get Sight/dashboard(s). Lists all Sights if no ID provided, or gets details for a specific Sight.

    Args:
        sight_id: Optional Sight ID. If not provided, lists all available Sights/dashboards.
                 If provided, returns details including widgets and data sources.

    Returns formatted Sight/dashboard information.
    """
    try:
        client = get_smartsheet_client()

        if sight_id:
            # Get specific sight
            s = client.Sights.get_sight(int(sight_id))

            text_output = f"Sight: {s.name}\n"
            text_output += "=" * 50 + "\n\n"

            if hasattr(s, 'access_level'):
                text_output += f"Access Level: {s.access_level}\n"
            if hasattr(s, 'permalink') and s.permalink:
                text_output += f"Permalink: {s.permalink}\n"
            if hasattr(s, 'created_at') and s.created_at:
                text_output += f"Created: {s.created_at}\n"
            if hasattr(s, 'modified_at') and s.modified_at:
                text_output += f"Last Modified: {s.modified_at}\n"

            if hasattr(s, 'widgets') and s.widgets:
                text_output += f"\n**Widgets ({len(s.widgets)}):**\n"
                for widget in s.widgets:
                    widget_type = getattr(widget, 'type', 'Unknown')
                    title = getattr(widget, 'title', 'Untitled')
                    text_output += f"  - {title} (Type: {widget_type})\n"

                    if hasattr(widget, 'contents'):
                        contents = widget.contents
                        if hasattr(contents, 'sheet_id'):
                            text_output += f"    Source Sheet ID: {contents.sheet_id}\n"
                        if hasattr(contents, 'report_id'):
                            text_output += f"    Source Report ID: {contents.report_id}\n"

            return text_output
        else:
            # List all sights
            response = client.Sights.list_sights(include_all=True)

            if not response.data:
                return "No Sights (dashboards) available."

            text_output = f"Found {len(response.data)} Sight(s)/Dashboard(s):\n\n"
            for s in response.data:
                text_output += f"- {s.name} (ID: {s.id}, Access: {getattr(s, 'access_level', 'Unknown')})\n"
                if hasattr(s, 'modified_at') and s.modified_at:
                    text_output += f"    Last Modified: {s.modified_at}\n"

            return text_output
    except Exception as e:
        return f"Error with sight: {str(e)}"


def report(report_id: str = None) -> str:
    """
    Get report(s). Lists all reports if no ID provided, or gets data from a specific report.

    Args:
        report_id: Optional report ID. If not provided, lists all available reports.
                  If provided, returns the report data including all rows and columns.

    Returns formatted report information or data.
    """
    try:
        client = get_smartsheet_client()

        if report_id:
            # Get specific report
            r = client.Reports.get_report(int(report_id), page_size=5000)

            columns = {col.virtual_id: col.title for col in r.columns}
            column_list = [col.title for col in r.columns]

            rows_data = []
            for row in r.rows:
                row_dict = {"row_number": row.row_number}
                if hasattr(row, 'sheet_id'):
                    row_dict["source_sheet_id"] = row.sheet_id
                for cell in row.cells:
                    col_name = columns.get(cell.virtual_column_id, f"Column_{cell.virtual_column_id}")
                    row_dict[col_name] = cell.display_value or cell.value
                rows_data.append(row_dict)

            text_output = f"Report: {r.name}\n"
            text_output += f"Total Rows: {len(rows_data)}\n"
            text_output += f"Columns: {', '.join(column_list)}\n\n"

            if rows_data:
                text_output += "Data:\n"
                for row in rows_data[:100]:
                    row_str = " | ".join(
                        f"{k}: {v}" for k, v in row.items()
                        if k not in ("source_sheet_id",) and v is not None
                    )
                    text_output += f"  Row {row['row_number']}: {row_str}\n"

                if len(rows_data) > 100:
                    text_output += f"\n  ... and {len(rows_data) - 100} more rows"

            return text_output
        else:
            # List all reports
            response = client.Reports.list_reports(include_all=True)

            if not response.data:
                return "No reports available."

            text_output = f"Found {len(response.data)} reports:\n\n"
            for r in response.data:
                text_output += f"- {r.name} (ID: {r.id}, Access: {getattr(r, 'access_level', 'Unknown')})\n"

            return text_output
    except Exception as e:
        return f"Error with report: {str(e)}"


def webhook(webhook_id: str = None, include_all: bool = False) -> str:
    """
    Get webhook(s). Lists all webhooks if no ID provided, or gets details for a specific webhook.

    Args:
        webhook_id: Optional webhook ID. If not provided, lists all webhooks owned by the user.
                   If provided, returns detailed webhook information including status and statistics.
        include_all: If True and listing webhooks, include all results without pagination.

    Returns formatted webhook information.
    """
    try:
        client = get_smartsheet_client()

        if webhook_id:
            # Get specific webhook
            w = client.Webhooks.get_webhook(int(webhook_id))

            text_output = f"Webhook: {getattr(w, 'name', 'Unnamed')}\n"
            text_output += "=" * 50 + "\n\n"

            text_output += f"**ID:** {getattr(w, 'id', 'N/A')}\n"

            status = getattr(w, 'status', None)
            if status:
                text_output += f"**Status:** {status}\n"

            enabled = getattr(w, 'enabled', None)
            if enabled is not None:
                text_output += f"**Enabled:** {'Yes' if enabled else 'No'}\n"

            disabled_details = getattr(w, 'disabled_details', None)
            if disabled_details:
                text_output += f"**Disabled Reason:** {disabled_details}\n"

            text_output += "\n**Scope Information:**\n"
            scope = getattr(w, 'scope', None)
            if scope:
                text_output += f"  Scope: {scope}\n"
            scope_object_id = getattr(w, 'scope_object_id', None)
            if scope_object_id:
                text_output += f"  Scope Object ID: {scope_object_id}\n"

            callback_url = getattr(w, 'callback_url', None)
            if callback_url:
                text_output += f"\n**Callback URL:** {callback_url}\n"

            events = getattr(w, 'events', None)
            if events:
                text_output += f"**Events:** {', '.join(events)}\n"

            stats = getattr(w, 'stats', None)
            if stats:
                text_output += "\n**Statistics:**\n"
                last_callback = getattr(stats, 'last_callback_attempt', None)
                if last_callback:
                    text_output += f"  Last Callback Attempt: {last_callback}\n"
                last_success = getattr(stats, 'last_successful_callback', None)
                if last_success:
                    text_output += f"  Last Successful Callback: {last_success}\n"

            created_at = getattr(w, 'created_at', None)
            if created_at:
                text_output += f"\n**Created:** {created_at}\n"

            return text_output
        else:
            # List all webhooks
            if include_all:
                response = client.Webhooks.list_webhooks(include_all=True)
            else:
                response = client.Webhooks.list_webhooks(page_size=100)

            if not response.data:
                return "No webhooks found."

            text_output = f"Found {len(response.data)} webhook(s):\n\n"

            for i, w in enumerate(response.data, 1):
                name = getattr(w, 'name', 'Unnamed')
                webhook_id = getattr(w, 'id', 'N/A')
                status = getattr(w, 'status', 'Unknown')
                enabled = getattr(w, 'enabled', None)
                scope = getattr(w, 'scope', None)

                text_output += f"{i}. {name} (ID: {webhook_id})\n"
                text_output += f"   Status: {status}"
                if enabled is not None:
                    text_output += f", Enabled: {'Yes' if enabled else 'No'}"
                text_output += "\n"
                if scope:
                    text_output += f"   Scope: {scope}\n"

            return text_output
    except Exception as e:
        return f"Error with webhook: {str(e)}"


def group(group_id: str = None) -> str:
    """
    Get group(s). Lists all groups if no ID provided, or gets details for a specific group.

    Args:
        group_id: Optional group ID. If not provided, lists all groups in the organization.
                 If provided, returns group details including members.

    Returns formatted group information.
    """
    try:
        client = get_smartsheet_client()

        if group_id:
            # Get specific group
            g = client.Groups.get_group(int(group_id))

            text_output = f"Group: {g.name}\n"
            text_output += "=" * 50 + "\n\n"

            text_output += f"**ID:** {g.id}\n"
            if hasattr(g, 'description') and g.description:
                text_output += f"**Description:** {g.description}\n"
            if hasattr(g, 'owner') and g.owner:
                text_output += f"**Owner:** {g.owner}\n"
            if hasattr(g, 'created_at') and g.created_at:
                text_output += f"**Created:** {g.created_at}\n"
            if hasattr(g, 'modified_at') and g.modified_at:
                text_output += f"**Modified:** {g.modified_at}\n"

            if hasattr(g, 'members') and g.members:
                text_output += f"\n**Members ({len(g.members)}):**\n"
                for member in g.members:
                    email = getattr(member, 'email', 'Unknown')
                    name = getattr(member, 'name', '')
                    text_output += f"  - {email}"
                    if name:
                        text_output += f" ({name})"
                    text_output += "\n"
            else:
                text_output += "\nNo members in this group.\n"

            return text_output
        else:
            # List all groups
            response = client.Groups.list_groups(include_all=True)

            if not response.data:
                return "No groups found."

            text_output = f"Found {len(response.data)} group(s):\n\n"

            for g in response.data:
                text_output += f"- {g.name} (ID: {g.id})\n"
                if hasattr(g, 'description') and g.description:
                    text_output += f"    Description: {g.description}\n"
                if hasattr(g, 'member_count') and g.member_count is not None:
                    text_output += f"    Members: {g.member_count}\n"

            return text_output
    except Exception as e:
        return f"Error with group: {str(e)}"


def user(user_id: str = None, include_last_login: bool = True, max_results: int = 100) -> str:
    """
    Get user(s). Lists all organization users if no ID provided, or gets details for a specific user.
    Requires System Admin permissions.

    Args:
        user_id: Optional user ID (numeric) or email. If not provided, lists all organization users.
                If provided, returns detailed user profile information.
        include_last_login: Include last login timestamps when listing users. Default: True.
        max_results: Maximum users to return when listing (1-1000). Default: 100.

    Returns formatted user information.
    """
    try:
        client = get_smartsheet_client()

        if user_id:
            # Get specific user
            if '@' in str(user_id):
                response = client.Users.list_users(email=user_id)
                if response.data:
                    user_id = response.data[0].id
                else:
                    return f"Error: User with email '{user_id}' not found"

            u = client.Users.get_user(int(user_id))

            text_output = "User Profile\n"
            text_output += "=" * 50 + "\n\n"

            name = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
            text_output += f"**Name:** {name or 'N/A'}\n"
            text_output += f"**Email:** {getattr(u, 'email', 'N/A')}\n"
            text_output += f"**ID:** {getattr(u, 'id', 'N/A')}\n"

            status = getattr(u, 'status', None)
            if status:
                text_output += f"**Status:** {status}\n"

            account = getattr(u, 'account', None)
            if account:
                acc_name = getattr(account, 'name', 'N/A')
                text_output += f"**Account:** {acc_name}\n"

            text_output += "\n**Permissions:**\n"
            text_output += f"  System Admin: {'Yes' if getattr(u, 'admin', False) else 'No'}\n"
            text_output += f"  Licensed: {'Yes' if getattr(u, 'licensed_sheet_creator', False) else 'No'}\n"
            text_output += f"  Group Admin: {'Yes' if getattr(u, 'group_admin', False) else 'No'}\n"

            last_login = getattr(u, 'last_login', None)
            if last_login:
                text_output += f"\n**Last Login:** {last_login}\n"

            return text_output
        else:
            # List all users
            include_params = []
            if include_last_login:
                include_params.append('lastLogin')

            if include_params:
                response = client.Users.list_users(include=','.join(include_params), page_size=min(max_results, 1000))
            else:
                response = client.Users.list_users(page_size=min(max_results, 1000))

            if not response.data:
                return "No users found."

            text_output = f"Organization Users ({len(response.data)}):\n"
            text_output += "=" * 50 + "\n\n"

            for i, u in enumerate(response.data, 1):
                name = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip() or 'N/A'
                email = getattr(u, 'email', 'N/A')
                text_output += f"{i}. {name}\n"
                text_output += f"   Email: {email}\n"
                text_output += f"   ID: {getattr(u, 'id', 'N/A')}\n"

                status = getattr(u, 'status', None)
                if status:
                    text_output += f"   Status: {status}\n"

                last_login = getattr(u, 'last_login', None)
                if last_login:
                    text_output += f"   Last Login: {last_login}\n"

                text_output += "\n"

            total_pages = getattr(response, 'total_pages', 1)
            if total_pages > 1:
                text_output += f"\nShowing page 1 of {total_pages}. Use pagination for more results.\n"

            return text_output
    except Exception as e:
        error_str = str(e)
        if '1003' in error_str or 'not authorized' in error_str.lower():
            return "Error: You must be a System Admin to access user information."
        if '1006' in error_str or 'not found' in error_str.lower():
            return f"Error: User '{user_id}' not found."
        return f"Error with user: {error_str}"


# =============================================================================
# UNIFIED SCOPE TOOLS (2 tools - consolidated from 5)
# =============================================================================

def attachment(sheet_id: str, row_id: str = None, attachment_id: str = None) -> str:
    """
    Get attachments at various scopes. Can retrieve sheet-level, row-level, or specific attachment details.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        row_id: Optional row ID. If provided without attachment_id, returns row-level attachments.
        attachment_id: Optional attachment ID. If provided, returns specific attachment details with download URL.

    Scope hierarchy:
    - attachment(sheet_id) → All attachments in the sheet
    - attachment(sheet_id, row_id) → Attachments for a specific row
    - attachment(sheet_id, attachment_id=id) → Specific attachment with download URL
    - attachment(sheet_id, row_id, attachment_id) → Specific attachment (row context ignored)
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if attachment_id:
            # Get specific attachment
            att = client.Attachments.get_attachment(resolved_id, int(attachment_id))

            text_output = "Attachment Details\n"
            text_output += "=" * 50 + "\n\n"

            text_output += f"**Name:** {getattr(att, 'name', 'N/A')}\n"
            text_output += f"**ID:** {getattr(att, 'id', 'N/A')}\n"

            if hasattr(att, 'attachment_type') and att.attachment_type:
                text_output += f"**Type:** {att.attachment_type}\n"
            if hasattr(att, 'mime_type') and att.mime_type:
                text_output += f"**MIME Type:** {att.mime_type}\n"
            if hasattr(att, 'size_in_kb') and att.size_in_kb:
                text_output += f"**Size:** {att.size_in_kb} KB\n"
            if hasattr(att, 'parent_type') and att.parent_type:
                text_output += f"**Parent Type:** {att.parent_type}\n"
            if hasattr(att, 'parent_id') and att.parent_id:
                text_output += f"**Parent ID:** {att.parent_id}\n"
            if hasattr(att, 'created_at') and att.created_at:
                text_output += f"**Created:** {att.created_at}\n"
            if hasattr(att, 'created_by') and att.created_by:
                creator = att.created_by
                creator_name = getattr(creator, 'name', None) or getattr(creator, 'email', 'Unknown')
                text_output += f"**Created By:** {creator_name}\n"

            if hasattr(att, 'url') and att.url:
                text_output += f"\n**Download URL** (temporary):\n{att.url}\n"
                text_output += "\n⚠️ Note: This URL expires after a short time.\n"

            return text_output

        elif row_id:
            # Get row-level attachments
            attachments = client.Attachments.list_row_attachments(resolved_id, int(row_id), include_all=True)

            text_output = f"Attachments for Row {row_id} in '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if attachments.data:
                text_output += f"Found {len(attachments.data)} attachment(s):\n\n"
                for att in attachments.data:
                    text_output += f"- {att.name} (ID: {att.id})\n"
                    if hasattr(att, 'attachment_type') and att.attachment_type:
                        text_output += f"    Type: {att.attachment_type}\n"
                    if hasattr(att, 'size_in_kb') and att.size_in_kb:
                        text_output += f"    Size: {att.size_in_kb} KB\n"
            else:
                text_output += "No attachments found for this row.\n"

            return text_output

        else:
            # Get sheet-level attachments
            attachments = client.Attachments.list_all_attachments(resolved_id, include_all=True)

            text_output = f"Attachments for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if attachments.data:
                text_output += f"Found {len(attachments.data)} attachment(s):\n\n"
                for att in attachments.data:
                    text_output += f"- {att.name} (ID: {att.id})\n"
                    if hasattr(att, 'parent_type') and att.parent_type:
                        text_output += f"    Parent Type: {att.parent_type}\n"
                    if hasattr(att, 'parent_id') and att.parent_id:
                        text_output += f"    Parent ID: {att.parent_id}\n"
                    if hasattr(att, 'attachment_type') and att.attachment_type:
                        text_output += f"    Type: {att.attachment_type}\n"
                    if hasattr(att, 'size_in_kb') and att.size_in_kb:
                        text_output += f"    Size: {att.size_in_kb} KB\n"
            else:
                text_output += "No attachments found in this sheet.\n"

            return text_output
    except Exception as e:
        return f"Error with attachment: {str(e)}"


def discussion(sheet_id: str, row_id: str = None) -> str:
    """
    Get discussions (comments) at various scopes. Can retrieve sheet-level or row-level discussions.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        row_id: Optional row ID. If provided, returns discussions for that specific row.
               If not provided, returns all discussions in the sheet.

    Returns formatted list of discussions with comments.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if row_id:
            # Get row-level discussions
            discussions = client.Discussions.get_row_discussions(resolved_id, int(row_id), include_all=True)
            text_output = f"Discussions for Row {row_id} in '{sheet.name}':\n"
        else:
            # Get sheet-level discussions
            discussions = client.Discussions.get_all_discussions(resolved_id, include_all=True)
            text_output = f"Discussions for '{sheet.name}':\n"

        text_output += "=" * 50 + "\n\n"

        if discussions.data:
            text_output += f"Found {len(discussions.data)} discussion(s):\n\n"
            for disc in discussions.data:
                text_output += f"Discussion (ID: {disc.id})\n"
                if hasattr(disc, 'title') and disc.title:
                    text_output += f"  Title: {disc.title}\n"
                if hasattr(disc, 'parent_type') and disc.parent_type:
                    text_output += f"  On: {disc.parent_type}\n"
                if hasattr(disc, 'parent_id') and disc.parent_id:
                    text_output += f"  Parent ID: {disc.parent_id}\n"
                if hasattr(disc, 'created_at') and disc.created_at:
                    text_output += f"  Started: {disc.created_at}\n"

                if hasattr(disc, 'comments') and disc.comments:
                    text_output += f"  Comments ({len(disc.comments)}):\n"
                    for comment in disc.comments[:5]:  # Show first 5 comments
                        author = "Unknown"
                        if hasattr(comment, 'created_by') and comment.created_by:
                            author = getattr(comment.created_by, 'name', None) or getattr(comment.created_by, 'email', 'Unknown')
                        text_output += f"    - {author}: {comment.text[:100]}{'...' if len(comment.text) > 100 else ''}\n"
                    if len(disc.comments) > 5:
                        text_output += f"    ... and {len(disc.comments) - 5} more comments\n"
                text_output += "\n"
        else:
            text_output += "No discussions found.\n"

        return text_output
    except Exception as e:
        return f"Error with discussion: {str(e)}"


# =============================================================================
# UNIFIED SEARCH (1 tool - consolidated from 2)
# =============================================================================

def search(query: str, sheet_id: str = None) -> str:
    """
    Search for text in Smartsheet. Can search globally or within a specific sheet.

    Args:
        query: The search text to find.
        sheet_id: Optional sheet ID or name. If provided, searches only within that sheet.
                 If not provided, searches across all accessible sheets.

    Returns formatted search results with locations.
    """
    if not query:
        return "Error: query parameter is required"

    try:
        client = get_smartsheet_client()

        if sheet_id:
            # Search within specific sheet
            resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

            if not resolved_id:
                return f"Error: Sheet '{sheet_id}' not found"

            if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
                return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

            results = client.Search.search_sheet(resolved_id, query)

            text_output = f"Search results for '{query}' in sheet:\n"
            text_output += "=" * 50 + "\n\n"

            if not results.results:
                text_output += f"No results found for '{query}' in this sheet.\n"
                return text_output

            text_output += f"Found {results.total_count} result(s):\n\n"

            for i, result in enumerate(results.results, 1):
                text = getattr(result, 'text', 'N/A')
                obj_type = getattr(result, 'object_type', 'Unknown')
                text_output += f"{i}. {obj_type}: {text}\n"

                context_data = getattr(result, 'context_data', None)
                if context_data:
                    for ctx in context_data:
                        ctx_type = getattr(ctx, 'object_type', '')
                        ctx_id = getattr(ctx, 'object_id', '')
                        ctx_name = getattr(ctx, 'name', '')
                        if ctx_type or ctx_id:
                            text_output += f"   Context: {ctx_type}"
                            if ctx_name:
                                text_output += f" - {ctx_name}"
                            if ctx_id:
                                text_output += f" (ID: {ctx_id})"
                            text_output += "\n"
                text_output += "\n"

            return text_output
        else:
            # Global search
            response = client.Search.search(query)

            results = []
            for result in response.results:
                parent_id = getattr(result, 'parent_object_id', None)
                parent_name = getattr(result, 'parent_object_name', None)

                if not _is_sheet_allowed(parent_id, parent_name):
                    continue

                results.append({
                    "text": result.text,
                    "object_type": result.object_type,
                    "object_id": result.object_id,
                    "parent_object_name": parent_name,
                    "parent_object_id": parent_id,
                })

            text_output = f"Search results for '{query}':\n"
            text_output += "=" * 50 + "\n\n"

            if results:
                for r in results[:20]:
                    text_output += f"- {r['text']} ({r['object_type']})\n"
                    if r.get('parent_object_name'):
                        text_output += f"    In: {r['parent_object_name']}\n"
                if len(results) > 20:
                    text_output += f"\n  ... and {len(results) - 20} more results"
            else:
                text_output += "No results found in allowed sheets."

            return text_output
    except Exception as e:
        return f"Error searching: {str(e)}"


# =============================================================================
# UNIFIED NAVIGATION (1 tool - consolidated from 3)
# =============================================================================

def navigation(view: Literal["home", "favorites", "templates"] = "home") -> str:
    """
    Access navigation views: home, favorites, or templates.

    Args:
        view: The view to display. Options:
            - "home": Overview of user's Smartsheet home (sheets, folders, workspaces)
            - "favorites": User's favorited items
            - "templates": Available templates

    Returns formatted view of the requested navigation area.
    """
    try:
        client = get_smartsheet_client()

        if view == "favorites":
            response = client.Favorites.list_favorites(include_all=True)

            favorites = {
                "sheets": [], "folders": [], "reports": [],
                "templates": [], "workspaces": [], "sights": [],
            }

            for fav in response.data:
                obj_type = getattr(fav, 'type', 'unknown').lower()
                obj_id = getattr(fav, 'object_id', None)

                fav_info = {"id": obj_id, "type": obj_type}

                if obj_type == 'sheet':
                    favorites["sheets"].append(fav_info)
                elif obj_type == 'folder':
                    favorites["folders"].append(fav_info)
                elif obj_type == 'report':
                    favorites["reports"].append(fav_info)
                elif obj_type == 'template':
                    favorites["templates"].append(fav_info)
                elif obj_type == 'workspace':
                    favorites["workspaces"].append(fav_info)
                elif obj_type == 'sight':
                    favorites["sights"].append(fav_info)

            total = sum(len(v) for v in favorites.values())

            if total == 0:
                return "No favorites found."

            text_output = f"Your Favorites ({total} items)\n"
            text_output += "=" * 50 + "\n\n"

            if favorites["sheets"]:
                text_output += f"**Sheets ({len(favorites['sheets'])}):**\n"
                for f in favorites["sheets"]:
                    text_output += f"  - ID: {f['id']}\n"

            if favorites["reports"]:
                text_output += f"\n**Reports ({len(favorites['reports'])}):**\n"
                for f in favorites["reports"]:
                    text_output += f"  - ID: {f['id']}\n"

            if favorites["sights"]:
                text_output += f"\n**Sights/Dashboards ({len(favorites['sights'])}):**\n"
                for f in favorites["sights"]:
                    text_output += f"  - ID: {f['id']}\n"

            if favorites["folders"]:
                text_output += f"\n**Folders ({len(favorites['folders'])}):**\n"
                for f in favorites["folders"]:
                    text_output += f"  - ID: {f['id']}\n"

            if favorites["workspaces"]:
                text_output += f"\n**Workspaces ({len(favorites['workspaces'])}):**\n"
                for f in favorites["workspaces"]:
                    text_output += f"  - ID: {f['id']}\n"

            if favorites["templates"]:
                text_output += f"\n**Templates ({len(favorites['templates'])}):**\n"
                for f in favorites["templates"]:
                    text_output += f"  - ID: {f['id']}\n"

            return text_output

        elif view == "templates":
            home = client.Home.list_all_contents()

            text_output = "Available Templates\n"
            text_output += "=" * 50 + "\n\n"

            if hasattr(home, 'templates') and home.templates:
                text_output += f"**Your Templates ({len(home.templates)}):**\n"
                for tmpl in home.templates:
                    text_output += f"  - {tmpl.name} (ID: {tmpl.id})\n"
                    if hasattr(tmpl, 'description') and tmpl.description:
                        text_output += f"    Description: {tmpl.description}\n"
                    if hasattr(tmpl, 'access_level') and tmpl.access_level:
                        text_output += f"    Access: {tmpl.access_level}\n"
            else:
                text_output += "No templates found.\n"

            return text_output

        else:  # home
            home = client.Home.list_all_contents()

            text_output = "Your Smartsheet Home\n"
            text_output += "=" * 50 + "\n\n"

            if hasattr(home, 'sheets') and home.sheets:
                allowed_sheets = [s for s in home.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed_sheets:
                    text_output += f"**Sheets ({len(allowed_sheets)}):**\n"
                    for sheet in allowed_sheets[:20]:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"
                    if len(allowed_sheets) > 20:
                        text_output += f"  ... and {len(allowed_sheets) - 20} more\n"

            if hasattr(home, 'folders') and home.folders:
                text_output += f"\n**Folders ({len(home.folders)}):**\n"
                for folder in home.folders[:20]:
                    text_output += f"  - {folder.name} (ID: {folder.id})\n"
                if len(home.folders) > 20:
                    text_output += f"  ... and {len(home.folders) - 20} more\n"

            if hasattr(home, 'workspaces') and home.workspaces:
                text_output += f"\n**Workspaces ({len(home.workspaces)}):**\n"
                for ws in home.workspaces[:20]:
                    text_output += f"  - {ws.name} (ID: {ws.id})\n"
                if len(home.workspaces) > 20:
                    text_output += f"  ... and {len(home.workspaces) - 20} more\n"

            if hasattr(home, 'reports') and home.reports:
                text_output += f"\n**Reports ({len(home.reports)}):**\n"
                for report in home.reports[:20]:
                    text_output += f"  - {report.name} (ID: {report.id})\n"
                if len(home.reports) > 20:
                    text_output += f"  ... and {len(home.reports) - 20} more\n"

            if hasattr(home, 'sights') and home.sights:
                text_output += f"\n**Sights/Dashboards ({len(home.sights)}):**\n"
                for sight in home.sights[:20]:
                    text_output += f"  - {sight.name} (ID: {sight.id})\n"
                if len(home.sights) > 20:
                    text_output += f"  ... and {len(home.sights) - 20} more\n"

            return text_output
    except Exception as e:
        return f"Error with navigation: {str(e)}"


# =============================================================================
# UNIFIED SHEET METADATA (1 tool - consolidated from 5)
# =============================================================================

def sheet_metadata(sheet_id: str, info: Literal["automation", "shares", "publish", "proofs", "references"]) -> str:
    """
    Get various metadata about a sheet: automation rules, shares, publish status, proofs, or cross-references.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        info: Type of metadata to retrieve. Options:
            - "automation": Automation rules configured for the sheet
            - "shares": Sharing info (who has access and at what level)
            - "publish": Publish status and URLs
            - "proofs": Proofs in the sheet
            - "references": Cross-sheet references

    Returns formatted metadata for the requested type.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if info == "automation":
            rules = client.Sheets.list_automation_rules(resolved_id, include_all=True)

            text_output = f"Automation Rules for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if rules.data:
                text_output += f"Found {len(rules.data)} automation rule(s):\n\n"
                for rule in rules.data:
                    status = "Enabled" if getattr(rule, 'enabled', False) else "Disabled"
                    text_output += f"**{getattr(rule, 'name', 'Unnamed Rule')}** (ID: {rule.id})\n"
                    text_output += f"  Status: {status}\n"

                    if hasattr(rule, 'action') and rule.action:
                        action_type = getattr(rule.action, 'type', 'Unknown')
                        text_output += f"  Type: {action_type}\n"
                        if hasattr(rule.action, 'frequency'):
                            text_output += f"  Frequency: {rule.action.frequency}\n"

                    if hasattr(rule, 'disabled_reason_text') and rule.disabled_reason_text:
                        text_output += f"  Disabled Reason: {rule.disabled_reason_text}\n"

                    can_modify = getattr(rule, 'user_can_modify', False)
                    text_output += f"  Can Modify: {'Yes' if can_modify else 'No'}\n\n"
            else:
                text_output += "No automation rules found for this sheet.\n"

            return text_output

        elif info == "shares":
            shares = client.Sheets.list_shares(resolved_id, include_all=True)

            text_output = f"Sharing Info for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if shares.data:
                text_output += f"Shared with {len(shares.data)} user(s)/group(s):\n\n"

                for share in shares.data:
                    share_type = getattr(share, 'type', 'USER')
                    email = getattr(share, 'email', '')
                    name = getattr(share, 'name', '')
                    access_level = getattr(share, 'access_level', 'Unknown')

                    if share_type == 'GROUP':
                        text_output += f"**Group**: {name or email}\n"
                    else:
                        text_output += f"**{email}**"
                        if name:
                            text_output += f" ({name})"
                        text_output += "\n"

                    text_output += f"   Access Level: {access_level}\n"
                    if hasattr(share, 'created_at') and share.created_at:
                        text_output += f"   Shared On: {share.created_at}\n"
                    text_output += "\n"
            else:
                text_output += "No shares found for this sheet.\n"

            return text_output

        elif info == "publish":
            publish = client.Sheets.get_publish_status(resolved_id)

            text_output = f"Publish Status for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            read_only = getattr(publish, 'read_only_lite_enabled', False)
            read_write = getattr(publish, 'read_write_enabled', False)
            ical = getattr(publish, 'ical_enabled', False)

            if not read_only and not read_write and not ical:
                text_output += "This sheet is not published.\n"
            else:
                if read_only:
                    text_output += "**Read-Only Published:** Yes\n"
                    if hasattr(publish, 'read_only_lite_url') and publish.read_only_lite_url:
                        text_output += f"  URL: {publish.read_only_lite_url}\n"

                if read_write:
                    text_output += "**Read-Write Published:** Yes\n"
                    if hasattr(publish, 'read_write_url') and publish.read_write_url:
                        text_output += f"  URL: {publish.read_write_url}\n"

                if ical:
                    text_output += "**iCal Published:** Yes\n"
                    if hasattr(publish, 'ical_url') and publish.ical_url:
                        text_output += f"  URL: {publish.ical_url}\n"

            return text_output

        elif info == "proofs":
            proofs = client.Proofs.list_proofs(resolved_id, include_all=True)

            text_output = f"Proofs in '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if proofs.data:
                text_output += f"Found {len(proofs.data)} proof(s):\n\n"
                for proof in proofs.data:
                    text_output += f"**Proof ID: {proof.id}**\n"
                    if hasattr(proof, 'name') and proof.name:
                        text_output += f"  Name: {proof.name}\n"
                    if hasattr(proof, 'row_id') and proof.row_id:
                        text_output += f"  Row ID: {proof.row_id}\n"
                    if hasattr(proof, 'version') and proof.version:
                        text_output += f"  Version: {proof.version}\n"
                    if hasattr(proof, 'created_at') and proof.created_at:
                        text_output += f"  Created: {proof.created_at}\n"
                    text_output += "\n"
            else:
                text_output += "No proofs found in this sheet.\n"

            return text_output

        elif info == "references":
            refs = client.Sheets.list_cross_sheet_references(resolved_id, include_all=True)

            text_output = f"Cross-Sheet References for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if refs.data:
                text_output += f"Found {len(refs.data)} cross-sheet reference(s):\n\n"
                for ref in refs.data:
                    text_output += f"**{ref.name}** (ID: {ref.id})\n"
                    if hasattr(ref, 'source_sheet_id') and ref.source_sheet_id:
                        text_output += f"   Source Sheet ID: {ref.source_sheet_id}\n"
                    if hasattr(ref, 'status') and ref.status:
                        text_output += f"   Status: {ref.status}\n"
                    text_output += "\n"
            else:
                text_output += "No cross-sheet references found in this sheet.\n"

            return text_output
        else:
            return f"Error: Unknown info type '{info}'. Valid options: automation, shares, publish, proofs, references"
    except Exception as e:
        return f"Error getting sheet metadata: {str(e)}"


# =============================================================================
# UNIFIED SHEET INFO (1 tool - consolidated from 4)
# =============================================================================

def sheet_info(sheet_id: str, info: Literal["columns", "stats", "summary_fields", "by_column"], columns: str = None) -> str:
    """
    Get various information about a sheet: column metadata, statistics, summary fields, or specific columns.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        info: Type of information to retrieve. Options:
            - "columns": Detailed column metadata (types, options, formulas)
            - "stats": Sheet statistics (row counts, fill rates, metadata)
            - "summary_fields": Summary fields (KPIs/metadata at sheet level)
            - "by_column": Get specific columns only (requires columns parameter)
        columns: Comma-separated list of column names (required only for "by_column")

    Returns formatted information for the requested type.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"
    if info == "by_column" and not columns:
        return "Error: columns parameter is required when info='by_column'"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        if info == "columns":
            sheet = client.Sheets.get_sheet(resolved_id)

            text_output = f"Columns for '{sheet.name}':\n"
            text_output += f"Total columns: {len(sheet.columns)}\n\n"

            for col in sheet.columns:
                text_output += f"**{col.title}** (ID: {col.id})\n"
                text_output += f"    Type: {col.type}\n"
                if col.primary:
                    text_output += "    Primary: Yes\n"
                if hasattr(col, 'options') and col.options:
                    text_output += f"    Options: {', '.join(col.options)}\n"
                if hasattr(col, 'symbol') and col.symbol:
                    text_output += f"    Symbol: {col.symbol}\n"
                if hasattr(col, 'system_column_type') and col.system_column_type:
                    text_output += f"    System Type: {col.system_column_type}\n"
                if hasattr(col, 'validation') and col.validation:
                    text_output += "    Has Validation: Yes\n"
                if hasattr(col, 'formula') and col.formula:
                    text_output += f"    Formula: {col.formula}\n"
                text_output += "\n"

            return text_output

        elif info == "stats":
            sheet = client.Sheets.get_sheet(resolved_id, page_size=5000)

            total_rows = len(sheet.rows)
            total_columns = len(sheet.columns)

            column_types = {}
            for col in sheet.columns:
                col_type = col.type
                column_types[col_type] = column_types.get(col_type, 0) + 1

            non_empty_cells = 0
            column_fill_rates = {}
            columns_map = {col.id: col.title for col in sheet.columns}

            for col in sheet.columns:
                column_fill_rates[col.title] = 0

            for row in sheet.rows:
                for cell in row.cells:
                    if cell.value is not None and cell.value != "":
                        non_empty_cells += 1
                        col_name = columns_map.get(cell.column_id, "Unknown")
                        column_fill_rates[col_name] = column_fill_rates.get(col_name, 0) + 1

            text_output = f"Summary for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            text_output += "**Basic Stats:**\n"
            text_output += f"  - Total Rows: {total_rows}\n"
            text_output += f"  - Total Columns: {total_columns}\n"
            text_output += f"  - Total Cells: {total_rows * total_columns}\n"
            text_output += f"  - Non-empty Cells: {non_empty_cells}\n"
            if total_rows * total_columns > 0:
                fill_rate = (non_empty_cells / (total_rows * total_columns)) * 100
                text_output += f"  - Fill Rate: {fill_rate:.1f}%\n"

            text_output += "\n**Column Types:**\n"
            for col_type, count in sorted(column_types.items()):
                text_output += f"  - {col_type}: {count}\n"

            text_output += "\n**Column Fill Rates:**\n"
            for col_name, filled in sorted(column_fill_rates.items(), key=lambda x: -x[1]):
                if total_rows > 0:
                    rate = (filled / total_rows) * 100
                    text_output += f"  - {col_name}: {rate:.0f}% ({filled}/{total_rows})\n"

            text_output += "\n**Metadata:**\n"
            if hasattr(sheet, 'created_at') and sheet.created_at:
                text_output += f"  - Created: {sheet.created_at}\n"
            if hasattr(sheet, 'modified_at') and sheet.modified_at:
                text_output += f"  - Last Modified: {sheet.modified_at}\n"
            if hasattr(sheet, 'owner') and sheet.owner:
                text_output += f"  - Owner: {sheet.owner}\n"
            if hasattr(sheet, 'permalink') and sheet.permalink:
                text_output += f"  - Permalink: {sheet.permalink}\n"

            return text_output

        elif info == "summary_fields":
            summary = client.Sheets.get_sheet_summary_fields(resolved_id, include_all=True)
            sheet = client.Sheets.get_sheet(resolved_id)

            text_output = f"Summary Fields for '{sheet.name}':\n"
            text_output += "=" * 50 + "\n\n"

            if summary.data:
                text_output += f"Found {len(summary.data)} summary field(s):\n\n"
                for field in summary.data:
                    title = getattr(field, 'title', 'Untitled')
                    text_output += f"**{title}**\n"

                    if hasattr(field, 'display_value') and field.display_value:
                        text_output += f"  Value: {field.display_value}\n"
                    elif hasattr(field, 'object_value') and field.object_value:
                        text_output += f"  Value: {field.object_value}\n"

                    if hasattr(field, 'type') and field.type:
                        text_output += f"  Type: {field.type}\n"
                    if hasattr(field, 'formula') and field.formula:
                        text_output += f"  Formula: {field.formula}\n"
                    if hasattr(field, 'locked') and field.locked:
                        text_output += f"  Locked: Yes\n"
                    text_output += "\n"
            else:
                text_output += "No summary fields found on this sheet.\n"
                text_output += "\nTip: Summary fields are defined at the top of a sheet and contain key metadata or KPIs.\n"

            return text_output

        elif info == "by_column":
            sheet = client.Sheets.get_sheet(resolved_id, page_size=5000)

            requested_cols = [c.strip().lower() for c in columns.split(",")]

            columns_map = {}
            target_column_ids = set()
            for col in sheet.columns:
                columns_map[col.id] = col.title
                if col.title.lower() in requested_cols:
                    target_column_ids.add(col.id)

            if not target_column_ids:
                available = ", ".join([col.title for col in sheet.columns])
                return f"Error: None of the requested columns found. Available: {available}"

            text_output = f"Sheet: {sheet.name}\n"
            text_output += f"Columns: {', '.join([columns_map[cid] for cid in target_column_ids])}\n"
            text_output += f"Total Rows: {len(sheet.rows)}\n\n"

            for row in sheet.rows:
                row_values = []
                for cell in row.cells:
                    if cell.column_id in target_column_ids:
                        col_name = columns_map[cell.column_id]
                        value = cell.display_value or cell.value
                        if value is not None:
                            row_values.append(f"{col_name}: {value}")

                if row_values:
                    text_output += f"  Row {row.row_number}: {' | '.join(row_values)}\n"

            return text_output
        else:
            return f"Error: Unknown info type '{info}'. Valid options: columns, stats, summary_fields, by_column"
    except Exception as e:
        return f"Error getting sheet info: {str(e)}"


# =============================================================================
# UNIFIED UPDATE REQUESTS (1 tool - consolidated from 2)
# =============================================================================

def update_requests(sheet_id: str, sent: bool = False) -> str:
    """
    Get update requests for a sheet. Can retrieve pending or sent update requests.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        sent: If False (default), returns pending update requests.
             If True, returns sent update requests with their status.

    Returns formatted list of update requests.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if sent:
            requests = client.Sheets.list_sent_update_requests(resolved_id, include_all=True)
            text_output = f"Sent Update Requests for '{sheet.name}':\n"
        else:
            requests = client.Sheets.list_update_requests(resolved_id, include_all=True)
            text_output = f"Update Requests for '{sheet.name}':\n"

        text_output += "=" * 50 + "\n\n"

        if requests.data:
            text_output += f"Found {len(requests.data)} update request(s):\n\n"
            for req in requests.data:
                text_output += f"**Request ID: {req.id}**\n"

                if sent:
                    if hasattr(req, 'sent_at') and req.sent_at:
                        text_output += f"  Sent At: {req.sent_at}\n"
                    if hasattr(req, 'sent_to') and req.sent_to:
                        email = getattr(req.sent_to, 'email', str(req.sent_to))
                        text_output += f"  Sent To: {email}\n"
                    if hasattr(req, 'status') and req.status:
                        text_output += f"  Status: {req.status}\n"
                else:
                    if hasattr(req, 'subject') and req.subject:
                        text_output += f"  Subject: {req.subject}\n"
                    if hasattr(req, 'message') and req.message:
                        msg = req.message[:100] + '...' if len(req.message) > 100 else req.message
                        text_output += f"  Message: {msg}\n"
                    if hasattr(req, 'send_to') and req.send_to:
                        recipients = [getattr(r, 'email', str(r)) for r in req.send_to]
                        text_output += f"  Recipients: {', '.join(recipients[:5])}\n"
                        if len(recipients) > 5:
                            text_output += f"    ... and {len(recipients) - 5} more\n"
                    if hasattr(req, 'schedule') and req.schedule:
                        text_output += f"  Scheduled: Yes\n"
                    if hasattr(req, 'created_at') and req.created_at:
                        text_output += f"  Created: {req.created_at}\n"

                text_output += "\n"
        else:
            text_output += f"No {'sent ' if sent else ''}update requests found for this sheet.\n"

        return text_output
    except Exception as e:
        return f"Error getting update requests: {str(e)}"


# =============================================================================
# STANDALONE TOOLS (9 tools - unique purposes, unchanged)
# =============================================================================

def compare_sheets(sheet_id_1: str, sheet_id_2: str, key_column: str) -> str:
    """
    Compare two sheets by a key column to find differences.

    Args:
        sheet_id_1: The first sheet ID or name.
        sheet_id_2: The second sheet ID or name.
        key_column: The column name to use as the comparison key.

    Returns summary of differences between the two sheets.
    """
    if not sheet_id_1 or not sheet_id_2 or not key_column:
        return "Error: sheet_id_1, sheet_id_2, and key_column parameters are required"

    try:
        client = get_smartsheet_client()

        # Resolve both sheets
        id1, name1 = _resolve_sheet_id(client, sheet_id_1)
        id2, name2 = _resolve_sheet_id(client, sheet_id_2)

        if not id1:
            return f"Error: Sheet '{sheet_id_1}' not found"
        if not id2:
            return f"Error: Sheet '{sheet_id_2}' not found"

        if not _is_sheet_allowed(id1, name1):
            return f"Error: Access to sheet '{name1 or id1}' is not permitted."
        if not _is_sheet_allowed(id2, name2):
            return f"Error: Access to sheet '{name2 or id2}' is not permitted."

        sheet1 = client.Sheets.get_sheet(id1, page_size=5000)
        sheet2 = client.Sheets.get_sheet(id2, page_size=5000)

        def get_column_id(sheet, col_name):
            for col in sheet.columns:
                if col.title.lower() == col_name.lower():
                    return col.id
            return None

        key_col_id_1 = get_column_id(sheet1, key_column)
        key_col_id_2 = get_column_id(sheet2, key_column)

        if not key_col_id_1:
            return f"Error: Column '{key_column}' not found in sheet '{sheet1.name}'"
        if not key_col_id_2:
            return f"Error: Column '{key_column}' not found in sheet '{sheet2.name}'"

        def build_key_map(sheet, key_col_id):
            key_map = {}
            for row in sheet.rows:
                for cell in row.cells:
                    if cell.column_id == key_col_id:
                        key = str(cell.display_value or cell.value or "")
                        if key:
                            key_map[key] = row.row_number
                        break
            return key_map

        keys1 = build_key_map(sheet1, key_col_id_1)
        keys2 = build_key_map(sheet2, key_col_id_2)

        only_in_1 = set(keys1.keys()) - set(keys2.keys())
        only_in_2 = set(keys2.keys()) - set(keys1.keys())
        in_both = set(keys1.keys()) & set(keys2.keys())

        text_output = f"Sheet Comparison\n"
        text_output += "=" * 50 + "\n\n"
        text_output += f"Sheet 1: {sheet1.name} ({len(keys1)} rows)\n"
        text_output += f"Sheet 2: {sheet2.name} ({len(keys2)} rows)\n"
        text_output += f"Key Column: {key_column}\n\n"

        text_output += f"**Summary:**\n"
        text_output += f"  - Common keys: {len(in_both)}\n"
        text_output += f"  - Only in '{sheet1.name}': {len(only_in_1)}\n"
        text_output += f"  - Only in '{sheet2.name}': {len(only_in_2)}\n\n"

        if only_in_1:
            text_output += f"**Keys only in '{sheet1.name}':**\n"
            for key in list(only_in_1)[:20]:
                text_output += f"  - {key} (Row {keys1[key]})\n"
            if len(only_in_1) > 20:
                text_output += f"  ... and {len(only_in_1) - 20} more\n"
            text_output += "\n"

        if only_in_2:
            text_output += f"**Keys only in '{sheet2.name}':**\n"
            for key in list(only_in_2)[:20]:
                text_output += f"  - {key} (Row {keys2[key]})\n"
            if len(only_in_2) > 20:
                text_output += f"  ... and {len(only_in_2) - 20} more\n"

        return text_output
    except Exception as e:
        return f"Error comparing sheets: {str(e)}"


def get_cell_history(sheet_id: str, row_id: str, column_id: str) -> str:
    """
    Get the revision history for a specific cell in a Smartsheet.

    Args:
        sheet_id: The ID of the sheet containing the cell.
        row_id: The ID of the row containing the cell.
        column_id: The ID of the column containing the cell.

    Returns formatted history of changes to the cell including who changed it and when.
    """
    if not sheet_id or not row_id or not column_id:
        return "Error: sheet_id, row_id, and column_id parameters are required"

    try:
        client = get_smartsheet_client()

        sheet = client.Sheets.get_sheet(int(sheet_id))
        if not _is_sheet_allowed(sheet.id, sheet.name):
            return f"Error: Access to sheet '{sheet.name}' is not permitted."

        col_name = "Unknown"
        for col in sheet.columns:
            if col.id == int(column_id):
                col_name = col.title
                break

        history = client.Cells.get_cell_history(int(sheet_id), int(row_id), int(column_id), include_all=True)

        text_output = f"Cell History for '{col_name}' in sheet '{sheet.name}':\n"
        text_output += "=" * 50 + "\n\n"

        if history.data:
            for entry in history.data:
                text_output += f"**Value:** {entry.display_value or entry.value or '(empty)'}\n"
                if hasattr(entry, 'modified_at') and entry.modified_at:
                    text_output += f"   Modified: {entry.modified_at}\n"
                if hasattr(entry, 'modified_by') and entry.modified_by:
                    modifier = entry.modified_by
                    name = getattr(modifier, 'name', None) or getattr(modifier, 'email', 'Unknown')
                    text_output += f"   By: {name}\n"
                text_output += "\n"
        else:
            text_output += "No history available for this cell.\n"

        return text_output
    except Exception as e:
        return f"Error getting cell history: {str(e)}"


def get_sheet_version(sheet_id: str) -> str:
    """
    Get version information for a Smartsheet.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.

    Returns sheet version and modification info.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        text_output = f"Version Info for '{sheet.name}':\n"
        text_output += "=" * 50 + "\n\n"

        if hasattr(sheet, 'version') and sheet.version:
            text_output += f"Version: {sheet.version}\n"
        if hasattr(sheet, 'created_at') and sheet.created_at:
            text_output += f"Created: {sheet.created_at}\n"
        if hasattr(sheet, 'modified_at') and sheet.modified_at:
            text_output += f"Last Modified: {sheet.modified_at}\n"
        if hasattr(sheet, 'owner') and sheet.owner:
            text_output += f"Owner: {sheet.owner}\n"
        if hasattr(sheet, 'owner_id') and sheet.owner_id:
            text_output += f"Owner ID: {sheet.owner_id}\n"
        if hasattr(sheet, 'total_row_count') and sheet.total_row_count is not None:
            text_output += f"Total Rows: {sheet.total_row_count}\n"
        if hasattr(sheet, 'access_level') and sheet.access_level:
            text_output += f"Your Access Level: {sheet.access_level}\n"
        if hasattr(sheet, 'permalink') and sheet.permalink:
            text_output += f"Permalink: {sheet.permalink}\n"

        return text_output
    except Exception as e:
        return f"Error getting sheet version: {str(e)}"


def get_events(since: str = None, days_back: int = 7, max_count: int = 100) -> str:
    """
    Get recent events/audit log from Smartsheet (Enterprise feature).

    Args:
        since: ISO 8601 datetime to start from (e.g., '2024-01-15T00:00:00Z').
               If not provided, defaults to `days_back` days ago.
        days_back: Number of days to look back if `since` is not provided. Default: 7.
        max_count: Maximum number of events to return (1-10000). Default: 100.

    Returns list of recent events with details.
    """
    try:
        client = get_smartsheet_client()

        if since:
            start_time = since
        else:
            start_time = (datetime.now() - timedelta(days=days_back)).isoformat()

        events_response = client.Events.list_events(since=start_time, max_count=min(max_count, 10000))

        text_output = f"Smartsheet Events (since {start_time[:10]}):\n"
        text_output += "=" * 60 + "\n\n"

        if not events_response.data:
            text_output += "No events found in the specified time range.\n"
            return text_output

        text_output += f"Found {len(events_response.data)} event(s):\n\n"

        for i, event in enumerate(events_response.data, 1):
            obj_type = getattr(event, 'object_type', 'Unknown')
            action = getattr(event, 'action', 'Unknown')
            text_output += f"**{i}. {action} {obj_type}**\n"

            event_id = getattr(event, 'event_id', 'N/A')
            timestamp = getattr(event, 'event_timestamp', 'N/A')
            text_output += f"  Event ID: {event_id}\n"
            text_output += f"  Time: {timestamp}\n"

            if hasattr(event, 'user_id'):
                text_output += f"  User ID: {event.user_id}\n"

            if hasattr(event, 'object_id'):
                text_output += f"  Object ID: {event.object_id}\n"

            if hasattr(event, 'additional_details') and event.additional_details:
                details = event.additional_details
                if isinstance(details, dict):
                    if 'sheetName' in details:
                        text_output += f"  Sheet Name: {details['sheetName']}\n"
                    if 'sheetId' in details:
                        text_output += f"  Sheet ID: {details['sheetId']}\n"

            text_output += "\n"

        if events_response.more_available:
            text_output += f"\nMore events available. Use stream_position to continue.\n"

        return text_output
    except Exception as e:
        error_str = str(e)
        if '1004' in error_str or 'not enabled' in error_str.lower() or 'not available' in error_str.lower():
            return "Error: Event Reporting is not available for this account. This feature requires a Smartsheet Enterprise plan."
        return f"Error getting events: {error_str}"


def get_current_user() -> str:
    """
    Get information about the current authenticated Smartsheet user.

    Returns formatted user profile information.
    """
    try:
        client = get_smartsheet_client()
        user = client.Users.get_current_user()

        text_output = "Current User Profile\n"
        text_output += "=" * 50 + "\n\n"

        if hasattr(user, 'email') and user.email:
            text_output += f"Email: {user.email}\n"
        if hasattr(user, 'first_name') and user.first_name:
            text_output += f"First Name: {user.first_name}\n"
        if hasattr(user, 'last_name') and user.last_name:
            text_output += f"Last Name: {user.last_name}\n"
        if hasattr(user, 'id') and user.id:
            text_output += f"User ID: {user.id}\n"
        if hasattr(user, 'account') and user.account:
            account = user.account
            if hasattr(account, 'name'):
                text_output += f"Account: {account.name}\n"
        if hasattr(user, 'locale') and user.locale:
            text_output += f"Locale: {user.locale}\n"
        if hasattr(user, 'time_zone') and user.time_zone:
            text_output += f"Timezone: {user.time_zone}\n"

        return text_output
    except Exception as e:
        return f"Error getting user info: {str(e)}"


def get_contacts() -> str:
    """
    List all contacts in the user's personal contacts list.

    Returns a formatted list of contacts.
    """
    try:
        client = get_smartsheet_client()
        response = client.Contacts.list_contacts(include_all=True)

        if not response.data:
            return "No contacts found."

        text_output = "Your Contacts\n"
        text_output += "=" * 50 + "\n\n"
        text_output += f"Found {len(response.data)} contact(s):\n\n"

        for contact in response.data:
            email = getattr(contact, 'email', 'Unknown')
            name = getattr(contact, 'name', '')
            contact_id = getattr(contact, 'id', '')

            text_output += f"**{email}**"
            if name:
                text_output += f" ({name})"
            if contact_id:
                text_output += f" [ID: {contact_id}]"
            text_output += "\n"

        return text_output
    except Exception as e:
        return f"Error getting contacts: {str(e)}"


def get_server_info() -> str:
    """
    Get Smartsheet server information and application constants.

    Returns server information including supported locales, time zones, and formats.
    """
    try:
        client = get_smartsheet_client()
        info = client.Server.server_info()

        text_output = "Smartsheet Server Info\n"
        text_output += "=" * 50 + "\n\n"

        if hasattr(info, 'formats') and info.formats:
            text_output += "**Formats:**\n"
            if hasattr(info.formats, 'currency'):
                text_output += f"  Currencies: {len(info.formats.currency)} supported\n"
            if hasattr(info.formats, 'date_format'):
                text_output += f"  Date Formats: {len(info.formats.date_format)} supported\n"

        if hasattr(info, 'supported_locales') and info.supported_locales:
            text_output += f"\n**Supported Locales:** {len(info.supported_locales)}\n"
            for locale in info.supported_locales[:10]:
                text_output += f"  - {locale}\n"
            if len(info.supported_locales) > 10:
                text_output += f"  ... and {len(info.supported_locales) - 10} more\n"

        return text_output
    except Exception as e:
        return f"Error getting server info: {str(e)}"


def list_org_sheets(modified_since: str = None) -> str:
    """
    List ALL sheets in the organization (Admin feature, requires System Admin).

    Args:
        modified_since: Optional ISO 8601 datetime to filter sheets modified after this time.

    Returns list of all sheets in the organization with owner info.
    """
    try:
        client = get_smartsheet_client()

        if modified_since:
            response = client.Users.list_org_sheets(modified_since=modified_since)
        else:
            response = client.Users.list_org_sheets()

        text_output = "Organization Sheets (All sheets in org)\n"
        text_output += "=" * 60 + "\n\n"

        if not response.data:
            text_output += "No sheets found in the organization.\n"
            return text_output

        text_output += f"Found {len(response.data)} sheet(s) across the organization:\n\n"

        for i, sheet in enumerate(response.data, 1):
            name = getattr(sheet, 'name', 'Untitled')
            sheet_id = getattr(sheet, 'id', 'N/A')
            text_output += f"{i}. {name} (ID: {sheet_id})\n"

            owner = getattr(sheet, 'owner', None)
            if owner:
                text_output += f"   Owner: {owner}\n"
            access = getattr(sheet, 'access_level', None)
            if access:
                text_output += f"   Access Level: {access}\n"
            modified = getattr(sheet, 'modified_at', None)
            if modified:
                text_output += f"   Modified: {modified}\n"

            text_output += "\n"

        total_pages = getattr(response, 'total_pages', 1)
        if total_pages > 1:
            total_count = getattr(response, 'total_count', len(response.data))
            text_output += f"\nTotal: {total_count} sheets across {total_pages} pages.\n"

        return text_output
    except Exception as e:
        error_str = str(e)
        if '1003' in error_str or 'not authorized' in error_str.lower():
            return "Error: You must be a System Admin to list organization sheets."
        return f"Error listing org sheets: {error_str}"


def get_image_urls(sheet_id: str, row_id: str, column_id_or_name: str) -> str:
    """
    Get temporary download URL for an image in a cell.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        row_id: The row ID (numeric).
        column_id_or_name: The column ID (numeric) or column title.

    Returns image information including temporary download URL.
    """
    if not sheet_id or not row_id or not column_id_or_name:
        return "Error: sheet_id, row_id, and column_id_or_name parameters are required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return f"Error: Access to sheet '{sheet_name_resolved or sheet_id}' is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        column_id = None
        if str(column_id_or_name).isdigit():
            column_id = int(column_id_or_name)
        else:
            for col in sheet.columns:
                if col.title.lower() == column_id_or_name.lower():
                    column_id = col.id
                    break
            if not column_id:
                return f"Error: Column '{column_id_or_name}' not found in sheet"

        row = client.Sheets.get_row(resolved_id, int(row_id))

        target_cell = None
        for cell in row.cells:
            if cell.column_id == column_id:
                target_cell = cell
                break

        if not target_cell:
            return f"Error: Cell not found at row {row_id}, column {column_id_or_name}"

        image = getattr(target_cell, 'image', None)
        if not image:
            return f"No image found in cell at row {row_id}, column {column_id_or_name}."

        text_output = "Cell Image Information\n"
        text_output += "=" * 50 + "\n\n"

        image_id = getattr(image, 'id', None)
        alt_text = getattr(image, 'alt_text', None)
        width = getattr(image, 'width', None)
        height = getattr(image, 'height', None)

        if alt_text:
            text_output += f"**Alt Text:** {alt_text}\n"
        if image_id:
            text_output += f"**Image ID:** {image_id}\n"
        if width and height:
            text_output += f"**Dimensions:** {width} x {height}\n"

        if image_id:
            image_url_obj = client.models.ImageUrl({"imageId": image_id})
            url_response = client.Images.get_image_urls([image_url_obj])

            if url_response.image_urls and len(url_response.image_urls) > 0:
                url = url_response.image_urls[0].url
                text_output += f"\n**Temporary Download URL:**\n{url}\n"
                text_output += "\n⚠️ Note: This URL expires after a short time.\n"

                error = getattr(url_response.image_urls[0], 'error', None)
                if error:
                    text_output += f"\n**Warning:** {error}\n"
            else:
                text_output += "\nUnable to retrieve download URL.\n"

        return text_output
    except Exception as e:
        return f"Error getting image URL: {str(e)}"


# =============================================================================
# EXPORT TOOLS LIST
# =============================================================================

SMARTSHEET_TOOLS = [
    # Core tools (5)
    list_sheets,
    get_sheet,
    get_row,
    filter_rows,
    count_rows_by_column,
    # Unified resource tools (7)
    workspace,
    folder,
    sight,
    report,
    webhook,
    group,
    user,
    # Unified scope tools (2)
    attachment,
    discussion,
    # Unified search (1)
    search,
    # Unified navigation (1)
    navigation,
    # Unified sheet metadata (1)
    sheet_metadata,
    # Unified sheet info (1)
    sheet_info,
    # Unified update requests (1)
    update_requests,
    # Standalone tools (9)
    compare_sheets,
    get_cell_history,
    get_sheet_version,
    get_events,
    get_current_user,
    get_contacts,
    get_server_info,
    list_org_sheets,
    get_image_urls,
]


if __name__ == "__main__":
    print("SmartSheet Tools for Agno Agent (READ-ONLY)")
    print("=" * 60)
    print(f"\nTotal tools: {len(SMARTSHEET_TOOLS)}")
    print("\nCONSOLIDATED FROM 49 TO 28 TOOLS (43% REDUCTION)\n")

    print("Core Tools (5):")
    print("  - list_sheets, get_sheet, get_row, filter_rows, count_rows_by_column")

    print("\nUnified Resource Tools (7):")
    print("  - workspace, folder, sight, report, webhook, group, user")

    print("\nUnified Scope Tools (2):")
    print("  - attachment, discussion")

    print("\nUnified Search (1):")
    print("  - search")

    print("\nUnified Navigation (1):")
    print("  - navigation")

    print("\nUnified Sheet Metadata (1):")
    print("  - sheet_metadata")

    print("\nUnified Sheet Info (1):")
    print("  - sheet_info")

    print("\nUnified Update Requests (1):")
    print("  - update_requests")

    print("\nStandalone Tools (9):")
    print("  - compare_sheets, get_cell_history, get_sheet_version, get_events")
    print("  - get_current_user, get_contacts, get_server_info, list_org_sheets")
    print("  - get_image_urls")
