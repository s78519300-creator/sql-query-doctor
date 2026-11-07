"""Configuration loading.

A config file is plain JSON. Every key is optional::

    {
      "dialect": "clickhouse",
      "large_table_rows": 100000000,
      "rules": {
        "SQD007": "off",
        "SQD001": "error",
        "SQD006": {"severity": "warning", "options": {"min_terms": 2}}
      },
      "tables": {
        "analytics.events": {
          "rows": 4200000000,
          "engine": "MergeTree",
          "partition_by": "toYYYYMM(event_date)",
          "order_by": ["event_type", "user_id", "event_time"],
          "nullable": ["referrer"],
          "high_cardinality": ["user_id", "session_id"]
        }
      }
    }

Table metadata is what turns the generic rules into useful ones: knowing that
``events`` has four billion rows and is partitioned by month is the difference
between "this query has no WHERE" and "this query scans every partition of a
4.2B row table".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Union

from .models import Severity
from .tokenizer import TokenType, significant, tokenize

DEFAULT_CONFIG_NAMES = ("sqldoctor.json", ".sqldoctor.json")
DIALECTS = ("clickhouse", "generic")
DEDUPLICATING_ENGINES = (
    "ReplacingMergeTree",
    "CollapsingMergeTree",
    "VersionedCollapsingMergeTree",
    "SummingMergeTree",
    "AggregatingMergeTree",
)

_TABLE_KEYS = {"large", "rows", "engine", "partition_by", "order_by", "nullable", "high_cardinality", "distributed"}
_TOP_KEYS = {"dialect", "large_table_rows", "rules", "tables", "$schema", "description"}


class ConfigError(ValueError):
    """Raised for unreadable or invalid configuration."""


@dataclass
class TableMeta:
    """Metadata about one table, as declared in the config."""

    name: str
    large: bool = False
    rows: Optional[int] = None
    engine: str = ""
    partition_by: str = ""
    order_by: List[str] = field(default_factory=list)
    nullable: FrozenSet[str] = frozenset()
    high_cardinality: FrozenSet[str] = frozenset()
    distributed: bool = False

    @property
    def partition_columns(self) -> FrozenSet[str]:
        """Columns that appear in the partition expression.

        ``toYYYYMM(event_date)`` yields ``{"event_date"}``; a tuple such as
        ``(tenant_id, toDate(ts))`` yields both columns.
        """
        return _expression_columns(self.partition_by)

    @property
    def sort_columns(self) -> FrozenSet[str]:
        """Columns of the sorting key (``ORDER BY`` of the table)."""
        cols: set = set()
        for expr in self.order_by:
            cols |= _expression_columns(expr)
        return frozenset(cols)

    @property
    def is_deduplicating(self) -> bool:
        """True for engines whose rows are only collapsed at merge time."""
        engine = self.engine.replace("Replicated", "", 1) if self.engine.startswith("Replicated") else self.engine
        return engine.split("(")[0].strip() in DEDUPLICATING_ENGINES


@dataclass
class RuleSettings:
    """Per-rule overrides from the config."""

    enabled: bool = True
    severity: Optional[Severity] = None
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    """Resolved configuration."""

    dialect: str = "clickhouse"
    large_table_rows: int = 100_000_000
    rules: Dict[str, RuleSettings] = field(default_factory=dict)
    tables: Dict[str, TableMeta] = field(default_factory=dict)
    source: Optional[str] = None

    def rule_settings(self, rule_id: str) -> RuleSettings:
        """Settings for ``rule_id`` (defaults if the config doesn't mention it)."""
        return self.rules.get(rule_id.upper(), RuleSettings())

    def table(self, name: Optional[str]) -> Optional[TableMeta]:
        """Look up table metadata.

        Matching is case-insensitive. A qualified name in the query
        (``analytics.events``) matches either the qualified config entry or a
        bare ``events`` entry; a bare name in the query matches a qualified
        entry if exactly one config table has that short name.
        """
        if not name:
            return None
        key = name.lower()
        if key in self.tables:
            return self.tables[key]
        short = key.rsplit(".", 1)[-1]
        if short in self.tables:
            return self.tables[short]
        candidates = [meta for k, meta in self.tables.items() if k.rsplit(".", 1)[-1] == short]
        return candidates[0] if len(candidates) == 1 else None

    def is_large(self, meta: Optional[TableMeta]) -> bool:
        """True if ``meta`` describes a table big enough to worry about."""
        if meta is None:
            return False
        return meta.large or (meta.rows is not None and meta.rows >= self.large_table_rows)


def _expression_columns(expr: str) -> FrozenSet[str]:
    toks = significant(tokenize(expr))
    cols = set()
    for i, tok in enumerate(toks):
        if tok.type is not TokenType.IDENTIFIER:
            continue
        if i + 1 < len(toks) and toks[i + 1].is_punct("("):
            continue
        cols.add(tok.name)
    return frozenset(cols)


def _as_list(value: Any, where: str) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"{where}: expected a string or a list of strings")


