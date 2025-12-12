# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- GitHub Actions CI workflow for linting, testing, and package building
- CONTRIBUTING.md with development guidelines
- CODE_OF_CONDUCT.md (Contributor Covenant)
- SECURITY.md with security policy and reporting guidelines
- Optional LangWatch integration (graceful fallback when not installed)
- Default system prompt fallback when LangWatch is unavailable

### Changed
- Made `langwatch` an optional dependency (install with `pip install smartsheet-agent[tracing]`)
- Enhanced pyproject.toml with full metadata, classifiers, and URLs
- Improved error handling for missing LangWatch

### Fixed
- Runtime dependency on langwatch now properly optional

## [0.2.0] - 2024-12-01

### Added
- Multi-level caching (L1 memory + L2 disk) for improved performance
- Fuzzy search tools: `find_sheets()` and `find_columns()`
- Smart query planning with `analyze_sheet()` for efficient multi-operation analysis
- Sheet scoping via `ALLOWED_SHEET_IDS` and `ALLOWED_SHEET_NAMES` environment variables
- Interactive CLI mode with slash commands and autocomplete
- Persistent conversation memory with SQLite storage
- Smart model routing based on query complexity
- Retry logic with exponential backoff for network errors

### Changed
- Consolidated tools from 49 to 31 unified tools
- Optimized pagination for large sheets
- Improved error messages with helpful suggestions

## [0.1.0] - 2024-11-15

### Added
- Initial release
- 49 read-only Smartsheet tools
- Multi-LLM support via OpenRouter
- Basic CLI interface
- LangWatch integration for tracing

[Unreleased]: https://github.com/enzolucchesi/smartsheet-agent/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/enzolucchesi/smartsheet-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/enzolucchesi/smartsheet-agent/releases/tag/v0.1.0
