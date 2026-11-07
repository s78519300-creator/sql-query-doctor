"""Core value types shared across the package."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict


class Severity(str, Enum):
    """How serious a finding is. Ordered: ``INFO < WARNING < ERROR``."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        """Numeric rank, useful for ``--fail-on`` thresholds."""
        return {"info": 1, "warning": 2, "error": 3}[self.value]

    @classmethod
    def parse(cls, value: str) -> Severity:
        """Parse a severity name, accepting a few common aliases."""
        aliases = {"warn": "warning", "note": "info", "err": "error"}
        normalized = aliases.get(value.strip().lower(), value.strip().lower())
        try:
            return cls(normalized)
        except ValueError:
            raise ValueError(f"unknown severity {value!r} (expected error, warning or info)") from None


@dataclass(frozen=True)
class Finding:
    """A single problem reported by a rule.

    Attributes:
        path: File the finding belongs to (``<stdin>`` for piped input).
        line: 1-based line number.
        column: 1-based column number.
        rule_id: Rule identifier, e.g. ``SQD005``.
        rule_name: Short slug, e.g. ``non-sargable-predicate``.
        severity: Effective severity after config overrides.
        message: Specific, human-readable description of this occurrence.
        snippet: The source line the finding points at.
    """

    path: str
    line: int
    column: int
    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to plain JSON-compatible types."""
        data = asdict(self)
        data["severity"] = self.severity.value
        return data
