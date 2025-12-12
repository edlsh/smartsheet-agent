#!/usr/bin/env python3
"""
Smartsheet Agent - AI agent for querying Smartsheet data.

This agent can help you:
- Query current jobs and their status
- Get status updates on projects
- Analyze KPIs and metrics from your Smartsheet data

Features:
- Persistent memory: Remembers user preferences and frequently accessed sheets
- Session continuity: Resume conversations with full context
- Multi-LLM support via OpenRouter

Supports multiple LLM providers via OpenRouter:
- Anthropic Claude
- OpenAI GPT
- Google Gemini
- Meta Llama
- And many more!
"""

import getpass
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables FIRST before any other imports that need them
load_dotenv()

import httpx
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.exceptions import ModelProviderError
from agno.models.openrouter import OpenRouter
from prompt_toolkit import prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Optional LangWatch integration - gracefully degrade if not installed
try:
    import langwatch

    LANGWATCH_AVAILABLE = True
except ImportError:
    LANGWATCH_AVAILABLE = False
    langwatch = None

from smartsheet_tools import (
    SMARTSHEET_TOOLS,
    get_cache_stats,
)
from smartsheet_tools import (
    clear_cache as clear_smartsheet_cache,
)

# ============================================================================
# Retry Configuration for Transient Network Errors
# ============================================================================

# Exception types that should trigger a retry (transient network issues)
RETRYABLE_EXCEPTIONS = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.TimeoutException,
    ConnectionError,
    ModelProviderError,  # Agno wraps connection errors in this
)


def run_with_retry(agent: Agent, user_input: str, stream: bool = True) -> None:
    """
    Run agent with automatic retry for transient network errors.

    Uses exponential backoff: waits 1s, 2s, 4s between retries.
    Maximum 3 attempts before giving up.
    """

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=lambda retry_state: print(
            f"\n⚠️  Connection error. Retrying in {retry_state.next_action.sleep:.1f}s... "
            f"(attempt {retry_state.attempt_number}/3)"
        ),
        reraise=True,
    )
    def _run():
        agent.print_response(user_input, stream=stream)

    try:
        _run()
    except RETRYABLE_EXCEPTIONS as e:
        print(f"\n❌ Failed after 3 attempts. Network error: {type(e).__name__}")
        print("   Possible causes:")
        print("   • Unstable internet connection")
        print("   • API provider temporarily unavailable")
        print("   • Rate limiting")
        print("\n   Please try again in a moment.")


# Initialize LangWatch for tracing and prompt management
if LANGWATCH_AVAILABLE:
    langwatch.setup()


def get_user_id() -> str:
    """
    Get a stable user identifier for memory personalization.

    Uses the system username combined with machine info to create
    a consistent but privacy-preserving user ID.
    """
    # Get system username
    username = getpass.getuser()

    # Create a hash-based ID for privacy (doesn't expose actual username)
    # but is consistent across sessions on the same machine
    machine_id = f"{username}@{os.uname().nodename}"
    user_hash = hashlib.sha256(machine_id.encode()).hexdigest()[:16]

    return f"user_{user_hash}"


# ============================================================================
# Slash Commands Configuration
# ============================================================================

SLASH_COMMANDS = {
    "/help": "Show all available commands",
    "/clear": "Start a new conversation (forget history)",
    "/model": "Switch models (e.g., /model openai/gpt-4o)",
    "/history": "Show conversation history info",
    "/memory": "Show what the agent remembers about you",
    "/forget": "Clear agent's memories about you",
    "/sheets": "List all available Smartsheets",
    "/reports": "List all available Smartsheet reports",
    "/summary": "Get summary stats for a sheet (e.g., /summary SheetName)",
    "/columns": "Show column metadata for a sheet (e.g., /columns SheetName)",
    "/search": "Search across all sheets (e.g., /search keyword)",
    "/refresh": "Clear Smartsheet cache (force fresh data on next request)",
    "/cache": "Show cache statistics (L1 memory, L2 disk)",
    "/quit": "Exit the application",
}


