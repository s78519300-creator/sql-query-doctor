"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from sql_query_doctor import Analyzer, Finding
from sql_query_doctor.config import Config, config_from_dict

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

#: Table metadata used by the metadata-driven rule tests.
TABLES: Dict[str, Any] = {
    "events": {
        "rows": 5_000_000_000,
        "engine": "MergeTree",
        "partition_by": "toYYYYMM(event_date)",
        "order_by": ["event_type", "user_id", "ts"],
        "nullable": ["referrer"],
        "high_cardinality": ["user_id"],
    },
    "events_all": {"rows": 20_000_000_000, "engine": "Distributed", "distributed": True},
    "users_all": {"rows": 1_000_000, "engine": "Distributed", "distributed": True},
    "users": {"rows": 1_000_000, "engine": "ReplacingMergeTree(version)"},
    "countries": {"rows": 250, "engine": "MergeTree"},
}


def make_config(**overrides: Any) -> Config:
    """Build a config with the shared table metadata plus ``overrides``."""
    data: Dict[str, Any] = {"tables": TABLES}
    data.update(overrides)
    return config_from_dict(data)


def lint(sql: str, config: Optional[Config] = None, path: str = "<string>", **kwargs: Any) -> List[Finding]:
    """Analyze ``sql`` with the shared test config."""
    return Analyzer(config=config or make_config(), **kwargs).analyze_sql(sql, path=path)


def rule_ids(sql: str, config: Optional[Config] = None) -> List[str]:
    """Rule ids reported for ``sql``."""
    return [f.rule_id for f in lint(sql, config)]


@pytest.fixture
def config() -> Config:
    return make_config()
