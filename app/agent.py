"""
Enhanced Smartsheet Agent with structured outputs and session management.

This module provides:
- Structured output support via Pydantic models
- User-based session management with persistent memory
- Optional LangWatch instrumentation
- Reusable agent factory
"""

import os
from typing import Optional

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openrouter import OpenRouter
from dotenv import load_dotenv

# Optional LangWatch integration
try:
    import langwatch

    LANGWATCH_AVAILABLE = True
except ImportError:
    LANGWATCH_AVAILABLE = False
    langwatch = None

from smartsheet_tools import SMARTSHEET_TOOLS

# Load environment variables
load_dotenv()

# Constants
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
DB_FILE = "tmp/smartsheet_agent.db"

# Default system prompt (used when LangWatch is not available)
DEFAULT_SYSTEM_PROMPT = """You are a Smartsheet data assistant with READ-ONLY access to Smartsheet data.

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


class SmartsheetAgentFactory:
    """Factory for creating Smartsheet agents with proper configuration."""

    _instance: Optional["SmartsheetAgentFactory"] = None
    _agents: dict[str, Agent] = {}
    _db: SqliteDb | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agents = {}
            cls._instance._db = None
        return cls._instance

    @property
    def db(self) -> SqliteDb:
        """Get or create the shared database instance."""
        if self._db is None:
            self._db = SqliteDb(db_file=DB_FILE)
        return self._db

    @staticmethod
    def get_system_prompt() -> str:
        """Get the system prompt from LangWatch prompt management or fallback."""
        if LANGWATCH_AVAILABLE:
            try:
                prompt = langwatch.prompts.get("smartsheet-agent")
                for message in prompt.messages:
                    if message.get("role") == "system":
                        return message.get("content", "")
            except Exception:
                pass  # Fall through to default
        return DEFAULT_SYSTEM_PROMPT

    @staticmethod
    def get_model(model_id: str | None = None) -> OpenRouter:
        """Get the configured OpenRouter model."""
        model = model_id or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        return OpenRouter(id=model)

    def get_agent(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
        model_id: str | None = None,
        enable_memory: bool = True,
    ) -> Agent:
        """
        Get or create an agent for a specific user/session.

        Args:
            user_id: Unique identifier for the user (enables personalized memory)
            session_id: Specific session ID (for resuming conversations)
            model_id: Override the default model
            enable_memory: Enable persistent user memories (default: True)

        Returns:
            Configured Smartsheet Agent with persistent memory
        """
        # Create cache key
        cache_key = f"{user_id or 'default'}:{session_id or 'new'}"

        # Return cached agent if available and no model override
        if cache_key in self._agents and model_id is None:
            return self._agents[cache_key]

        # Create new agent with persistent memory
        agent = Agent(
            name="Smartsheet Agent",
            model=self.get_model(model_id),
            tools=SMARTSHEET_TOOLS,
            instructions=self.get_system_prompt(),
            markdown=True,
            # Database for session and memory storage
            db=self.db,
            # User and session identification
            user_id=user_id,
            session_id=session_id,
            # Conversation history - OPTIMIZED: reduced from 10 to 5 for efficiency
            add_history_to_context=True,
            num_history_runs=5,  # Reduced from 10 for better performance
            # Persistent memory features
            enable_user_memories=enable_memory,  # Remember facts about the user
            enable_session_summaries=enable_memory,  # Summarize sessions for context
        )

        # Cache the agent
        self._agents[cache_key] = agent
        return agent

    def get_user_memories(self, user_id: str) -> list:
        """
        Get all memories stored for a specific user.

        Args:
            user_id: The user's unique identifier

        Returns:
            List of memory objects for the user
        """
        # Create a temporary agent to access memories
        agent = self.get_agent(user_id=user_id)
        return agent.get_user_memories(user_id=user_id)

    def clear_user_memories(self, user_id: str) -> None:
        """
        Clear all memories for a specific user.

        Args:
            user_id: The user's unique identifier
        """
        self.db.clear_memories(user_id=user_id)

    def clear_cache(self):
        """Clear the agent cache."""
        self._agents.clear()


# Global factory instance
agent_factory = SmartsheetAgentFactory()


def _run_smartsheet_agent_impl(
    query: str,
    user_id: str | None = None,
    session_id: str | None = None,
    model_id: str | None = None,
    stream: bool = True,
) -> str:
    """Internal implementation of run_smartsheet_agent."""
    agent = agent_factory.get_agent(
        user_id=user_id,
        session_id=session_id,
        model_id=model_id,
    )

    if stream:
        agent.print_response(query, stream=True)
        return ""
    else:
        response = agent.run(query)
        return response.content if response else ""


def run_smartsheet_agent(
    query: str,
    user_id: str | None = None,
    session_id: str | None = None,
    model_id: str | None = None,
    stream: bool = True,
) -> str:
    """
    Run the Smartsheet Agent with a query.

    Args:
        query: The user's question or command
        user_id: Optional user ID for personalized sessions
        session_id: Optional session ID for conversation continuity
        model_id: Optional model override
        stream: Whether to stream the response

    Returns:
        The agent's response as a string
    """
    if LANGWATCH_AVAILABLE:

        @langwatch.trace(name="smartsheet_agent_run")
        def traced_run():
            return _run_smartsheet_agent_impl(query, user_id, session_id, model_id, stream)

        return traced_run()
    else:
        return _run_smartsheet_agent_impl(query, user_id, session_id, model_id, stream)


def create_agent_for_testing(
    user_id: str | None = None,
    model_id: str | None = None,
) -> Agent:
    """
    Create a fresh agent instance for testing.

    This bypasses the cache to ensure clean state for tests.

    Args:
        user_id: Optional user ID
        model_id: Optional model override

    Returns:
        Fresh Agent instance
    """
    return Agent(
        name="Smartsheet Agent",
        model=SmartsheetAgentFactory.get_model(model_id),
        tools=SMARTSHEET_TOOLS,
        instructions=SmartsheetAgentFactory.get_system_prompt(),
        markdown=True,
    )