class SlashCommandCompleter(Completer):
    """Autocomplete for slash commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # Only show completions if text starts with '/'
        if text.startswith("/"):
            for cmd, description in SLASH_COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=description,
                    )


# Style for the prompt
PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
    }
)

# Ensure storage directory exists
STORAGE_DIR = Path("tmp")
STORAGE_DIR.mkdir(exist_ok=True)
DB_FILE = str(STORAGE_DIR / "smartsheet_agent.db")

# Default model - can be overridden via environment variable
# NOTE: Model must support function/tool calling for Smartsheet tools to work
# Free options: google/gemini-2.0-flash-001
# Paid options: openai/gpt-4o-mini, anthropic/claude-3-5-haiku, google/gemini-2.5-flash
DEFAULT_MODEL = "google/gemini-2.5-flash"

# Model routing configuration for efficiency
MODEL_ROUTING = {
    "fast": "google/gemini-2.0-flash-001",  # Simple queries, listing, basic lookups
    "default": "google/gemini-2.5-flash",  # Most queries
    "complex": "anthropic/claude-3-5-sonnet",  # Complex analysis, comparisons
}

# Query patterns for smart routing
SIMPLE_QUERY_PATTERNS = [
    "list sheets",
    "show sheets",
    "what sheets",
    "available sheets",
    "list reports",
    "show reports",
    "what reports",
    "list workspaces",
    "show workspaces",
    "help",
    "what can you do",
    "commands",
    "how to",
    "/sheets",
    "/reports",
    "/help",
]

COMPLEX_QUERY_PATTERNS = [
    "analyze",
    "compare",
    "summarize all",
    "trend",
    "pattern",
    "explain why",
    "what caused",
    "relationship between",
    "forecast",
    "predict",
    "correlation",
    "insights",
    "comprehensive",
    "detailed analysis",
    "breakdown of all",
]


def get_routed_model(query: str) -> str:
    """
    Route queries to appropriate models based on complexity.

    Simple queries -> Fast model (lower cost, lower latency)
    Complex queries -> Capable model (better reasoning)
    Default -> Balanced model
    """
    query_lower = query.lower().strip()

    # Check for explicit model override
    env_model = os.getenv("OPENROUTER_MODEL")
    if env_model:
        return env_model

    # Check for simple patterns -> use fast model
    for pattern in SIMPLE_QUERY_PATTERNS:
        if pattern in query_lower:
            return MODEL_ROUTING["fast"]

    # Check for complex patterns -> use capable model
    for pattern in COMPLEX_QUERY_PATTERNS:
        if pattern in query_lower:
            return MODEL_ROUTING["complex"]

    # Default model
    return MODEL_ROUTING["default"]


def get_system_prompt() -> str:
    """Get the system prompt from LangWatch prompt management or fallback."""
    if LANGWATCH_AVAILABLE:
        try:
            prompt = langwatch.prompts.get("smartsheet-agent")
            # Extract the system message content from the prompt
            for message in prompt.messages:
                if message.get("role") == "system":
                    return message.get("content", "")
        except Exception:
            pass  # Fall through to default prompt

    # Default system prompt when LangWatch is not available
    return """You are a Smartsheet data assistant with READ-ONLY access to Smartsheet data.

Your capabilities:
- List and search sheets, reports, dashboards, and workspaces
- Query and filter data from sheets
- Analyze metrics and generate summaries
- View cell history and audit information
- Access attachments, discussions, and sharing info

Important guidelines:
- You can only READ data - no modifications are possible
- Always use the appropriate tool for the task
- When users ask about sheets by partial name, use find_sheets() first
- When users ask about columns by partial name, use find_columns() first
- For complex analysis, prefer analyze_sheet() to minimize API calls
- Present data clearly and concisely

