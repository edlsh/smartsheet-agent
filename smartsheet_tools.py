#!/usr/bin/env python3
"""
Optimized Smartsheet tools for SmartSheetBot.

This module provides READ-ONLY tools for interacting with Smartsheet data.
Optimizations include:
- Agno @tool decorator with cache_results for automatic caching
- Multi-level caching (L1 memory + L2 disk)
- Async tool execution support
- Rate limiting with exponential backoff
- Pagination optimization

CONSOLIDATED TOOLS (31 total):
    Core (5): list_sheets, get_sheet, get_row, filter_rows, count_rows_by_column
    Fuzzy Search (2): find_sheets, find_columns - search by partial/approximate names
    Smart Query Planning (1): analyze_sheet - efficient multi-operation analysis
    Unified Resource (7): workspace, folder, sight, report, webhook, group, user
    Unified Scope (2): attachment, discussion
    Unified Search (1): search
    Unified Navigation (1): navigation
    Unified Sheet Metadata (1): sheet_metadata
    Unified Sheet Info (1): sheet_info
    Unified Update Requests (1): update_requests
    Standalone (9): compare_sheets, get_cell_history, get_sheet_version, get_events,
                   get_current_user, get_contacts, get_server_info, list_org_sheets, get_image_urls
"""

import asyncio
import hashlib
import json
import os
import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Literal

import smartsheet
from agno.tools import tool

# =============================================================================
# MULTI-LEVEL CACHING CONFIGURATION
# =============================================================================

# Cache configuration from environment
CACHE_TTL_L1 = int(os.getenv("SMARTSHEET_CACHE_TTL_L1", "60"))  # L1: 1 minute (memory)
CACHE_TTL_L2 = int(os.getenv("SMARTSHEET_CACHE_TTL_L2", "300"))  # L2: 5 minutes (disk)
CACHE_DIR = Path(os.getenv("SMARTSHEET_CACHE_DIR", "tmp/cache"))
MAX_L1_ENTRIES = int(os.getenv("SMARTSHEET_CACHE_MAX_L1", "100"))

# Ensure cache directory exists
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class MultiLevelCache:
    """
    Multi-level cache with L1 (memory) and L2 (disk) tiers.

    L1: Fast in-memory cache with short TTL
    L2: Disk-based cache with longer TTL for persistence
    """

    def __init__(
        self,
        l1_ttl: int = CACHE_TTL_L1,
        l2_ttl: int = CACHE_TTL_L2,
        max_l1_entries: int = MAX_L1_ENTRIES,
    ):
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl
        self.max_l1_entries = max_l1_entries
        self._l1_cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def _generate_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        """Generate a unique cache key based on function name and arguments."""
        # Sort kwargs for consistent hashing
        key_data = json.dumps(
            {
                "func": func_name,
                "args": [str(a) for a in args],
                "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
            },
            sort_keys=True,
        )
        return hashlib.md5(key_data.encode()).hexdigest()

    def _get_l2_path(self, key: str) -> Path:
        """Get the disk cache path for a key."""
        return CACHE_DIR / f"{key}.pkl"

    def get(self, func_name: str, args: tuple, kwargs: dict) -> tuple[bool, Any]:
        """
        Get value from cache. Checks L1 first, then L2.
        Returns (hit, value) tuple.
        """
        key = self._generate_key(func_name, args, kwargs)

        # Check L1 (memory)
        with self._lock:
            if key in self._l1_cache:
                value, timestamp = self._l1_cache[key]
                if time.time() - timestamp < self.l1_ttl:
                    return True, value
                else:
                    del self._l1_cache[key]

        # Check L2 (disk)
        l2_path = self._get_l2_path(key)
        if l2_path.exists():
            try:
                with open(l2_path, "rb") as f:
                    data = pickle.load(f)
                if time.time() - data["timestamp"] < self.l2_ttl:
                    # Promote to L1
                    self._set_l1(key, data["value"])
                    return True, data["value"]
                else:
                    l2_path.unlink()  # Remove expired
            except (pickle.PickleError, KeyError, OSError):
                pass

        return False, None

    def _set_l1(self, key: str, value: Any):
        """Set value in L1 cache with LRU eviction."""
        with self._lock:
            # LRU eviction if at capacity
            if len(self._l1_cache) >= self.max_l1_entries:
                # Remove oldest entry
                oldest_key = min(self._l1_cache, key=lambda k: self._l1_cache[k][1])
                del self._l1_cache[oldest_key]

            self._l1_cache[key] = (value, time.time())

    def set(self, func_name: str, args: tuple, kwargs: dict, value: Any):
        """Set value in both L1 and L2 caches."""
        key = self._generate_key(func_name, args, kwargs)

        # Set in L1
        self._set_l1(key, value)

        # Set in L2 (disk)
        try:
            l2_path = self._get_l2_path(key)
            with open(l2_path, "wb") as f:
                pickle.dump({"value": value, "timestamp": time.time()}, f)
        except (pickle.PickleError, OSError):
            pass  # Fail silently for disk cache

    def clear(self):
        """Clear all caches."""
        with self._lock:
            self._l1_cache.clear()

        # Clear L2
        for cache_file in CACHE_DIR.glob("*.pkl"):
            try:
                cache_file.unlink()
            except OSError:
                pass

    def get_stats(self) -> dict:
        """Get cache statistics."""
        l2_count = len(list(CACHE_DIR.glob("*.pkl")))
        with self._lock:
            l1_count = len(self._l1_cache)
        return {"l1_entries": l1_count, "l2_entries": l2_count, "l1_max": self.max_l1_entries}


# Global cache instance
_cache = MultiLevelCache()


def cached_tool(func):
    """
    Decorator that adds multi-level caching to a tool function.
    Works alongside Agno's @tool decorator.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # Check cache
        hit, value = _cache.get(func.__name__, args, kwargs)
        if hit:
            return value

        # Execute and cache
        result = func(*args, **kwargs)
        _cache.set(func.__name__, args, kwargs, result)
        return result

    return wrapper


# =============================================================================
# SMARTSHEET CLIENT & HELPERS
# =============================================================================

# Client singleton with TTL
_client_cache = {"client": None, "created_at": 0}
_client_lock = threading.Lock()


def get_smartsheet_client() -> smartsheet.Smartsheet:
    """
    Get an authenticated Smartsheet client (singleton with TTL).
    Thread-safe with automatic refresh.
    """
    global _client_cache

    with _client_lock:
        # Return cached client if still valid (refresh every 5 minutes)
        if _client_cache["client"] and time.time() - _client_cache["created_at"] < 300:
            return _client_cache["client"]

        token = os.getenv("SMARTSHEET_ACCESS_TOKEN")
        if not token:
            raise ValueError("SMARTSHEET_ACCESS_TOKEN environment variable is not set")

        client = smartsheet.Smartsheet(token)
        client.errors_as_exceptions(True)

        _client_cache["client"] = client
        _client_cache["created_at"] = time.time()

        return client


# Thread pool for async operations
_executor = ThreadPoolExecutor(max_workers=4)


async def run_async(func, *args, **kwargs):
    """Run a synchronous function asynchronously using thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


@lru_cache(maxsize=1)
def _get_allowed_sheet_ids() -> frozenset[int]:
    """Get set of allowed sheet IDs from environment (cached)."""
    ids_str = os.getenv("ALLOWED_SHEET_IDS", "").strip()
    if not ids_str:
        return frozenset()
    return frozenset(int(id.strip()) for id in ids_str.split(",") if id.strip().isdigit())


@lru_cache(maxsize=1)
def _get_allowed_sheet_names() -> frozenset[str]:
    """Get set of allowed sheet names from environment (cached, lowercase)."""
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


def _resolve_sheet_id(client, sheet_id: str) -> tuple[int, str]:
    """Resolve sheet ID from name if needed. Returns (id, name)."""
    if str(sheet_id).isdigit():
        return int(sheet_id), None

    # Search in cached sheets list
    try:
        response = client.Sheets.list_sheets(include_all=True)
        for sheet in response.data:
            if sheet.name.lower() == sheet_id.lower():
                return sheet.id, sheet.name
    except Exception:
        pass

    return None, None


