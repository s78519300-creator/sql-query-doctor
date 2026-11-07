"""sql-query-doctor: a static analyzer for slow-query anti-patterns in SQL.

Typical library use::

    from sql_query_doctor import Analyzer, load_config

    analyzer = Analyzer(config=load_config("sqldoctor.json"))
    for finding in analyzer.analyze_sql("SELECT * FROM events"):
        print(finding.rule_id, finding.message)
"""

__version__ = "0.1.0"

from .analyzer import Analyzer, analyze  # noqa: E402
from .config import Config, ConfigError, TableMeta, load_config  # noqa: E402
from .models import Finding, Severity  # noqa: E402
from .rules import RULES, Rule, all_rules, get_rule  # noqa: E402

__all__ = [
    "Analyzer",
    "Config",
    "ConfigError",
    "Finding",
    "RULES",
    "Rule",
    "Severity",
    "TableMeta",
    "__version__",
    "all_rules",
    "analyze",
    "get_rule",
    "load_config",
]