Be helpful, accurate, and efficient in answering questions about Smartsheet data."""


def get_model() -> OpenRouter:
    """Get the configured OpenRouter model."""
    model_id = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
    return OpenRouter(id=model_id)


# Shared database instance for memory persistence
_db = SqliteDb(db_file=DB_FILE)


def create_agent(user_id: str = None, session_id: str = None, model_id: str = None) -> Agent:
    """
    Create and configure the Smartsheet Agent with conversation memory.

    Args:
        user_id: Unique user identifier for personalized memory
        session_id: Session ID for conversation continuity
        model_id: Optional model override (for smart routing)

    Returns:
        Configured Agent with persistent memory enabled
    """
    # Use provided model or default
    model = OpenRouter(id=model_id) if model_id else get_model()

    return Agent(
        name="Smartsheet Agent",
        model=model,
        tools=SMARTSHEET_TOOLS,
        instructions=get_system_prompt(),
        markdown=True,
        # Database configuration for session and memory persistence
        db=_db,
        user_id=user_id,
        session_id=session_id,
        # Conversation history - OPTIMIZED: reduced from 10 to 5 for efficiency
        add_history_to_context=True,  # Include conversation history in prompts
        num_history_runs=5,  # Remember last 5 conversation exchanges (reduced from 10)
        # Persistent memory features
        enable_user_memories=True,  # Remember facts about the user
        enable_session_summaries=True,  # Summarize sessions for context
    )


def get_user_memories(user_id: str) -> list:
    """Get all memories stored for a user."""
    # Query memories directly from the database
    try:
        memories = _db.get_memories(user_id=user_id)
        return memories if memories else []
    except Exception:
        return []


def clear_user_memories(user_id: str) -> None:
    """Clear all memories for a user."""
    try:
        _db.clear_memories(user_id=user_id)
    except Exception as e:
        print(f"Note: Could not clear memories: {e}")


def check_environment() -> bool:
    """Verify required environment variables are set."""
    missing = []

    if not os.getenv("OPENROUTER_API_KEY"):
        missing.append("OPENROUTER_API_KEY")
        print("Error: OPENROUTER_API_KEY environment variable is not set.")
        print("Get your API key from: https://openrouter.ai/settings/keys")

    if not os.getenv("SMARTSHEET_ACCESS_TOKEN"):
        missing.append("SMARTSHEET_ACCESS_TOKEN")
        print("Error: SMARTSHEET_ACCESS_TOKEN environment variable is not set.")
        print(
            "Get your token from: https://app.smartsheet.com/b/home (Account > Personal Settings > API Access)"
        )

    return len(missing) == 0


def _run_agent_impl(user_prompt: str) -> None:
    """Internal implementation of run_agent."""
    if not check_environment():
        return

    model_id = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)

    print(f"\n{'=' * 60}")
    print("Smartsheet Agent")
    print(f"{'=' * 60}")
    print(f"Model: {model_id}")
    print(f"\nQuery: {user_prompt}\n")
    print("-" * 60)

    agent = create_agent()
    run_with_retry(agent, user_prompt, stream=True)


def run_agent(user_prompt: str) -> None:
    """Run the Smartsheet Agent with the given prompt."""
    if LANGWATCH_AVAILABLE:
        # Use LangWatch tracing when available
        @langwatch.trace(name="smartsheet_agent_query")
        def traced_run():
            _run_agent_impl(user_prompt)

        traced_run()
    else:
        _run_agent_impl(user_prompt)


def show_help() -> None:
    """Display all available slash commands."""
    print("\n" + "─" * 50)
    print("📋 Available Commands")
    print("─" * 50)
    for cmd, description in SLASH_COMMANDS.items():
        print(f"  {cmd:<12} {description}")
    print("─" * 50)
    print("💡 Tip: Type '/' to see autocomplete suggestions\n")


def interactive_mode() -> None:
    """Run the agent in interactive mode with slash command autocomplete."""
    if not check_environment():
        return

    # Get stable user ID for memory personalization
    user_id = get_user_id()
    model_id = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)

    print("\n" + "=" * 60)
    print("🤖 Smartsheet Agent - Interactive Mode")
    print("=" * 60)
    print(f"📊 Model: {model_id}")
    print("🧠 Memory: Enabled (your preferences will be remembered)")
    print("\nAsk questions about your Smartsheet data.")
    print("Type '/' for command autocomplete, or '/help' for all commands.\n")

    agent = create_agent(user_id=user_id)
    completer = SlashCommandCompleter()

    while True:
        try:
            # Use prompt_toolkit with autocomplete
            user_input = prompt(
                "You: ",
                completer=completer,
                style=PROMPT_STYLE,
                complete_while_typing=True,
            ).strip()

            if not user_input:
                continue

            # ── Slash Command Handling ──────────────────────────────────

            if user_input.lower() in ("/quit", "/exit", "quit", "exit", "q"):
                print("\n👋 Goodbye!")
                break

            if user_input.lower() == "/help":
                show_help()
                continue

            if user_input.lower().startswith("/model "):
                new_model = user_input[7:].strip()
                if not new_model:
                    print("\n⚠️  Usage: /model <model_name>")
                    print("   Example: /model openai/gpt-4o")
                    continue
                os.environ["OPENROUTER_MODEL"] = new_model
                agent = create_agent(user_id=user_id)  # Preserve user_id for memory continuity
                print(f"\n✅ Switched to model: {new_model}")
                continue

            if user_input.lower() == "/clear":
                agent = create_agent(
                    user_id=user_id
                )  # Create fresh agent with new session but same user
                print("\n✅ Conversation cleared. Starting fresh!")
                print("   (Your memories are preserved. Use /forget to clear them.)")
                continue

            if user_input.lower() == "/history":
                session_id = getattr(agent, "session_id", None)
                print(f"\n📜 Session ID: {session_id or 'Not set'}")
                print(f"   History runs: {agent.num_history_runs}")
                print(f"   User ID: {user_id}")
                continue

            if user_input.lower() == "/memory":
                print("\n🧠 Agent Memories")
                print("-" * 40)
                try:
                    memories = agent.get_user_memories(user_id=user_id)
                    if memories:
                        for i, mem in enumerate(memories, 1):
                            # Handle both dict and object formats
                            if hasattr(mem, "memory"):
                                content = mem.memory
                            elif isinstance(mem, dict):
                                content = mem.get("memory", mem.get("content", str(mem)))
                            else:
                                content = str(mem)
                            print(f"  {i}. {content}")
                    else:
                        print("  No memories stored yet.")
                        print("  💡 Tip: Tell me about your preferences or frequently used sheets!")
                except Exception as e:
                    print(f"  Could not retrieve memories: {e}")
                print()
                continue

            if user_input.lower() == "/forget":
                print("\n⚠️  This will clear all memories about you.")
                confirm = input("Are you sure? (yes/no): ").strip().lower()
                if confirm == "yes":
                    clear_user_memories(user_id)
                    print("✅ All memories cleared.")
                else:
                    print("❌ Cancelled.")
                continue

            if user_input.lower() == "/refresh":
                clear_smartsheet_cache()
                print("\n✅ Smartsheet cache cleared. Next request will fetch fresh data.")
                continue

            if user_input.lower() == "/cache":
                stats = get_cache_stats()
                print("\n📊 Cache Statistics")
                print("-" * 40)
                print(f"  L1 (Memory): {stats['l1_entries']}/{stats['l1_max']} entries")
                print(f"  L2 (Disk):   {stats['l2_entries']} entries")
                print("\n💡 Use /refresh to clear cache and fetch fresh data.")
                continue

            if user_input.lower() == "/sheets":
                print("\n📋 Fetching available sheets...")
                run_with_retry(agent, "List all available Smartsheets")
                continue

            if user_input.lower() == "/reports":
                print("\n📊 Fetching available reports...")
                run_with_retry(agent, "List all available Smartsheet reports")
                continue

            if user_input.lower().startswith("/summary "):
                sheet_name = user_input[9:].strip()
                if not sheet_name:
                    print("\n⚠️  Usage: /summary <sheet_name>")
                    continue
                print(f"\n📊 Getting summary for '{sheet_name}'...")
                run_with_retry(
                    agent,
                    f"Get a detailed summary and statistics for the sheet named '{sheet_name}'",
                )
                continue

            if user_input.lower().startswith("/columns "):
                sheet_name = user_input[9:].strip()
                if not sheet_name:
                    print("\n⚠️  Usage: /columns <sheet_name>")
                    continue
                print(f"\n📋 Getting columns for '{sheet_name}'...")
                run_with_retry(
                    agent, f"Get detailed column metadata for the sheet named '{sheet_name}'"
                )
                continue

            if user_input.lower().startswith("/search "):
                query = user_input[8:].strip()
                if not query:
                    print("\n⚠️  Usage: /search <keyword>")
                    continue
                print(f"\n🔍 Searching for '{query}'...")
                run_with_retry(agent, f"Search across all sheets for '{query}'")
                continue

            # Unknown slash command
            if user_input.startswith("/"):
                print(f"\n⚠️  Unknown command: {user_input.split()[0]}")
                print("   Type '/help' to see available commands.")
                continue

            # ── Regular Query with Smart Routing ───────────────────────

            # Get optimal model for this query
            routed_model = get_routed_model(user_input)
            current_model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)

            # If routing suggests a different model, create a new agent for this query
            if routed_model != current_model and not os.getenv("OPENROUTER_MODEL"):
                # Create temporary agent with routed model
                temp_agent = create_agent(user_id=user_id, model_id=routed_model)
                run_with_retry(temp_agent, user_input)
            else:
                run_with_retry(agent, user_input)

        except KeyboardInterrupt:
            print("\n\n👋 Session interrupted. Goodbye!")
            break
        except EOFError:
            print("\n\n👋 Goodbye!")
            break


def main() -> None:
    """Main entry point."""
    import sys

    if len(sys.argv) > 1:
        # Run with command-line prompt
        user_prompt = " ".join(sys.argv[1:])
        run_agent(user_prompt)
    else:
        # Run in interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