def clear_cache():
    """Clear all cached data including multi-level cache."""
    _cache.clear()
    _get_allowed_sheet_ids.cache_clear()
    _get_allowed_sheet_names.cache_clear()


def get_cache_stats() -> dict:
    """Get cache statistics for monitoring."""
    return _cache.get_stats()


# =============================================================================
# CORE TOOLS (5) - with Agno @tool decorator and caching
# =============================================================================


@tool(cache_results=True)
@cached_tool
def list_sheets(use_cache: bool = True) -> str:
    """
    List all Smartsheet sheets accessible to the user.

    Args:
        use_cache: If True (default), use cached data. Set False to force fresh fetch.

    Returns a formatted list of all available sheets with their IDs and access levels.
    """
    try:
        if not use_cache:
            clear_cache()

        client = get_smartsheet_client()
        response = client.Sheets.list_sheets(include_all=True)

        sheets = []
        for sheet in response.data:
            if not _is_sheet_allowed(sheet.id, sheet.name):
                continue
            sheets.append(
                {
                    "id": sheet.id,
                    "name": sheet.name,
                    "access_level": sheet.access_level,
                    "created_at": str(sheet.created_at) if sheet.created_at else None,
                    "modified_at": str(sheet.modified_at) if sheet.modified_at else None,
                }
            )

        if not sheets:
            return "No sheets available in the configured scope."

        result = f"Found {len(sheets)} sheets:\n"
        for s in sheets:
            result += f"- {s['name']} (ID: {s['id']}, Access: {s['access_level']})\n"

        return result
    except Exception as e:
        return f"Error listing sheets: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_sheet(sheet_id: str, max_rows: int = 1000) -> str:
    """
    Get detailed data from a specific Smartsheet.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name to retrieve.
        max_rows: Maximum number of rows to return (default 1000, for large sheets).

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

        # Optimized: Use page_size for better pagination
        sheet = client.Sheets.get_sheet(resolved_id, page_size=min(max_rows, 5000))
        columns = {col.id: col.title for col in sheet.columns}
        column_list = [col.title for col in sheet.columns]

        rows_data = []
        for i, row in enumerate(sheet.rows):
            if i >= max_rows:
                break
            row_dict = {"row_id": row.id, "row_number": row.row_number}
            for cell in row.cells:
                col_name = columns.get(cell.column_id, f"Column_{cell.column_id}")
                row_dict[col_name] = cell.display_value or cell.value
            rows_data.append(row_dict)

        text_output = f"Sheet: {sheet.name}\n"
        text_output += f"Total Rows: {len(sheet.rows)} (showing {len(rows_data)})\n"
        text_output += f"Columns: {', '.join(column_list)}\n\n"

        if rows_data:
            text_output += "Data:\n"
            for row in rows_data:
                row_str = " | ".join(
                    f"{k}: {v}"
                    for k, v in row.items()
                    if k not in ("row_id", "row_number") and v is not None
                )
                text_output += f"  Row {row['row_number']}: {row_str}\n"

        return text_output
    except Exception as e:
        return f"Error getting sheet: {str(e)}"


@tool(cache_results=True)
@cached_tool
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


@tool(cache_results=True)
@cached_tool
def filter_rows(
    sheet_id: str,
    column_name: str,
    filter_value: str,
    match_type: str = "contains",
    max_results: int = 50,
) -> str:
    """
    Filter rows in a Smartsheet based on column values.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name to filter.
        column_name: The name of the column to filter on.
        filter_value: The value to filter for.
        match_type: Type of match - "contains", "equals", "starts_with", "ends_with"
        max_results: Maximum number of matching rows to return (default 50)

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
            if len(matching_rows) >= max_results:
                break
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
                    else:  # contains
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
            for row in matching_rows:
                row_str = " | ".join(
                    f"{k}: {v}" for k, v in row.items() if k not in ("row_id",) and v is not None
                )
                text_output += f"  {row_str}\n"
        else:
            text_output += "  No matching rows found."

        return text_output
    except Exception as e:
        return f"Error filtering rows: {str(e)}"


@tool(cache_results=True)
@cached_tool
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
# UNIFIED RESOURCE TOOLS (7) - with caching
# =============================================================================


