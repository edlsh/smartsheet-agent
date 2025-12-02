#!/usr/bin/env python3
"""
Parallel Workflow Support for Smartsheet Agent.

This module provides workflow patterns for executing complex queries 
that benefit from parallel execution.

Example use cases:
- Fetching data from multiple sheets simultaneously
- Running search and filter operations in parallel
- Gathering organizational context (workspaces, folders, sheets) at once
"""

import asyncio
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from agno.agent import Agent
from agno.models.openrouter import OpenRouter

from smartsheet_tools_optimized import (
    list_sheets,
    workspace,
    folder,
    report,
    sight,
    navigation,
    get_sheet,
    filter_rows,
    search,
)


# Thread pool for parallel execution
_executor = ThreadPoolExecutor(max_workers=6)


def run_parallel_tools(tool_calls: List[Dict[str, Any]], timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Execute multiple tool calls in parallel.
    
    Args:
        tool_calls: List of dicts with 'tool' (function) and 'kwargs' (arguments)
        timeout: Maximum time to wait for all calls (seconds)
    
    Returns:
        List of results with 'tool_name', 'result', and 'error' (if any)
    
    Example:
        results = run_parallel_tools([
            {'tool': list_sheets, 'kwargs': {}},
            {'tool': workspace, 'kwargs': {}},
            {'tool': report, 'kwargs': {}},
        ])
    """
    results = []
    futures = {}
    
    for call in tool_calls:
        tool = call['tool']
        kwargs = call.get('kwargs', {})
        future = _executor.submit(tool, **kwargs)
        futures[future] = tool.__name__
    
    for future in as_completed(futures, timeout=timeout):
        tool_name = futures[future]
        try:
            result = future.result()
            results.append({
                'tool_name': tool_name,
                'result': result,
                'error': None
            })
        except Exception as e:
            results.append({
                'tool_name': tool_name,
                'result': None,
                'error': str(e)
            })
    
    return results


async def run_parallel_tools_async(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Execute multiple tool calls in parallel (async version).
    
    Args:
        tool_calls: List of dicts with 'tool' (function) and 'kwargs' (arguments)
    
    Returns:
        List of results with 'tool_name', 'result', and 'error' (if any)
    """
    loop = asyncio.get_event_loop()
    
    async def run_tool(tool, kwargs):
        return await loop.run_in_executor(_executor, lambda: tool(**kwargs))
    
    tasks = []
    tool_names = []
    
    for call in tool_calls:
        tool = call['tool']
        kwargs = call.get('kwargs', {})
        tasks.append(run_tool(tool, kwargs))
        tool_names.append(tool.__name__)
    
    results = []
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, result in enumerate(completed):
        if isinstance(result, Exception):
            results.append({
                'tool_name': tool_names[i],
                'result': None,
                'error': str(result)
            })
        else:
            results.append({
                'tool_name': tool_names[i],
                'result': result,
                'error': None
            })
    
    return results


# =============================================================================
# PRE-BUILT PARALLEL WORKFLOWS
# =============================================================================

def get_organization_overview() -> str:
    """
    Get a comprehensive organization overview by fetching:
    - All sheets
    - All workspaces
    - All reports
    - All dashboards
    
    Runs all queries in parallel for maximum efficiency.
    """
    start_time = time.time()
    
    results = run_parallel_tools([
        {'tool': list_sheets, 'kwargs': {}},
        {'tool': workspace, 'kwargs': {}},
        {'tool': report, 'kwargs': {}},
        {'tool': sight, 'kwargs': {}},
    ])
    
    elapsed = time.time() - start_time
    
    output = f"📊 Organization Overview (fetched in {elapsed:.2f}s)\n"
    output += "=" * 60 + "\n\n"
    
    for r in results:
        if r['error']:
            output += f"**{r['tool_name']}**: Error - {r['error']}\n\n"
        else:
            output += f"**{r['tool_name']}**:\n{r['result']}\n\n"
    
    return output


def parallel_search_sheets(query: str, sheet_ids: List[str]) -> str:
    """
    Search for a term across multiple sheets in parallel.
    
    Args:
        query: Search term
        sheet_ids: List of sheet IDs to search within
    
    Returns:
        Combined search results from all sheets
    """
    start_time = time.time()
    
    tool_calls = [
        {'tool': search, 'kwargs': {'query': query, 'sheet_id': sid}}
        for sid in sheet_ids
    ]
    
    results = run_parallel_tools(tool_calls)
    
    elapsed = time.time() - start_time
    
    output = f"🔍 Parallel Search Results for '{query}' (fetched in {elapsed:.2f}s)\n"
    output += "=" * 60 + "\n\n"
    
    for r in results:
        if r['error']:
            output += f"**Search Error**: {r['error']}\n\n"
        else:
            output += f"{r['result']}\n\n"
    
    return output


def parallel_get_sheets(sheet_ids: List[str], max_rows: int = 100) -> str:
    """
    Fetch data from multiple sheets in parallel.
    
    Args:
        sheet_ids: List of sheet IDs or names
        max_rows: Maximum rows per sheet
    
    Returns:
        Combined sheet data
    """
    start_time = time.time()
    
    tool_calls = [
        {'tool': get_sheet, 'kwargs': {'sheet_id': sid, 'max_rows': max_rows}}
        for sid in sheet_ids
    ]
    
    results = run_parallel_tools(tool_calls)
    
    elapsed = time.time() - start_time
    
    output = f"📋 Parallel Sheet Data (fetched in {elapsed:.2f}s)\n"
    output += "=" * 60 + "\n\n"
    
    for r in results:
        if r['error']:
            output += f"**Error**: {r['error']}\n\n"
        else:
            output += f"{r['result']}\n\n"
    
    return output


def get_home_and_favorites() -> str:
    """
    Get both home view and favorites in parallel.
    """
    start_time = time.time()
    
    results = run_parallel_tools([
        {'tool': navigation, 'kwargs': {'view': 'home'}},
        {'tool': navigation, 'kwargs': {'view': 'favorites'}},
    ])
    
    elapsed = time.time() - start_time
    
    output = f"🏠 Home & Favorites (fetched in {elapsed:.2f}s)\n"
    output += "=" * 60 + "\n\n"
    
    for r in results:
        if r['error']:
            output += f"**Error**: {r['error']}\n\n"
        else:
            output += f"{r['result']}\n\n"
    
    return output


# =============================================================================
# WORKFLOW DETECTION & ROUTING
# =============================================================================

def detect_workflow_opportunity(query: str) -> Optional[str]:
    """
    Detect if a query could benefit from a parallel workflow.
    
    Returns the workflow name if applicable, None otherwise.
    """
    query_lower = query.lower()
    
    # Organization overview patterns
    overview_patterns = [
        "organization overview", "org overview", "everything", "all sheets",
        "full overview", "comprehensive view", "show me everything"
    ]
    if any(p in query_lower for p in overview_patterns):
        return "organization_overview"
    
    # Home and favorites patterns
    home_patterns = ["home and favorites", "favorites and home", "my home"]
    if any(p in query_lower for p in home_patterns):
        return "home_and_favorites"
    
    return None


def execute_workflow(workflow_name: str) -> str:
    """
    Execute a named workflow.
    """
    workflows = {
        "organization_overview": get_organization_overview,
        "home_and_favorites": get_home_and_favorites,
    }
    
    if workflow_name in workflows:
        return workflows[workflow_name]()
    else:
        return f"Unknown workflow: {workflow_name}"


if __name__ == "__main__":
    print("Smartsheet Parallel Workflows")
    print("=" * 60)
    print("\nAvailable workflows:")
    print("  - get_organization_overview()")
    print("  - parallel_search_sheets(query, sheet_ids)")
    print("  - parallel_get_sheets(sheet_ids)")
    print("  - get_home_and_favorites()")
    print("\nParallel tool execution:")
    print("  - run_parallel_tools(tool_calls)")
    print("  - run_parallel_tools_async(tool_calls)")
