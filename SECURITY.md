# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it by emailing **enzo@lucchesi.dev**.

**Please do NOT open a public GitHub issue for security vulnerabilities.**

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 7 days
- **Resolution**: Depending on severity, typically within 30 days

## Security Considerations

### Credentials

This project handles sensitive credentials:

- **SMARTSHEET_ACCESS_TOKEN**: Smartsheet API token
- **OPENROUTER_API_KEY**: LLM provider API key
- **LANGWATCH_API_KEY**: Optional tracing API key

**All credentials must be stored in environment variables or `.env` files (never committed to git).**

### Read-Only Design

Smartsheet Agent is designed to be **read-only** by architecture:

- All 31 tools only perform read operations
- No create, update, or delete operations are implemented
- Sheet scoping (`ALLOWED_SHEET_IDS`, `ALLOWED_SHEET_NAMES`) restricts access

### Data Caching

- L1 cache: In-memory (clears on restart)
- L2 cache: Disk-based in `tmp/cache/` (contains read-only Smartsheet data)
- Set `SMARTSHEET_CACHE_DISABLE_DISK=1` to disable disk caching for sensitive environments

### Third-Party Services

When enabled, the following services may receive data:

- **OpenRouter**: Query prompts and responses
- **LangWatch** (optional): Traces, prompts, and responses for monitoring

## Best Practices

1. Use environment variables for all secrets
2. Never commit `.env` files
3. Use sheet scoping to limit accessible data
4. Review LangWatch integration if handling sensitive data
5. Regularly rotate API tokens
