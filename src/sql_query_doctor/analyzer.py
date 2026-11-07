"""The engine: run the enabled rules over SQL and collect findings.

Inline suppressions are written as SQL comments::

    -- sqldoctor: ignore SQD001, SQD007   (this statement only)
    -- sqldoctor: ignore                  (all rules, this statement)
    -- sqldoctor: ignore-file SQD016      (whole file)

A comment belongs to the statement that follows it (or the one it sits in).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple, Union

from .config import Config
from .models import Finding, Severity
from .parser import Statement, split_statements
from .rules import Rule, RuleContext, all_rules
from .tokenizer import Token

_SUPPRESS_RE = re.compile(r"sqldoctor:\s*(ignore-file|ignore)\b([\w\s,]*)", re.IGNORECASE)
ALL = "*"


@dataclass
class Analyzer:
    """Runs rules according to a :class:`Config`.

    Attributes:
        config: Effective configuration.
        select: If set, only these rule ids run (CLI ``--select``).
        ignore: Rule ids that never run (CLI ``--ignore``).
    """

    config: Config = field(default_factory=Config)
    select: Optional[Collection[str]] = None
    ignore: Collection[str] = ()

    def active_rules(self) -> List[Rule]:
        """Rules that are enabled for this configuration."""
        selected = {s.upper() for s in self.select} if self.select else None
        ignored = {s.upper() for s in self.ignore}
        active = []
        for r in all_rules():
            if selected is not None and r.id not in selected:
                continue
            if r.id in ignored or not self.config.rule_settings(r.id).enabled:
                continue
            if r.clickhouse_only and self.config.dialect != "clickhouse":
                continue
            active.append(r)
        return active

    def analyze_sql(self, sql: str, path: str = "<string>") -> List[Finding]:
        """Analyze a SQL string and return findings sorted by position."""
        lines = sql.splitlines()
        statements = split_statements(sql)
        file_ignores = _file_suppressions(statements)
        if ALL in file_ignores:
            return []

        findings: List[Finding] = []
        seen: Set[Tuple[str, int, int]] = set()
        rules = [r for r in self.active_rules() if r.id not in file_ignores]
        for stmt in statements:
            stmt_ignores = _statement_suppressions(stmt)
            if ALL in stmt_ignores:
                continue
            for r in rules:
                if r.id in stmt_ignores:
                    continue
                settings = self.config.rule_settings(r.id)
                ctx = RuleContext(statement=stmt, config=self.config, options=settings.options)
                for hit in r.check(ctx):
                    key = (r.id, hit.token.line, hit.token.column)
                    if key in seen:
                        continue
                    seen.add(key)
                    severity = settings.severity or hit.severity or r.severity
                    findings.append(_make_finding(path, r, severity, hit.message, hit.token, lines))
        findings.sort(key=lambda f: (f.line, f.column, f.rule_id))
        return findings

    def analyze_file(self, path: Union[str, Path]) -> List[Finding]:
        """Read a file (UTF-8, BOM tolerated) and analyze it."""
        p = Path(path)
        sql = p.read_text(encoding="utf-8-sig")
        return self.analyze_sql(sql, path=p.as_posix())

    def analyze_files(self, paths: Iterable[Union[str, Path]]) -> Dict[str, List[Finding]]:
        """Analyze several files; returns ``{path: findings}`` in input order."""
        return {Path(p).as_posix(): self.analyze_file(p) for p in paths}


def _make_finding(path: str, r: Rule, severity: Severity, message: str, tok: Token, lines: List[str]) -> Finding:
    snippet = lines[tok.line - 1] if 0 < tok.line <= len(lines) else ""
    return Finding(
        path=path,
        line=tok.line,
        column=tok.column,
        rule_id=r.id,
        rule_name=r.name,
        severity=severity,
        message=message,
        snippet=snippet.rstrip(),
    )


def _parse_suppression(comment: str) -> Optional[Tuple[str, FrozenSet[str]]]:
    match = _SUPPRESS_RE.search(comment)
    if not match:
        return None
    ids = frozenset(x.upper() for x in re.findall(r"SQD\d{3}", match.group(2), re.IGNORECASE))
    return match.group(1).lower(), ids or frozenset({ALL})


def _statement_suppressions(stmt: Statement) -> FrozenSet[str]:
    ids: Set[str] = set()
    for c in stmt.comments:
        parsed = _parse_suppression(c.value)
        if parsed and parsed[0] == "ignore":
            ids |= parsed[1]
    return frozenset(ids)


def _file_suppressions(statements: List[Statement]) -> FrozenSet[str]:
    ids: Set[str] = set()
    for stmt in statements:
        for c in stmt.comments:
            parsed = _parse_suppression(c.value)
            if parsed and parsed[0] == "ignore-file":
                ids |= parsed[1]
    return frozenset(ids)


def analyze(sql: str, config: Optional[Config] = None, path: str = "<string>") -> List[Finding]:
    """Convenience wrapper: analyze ``sql`` with ``config`` (or defaults)."""
    return Analyzer(config=config or Config()).analyze_sql(sql, path=path)
