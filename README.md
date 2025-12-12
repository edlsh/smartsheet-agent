# Smartsheet Agent 🤖📊

An AI-powered **read-only** agent for querying and analyzing Smartsheet data. Ask questions about your jobs, projects, KPIs, and metrics in natural language.

## Features

- **Read-Only by Design** - Safe data access with no modification capabilities
- **Model Agnostic** - Use any LLM provider via OpenRouter (Claude, GPT-4, Gemini, Llama, etc.)
- **Natural Language Queries** - Ask questions about your Smartsheet data conversationally
- **31 Powerful Tools** - Comprehensive read-only access to sheets, reports, attachments, discussions, dashboards, webhooks, images, and more
- **Interactive Mode** - Chat with your data in a conversational session
- **Easy Model Switching** - Change models on-the-fly during interactive sessions
- **Cell History Audit** - Track who changed what and when
- **Smart Filtering** - Query data with flexible filter options
- **Attachments & Discussions** - View files and comments attached to rows and sheets
- **Workspace Navigation** - Browse workspaces, folders, and organizational structure

## Supported Models

Via [OpenRouter](https://openrouter.ai/), you can use models that **support function/tool calling**:

> ⚠️ **Important**: Smartsheet Agent requires models with tool/function calling support. Some free-tier models (e.g., `:free` suffix models) may not support tool use through certain providers.

### Recommended Models (Tool Calling Support)

| Provider | Model | Pricing | Tool Support |
|----------|-------|---------|--------------|
| Google | `google/gemini-2.0-flash-001` | Free | ✅ Verified |
| OpenAI | `openai/gpt-4o-mini` | Paid | ✅ Verified |
| Anthropic | `anthropic/claude-3-5-haiku` | Paid | ✅ Verified |
| Meta | `meta-llama/llama-3.3-70b-instruct` | Low cost | ✅ Verified |
| Moonshot | `moonshotai/kimi-k2` | Paid | ✅ Verified |

### Models to Avoid

| Model | Issue |
|-------|-------|
| `*:free` suffix models | Many free-tier providers don't expose tool calling |
| Completion-only models | No function calling support |

See the [full model list](https://openrouter.ai/models) on OpenRouter.

## Installation

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/enzolucchesi/smartsheet-agent.git
   cd smartsheet-agent
   ```

2. **Install dependencies:**
   ```bash
   # Using uv (recommended)
   uv sync
   
   # Or using pip
   pip install -e .
   ```

3. **Configure environment variables:**
   
   Create a `.env` file in the project root:
   ```bash
   # Required: OpenRouter API key
   # Get yours at: https://openrouter.ai/settings/keys
   OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx
   
   # Required: Smartsheet API token
   # Get yours at: https://app.smartsheet.com/b/home (Account > Personal Settings > API Access)
   SMARTSHEET_ACCESS_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxx
   
   # Optional: Default model (defaults to google/gemini-2.0-flash-001)
   # Must support function/tool calling - see "Supported Models" section
   OPENROUTER_MODEL=google/gemini-2.0-flash-001
   ```

## Usage

### Interactive Mode

Start an interactive chat session:

```bash
# Using uv
uv run python main.py

# Or if installed
   smartsheet-agent
```

In interactive mode, use slash commands:
- `/help` - Show all available commands
- `/sheets` - List all available Smartsheets
- `/reports` - List all available reports
- `/summary <sheet>` - Get statistics for a sheet
- `/columns <sheet>` - Show column metadata for a sheet
- `/search <keyword>` - Search across all sheets
- `/model <model-id>` - Switch models (e.g., `/model openai/gpt-4o`)
- `/clear` - Start a new conversation
- `/quit` - Exit the application

Type `/` to see autocomplete suggestions.

### Single Query Mode

Run a single query from the command line:

```bash
uv run python main.py "List all my sheets"
uv run python main.py "What's the status of the Marketing Campaign project?"
uv run python main.py "Search for tasks assigned to John"
```

### Example Queries

```
# Basic queries
"List all my available sheets"
"Show me the data from the 'Project Tracker' sheet"
"Search for all items containing 'overdue'"

# Analysis queries
"Give me a summary of the Project Tracker sheet"
"What columns are in the Job Board sheet?"
"Show me only the Name and Status columns from Project Tracker"

# Filtering queries
"Find all rows where Status contains 'Complete'"
"Filter the Job Board for jobs that start with 'Marketing'"
"Show me all items where Priority equals 'High'"

# Audit queries
"Who changed the status of row 5 in the Project Tracker?"
"Show me the history of changes for that cell"

# Reports
"List all available reports"
"Show me the data from the Weekly Status report"
```

## Available Tools

All tools are **read-only** - no data can be created, modified, or deleted. Tools are consolidated for efficiency.

### Core Tools (5)

| Tool | Description |
|------|-------------|
| `list_sheets` | List all Smartsheets accessible to your account |
| `get_sheet` | Get detailed data from a specific sheet (by ID or name) |
| `get_row` | Get information about a specific row |
| `filter_rows` | Filter rows by column values (contains, equals, starts_with, ends_with) |
| `count_rows_by_column` | Count rows grouped by column values (useful for status breakdowns) |

### Fuzzy Search Tools (2)

| Tool | Description |
|------|-------------|
| `find_sheets` | Search for sheets by partial or approximate name |
| `find_columns` | Search for columns in a sheet by partial name |

### Smart Analysis (1)

| Tool | Description |
|------|-------------|
| `analyze_sheet` | Perform multiple analysis operations in a single efficient call (summary, columns, stats, filter, count, sample) |

### Unified Resource Tools (7)

| Tool | Description |
|------|-------------|
| `workspace` | List workspaces or get workspace details (action: list/get) |
| `folder` | List folders or get folder contents (action: list/get) |
| `sight` | List dashboards or get dashboard details (action: list/get) |
| `report` | List reports or get report data (action: list/get) |
| `webhook` | List webhooks or get webhook details (action: list/get) |
| `group` | List groups or get group members (action: list/get) |
| `user` | List org users or get user profile (action: list/get) |

### Unified Scope Tools (2)

| Tool | Description |
|------|-------------|
| `attachment` | Get attachments for a sheet or row (scope: sheet/row) |
| `discussion` | Get discussions for a sheet or row (scope: sheet/row) |

### Navigation & Metadata (4)

| Tool | Description |
|------|-------------|
| `search` | Search across all sheets or within a specific sheet |
| `navigation` | Get home overview, favorites, or templates |
| `sheet_metadata` | Get columns, summary fields, shares, publish status, or automation rules |
| `sheet_info` | Get proofs or cross-sheet references for a sheet |

### Update Requests (1)

| Tool | Description |
|------|-------------|
| `update_requests` | List update requests (action: list/sent) |

### Standalone Tools (9)

| Tool | Description |
|------|-------------|
| `compare_sheets` | Compare two sheets by a key column to find differences |
| `get_cell_history` | Get revision history for a specific cell |
| `get_sheet_version` | Get sheet version and modification info |
| `get_events` | Get recent events/audit log (Enterprise feature) |
| `get_current_user` | Get current authenticated user profile |
| `get_contacts` | List personal contacts |
| `get_server_info` | Get Smartsheet server info and constants |
| `list_org_sheets` | List ALL sheets in the organization (Admin feature) |
| `get_image_urls` | Get temporary download URL for cell images |

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `SMARTSHEET_ACCESS_TOKEN` | Yes | Your Smartsheet API access token |
| `OPENROUTER_MODEL` | No | Default model to use (default: `google/gemini-2.5-flash`). Must support tool calling. |
| `ALLOWED_SHEET_IDS` | No | Comma-separated list of sheet IDs to restrict access (e.g., `123456789,987654321`) |
| `ALLOWED_SHEET_NAMES` | No | Comma-separated list of sheet names to restrict access (e.g., `"Project Tracker,Job Board"`) |

### Sheet Scoping

By default, Smartsheet Agent can access all sheets available to your Smartsheet account. To restrict access to specific sheets, set either or both of the scoping environment variables:

```bash
# Restrict by sheet IDs
ALLOWED_SHEET_IDS=123456789012345,987654321098765

# Restrict by sheet names (case-insensitive)
ALLOWED_SHEET_NAMES="Project Tracker,Job Status Board,KPI Dashboard"

# Or use both (sheets matching either will be allowed)
ALLOWED_SHEET_IDS=123456789012345
ALLOWED_SHEET_NAMES="Project Tracker"
```

When scoping is configured:
- `list_sheets` only shows allowed sheets
- `get_sheet` returns an error for non-allowed sheets
- `get_row` validates sheet access before returning data
- `search_sheets` filters results to only include matches from allowed sheets

### Switching Models

You can switch models in several ways:

1. **Environment variable:**
   ```bash
   export OPENROUTER_MODEL=openai/gpt-4o
   ```

2. **In interactive mode:**
   ```
   You: model google/gemini-2.0-flash-exp
   ✓ Switched to model: google/gemini-2.0-flash-exp
   ```

## Architecture

Smartsheet Agent uses:
- **[Agno](https://docs.agno.com/)** - Lightweight agent framework
- **[OpenRouter](https://openrouter.ai/)** - Unified API for 100+ LLM providers
- **[Smartsheet Python SDK](https://github.com/smartsheet/smartsheet-python-sdk)** - Official Smartsheet API client

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run linting
uv run ruff check .

# Run tests
uv run pytest tests/
```

### Testing with Scenario

This project uses [LangWatch Scenario](https://scenario.langwatch.ai/) for end-to-end agent testing. Scenario simulates real users interacting with your agent to validate behavior.

```bash
# Run scenario tests
uv run pytest tests/scenarios/

# Run a specific scenario
uv run pytest tests/scenarios/test_smartsheet_agent.py -v
```

### LangWatch Integration

The agent is instrumented with [LangWatch](https://langwatch.ai/) for:
- Trace monitoring and debugging
- Prompt versioning and management
- Performance analytics

Set your `LANGWATCH_API_KEY` in `.env` to enable tracing.

### Prompt Management

Prompts are managed using LangWatch Prompt CLI:

```bash
# List prompts
langwatch prompt list

# Create a new prompt
langwatch prompt create my_prompt

# Sync prompts
langwatch prompt sync
```

## License

MIT
