"""
Scenario tests for the Smartsheet Agent.

These tests validate that the agent correctly handles user queries
and calls the appropriate Smartsheet tools.
"""

import os
import pytest
import scenario
from dotenv import load_dotenv

# Load environment variables for tests
load_dotenv()

# Configure Scenario to use OpenRouter (via litellm) since we have OPENROUTER_API_KEY
# Set up litellm to use OpenRouter
os.environ["LITELLM_BASE_URL"] = "https://openrouter.ai/api/v1"
os.environ["LITELLM_API_KEY"] = os.getenv("OPENROUTER_API_KEY", "")
os.environ["OPENAI_API_KEY"] = os.getenv("OPENROUTER_API_KEY", "")
os.environ["OPENAI_BASE_URL"] = "https://openrouter.ai/api/v1"

# Configure Scenario with a model that supports tool calling via OpenRouter
scenario.configure(default_model="openai/gpt-4o-mini")


def create_smartsheet_agent():
    """Create a fresh instance of the Smartsheet agent for testing."""
    from agno.agent import Agent
    from agno.models.openrouter import OpenRouter
    from smartsheet_tools_optimized import SMARTSHEET_TOOLS
    import langwatch

    # Get system prompt from LangWatch
    prompt = langwatch.prompts.get("smartsheet-agent")
    system_prompt = ""
    for message in prompt.messages:
        if message.get("role") == "system":
            system_prompt = message.get("content", "")
            break

    return Agent(
        name="Smartsheet Agent",
        model=OpenRouter(id=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")),
        tools=SMARTSHEET_TOOLS,
        instructions=system_prompt,
        markdown=True,
    )


class SmartsheetAgentAdapter(scenario.AgentAdapter):
    """Adapter to wrap the Smartsheet Agent for Scenario testing."""

    def __init__(self):
        self.agent = create_smartsheet_agent()

    async def call(self, input: scenario.AgentInput) -> scenario.AgentReturnTypes:
        """Process input and return agent response."""
        # Get the last user message
        last_message = input.messages[-1] if input.messages else None
        if not last_message or last_message.get("role") != "user":
            return {"role": "assistant", "content": "I need a question to answer."}

        user_query = last_message.get("content", "")

        # Run the agent and get response
        response = self.agent.run(user_query)

        # Return the response content
        return {"role": "assistant", "content": response.content}


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_understands_list_sheets_request():
    """Test that the agent understands requests to list sheets."""
    result = await scenario.run(
        name="list sheets request",
        description="""
        User wants to see all available Smartsheets.
        The agent should understand this request and attempt to list sheets.
        Note: The agent may report API connection issues if credentials aren't configured.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should understand the user wants to see available sheets",
                "The agent should attempt to help with the request or explain why it cannot (such as missing credentials)",
            ])
        ],
        script=[
            scenario.user("What sheets do I have access to?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_handles_status_query():
    """Test that the agent handles queries about project status."""
    result = await scenario.run(
        name="project status query",
        description="""
        User asks about the status of a project or jobs.
        The agent should understand this and provide guidance on how to help.
        Note: The agent may report API connection issues if credentials aren't configured.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should understand the user is asking about project or job status",
                "The agent should attempt to help or explain what it needs (like API credentials) to assist",
            ])
        ],
        script=[
            scenario.user("What's the current status of our active projects?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_explains_capabilities():
    """Test that the agent can explain what it can do."""
    result = await scenario.run(
        name="capabilities explanation",
        description="""
        User asks what the agent can help with.
        The agent should explain its Smartsheet data retrieval capabilities.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should explain it can help with Smartsheet data",
                "The agent should mention some specific capabilities like viewing sheets, reports, or searching",
                "The response should be informative and helpful",
            ])
        ],
        script=[
            scenario.user("What can you help me with?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_understands_search_request():
    """Test that the agent understands search requests."""
    result = await scenario.run(
        name="search request",
        description="""
        User wants to search for specific data in Smartsheets.
        The agent should understand and attempt to help with the search.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should understand the user wants to search for data",
                "The agent should offer to search or ask clarifying questions about what to search for",
                "The response should be relevant to finding data in Smartsheets",
            ])
        ],
        script=[
            scenario.user("Can you search for all items marked as 'overdue'?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_read_only_constraint():
    """Test that the agent correctly states it cannot modify data."""
    result = await scenario.run(
        name="read only constraint",
        description="""
        User asks the agent to create or modify data.
        The agent should clearly explain it is read-only.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should clearly communicate that it cannot create, update, or delete data",
                "The agent should explain it has read-only access",
            ])
        ],
        script=[
            scenario.user("Can you add a new row to my project tracker sheet?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_uses_fuzzy_search_for_partial_sheet_names():
    """Test that the agent searches for sheets when given a partial/approximate name."""
    result = await scenario.run(
        name="fuzzy sheet name search",
        description="""
        User asks about a sheet using an informal or partial name.
        The agent should proactively search for matching sheets and present options or
        identify the best match. The key behavior is that the agent searches first
        rather than simply asking for the exact name.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should search for sheets matching the user's description",
                "The agent should identify or present potential matches to the user",
                "The agent should be proactive in finding the sheet rather than just asking for the exact name",
            ])
        ],
        script=[
            scenario.user("How many total jobs are in the job log retainer sheet?"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_handles_informal_sheet_references():
    """Test that the agent handles informal sheet references by searching."""
    result = await scenario.run(
        name="informal sheet reference handling",
        description="""
        User refers to a sheet informally without the exact name.
        The agent should search for similar sheets and present options for the user to choose from.
        This demonstrates the fuzzy matching workflow: search first, then confirm with user.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should attempt to search for sheets matching the user's description",
                "The agent should present search results or options to the user",
                "The agent should proactively help find the sheet rather than just asking for the exact name",
            ])
        ],
        script=[
            scenario.user("Show me the status breakdown from that project tracker spreadsheet"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_uses_efficient_analysis_for_complex_queries():
    """Test that the agent attempts to provide comprehensive analysis for complex queries."""
    result = await scenario.run(
        name="efficient multi-operation analysis",
        description="""
        User asks a complex question about a specific sheet that requires multiple pieces 
        of information. The agent should search for the sheet and either provide analysis 
        or confirm before proceeding. The key is that the agent attempts to help with 
        the multi-faceted request.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should search for the sheet or identify it from the user's description",
                "The agent should acknowledge the user's request for comprehensive analysis",
                "The agent should be helpful and offer to provide the requested information",
            ])
        ],
        script=[
            scenario.user("Give me a full analysis of the Job-Log / Retainers sheet - how many rows, breakdown by status, and column info"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"


@pytest.mark.agent_test
@pytest.mark.asyncio
async def test_agent_searches_for_columns_by_partial_name():
    """Test that the agent can find columns by partial/informal names."""
    result = await scenario.run(
        name="fuzzy column search",
        description="""
        User references a column by partial or informal name (e.g., "status" instead of "Job Status").
        The agent should search for matching columns and either:
        - Use the best match if confident
        - Present options for clarification if multiple matches exist
        The agent should NOT just fail with "column not found" without trying to search.
        """,
        agents=[
            SmartsheetAgentAdapter(),
            scenario.UserSimulatorAgent(),
            scenario.JudgeAgent(criteria=[
                "The agent should attempt to find or search for columns matching the user's description",
                "The agent should either find a match or present similar column options",
                "The agent should be proactive in helping resolve column names rather than just failing",
            ])
        ],
        script=[
            scenario.user("In the Job-Log / Retainers sheet, filter by the status column where it shows active jobs"),
            scenario.agent(),
            scenario.judge(),
        ],
    )

    assert result.success, f"Test failed: {result}"
