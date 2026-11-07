# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-08

### Added
- Tokenizer that understands strings, quoted identifiers, comments and
  ClickHouse query parameters.
- Parser that finds SELECT blocks, subqueries, CTEs and join structure.
- 18 rules (SQD001–SQD018) covering generic SQL anti-patterns and
  ClickHouse-specific issues (FINAL, GLOBAL IN, partition filters,
  Nullable equality, join ordering).
- Table metadata in the config: row counts, engine, partition key, sorting key.
- Per-rule options and inline suppressions (`-- sqldoctor: ignore SQD001`).
- Text, JSON and SARIF 2.1.0 output for GitHub code scanning.
- `sqldoctor check`, `sqldoctor rules` and `sqldoctor explain` commands.

[Unreleased]: https://github.com/s78519300-creator/sql-query-doctor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/s78519300-creator/sql-query-doctor/releases/tag/v0.1.0
