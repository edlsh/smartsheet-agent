"""
Pytest-compatible evaluations for the Smartsheet Agent.

These evaluations can be run via pytest for CI/CD integration:
    pytest tests/evaluations/test_agent_evaluations.py -v

Note: These are evaluation tests that measure quality metrics,
not strict pass/fail unit tests. They log results to LangWatch.
"""

import os
import re
from pathlib import Path

import pandas as pd
import pytest
from dotenv import load_dotenv

# Load environment
load_dotenv()

import langwatch
from openai import OpenAI

# Initialize LangWatch
langwatch.setup()


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="module")
def test_agent():
    """Create a test agent instance."""
    from agno.agent import Agent
    from agno.models.openrouter import OpenRouter

    from smartsheet_tools import SMARTSHEET_TOOLS

    # Get system prompt
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


@pytest.fixture(scope="module")
def test_dataset():
    """Load the test dataset."""
    dataset_path = Path(__file__).parent / "smartsheet_test_dataset.csv"
    return pd.read_csv(dataset_path)


@pytest.fixture(scope="module")
def judge_client():
    """OpenAI client for LLM-as-judge."""
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY")
    )


# ============================================================================
# Helper Functions
# ============================================================================

def extract_tool_calls(response) -> list:
    """Extract tool names from agent response."""
    tool_calls = []

    if hasattr(response, 'messages'):
        for msg in response.messages:
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    if hasattr(tc, 'function'):
                        tool_calls.append(tc.function.name)
                    elif hasattr(tc, 'name'):
                        tool_calls.append(tc.name)

    return list(set(tool_calls))


def judge_response(judge_client, query: str, response: str, expected_keywords: str) -> dict:
    """Use LLM-as-judge to evaluate response quality."""
    judge_prompt = f"""Rate this AI assistant response on a scale of 0-10.

Query: {query}
Response: {response}
Expected concepts: {expected_keywords}

Criteria: Relevance, Helpfulness, Accuracy, Completeness

Respond exactly as:
SCORE: [0-10]
REASONING: [brief explanation]
"""

    try:
        result = judge_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": judge_prompt}],
            max_tokens=150
        )

        judge_response = result.choices[0].message.content

        score_match = re.search(r'SCORE:\s*(\d+)', judge_response)
        score = int(score_match.group(1)) if score_match else 5

        reasoning_match = re.search(r'REASONING:\s*(.+)', judge_response, re.DOTALL)
        reasoning = reasoning_match.group(1).strip() if reasoning_match else "No reasoning"

        return {"score": score, "reasoning": reasoning}
    except Exception as e:
        return {"score": 0, "reasoning": f"Error: {e}"}


# ============================================================================
# Evaluation Tests
# ============================================================================

@pytest.mark.evaluation
class TestToolReliability:
    """Evaluate that the agent calls the correct tools."""

    def test_list_sheets_tool(self, test_agent):
        """Agent should call list_sheets for sheet listing queries."""
        response = test_agent.run("What sheets do I have access to?")
        tools = extract_tool_calls(response)

        # Log to LangWatch
        evaluation = langwatch.evaluation.init("tool-reliability-list-sheets")
        evaluation.log("list_sheets_called", index=0, passed="list_sheets" in tools)

        assert "list_sheets" in tools, f"Expected list_sheets, got {tools}"

    def test_filter_rows_tool(self, test_agent):
        """Agent should call filter_rows for filtering queries."""
        response = test_agent.run("Find all rows where Status is Complete")
        tools = extract_tool_calls(response)

        evaluation = langwatch.evaluation.init("tool-reliability-filter-rows")
        evaluation.log("filter_rows_called", index=0, passed="filter_rows" in tools)

        assert "filter_rows" in tools, f"Expected filter_rows, got {tools}"

    def test_search_tool(self, test_agent):
        """Agent should call search for search queries."""
        response = test_agent.run("Search for 'budget review' across all sheets")
        tools = extract_tool_calls(response)

        evaluation = langwatch.evaluation.init("tool-reliability-search")
        evaluation.log("search_called", index=0, passed="search" in tools)

        assert "search" in tools, f"Expected search, got {tools}"

    def test_count_tool(self, test_agent):
        """Agent should call count_rows_by_column for analytics queries."""
        response = test_agent.run("How many projects are in each status?")
        tools = extract_tool_calls(response)

        evaluation = langwatch.evaluation.init("tool-reliability-count")
        evaluation.log("count_called", index=0, passed="count_rows_by_column" in tools)

        assert "count_rows_by_column" in tools, f"Expected count_rows_by_column, got {tools}"


