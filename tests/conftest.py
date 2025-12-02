"""
Pytest configuration for Smartsheet Agent tests.
"""

import os
import pytest
from dotenv import load_dotenv

# Load environment variables before tests
load_dotenv()


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "agent_test: mark test as an agent scenario test"
    )


@pytest.fixture(scope="session", autouse=True)
def setup_environment():
    """Ensure environment is set up before running tests."""
    required_vars = ["OPENROUTER_API_KEY", "LANGWATCH_API_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")