@tool(cache_results=True)
@cached_tool
def workspace(workspace_id: str = None) -> str:
    """
    Get workspace(s). Lists all workspaces if no ID provided, or gets details for a specific workspace.

    Args:
        workspace_id: Optional workspace ID. If not provided, lists all workspaces.
    """
    try:
        client = get_smartsheet_client()

        if workspace_id:
            ws = client.Workspaces.get_workspace(int(workspace_id))
            text_output = f"Workspace: {ws.name}\n{'=' * 50}\n\n"

            if hasattr(ws, "access_level"):
                text_output += f"Access Level: {ws.access_level}\n"
            if hasattr(ws, "permalink") and ws.permalink:
                text_output += f"Permalink: {ws.permalink}\n"

            if hasattr(ws, "sheets") and ws.sheets:
                allowed_sheets = [s for s in ws.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed_sheets:
                    text_output += f"\n**Sheets ({len(allowed_sheets)}):**\n"
                    for sheet in allowed_sheets:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"

            if hasattr(ws, "folders") and ws.folders:
                text_output += f"\n**Folders ({len(ws.folders)}):**\n"
                for folder in ws.folders:
                    text_output += f"  - {folder.name} (ID: {folder.id})\n"

            return text_output
        else:
            response = client.Workspaces.list_workspaces(include_all=True)
            if not response.data:
                return "No workspaces available."

            text_output = f"Found {len(response.data)} workspace(s):\n\n"
            for ws in response.data:
                text_output += (
                    f"- {ws.name} (ID: {ws.id}, Access: {getattr(ws, 'access_level', 'Unknown')})\n"
                )

            return text_output
    except Exception as e:
        return f"Error with workspace: {str(e)}"


@tool(cache_results=True)
@cached_tool
def folder(folder_id: str = None) -> str:
    """
    Get folder(s). Lists home-level folders if no ID provided, or gets details for a specific folder.

    Args:
        folder_id: Optional folder ID. If not provided, lists all home-level folders.
    """
    try:
        client = get_smartsheet_client()

        if folder_id:
            f = client.Folders.get_folder(int(folder_id))
            text_output = f"Folder: {f.name}\n{'=' * 50}\n\n"

            if hasattr(f, "sheets") and f.sheets:
                allowed_sheets = [s for s in f.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed_sheets:
                    text_output += f"\n**Sheets ({len(allowed_sheets)}):**\n"
                    for sheet in allowed_sheets:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"

            if hasattr(f, "folders") and f.folders:
                text_output += f"\n**Subfolders ({len(f.folders)}):**\n"
                for subfolder in f.folders:
                    text_output += f"  - {subfolder.name} (ID: {subfolder.id})\n"

            return text_output
        else:
            response = client.Home.list_folders(include_all=True)
            if not response.data:
                return "No folders available at home level."

            text_output = f"Found {len(response.data)} folder(s):\n\n"
            for f in response.data:
                text_output += f"- {f.name} (ID: {f.id})\n"

            return text_output
    except Exception as e:
        return f"Error with folder: {str(e)}"


@tool(cache_results=True)
@cached_tool
def sight(sight_id: str = None) -> str:
    """
    Get Sight/dashboard(s). Lists all Sights if no ID provided, or gets details for a specific Sight.

    Args:
        sight_id: Optional Sight ID. If not provided, lists all available Sights/dashboards.
    """
    try:
        client = get_smartsheet_client()

        if sight_id:
            s = client.Sights.get_sight(int(sight_id))
            text_output = f"Sight: {s.name}\n{'=' * 50}\n\n"

            if hasattr(s, "access_level"):
                text_output += f"Access Level: {s.access_level}\n"
            if hasattr(s, "widgets") and s.widgets:
                text_output += f"\n**Widgets ({len(s.widgets)}):**\n"
                for widget in s.widgets:
                    widget_type = getattr(widget, "type", "Unknown")
                    title = getattr(widget, "title", "Untitled")
                    text_output += f"  - {title} (Type: {widget_type})\n"

            return text_output
        else:
            response = client.Sights.list_sights(include_all=True)
            if not response.data:
                return "No Sights (dashboards) available."

            text_output = f"Found {len(response.data)} Sight(s)/Dashboard(s):\n\n"
            for s in response.data:
                text_output += f"- {s.name} (ID: {s.id})\n"

            return text_output
    except Exception as e:
        return f"Error with sight: {str(e)}"


@tool(cache_results=True)
@cached_tool
def report(report_id: str = None, max_rows: int = 100) -> str:
    """
    Get report(s). Lists all reports if no ID provided, or gets data from a specific report.

    Args:
        report_id: Optional report ID. If not provided, lists all available reports.
        max_rows: Maximum rows to return when fetching report data (default 100).
    """
    try:
        client = get_smartsheet_client()

        if report_id:
            r = client.Reports.get_report(int(report_id), page_size=min(max_rows, 5000))
            columns = {col.virtual_id: col.title for col in r.columns}
            column_list = [col.title for col in r.columns]

            rows_data = []
            for i, row in enumerate(r.rows):
                if i >= max_rows:
                    break
                row_dict = {"row_number": row.row_number}
                for cell in row.cells:
                    col_name = columns.get(
                        cell.virtual_column_id, f"Column_{cell.virtual_column_id}"
                    )
                    row_dict[col_name] = cell.display_value or cell.value
                rows_data.append(row_dict)

            text_output = f"Report: {r.name}\n"
            text_output += f"Total Rows: {len(r.rows)} (showing {len(rows_data)})\n"
            text_output += f"Columns: {', '.join(column_list)}\n\n"

            if rows_data:
                text_output += "Data:\n"
                for row in rows_data:
                    row_str = " | ".join(f"{k}: {v}" for k, v in row.items() if v is not None)
                    text_output += f"  Row {row['row_number']}: {row_str}\n"

            return text_output
        else:
            response = client.Reports.list_reports(include_all=True)
            if not response.data:
                return "No reports available."

            text_output = f"Found {len(response.data)} reports:\n\n"
            for r in response.data:
                text_output += f"- {r.name} (ID: {r.id})\n"

            return text_output
    except Exception as e:
        return f"Error with report: {str(e)}"


@tool(cache_results=True)
@cached_tool
def webhook(webhook_id: str = None) -> str:
    """
    Get webhook(s). Lists all webhooks if no ID provided, or gets details for a specific webhook.

    Args:
        webhook_id: Optional webhook ID. If not provided, lists all webhooks owned by the user.
    """
    try:
        client = get_smartsheet_client()

        if webhook_id:
            w = client.Webhooks.get_webhook(int(webhook_id))
            text_output = f"Webhook: {getattr(w, 'name', 'Unnamed')}\n{'=' * 50}\n\n"
            text_output += f"**ID:** {getattr(w, 'id', 'N/A')}\n"
            text_output += f"**Status:** {getattr(w, 'status', 'Unknown')}\n"
            text_output += f"**Enabled:** {'Yes' if getattr(w, 'enabled', False) else 'No'}\n"
            return text_output
        else:
            response = client.Webhooks.list_webhooks(include_all=True)
            if not response.data:
                return "No webhooks found."

            text_output = f"Found {len(response.data)} webhook(s):\n\n"
            for w in response.data:
                text_output += (
                    f"- {getattr(w, 'name', 'Unnamed')} (ID: {getattr(w, 'id', 'N/A')})\n"
                )

            return text_output
    except Exception as e:
        return f"Error with webhook: {str(e)}"


@tool(cache_results=True)
@cached_tool
def group(group_id: str = None) -> str:
    """
    Get group(s). Lists all groups if no ID provided, or gets details for a specific group.

    Args:
        group_id: Optional group ID. If not provided, lists all groups in the organization.
    """
    try:
        client = get_smartsheet_client()

        if group_id:
            g = client.Groups.get_group(int(group_id))
            text_output = f"Group: {g.name}\n{'=' * 50}\n\n"
            text_output += f"**ID:** {g.id}\n"

            if hasattr(g, "members") and g.members:
                text_output += f"\n**Members ({len(g.members)}):**\n"
                for member in g.members:
                    email = getattr(member, "email", "Unknown")
                    text_output += f"  - {email}\n"

            return text_output
        else:
            response = client.Groups.list_groups(include_all=True)
            if not response.data:
                return "No groups found."

            text_output = f"Found {len(response.data)} group(s):\n\n"
            for g in response.data:
                text_output += f"- {g.name} (ID: {g.id})\n"

            return text_output
    except Exception as e:
        return f"Error with group: {str(e)}"


@tool(cache_results=True)
@cached_tool
def user(user_id: str = None, max_results: int = 50) -> str:
    """
    Get user(s). Lists all organization users if no ID provided, or gets details for a specific user.
    Requires System Admin permissions.

    Args:
        user_id: Optional user ID or email. If not provided, lists all organization users.
        max_results: Maximum users to return when listing (default 50).
    """
    try:
        client = get_smartsheet_client()

        if user_id:
            if "@" in str(user_id):
                response = client.Users.list_users(email=user_id)
                if response.data:
                    user_id = response.data[0].id
                else:
                    return f"Error: User with email '{user_id}' not found"

            u = client.Users.get_user(int(user_id))
            name = (
                f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
            )
            text_output = f"User Profile\n{'=' * 50}\n\n"
            text_output += f"**Name:** {name or 'N/A'}\n"
            text_output += f"**Email:** {getattr(u, 'email', 'N/A')}\n"
            text_output += f"**ID:** {getattr(u, 'id', 'N/A')}\n"
            return text_output
        else:
            response = client.Users.list_users(page_size=min(max_results, 100))
            if not response.data:
                return "No users found."

            text_output = f"Organization Users ({len(response.data)}):\n{'=' * 50}\n\n"
            for u in response.data:
                name = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
                text_output += f"- {name or 'N/A'} ({getattr(u, 'email', 'N/A')})\n"

            return text_output
    except Exception as e:
        if "1003" in str(e) or "not authorized" in str(e).lower():
            return "Error: You must be a System Admin to access user information."
        return f"Error with user: {str(e)}"


# =============================================================================
# UNIFIED SCOPE TOOLS (2)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def attachment(sheet_id: str, row_id: str = None, attachment_id: str = None) -> str:
    """
    Get attachments at various scopes.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        row_id: Optional row ID for row-level attachments.
        attachment_id: Optional attachment ID for specific attachment with download URL.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if attachment_id:
            att = client.Attachments.get_attachment(resolved_id, int(attachment_id))
            text_output = f"Attachment: {getattr(att, 'name', 'N/A')}\n"
            text_output += f"Type: {getattr(att, 'attachment_type', 'Unknown')}\n"
            if hasattr(att, "url") and att.url:
                text_output += f"\n**Download URL** (temporary): {att.url}\n"
            return text_output
        elif row_id:
            attachments = client.Attachments.list_row_attachments(
                resolved_id, int(row_id), include_all=True
            )
            text_output = f"Attachments for Row {row_id}:\n"
            if attachments.data:
                for att in attachments.data:
                    text_output += f"- {att.name} (ID: {att.id})\n"
            else:
                text_output += "No attachments found.\n"
            return text_output
        else:
            attachments = client.Attachments.list_all_attachments(resolved_id, include_all=True)
            text_output = f"Attachments for '{sheet.name}':\n"
            if attachments.data:
                for att in attachments.data:
                    text_output += f"- {att.name} (ID: {att.id})\n"
            else:
                text_output += "No attachments found.\n"
            return text_output
    except Exception as e:
        return f"Error with attachment: {str(e)}"


@tool(cache_results=True)
@cached_tool
def discussion(sheet_id: str, row_id: str = None) -> str:
    """
    Get discussions (comments) at various scopes.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        row_id: Optional row ID for row-level discussions.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if row_id:
            discussions = client.Discussions.get_row_discussions(
                resolved_id, int(row_id), include_all=True
            )
            text_output = f"Discussions for Row {row_id}:\n"
        else:
            discussions = client.Discussions.get_all_discussions(resolved_id, include_all=True)
            text_output = f"Discussions for '{sheet.name}':\n"

        if discussions.data:
            for disc in discussions.data:
                text_output += f"\nDiscussion (ID: {disc.id})\n"
                if hasattr(disc, "comments") and disc.comments:
                    for comment in disc.comments[:3]:
                        author = "Unknown"
                        if hasattr(comment, "created_by") and comment.created_by:
                            author = getattr(comment.created_by, "name", "Unknown")
                        text_output += f"  - {author}: {comment.text[:100]}...\n"
        else:
            text_output += "No discussions found.\n"

        return text_output
    except Exception as e:
        return f"Error with discussion: {str(e)}"


# =============================================================================
# UNIFIED SEARCH (1)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def search(query: str, sheet_id: str = None, max_results: int = 20) -> str:
    """
    Search for text in Smartsheet. Can search globally or within a specific sheet.

    Args:
        query: The search text to find.
        sheet_id: Optional sheet ID to limit search scope.
        max_results: Maximum results to return (default 20).
    """
    if not query:
        return "Error: query parameter is required"

    try:
        client = get_smartsheet_client()

        if sheet_id:
            resolved_id, _ = _resolve_sheet_id(client, sheet_id)
            if not resolved_id:
                return f"Error: Sheet '{sheet_id}' not found"

            results = client.Search.search_sheet(resolved_id, query)
            text_output = f"Search results for '{query}' in sheet:\n"
        else:
            results = client.Search.search(query)
            text_output = f"Search results for '{query}':\n"

        if not results.results:
            return f"No results found for '{query}'."

        text_output += f"Found {results.total_count} result(s):\n\n"

        for i, result in enumerate(results.results[:max_results], 1):
            text = getattr(result, "text", "N/A")
            obj_type = getattr(result, "object_type", "Unknown")
            text_output += f"{i}. {obj_type}: {text}\n"

        return text_output
    except Exception as e:
        return f"Error searching: {str(e)}"


# =============================================================================
# UNIFIED NAVIGATION (1)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def navigation(view: Literal["home", "favorites", "templates"] = "home") -> str:
    """
    Access navigation views: home, favorites, or templates.

    Args:
        view: The view to display ("home", "favorites", or "templates").
    """
    try:
        client = get_smartsheet_client()

        if view == "favorites":
            response = client.Favorites.list_favorites(include_all=True)
            if not response.data:
                return "No favorites found."

            text_output = f"Your Favorites ({len(response.data)} items)\n{'=' * 50}\n\n"
            for fav in response.data:
                obj_type = getattr(fav, "type", "unknown")
                obj_id = getattr(fav, "object_id", None)
                text_output += f"- {obj_type}: ID {obj_id}\n"

            return text_output

        elif view == "templates":
            home = client.Home.list_all_contents()
            text_output = f"Available Templates\n{'=' * 50}\n\n"

            if hasattr(home, "templates") and home.templates:
                for tmpl in home.templates:
                    text_output += f"- {tmpl.name} (ID: {tmpl.id})\n"
            else:
                text_output += "No templates found.\n"

            return text_output

        else:  # home
            home = client.Home.list_all_contents()
            text_output = f"Your Smartsheet Home\n{'=' * 50}\n\n"

            if hasattr(home, "sheets") and home.sheets:
                allowed = [s for s in home.sheets if _is_sheet_allowed(s.id, s.name)]
                if allowed:
                    text_output += f"**Sheets ({len(allowed)}):**\n"
                    for sheet in allowed[:20]:
                        text_output += f"  - {sheet.name} (ID: {sheet.id})\n"

            if hasattr(home, "workspaces") and home.workspaces:
                text_output += f"\n**Workspaces ({len(home.workspaces)}):**\n"
                for ws in home.workspaces[:10]:
                    text_output += f"  - {ws.name} (ID: {ws.id})\n"

            return text_output
    except Exception as e:
        return f"Error with navigation: {str(e)}"


# =============================================================================
# UNIFIED SHEET METADATA (1)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def sheet_metadata(
    sheet_id: str, info: Literal["automation", "shares", "publish", "proofs", "references"]
) -> str:
    """
    Get various metadata about a sheet.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        info: Type of metadata ("automation", "shares", "publish", "proofs", "references").
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if info == "automation":
            rules = client.Sheets.list_automation_rules(resolved_id, include_all=True)
            text_output = f"Automation Rules for '{sheet.name}':\n"
            if rules.data:
                for rule in rules.data:
                    text_output += f"- {getattr(rule, 'name', 'Unnamed')} (Enabled: {getattr(rule, 'enabled', 'Unknown')})\n"
            else:
                text_output += "No automation rules found.\n"
            return text_output

        elif info == "shares":
            shares = client.Sheets.list_shares(resolved_id, include_all=True)
            text_output = f"Sharing for '{sheet.name}':\n"
            if shares.data:
                for share in shares.data:
                    email = getattr(share, "email", "N/A")
                    level = getattr(share, "access_level", "Unknown")
                    text_output += f"- {email}: {level}\n"
            else:
                text_output += "No shares found.\n"
            return text_output

        elif info == "publish":
            status = client.Sheets.get_publish_status(resolved_id)
            text_output = f"Publish Status for '{sheet.name}':\n"
            text_output += f"Read-Only Full: {getattr(status, 'read_only_full_enabled', False)}\n"
            text_output += f"Read-Only Lite: {getattr(status, 'read_only_lite_enabled', False)}\n"
            return text_output

        elif info == "proofs":
            text_output = f"Proofs for '{sheet.name}':\n"
            text_output += "(Proofs API not directly supported - check attachments)\n"
            return text_output

        elif info == "references":
            refs = client.Sheets.list_cross_sheet_references(resolved_id)
            text_output = f"Cross-Sheet References for '{sheet.name}':\n"
            if refs.data:
                for ref in refs.data:
                    text_output += f"- {getattr(ref, 'name', 'Unnamed')} (ID: {ref.id})\n"
            else:
                text_output += "No cross-sheet references found.\n"
            return text_output

        else:
            return f"Error: Unknown info type '{info}'"

    except Exception as e:
        return f"Error with sheet_metadata: {str(e)}"


# =============================================================================
# UNIFIED SHEET INFO (1)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def sheet_info(
    sheet_id: str,
    info: Literal["columns", "stats", "summary_fields", "by_column"],
    columns: str = None,
) -> str:
    """
    Get sheet information by type.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        info: Type of info ("columns", "stats", "summary_fields", "by_column").
        columns: Comma-separated column names (only for info="by_column").
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if info == "columns":
            text_output = f"Columns for '{sheet.name}':\n{'=' * 50}\n\n"
            for col in sheet.columns:
                text_output += f"- {col.title} (ID: {col.id}, Type: {col.type})\n"
                if hasattr(col, "options") and col.options:
                    text_output += f"    Options: {', '.join(col.options)}\n"
            return text_output

        elif info == "stats":
            text_output = f"Statistics for '{sheet.name}':\n{'=' * 50}\n\n"
            text_output += f"Total Rows: {len(sheet.rows)}\n"
            text_output += f"Total Columns: {len(sheet.columns)}\n"

            # Column type breakdown
            type_counts = {}
            for col in sheet.columns:
                col_type = col.type
                type_counts[col_type] = type_counts.get(col_type, 0) + 1

            text_output += "\nColumn Types:\n"
            for col_type, count in sorted(type_counts.items()):
                text_output += f"  - {col_type}: {count}\n"

            return text_output

        elif info == "summary_fields":
            text_output = f"Summary Fields for '{sheet.name}':\n{'=' * 50}\n\n"

            if hasattr(sheet, "summary") and sheet.summary and hasattr(sheet.summary, "fields"):
                for field in sheet.summary.fields:
                    title = getattr(field, "title", "Untitled")
                    value = getattr(field, "display_value", getattr(field, "object_value", "N/A"))
                    text_output += f"- {title}: {value}\n"
            else:
                text_output += "No summary fields found.\n"

            return text_output

        elif info == "by_column":
            if not columns:
                return "Error: columns parameter is required for info='by_column'"

            column_names = [c.strip() for c in columns.split(",")]
            col_id_map = {}
            for col in sheet.columns:
                if col.title.lower() in [c.lower() for c in column_names]:
                    col_id_map[col.id] = col.title

            if not col_id_map:
                return f"Error: None of the specified columns found. Available: {', '.join([c.title for c in sheet.columns])}"

            text_output = (
                f"Data from '{sheet.name}' - Columns: {', '.join(col_id_map.values())}\n\n"
            )

            for row in sheet.rows[:50]:  # Limit to 50 rows
                row_data = []
                for cell in row.cells:
                    if cell.column_id in col_id_map:
                        col_name = col_id_map[cell.column_id]
                        value = cell.display_value or cell.value
                        row_data.append(f"{col_name}: {value}")
                if row_data:
                    text_output += f"Row {row.row_number}: {' | '.join(row_data)}\n"

            return text_output

        else:
            return f"Error: Unknown info type '{info}'"

    except Exception as e:
        return f"Error with sheet_info: {str(e)}"


# =============================================================================
# UNIFIED UPDATE REQUESTS (1)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def update_requests(sheet_id: str, sent: bool = False) -> str:
    """
    Get update requests for a sheet.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
        sent: If True, get sent requests. If False (default), get pending requests.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        if sent:
            requests = client.Sheets.list_sent_update_requests(resolved_id, include_all=True)
            text_output = "Sent Update Requests:\n"
        else:
            requests = client.Sheets.list_update_requests(resolved_id, include_all=True)
            text_output = "Pending Update Requests:\n"

        if requests.data:
            for req in requests.data:
                text_output += f"- ID: {req.id}, Sent To: {getattr(req, 'sent_to', 'N/A')}\n"
        else:
            text_output += "No update requests found.\n"

        return text_output
    except Exception as e:
        return f"Error with update_requests: {str(e)}"


# =============================================================================
# STANDALONE TOOLS (9)
# =============================================================================


@tool(cache_results=True)
@cached_tool
def compare_sheets(sheet_id_1: str, sheet_id_2: str, key_column: str) -> str:
    """
    Compare two sheets by a key column to find differences.

    Args:
        sheet_id_1: First sheet ID or name.
        sheet_id_2: Second sheet ID or name.
        key_column: Column name to use as the comparison key.
    """
    if not all([sheet_id_1, sheet_id_2, key_column]):
        return "Error: sheet_id_1, sheet_id_2, and key_column are required"

    try:
        client = get_smartsheet_client()

        resolved_id_1, _ = _resolve_sheet_id(client, sheet_id_1)
        resolved_id_2, _ = _resolve_sheet_id(client, sheet_id_2)

        if not resolved_id_1:
            return f"Error: Sheet '{sheet_id_1}' not found"
        if not resolved_id_2:
            return f"Error: Sheet '{sheet_id_2}' not found"

        sheet1 = client.Sheets.get_sheet(resolved_id_1)
        sheet2 = client.Sheets.get_sheet(resolved_id_2)

        # Find key column in both sheets
        key_col_1 = None
        key_col_2 = None

        for col in sheet1.columns:
            if col.title.lower() == key_column.lower():
                key_col_1 = col.id
                break

        for col in sheet2.columns:
            if col.title.lower() == key_column.lower():
                key_col_2 = col.id
                break

        if not key_col_1 or not key_col_2:
            return f"Error: Key column '{key_column}' not found in both sheets"

        # Build key sets
        keys_1 = set()
        keys_2 = set()

        for row in sheet1.rows:
            for cell in row.cells:
                if cell.column_id == key_col_1:
                    keys_1.add(str(cell.display_value or cell.value or ""))
                    break

        for row in sheet2.rows:
            for cell in row.cells:
                if cell.column_id == key_col_2:
                    keys_2.add(str(cell.display_value or cell.value or ""))
                    break

        only_in_1 = keys_1 - keys_2
        only_in_2 = keys_2 - keys_1
        in_both = keys_1 & keys_2

        text_output = f"Comparison Results\n{'=' * 50}\n\n"
        text_output += f"Sheet 1: {sheet1.name} ({len(keys_1)} unique keys)\n"
        text_output += f"Sheet 2: {sheet2.name} ({len(keys_2)} unique keys)\n\n"
        text_output += f"In both: {len(in_both)}\n"
        text_output += f"Only in Sheet 1: {len(only_in_1)}\n"
        text_output += f"Only in Sheet 2: {len(only_in_2)}\n"

        if only_in_1:
            text_output += f"\n**Only in {sheet1.name}:**\n"
            for key in list(only_in_1)[:10]:
                text_output += f"  - {key}\n"

        if only_in_2:
            text_output += f"\n**Only in {sheet2.name}:**\n"
            for key in list(only_in_2)[:10]:
                text_output += f"  - {key}\n"

        return text_output
    except Exception as e:
        return f"Error comparing sheets: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_cell_history(sheet_id: str, row_id: str, column_id: str) -> str:
    """
    Get revision history for a specific cell.

    Args:
        sheet_id: The sheet ID.
        row_id: The row ID.
        column_id: The column ID or column name.
    """
    if not all([sheet_id, row_id, column_id]):
        return "Error: sheet_id, row_id, and column_id are required"

    try:
        client = get_smartsheet_client()
        sheet = client.Sheets.get_sheet(int(sheet_id))

        # Resolve column name to ID if needed
        if not str(column_id).isdigit():
            for col in sheet.columns:
                if col.title.lower() == column_id.lower():
                    column_id = col.id
                    break

        history = client.Cells.get_cell_history(
            int(sheet_id), int(row_id), int(column_id), include_all=True
        )

        text_output = f"Cell History:\n{'=' * 50}\n\n"

        if history.data:
            for entry in history.data:
                modified_at = getattr(entry, "modified_at", "Unknown")
                modified_by = getattr(entry, "modified_by", {})
                user_name = getattr(modified_by, "name", "Unknown")
                value = entry.display_value or entry.value
                text_output += f"- {modified_at}: {value} (by {user_name})\n"
        else:
            text_output += "No history found.\n"

        return text_output
    except Exception as e:
        return f"Error getting cell history: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_sheet_version(sheet_id: str) -> str:
    """
    Get sheet version and modification info.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name.
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required"

    try:
        client = get_smartsheet_client()
        resolved_id, _ = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return f"Error: Sheet '{sheet_id}' not found"

        sheet = client.Sheets.get_sheet(resolved_id)

        text_output = f"Sheet Version Info: {sheet.name}\n{'=' * 50}\n\n"
        text_output += f"Version: {getattr(sheet, 'version', 'N/A')}\n"
        text_output += f"Created At: {getattr(sheet, 'created_at', 'N/A')}\n"
        text_output += f"Modified At: {getattr(sheet, 'modified_at', 'N/A')}\n"

        return text_output
    except Exception as e:
        return f"Error getting sheet version: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_events(days_back: int = 7, max_count: int = 50) -> str:
    """
    Get recent audit events (Enterprise feature).

    Args:
        days_back: Number of days to look back (default 7).
        max_count: Maximum events to return (default 50).
    """
    try:
        client = get_smartsheet_client()

        since = datetime.utcnow() - timedelta(days=days_back)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        events = client.Events.list_events(since=since_str, max_count=max_count)

        text_output = f"Recent Events (last {days_back} days):\n{'=' * 50}\n\n"

        if events.data:
            for event in events.data:
                event_type = getattr(event, "event_type", "Unknown")
                timestamp = getattr(event, "event_timestamp", "N/A")
                text_output += f"- {timestamp}: {event_type}\n"
        else:
            text_output += "No events found.\n"

        return text_output
    except Exception as e:
        if "not authorized" in str(e).lower() or "1003" in str(e):
            return "Error: Events API requires Enterprise plan with appropriate permissions."
        return f"Error getting events: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_current_user() -> str:
    """Get current authenticated user profile."""
    try:
        client = get_smartsheet_client()
        user = client.Users.get_current_user()

        text_output = f"Current User\n{'=' * 50}\n\n"
        text_output += f"Name: {getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}\n"
        text_output += f"Email: {getattr(user, 'email', 'N/A')}\n"
        text_output += f"ID: {getattr(user, 'id', 'N/A')}\n"

        return text_output
    except Exception as e:
        return f"Error getting current user: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_contacts() -> str:
    """List personal contacts."""
    try:
        client = get_smartsheet_client()
        contacts = client.Contacts.list_contacts(include_all=True)

        text_output = f"Personal Contacts\n{'=' * 50}\n\n"

        if contacts.data:
            for contact in contacts.data:
                name = getattr(contact, "name", "N/A")
                email = getattr(contact, "email", "N/A")
                text_output += f"- {name} ({email})\n"
        else:
            text_output += "No contacts found.\n"

        return text_output
    except Exception as e:
        return f"Error getting contacts: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_server_info() -> str:
    """Get Smartsheet server info and constants."""
    try:
        client = get_smartsheet_client()
        info = client.Server.server_info()

        text_output = f"Server Info\n{'=' * 50}\n\n"

        if hasattr(info, "supported_locales"):
            text_output += f"Supported Locales: {len(info.supported_locales)}\n"

        if hasattr(info, "formats"):
            text_output += "Formats Available: Yes\n"

        return text_output
    except Exception as e:
        return f"Error getting server info: {str(e)}"


@tool(cache_results=True)
@cached_tool
def list_org_sheets(max_results: int = 100) -> str:
    """
    List ALL sheets in the organization (Admin feature).

    Args:
        max_results: Maximum sheets to return (default 100).
    """
    try:
        client = get_smartsheet_client()
        response = client.Users.list_org_sheets(page_size=min(max_results, 1000))

        text_output = f"Organization Sheets\n{'=' * 50}\n\n"

        if response.data:
            text_output += f"Found {len(response.data)} sheets:\n\n"
            for sheet in response.data[:max_results]:
                text_output += f"- {sheet.name} (ID: {sheet.id})\n"
        else:
            text_output += "No sheets found.\n"

        return text_output
    except Exception as e:
        if "not authorized" in str(e).lower():
            return "Error: This feature requires System Admin permissions."
        return f"Error listing org sheets: {str(e)}"


@tool(cache_results=True)
@cached_tool
def get_image_urls(sheet_id: str, row_id: str, column_id_or_name: str) -> str:
    """
    Get temporary download URL for cell images.

    Args:
        sheet_id: The sheet ID.
        row_id: The row ID.
        column_id_or_name: The column ID or column name.
    """
    if not all([sheet_id, row_id, column_id_or_name]):
        return "Error: sheet_id, row_id, and column_id_or_name are required"

    try:
        client = get_smartsheet_client()
        sheet = client.Sheets.get_sheet(int(sheet_id))

        # Resolve column name to ID if needed
        column_id = column_id_or_name
        if not str(column_id_or_name).isdigit():
            for col in sheet.columns:
                if col.title.lower() == column_id_or_name.lower():
                    column_id = col.id
                    break

        # Get row to find image
        row = client.Sheets.get_row(int(sheet_id), int(row_id))

        image_id = None
        for cell in row.cells:
            if cell.column_id == int(column_id):
                if hasattr(cell, "image") and cell.image:
                    image_id = cell.image.id
                break

        if not image_id:
            return "No image found in the specified cell."

        # Get image URL
        url_response = client.Sheets.get_row_cell_image_urls(
            int(sheet_id), int(row_id), [{"columnId": int(column_id), "imageId": image_id}]
        )

        text_output = "Image URL:\n"
        if url_response.image_urls:
            text_output += f"{url_response.image_urls[0].url}\n"
            text_output += "\n⚠️ This URL is temporary and will expire.\n"
        else:
            text_output += "Unable to retrieve image URL.\n"

        return text_output
    except Exception as e:
        return f"Error getting image URL: {str(e)}"


# =============================================================================
# FUZZY SHEET SEARCH (1) - Helps users find sheets by partial/approximate names
# =============================================================================


def _calculate_similarity(s1: str, s2: str) -> float:
    """
    Calculate similarity ratio between two strings using sequence matching.
    Returns a score between 0.0 and 1.0.
    """
    from difflib import SequenceMatcher

    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def _tokenize(text: str) -> set:
    """Extract meaningful tokens from text for word-based matching."""
    import re

    # Split on non-alphanumeric characters and filter short tokens
    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return {t for t in tokens if len(t) >= 2}


@tool(cache_results=True)
@cached_tool
def find_sheets(query: str, max_results: int = 5, include_ids: bool = True) -> str:
    """
    Search for sheets by partial or approximate name. Use this when users provide
    an inexact sheet name and you need to find matching sheets to confirm with them.

    This tool performs fuzzy matching to find sheets that:
    - Contain the search query as a substring
    - Have similar words in their name
    - Are similar based on text similarity scoring

    Args:
        query: Partial or approximate sheet name to search for (e.g., "job log", "retainer", "project tracker")
        max_results: Maximum number of matching sheets to return (default 5)
        include_ids: Include sheet IDs in the results (default True, helpful for disambiguation)

    Returns a ranked list of matching sheets with confidence indicators.

    Example usage:
    - User says "job log retainer sheet" but exact name is "Job Log - Retainer Projects"
    - Call find_sheets("job log retainer") to find matches
    - Present options to user and ask them to confirm which sheet they meant
    """
    if not query:
        return "Error: query parameter is required. Provide a partial or approximate sheet name."

    try:
        client = get_smartsheet_client()
        response = client.Sheets.list_sheets(include_all=True)

        if not response.data:
            return "No sheets available."

        query_lower = query.lower().strip()
        query_tokens = _tokenize(query)

        # Score each sheet
        matches = []
        for sheet in response.data:
            # Skip sheets not in allowed list (if configured)
            if not _is_sheet_allowed(sheet.id, sheet.name):
                continue

            sheet_name = sheet.name
            sheet_name_lower = sheet_name.lower()
            sheet_tokens = _tokenize(sheet_name)

            # Calculate different match scores
            scores = {
                "exact": 1.0 if sheet_name_lower == query_lower else 0.0,
                "contains": 1.0 if query_lower in sheet_name_lower else 0.0,
                "contained": 0.8 if sheet_name_lower in query_lower else 0.0,
                "similarity": _calculate_similarity(query_lower, sheet_name_lower),
                "word_match": 0.0,
            }

            # Word-based matching (how many query tokens appear in sheet name)
            if query_tokens:
                matching_tokens = query_tokens & sheet_tokens
                scores["word_match"] = (
                    len(matching_tokens) / len(query_tokens) if query_tokens else 0.0
                )

            # Also check if any sheet token contains any query token (partial word match)
            partial_word_score = 0.0
            for qt in query_tokens:
                for st in sheet_tokens:
                    if qt in st or st in qt:
                        partial_word_score = max(partial_word_score, 0.6)
            scores["partial_word"] = partial_word_score

            # Compute weighted overall score
            overall_score = (
                scores["exact"] * 2.0  # Exact match is best
                + scores["contains"] * 1.5  # Query in sheet name is very good
                + scores["contained"] * 0.8  # Sheet name in query is okay
                + scores["word_match"] * 1.2  # Word overlap is good
                + scores["partial_word"] * 0.8  # Partial word match is okay
                + scores["similarity"] * 0.5  # Overall similarity
            ) / 6.7  # Normalize to roughly 0-1 range

            if overall_score > 0.1:  # Minimum threshold
                match_type = (
                    "exact"
                    if scores["exact"] > 0
                    else "contains"
                    if scores["contains"] > 0
                    else "word match"
                    if scores["word_match"] > 0.5
                    else "partial"
                    if scores["partial_word"] > 0
                    else "similar"
                )

                matches.append(
                    {
                        "name": sheet_name,
                        "id": sheet.id,
                        "score": overall_score,
                        "match_type": match_type,
                        "access_level": getattr(sheet, "access_level", "Unknown"),
                    }
                )

        # Sort by score descending and limit results
        matches.sort(key=lambda x: x["score"], reverse=True)
        matches = matches[:max_results]

        if not matches:
            text_output = f"No sheets found matching '{query}'.\n\n"
            text_output += "Suggestions:\n"
            text_output += "- Try using different keywords\n"
            text_output += "- Use list_sheets() to see all available sheets\n"
            return text_output

        # Format output
        text_output = f"Found {len(matches)} sheet(s) matching '{query}':\n\n"

        for i, match in enumerate(matches, 1):
            confidence = (
                "HIGH" if match["score"] > 0.7 else "MEDIUM" if match["score"] > 0.4 else "LOW"
            )
            text_output += f"{i}. {match['name']}"
            if include_ids:
                text_output += f" (ID: {match['id']})"
            text_output += f"\n   Match: {match['match_type'].upper()} | Confidence: {confidence}\n"

        text_output += "\n**Reply with the number (e.g., '1') to select a sheet**, or provide the exact sheet name or ID."

        return text_output
    except Exception as e:
        return f"Error searching for sheets: {str(e)}"


# =============================================================================
# FUZZY COLUMN SEARCH (1) - Helps users find columns by partial/approximate names
# =============================================================================


@tool(cache_results=True)
@cached_tool
def find_columns(sheet_id: str, query: str, max_results: int = 5) -> str:
    """
    Search for columns in a sheet by partial or approximate name. Use this when users
    reference a column informally and you need to find the exact column name.

    This tool performs fuzzy matching to find columns that:
    - Contain the search query as a substring
    - Have similar words in their name
    - Are similar based on text similarity scoring

    Args:
        sheet_id: The sheet ID (numeric) or sheet name to search within
        query: Partial or approximate column name (e.g., "status", "job name", "date")
        max_results: Maximum number of matching columns to return (default 5)

    Returns a ranked list of matching columns with their types and confidence indicators.

    Example usage:
    - User says "filter by job status" but column is "Current Job Status"
    - Call find_columns(sheet_id, "job status") to find matches
    - Use the exact column name in subsequent operations
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required."
    if not query:
        return "Error: query parameter is required. Provide a partial or approximate column name."

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return (
                f"Error: Sheet '{sheet_id}' not found. Use find_sheets() to search for the sheet."
            )

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        sheet = client.Sheets.get_sheet(resolved_id)

        if not sheet.columns:
            return f"Sheet '{sheet.name}' has no columns."

        query_lower = query.lower().strip()
        query_tokens = _tokenize(query)

        # Score each column
        matches = []
        for col in sheet.columns:
            col_name = col.title
            col_name_lower = col_name.lower()
            col_tokens = _tokenize(col_name)

            # Calculate different match scores
            scores = {
                "exact": 1.0 if col_name_lower == query_lower else 0.0,
                "contains": 1.0 if query_lower in col_name_lower else 0.0,
                "contained": 0.8 if col_name_lower in query_lower else 0.0,
                "similarity": _calculate_similarity(query_lower, col_name_lower),
                "word_match": 0.0,
            }

            # Word-based matching
            if query_tokens:
                matching_tokens = query_tokens & col_tokens
                scores["word_match"] = (
                    len(matching_tokens) / len(query_tokens) if query_tokens else 0.0
                )

            # Partial word match
            partial_word_score = 0.0
            for qt in query_tokens:
                for ct in col_tokens:
                    if qt in ct or ct in qt:
                        partial_word_score = max(partial_word_score, 0.6)
            scores["partial_word"] = partial_word_score

            # Compute weighted overall score
            overall_score = (
                scores["exact"] * 2.0
                + scores["contains"] * 1.5
                + scores["contained"] * 0.8
                + scores["word_match"] * 1.2
                + scores["partial_word"] * 0.8
                + scores["similarity"] * 0.5
            ) / 6.7

            if overall_score > 0.1:
                match_type = (
                    "exact"
                    if scores["exact"] > 0
                    else "contains"
                    if scores["contains"] > 0
                    else "word match"
                    if scores["word_match"] > 0.5
                    else "partial"
                    if scores["partial_word"] > 0
                    else "similar"
                )

                matches.append(
                    {
                        "name": col_name,
                        "id": col.id,
                        "type": col.type,
                        "score": overall_score,
                        "match_type": match_type,
                        "options": getattr(col, "options", None),
                    }
                )

        # Sort by score descending
        matches.sort(key=lambda x: x["score"], reverse=True)
        matches = matches[:max_results]

        if not matches:
            all_columns = ", ".join([c.title for c in sheet.columns])
            text_output = f"No columns found matching '{query}' in '{sheet.name}'.\n\n"
            text_output += f"Available columns: {all_columns}\n"
            return text_output

        # Format output
        text_output = f"Found {len(matches)} column(s) matching '{query}' in '{sheet.name}':\n\n"

        for i, match in enumerate(matches, 1):
            confidence = (
                "HIGH" if match["score"] > 0.7 else "MEDIUM" if match["score"] > 0.4 else "LOW"
            )
            text_output += f'{i}. "{match["name"]}" (Type: {match["type"]})\n'
            text_output += f"   Match: {match['match_type'].upper()} | Confidence: {confidence}\n"
            if match["options"]:
                text_output += f"   Options: {', '.join(match['options'][:5])}"
                if len(match["options"]) > 5:
                    text_output += f" (+{len(match['options']) - 5} more)"
                text_output += "\n"

        if len(matches) == 1 and matches[0]["score"] > 0.7:
            text_output += f'\n✓ Best match: "{matches[0]["name"]}" - proceeding with this column.'
        else:
            text_output += "\n**Reply with the number (e.g., '1') to select a column**, or use the exact column name."

        return text_output
    except Exception as e:
        return f"Error searching for columns: {str(e)}"


# =============================================================================
# SMART QUERY PLANNING (1) - Efficient multi-operation analysis on a single sheet
# =============================================================================

# In-memory sheet data cache for smart query planning
_sheet_data_cache: dict[int, tuple[Any, float]] = {}
_sheet_data_cache_ttl = 120  # 2 minutes for loaded sheet data


def _get_cached_sheet_data(client, sheet_id: int) -> Any:
    """Get sheet data from cache or fetch it."""
    global _sheet_data_cache

    now = time.time()
    if sheet_id in _sheet_data_cache:
        data, timestamp = _sheet_data_cache[sheet_id]
        if now - timestamp < _sheet_data_cache_ttl:
            return data

    # Fetch fresh data
    sheet = client.Sheets.get_sheet(sheet_id, page_size=5000)
    _sheet_data_cache[sheet_id] = (sheet, now)

    # Clean old entries
    for sid in list(_sheet_data_cache.keys()):
        if now - _sheet_data_cache[sid][1] > _sheet_data_cache_ttl:
            del _sheet_data_cache[sid]

    return sheet


@tool(cache_results=True)
def analyze_sheet(
    sheet_id: str,
    operations: str = "summary",
    column_name: str = None,
    filter_column: str = None,
    filter_value: str = None,
    filter_type: str = "contains",
    group_by: str = None,
) -> str:
    """
    Perform multiple analysis operations on a sheet in a single efficient call.
    This tool fetches the sheet data ONCE and performs all requested operations locally,
    avoiding multiple API calls.

    Use this instead of calling get_sheet + filter_rows + count_rows_by_column separately.

    Args:
        sheet_id: The sheet ID (numeric) or sheet name
        operations: Comma-separated list of operations to perform. Options:
            - "summary": Row count, column count, column types (default)
            - "columns": List all columns with types
            - "stats": Statistics including fill rates per column
            - "filter": Filter rows (requires filter_column and filter_value)
            - "count": Count rows grouped by a column (requires group_by)
            - "sample": Show first 5 rows as a sample
            - "all": Perform summary + columns + stats
        column_name: Specific column to analyze (for targeted stats)
        filter_column: Column to filter on (for "filter" operation)
        filter_value: Value to filter for (for "filter" operation)
        filter_type: Filter match type: "contains", "equals", "starts_with", "ends_with"
        group_by: Column to group and count by (for "count" operation)

    Returns comprehensive analysis results in a single response.

    Examples:
        analyze_sheet("Project Tracker", operations="summary,count", group_by="Status")
        analyze_sheet("Job Log", operations="filter,stats", filter_column="Status", filter_value="Active")
        analyze_sheet("Sales Data", operations="all")
    """
    if not sheet_id:
        return "Error: sheet_id parameter is required."

    try:
        client = get_smartsheet_client()
        resolved_id, sheet_name_resolved = _resolve_sheet_id(client, sheet_id)

        if not resolved_id:
            return (
                f"Error: Sheet '{sheet_id}' not found. Use find_sheets() to search for the sheet."
            )

        if not _is_sheet_allowed(resolved_id, sheet_name_resolved):
            return "Error: Access to sheet is not permitted."

        # Fetch sheet data (cached for 2 minutes)
        sheet = _get_cached_sheet_data(client, resolved_id)

        # Parse requested operations
        ops = [op.strip().lower() for op in operations.split(",")]
        if "all" in ops:
            ops = ["summary", "columns", "stats"]

        # Build column mappings
        columns = {col.id: col for col in sheet.columns}
        col_by_name = {col.title.lower(): col for col in sheet.columns}

        # Resolve fuzzy column names if provided
        def resolve_column(name):
            if not name:
                return None
            name_lower = name.lower()
            # Exact match first
            if name_lower in col_by_name:
                return col_by_name[name_lower]
            # Partial match
            for col_name, col in col_by_name.items():
                if name_lower in col_name or col_name in name_lower:
                    return col
            return None

        # Build output
        text_output = f"📊 Analysis: {sheet.name}\n"
        text_output += "=" * 60 + "\n\n"

        # ── SUMMARY ──
        if "summary" in ops:
            text_output += "## Summary\n"
            text_output += f"- Total Rows: {len(sheet.rows)}\n"
            text_output += f"- Total Columns: {len(sheet.columns)}\n"

            # Column type breakdown
            type_counts = {}
            for col in sheet.columns:
                type_counts[col.type] = type_counts.get(col.type, 0) + 1
            text_output += (
                f"- Column Types: {', '.join(f'{t}({c})' for t, c in type_counts.items())}\n"
            )
            text_output += "\n"

        # ── COLUMNS ──
        if "columns" in ops:
            text_output += "## Columns\n"
            for i, col in enumerate(sheet.columns, 1):
                text_output += f"{i}. {col.title} ({col.type})"
                if hasattr(col, "options") and col.options:
                    text_output += f" - Options: {', '.join(col.options[:3])}"
                    if len(col.options) > 3:
                        text_output += f" +{len(col.options) - 3} more"
                text_output += "\n"
            text_output += "\n"

        # ── STATS ──
        if "stats" in ops:
            text_output += "## Column Statistics\n"

            # Calculate fill rates
            col_fill_rates = {col.id: 0 for col in sheet.columns}
            for row in sheet.rows:
                for cell in row.cells:
                    if cell.value is not None and str(cell.value).strip():
                        col_fill_rates[cell.column_id] = col_fill_rates.get(cell.column_id, 0) + 1

            for col in sheet.columns:
                fill_count = col_fill_rates.get(col.id, 0)
                fill_pct = (fill_count / len(sheet.rows) * 100) if sheet.rows else 0
                bar = "█" * int(fill_pct / 10)
                text_output += f"- {col.title}: {fill_pct:.0f}% filled {bar}\n"
            text_output += "\n"

        # ── FILTER ──
        if "filter" in ops:
            if not filter_column or not filter_value:
                text_output += "## Filter\n⚠️ Skipped: filter_column and filter_value required\n\n"
            else:
                filter_col = resolve_column(filter_column)
                if not filter_col:
                    text_output += f"## Filter\n⚠️ Column '{filter_column}' not found. "
                    text_output += f"Available: {', '.join([c.title for c in sheet.columns])}\n\n"
                else:
                    text_output += f"## Filter: {filter_col.title} {filter_type} '{filter_value}'\n"

                    filter_value_lower = str(filter_value).lower()
                    matching_rows = []

                    for row in sheet.rows:
                        for cell in row.cells:
                            if cell.column_id == filter_col.id:
                                cell_value = str(cell.display_value or cell.value or "").lower()

                                match = False
                                if filter_type == "equals":
                                    match = cell_value == filter_value_lower
                                elif filter_type == "starts_with":
                                    match = cell_value.startswith(filter_value_lower)
                                elif filter_type == "ends_with":
                                    match = cell_value.endswith(filter_value_lower)
                                else:  # contains
                                    match = filter_value_lower in cell_value

                                if match:
                                    row_data = {"row_num": row.row_number}
                                    for c in row.cells:
                                        col_name = columns[c.column_id].title
                                        row_data[col_name] = c.display_value or c.value
                                    matching_rows.append(row_data)
                                break

                    text_output += f"Found {len(matching_rows)} matching rows\n"

                    # Show first 10 matches
                    for row in matching_rows[:10]:
                        row_str = " | ".join(
                            f"{k}: {v}" for k, v in row.items() if v is not None and k != "row_num"
                        )
                        text_output += f"  Row {row['row_num']}: {row_str[:100]}{'...' if len(row_str) > 100 else ''}\n"

                    if len(matching_rows) > 10:
                        text_output += f"  ... and {len(matching_rows) - 10} more\n"
                    text_output += "\n"

        # ── COUNT/GROUP BY ──
        if "count" in ops:
            if not group_by:
                text_output += "## Count by Column\n⚠️ Skipped: group_by parameter required\n\n"
            else:
                group_col = resolve_column(group_by)
                if not group_col:
                    text_output += f"## Count\n⚠️ Column '{group_by}' not found. "
                    text_output += f"Available: {', '.join([c.title for c in sheet.columns])}\n\n"
                else:
                    text_output += f"## Count by: {group_col.title}\n"

                    counts = {}
                    for row in sheet.rows:
                        for cell in row.cells:
                            if cell.column_id == group_col.id:
                                value = str(cell.display_value or cell.value or "(empty)")
                                counts[value] = counts.get(value, 0) + 1
                                break

                    total = len(sheet.rows)
                    for value, count in sorted(counts.items(), key=lambda x: -x[1]):
                        pct = (count / total * 100) if total else 0
                        bar = "█" * int(pct / 5)
                        text_output += f"  {value}: {count} ({pct:.1f}%) {bar}\n"
                    text_output += "\n"

        # ── SAMPLE ──
        if "sample" in ops:
            text_output += "## Sample Data (First 5 Rows)\n"

            for row in sheet.rows[:5]:
                row_data = []
                for cell in row.cells:
                    col_name = columns[cell.column_id].title
                    value = cell.display_value or cell.value
                    if value is not None:
                        row_data.append(f"{col_name}: {value}")
                text_output += f"Row {row.row_number}: {' | '.join(row_data[:4])}\n"
            text_output += "\n"

        return text_output

    except Exception as e:
        return f"Error analyzing sheet: {str(e)}"


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
    # Fuzzy search tools (2) - for finding sheets/columns by partial names
    find_sheets,
    find_columns,
    # Smart query planning (1) - efficient multi-operation analysis
    analyze_sheet,
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

# Async versions for all tools
SMARTSHEET_TOOLS_ASYNC = {tool.name: lambda t=tool: run_async(t) for tool in SMARTSHEET_TOOLS}


if __name__ == "__main__":
    print("Optimized SmartSheet Tools for Agno Agent (READ-ONLY)")
    print("=" * 60)
    print(f"\nTotal tools: {len(SMARTSHEET_TOOLS)}")
    print("\nOptimizations:")
    print("  ✓ Agno @tool decorator with cache_results")
    print("  ✓ Multi-level caching (L1 memory + L2 disk)")
    print("  ✓ Async tool execution support")
    print("  ✓ Pagination optimization")
    print(f"\nCache stats: {get_cache_stats()}")
