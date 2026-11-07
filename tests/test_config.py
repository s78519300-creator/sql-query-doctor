import json

import pytest

from conftest import EXAMPLES, make_config, rule_ids
from sql_query_doctor import RULES, ConfigError, Severity, load_config
from sql_query_doctor.config import config_from_dict


def test_example_config_loads():
    cfg = load_config(EXAMPLES / "sqldoctor.json", known_rules=set(RULES))
    events = cfg.table("analytics.events")
    assert events is not None and cfg.is_large(events)
    assert events.partition_columns == {"event_date"}
    assert "user_id" in events.sort_columns


def test_rule_shorthands():
    rules = {"SQD001": "off", "sqd007": "error", "SQD006": {"severity": "warn", "options": {"min_terms": 2}}}
    cfg = config_from_dict({"rules": rules})
    assert not cfg.rule_settings("SQD001").enabled
    assert cfg.rule_settings("SQD007").severity is Severity.ERROR
    assert cfg.rule_settings("SQD006").severity is Severity.WARNING
    assert cfg.rule_settings("SQD006").options == {"min_terms": 2}


def test_disabled_rule_and_severity_override_apply():
    sql = "SELECT * FROM t ORDER BY x"
    assert {"SQD001", "SQD007"} <= set(rule_ids(sql))
    cfg = make_config(rules={"SQD001": "off", "SQD007": "error"})
    findings = rule_ids(sql, cfg)
    assert "SQD001" not in findings and "SQD007" in findings


def test_table_lookup_by_short_and_qualified_name():
    cfg = config_from_dict({"tables": {"analytics.events": {"large": True}, "users": {}}})
    assert cfg.table("events") is cfg.table("analytics.events")
    assert cfg.table("crm.users") is cfg.table("users")
    assert cfg.table("missing") is None


@pytest.mark.parametrize(
    "data, message",
    [
        ({"dialect": "oracle"}, "dialect"),
        ({"rules": {"SQD999": "off"}}, "unknown rule id"),
        ({"rules": {"SQD001": "loud"}}, "unknown severity"),
        ({"tables": {"t": {"rows": "many"}}}, "rows"),
        ({"tables": {"t": {"partition": "x"}}}, "unknown key"),
        ({"colour": True}, "unknown top-level key"),
    ],
)
def test_invalid_config_is_rejected(data, message):
    with pytest.raises(ConfigError, match=message):
        config_from_dict(data, known_rules=set(RULES))


def test_invalid_json_reports_position(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"rules": {,}}')
    with pytest.raises(ConfigError, match="line 1"):
        load_config(path)


def test_replicated_engines_count_as_deduplicating(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"tables": {"t": {"engine": "ReplicatedReplacingMergeTree('/p', '{replica}', ver)"}}}))
    assert load_config(path).table("t").is_deduplicating
