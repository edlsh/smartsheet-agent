# Contributing to Smartsheet Agent

Thank you for your interest in contributing! This document provides guidelines for contributing.

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting Started

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Smartsheet API access token
- OpenRouter API key

### Development Setup

```bash
# Clone and install
git clone https://github.com/enzolucchesi/smartsheet-agent.git
cd smartsheet-agent
uv sync --dev

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Verify setup
uv run ruff check .
uv run pytest tests/ --ignore=tests/scenarios/ --ignore=tests/evaluations/
```

## Development Workflow

### Branching

- `main` - Stable release branch
- `feature/your-feature` - New features
- `fix/bug-description` - Bug fixes

### Making Changes

1. Create a branch: `git checkout -b feature/your-feature`
2. Make changes following coding standards
3. Run linting: `uv run ruff check . && uv run ruff format .`
4. Run tests: `uv run pytest tests/ --ignore=tests/scenarios/`
5. Commit with conventional message: `git commit -m "feat: add feature"`
6. Push and create PR

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `refactor:` Code refactoring
- `test:` Tests
- `chore:` Maintenance

## Coding Standards

- Follow PEP 8, use ruff for linting
- Max line length: 100 characters
- Use type hints for function signatures
- **All tools must be READ-ONLY** - no write operations

### Adding New Tools

Add to `smartsheet_tools.py`:

```python
@tool(cache_results=True)
@cached_tool
def my_new_tool(param1: str, param2: int = 10) -> str:
    """
    Brief description.

    Args:
        param1: Description
        param2: Description (default: 10)

    Returns:
        Formatted result string
    """
    try:
        client = get_smartsheet_client()
        # ... implementation (READ-ONLY operations only)
        return "Result"
    except Exception as e:
        return f"Error: {str(e)}"
```

Then add to `SMARTSHEET_TOOLS` list.

## Testing

```bash
# Unit tests (no API keys)
uv run pytest tests/ --ignore=tests/scenarios/ --ignore=tests/evaluations/

# Scenario tests (requires API keys)
uv run pytest tests/scenarios/ -v
```

## Pull Request Checklist

- [ ] Linting passes (`uv run ruff check .`)
- [ ] Tests pass
- [ ] Documentation updated if needed
- [ ] New tools are read-only
- [ ] Conventional commit messages

## Questions?

Open an issue or reach out to maintainers. Thank you for contributing! 🎉