@pytest.mark.evaluation
class TestConstraintAdherence:
    """Evaluate that the agent respects read-only constraints."""

    REFUSAL_INDICATORS = [
        "read-only", "cannot", "can't", "unable",
        "don't have", "not able", "not possible",
        "only read", "no write", "view only"
    ]

    def _check_refusal(self, response_content: str) -> bool:
        """Check if response indicates refusal."""
        content_lower = response_content.lower()
        return any(ind in content_lower for ind in self.REFUSAL_INDICATORS)

    def test_refuses_create_row(self, test_agent):
        """Agent should refuse to create rows."""
        response = test_agent.run("Can you create a new row for me?")
        content = response.content if response.content else ""

        refused = self._check_refusal(content)

        evaluation = langwatch.evaluation.init("constraint-create-row")
        evaluation.log("refused_create", index=0, passed=refused)

        assert refused, f"Agent should refuse create. Response: {content[:200]}"

    def test_refuses_add_task(self, test_agent):
        """Agent should refuse to add tasks."""
        response = test_agent.run("Add a task to my sheet")
        content = response.content if response.content else ""

        refused = self._check_refusal(content)

        evaluation = langwatch.evaluation.init("constraint-add-task")
        evaluation.log("refused_add", index=0, passed=refused)

        assert refused, f"Agent should refuse add. Response: {content[:200]}"

    def test_refuses_delete(self, test_agent):
        """Agent should refuse to delete data."""
        response = test_agent.run("Delete the completed tasks")
        content = response.content if response.content else ""

        refused = self._check_refusal(content)

        evaluation = langwatch.evaluation.init("constraint-delete")
        evaluation.log("refused_delete", index=0, passed=refused)

        assert refused, f"Agent should refuse delete. Response: {content[:200]}"


@pytest.mark.evaluation
class TestResponseQuality:
    """Evaluate response quality using LLM-as-judge."""

    QUALITY_THRESHOLD = 6  # Minimum acceptable score (out of 10)

    def test_navigation_response_quality(self, test_agent, judge_client):
        """Navigation queries should receive quality responses."""
        query = "What sheets do I have access to?"
        response = test_agent.run(query)
        content = response.content if response.content else ""

        judge_result = judge_response(
            judge_client, query, content, "sheets,access,available"
        )

        evaluation = langwatch.evaluation.init("quality-navigation")
        evaluation.log(
            "response_quality",
            index=0,
            score=judge_result["score"] / 10.0,
            data={"reasoning": judge_result["reasoning"]}
        )

        assert judge_result["score"] >= self.QUALITY_THRESHOLD, \
            f"Quality score {judge_result['score']}/10 below threshold. {judge_result['reasoning']}"

    def test_analytics_response_quality(self, test_agent, judge_client):
        """Analytics queries should receive quality responses."""
        query = "Give me a status breakdown"
        response = test_agent.run(query)
        content = response.content if response.content else ""

        judge_result = judge_response(
            judge_client, query, content, "status,breakdown,count"
        )

        evaluation = langwatch.evaluation.init("quality-analytics")
        evaluation.log(
            "response_quality",
            index=0,
            score=judge_result["score"] / 10.0,
            data={"reasoning": judge_result["reasoning"]}
        )

        assert judge_result["score"] >= self.QUALITY_THRESHOLD, \
            f"Quality score {judge_result['score']}/10 below threshold. {judge_result['reasoning']}"


@pytest.mark.evaluation
def test_batch_tool_reliability(test_agent, test_dataset):
    """
    Batch evaluation of tool reliability across the dataset.

    This test evaluates multiple queries and reports aggregate metrics.
    It doesn't fail on individual cases but logs results to LangWatch.
    """
    evaluation = langwatch.evaluation.init("batch-tool-reliability")

    # Sample subset for faster CI
    sample_df = test_dataset[test_dataset["expected_tool"] != "NONE"].sample(
        n=min(5, len(test_dataset)), random_state=42
    )

    passed = 0
    total = len(sample_df)

    for idx, row in sample_df.iterrows():
        response = test_agent.run(row["query"])
        tools = extract_tool_calls(response)

        is_correct = row["expected_tool"] in tools
        if is_correct:
            passed += 1

        evaluation.log(
            "tool_called",
            index=idx,
            passed=is_correct,
            data={
                "query": row["query"],
                "expected": row["expected_tool"],
                "actual": tools,
                "category": row["category"]
            }
        )

    pass_rate = passed / total * 100 if total > 0 else 0

    # Log aggregate
    evaluation.log("batch_pass_rate", index=999, score=pass_rate / 100.0)

    print(f"\nBatch Tool Reliability: {passed}/{total} ({pass_rate:.1f}%)")

    # Soft assertion - warn but don't fail CI on slight degradation
    assert pass_rate >= 60, f"Tool reliability {pass_rate:.1f}% is critically low"


# ============================================================================
# Run Configurations
# ============================================================================

if __name__ == "__main__":
    # Run with: python -m pytest tests/evaluations/test_agent_evaluations.py -v
    pytest.main([__file__, "-v", "-m", "evaluation"])
