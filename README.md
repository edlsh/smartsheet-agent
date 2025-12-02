# Smartsheet Agent 🤖📊

An AI-powered **read-only** agent for querying and analyzing Smartsheet data. Ask questions about your jobs, projects, KPIs, and metrics in natural language.

## Features

- **Read-Only by Design** - Safe data access with no modification capabilities
- **Model Agnostic** - Use any LLM provider via OpenRouter (Claude, GPT-4, Gemini, Llama, etc.)
- **Natural Language Queries** - Ask questions about your Smartsheet data conversationally
- **49 Powerful Tools** - Comprehensive read-only access to sheets, reports, attachments, discussions, dashboards, automation, webhooks, images, and more
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

All tools are **read-only** - no data can be created, modified, or deleted.

### Core Tools

| Tool | Description |
|------|-------------|
| `list_sheets` | List all Smartsheets accessible to your account |
| `get_sheet` | Get detailed data from a specific sheet (by ID or name) |
| `get_row` | Get information about a specific row |
| `search_sheets` | Search across all sheets for specific text |
| `search_sheet` | Search within a specific sheet for text |

### Analysis Tools

| Tool | Description |
|------|-------------|
| `get_columns` | Get detailed column metadata (types, options, formulas) |
| `get_sheet_summary` | Get statistics: row counts, fill rates, column types |
| `filter_rows` | Filter rows by column values (contains, equals, starts_with, ends_with) |
| `get_sheet_by_column` | Get only specific columns from a sheet (useful for large sheets) |
| `count_rows_by_column` | Count rows grouped by column values (useful for status breakdowns) |
| `get_summary_fields` | Get sheet summary fields (KPIs/metadata at sheet level) |

### Audit Tools

| Tool | Description |
|------|-------------|
| `get_cell_history` | Get revision history for a specific cell (who changed what, when) |
| `get_sheet_version` | Get sheet version and modification info |
| `get_events` | Get recent events/audit log (Enterprise feature) |

### Reports

| Tool | Description |
|------|-------------|
| `get_reports` | List all available Smartsheet reports |
| `get_report` | Get data from a specific report |

### Attachments & Discussions

| Tool | Description |
|------|-------------|
| `get_row_attachments` | Get all attachments for a specific row |
| `get_sheet_attachments` | Get all attachments in a sheet |
| `get_attachment` | Get attachment details with download URL |
| `get_row_discussions` | Get discussions/comments for a specific row |
| `get_sheet_discussions` | Get all discussions/comments in a sheet |

### Organization

| Tool | Description |
|------|-------------|
| `get_workspaces` | List all workspaces |
| `get_workspace` | Get workspace details including sheets, folders, reports |
| `get_folders` | List home-level folders |
| `get_folder` | Get folder details and contents |
| `get_home` | Get overview of user's Smartsheet home (all root-level content) |
| `get_favorites` | Get user's favorite/starred items |
| `get_templates` | List all available templates (public and user) |

### Sights (Dashboards)

| Tool | Description |
|------|-------------|
| `get_sights` | List all Sights (dashboards) |
| `get_sight` | Get Sight details and widgets |

### Advanced

| Tool | Description |
|------|-------------|
| `get_current_user` | Get current authenticated user profile |
| `get_cross_sheet_references` | Get cross-sheet references in a sheet |
| `compare_sheets` | Compare two sheets by a key column to find differences |

### Automation & Admin

| Tool | Description |
|------|-------------|
| `get_automation_rules` | List automation rules for a sheet |
| `get_groups` | List all groups in the organization |
| `get_group` | Get group details and members |
| `get_contacts` | List personal contacts |
| `get_sheet_shares` | Get sharing info for a sheet (who has access) |
| `list_users` | List all users in the organization (Admin feature) |
| `get_user` | Get detailed user profile by ID or email (Admin feature) |
| `list_org_sheets` | List ALL sheets in the organization (Admin feature) |

### Publishing & Requests

| Tool | Description |
|------|-------------|
| `get_server_info` | Get Smartsheet server info and constants |
| `get_sheet_publish_status` | Get sheet publish status and URLs |
| `get_proofs` | List proofs in a sheet |
| `get_update_requests` | List update requests for a sheet |
| `get_sent_update_requests` | List sent update requests |

### Webhooks

| Tool | Description |
|------|-------------|
| `list_webhooks` | List all webhooks owned by the user |
| `get_webhook` | Get detailed webhook information and statistics |

### Images

| Tool | Description |
|------|-------------|
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