def _parse_rule(rule_id: str, value: Union[str, bool, Mapping[str, Any]]) -> RuleSettings:
    where = f"rules.{rule_id}"
    if isinstance(value, bool):
        return RuleSettings(enabled=value)
    if isinstance(value, str):
        if value.lower() in ("off", "disable", "disabled", "false"):
            return RuleSettings(enabled=False)
        if value.lower() in ("on", "enable", "enabled", "true"):
            return RuleSettings()
        try:
            return RuleSettings(severity=Severity.parse(value))
        except ValueError as exc:
            raise ConfigError(f"{where}: {exc}") from None
    if isinstance(value, Mapping):
        unknown = set(value) - {"enabled", "severity", "options"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        settings = RuleSettings(enabled=bool(value.get("enabled", True)))
        if "severity" in value:
            try:
                settings.severity = Severity.parse(str(value["severity"]))
            except ValueError as exc:
                raise ConfigError(f"{where}: {exc}") from None
        options = value.get("options", {})
        if not isinstance(options, Mapping):
            raise ConfigError(f"{where}.options: expected an object")
        settings.options = dict(options)
        return settings
    raise ConfigError(f'{where}: expected "off", a severity, or an object')


def _parse_table(name: str, value: Any) -> TableMeta:
    where = f"tables.{name}"
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where}: expected an object")
    unknown = set(value) - _TABLE_KEYS
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    rows = value.get("rows")
    if rows is not None and (not isinstance(rows, int) or isinstance(rows, bool) or rows < 0):
        raise ConfigError(f"{where}.rows: expected a non-negative integer")
    order_by = value.get("order_by", [])
    return TableMeta(
        name=name.lower(),
        large=bool(value.get("large", False)),
        rows=rows,
        engine=str(value.get("engine", "")),
        partition_by=str(value.get("partition_by", "")),
        order_by=_as_list(order_by, f"{where}.order_by") if order_by else [],
        nullable=frozenset(c.lower() for c in _as_list(value.get("nullable", []), f"{where}.nullable")),
        high_cardinality=frozenset(c.lower() for c in _as_list(value.get("high_cardinality", []), f"{where}.high_cardinality")),
        distributed=bool(value.get("distributed", False)),
    )


def config_from_dict(data: Mapping[str, Any], source: Optional[str] = None, known_rules: Optional[set] = None) -> Config:
    """Build a :class:`Config` from already-parsed JSON.

    Args:
        data: The decoded JSON object.
        source: Where it came from, for error messages.
        known_rules: If given, rule ids outside this set are rejected so a
            typo like ``SQD01`` doesn't silently do nothing.
    """
    if not isinstance(data, Mapping):
        raise ConfigError("config root must be a JSON object")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {', '.join(sorted(unknown))}")

    config = Config(source=source)
    dialect = str(data.get("dialect", config.dialect)).lower()
    if dialect not in DIALECTS:
        raise ConfigError(f"dialect: expected one of {', '.join(DIALECTS)}, got {dialect!r}")
    config.dialect = dialect

    threshold = data.get("large_table_rows", config.large_table_rows)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        raise ConfigError("large_table_rows: expected a positive integer")
    config.large_table_rows = threshold

    rules = data.get("rules", {})
    if not isinstance(rules, Mapping):
        raise ConfigError("rules: expected an object keyed by rule id")
    for rule_id, value in rules.items():
        rid = rule_id.upper()
        if known_rules is not None and rid not in known_rules:
            raise ConfigError(f"rules: unknown rule id {rule_id!r}")
        config.rules[rid] = _parse_rule(rid, value)

    tables = data.get("tables", {})
    if not isinstance(tables, Mapping):
        raise ConfigError("tables: expected an object keyed by table name")
    for name, value in tables.items():
        config.tables[name.lower()] = _parse_table(name, value)
    return config


def load_config(path: Union[str, Path], known_rules: Optional[set] = None) -> Config:
    """Read and validate a JSON config file."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {p}: {exc.strerror or exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from None
    try:
        return config_from_dict(data, source=str(p), known_rules=known_rules)
    except ConfigError as exc:
        raise ConfigError(f"{p}: {exc}") from None


def discover_config(start: Union[str, Path] = ".") -> Optional[Path]:
    """Find a default config file in ``start`` (no parent traversal)."""
    base = Path(start)
    for name in DEFAULT_CONFIG_NAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None
