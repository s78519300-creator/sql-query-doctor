"""Positive and negative cases for every rule.

Each case is (rule id, SQL). A positive case must produce at least one finding
for that rule; a negative case must produce none.
"""

from __future__ import annotations

import pytest

from conftest import lint, make_config, rule_ids
from sql_query_doctor import Severity
from sql_query_doctor.rules import all_rules

POSITIVE = [
    ("SQD001", "SELECT * FROM events WHERE event_date = today()"),
    ("SQD001", "SELECT e.*, u.name FROM events e JOIN users u ON u.id = e.user_id"),
    ("SQD002", "DELETE FROM events"),
    ("SQD002", "UPDATE users SET plan = 'free'"),
    ("SQD002", "ALTER TABLE events DELETE WHERE 1"),
    ("SQD002", "ALTER TABLE events UPDATE x = 1 WHERE 1 = 1"),
    ("SQD003", "SELECT user_id, count() FROM events GROUP BY user_id"),
    ("SQD004", "SELECT id FROM t WHERE url LIKE '%/checkout'"),
    ("SQD004", "SELECT id FROM t WHERE name NOT ILIKE '_ob%'"),
    ("SQD005", "SELECT id FROM t WHERE toDate(ts) = today()"),
    ("SQD005", "SELECT id FROM t WHERE lower(email) IN ('a@x.io', 'b@x.io')"),
    ("SQD006", "SELECT id FROM t WHERE status = 'a' OR status = 'b' OR status = 'c'"),
    ("SQD007", "SELECT id, ts FROM t WHERE x = 1 ORDER BY ts"),
    ("SQD008", "SELECT DISTINCT u.id FROM users u JOIN orders o ON o.user_id = u.id"),
    ("SQD008", "SELECT DISTINCT a FROM t GROUP BY a"),
    ("SQD009", "SELECT id FROM t WHERE id NOT IN (SELECT user_id FROM banned)"),
    ("SQD010", "SELECT a.x, b.y FROM a, b WHERE a.id = b.id"),
    ("SQD010", "SELECT a.x, b.y FROM a, b"),
    ("SQD011", "SELECT count(DISTINCT user_id) FROM t"),
    ("SQD011", "SELECT uniqExact(session_id) FROM t"),
    ("SQD012", "SELECT plan FROM users WHERE id = 1"),
    ("SQD012", "SELECT count() FROM countries FINAL"),
    ("SQD013", "SELECT c.name, e.user_id FROM countries c JOIN events e ON e.country = c.code"),
    ("SQD013", "SELECT 1 FROM (SELECT id FROM x) s LEFT JOIN events e ON e.user_id = s.id"),
    ("SQD014", "SELECT id FROM t WHERE deleted_at = NULL"),
    ("SQD014", "SELECT id FROM t WHERE NULL <> deleted_at"),
    ("SQD015", "SELECT event_id FROM events WHERE event_date = today() AND referrer = 'google'"),
    ("SQD015", "SELECT 1 FROM countries c JOIN events e ON e.referrer = c.code"),
    ("SQD016", "SELECT count() FROM events WHERE user_id = 42"),
    ("SQD017", "SELECT count() FROM events_all WHERE user_id IN (SELECT user_id FROM users_all)"),
    ("SQD017", "SELECT count() FROM events_all e JOIN users_all u ON u.user_id = e.user_id"),
    ("SQD018", "SELECT id FROM t ORDER BY id LIMIT 100 OFFSET 20000"),
    ("SQD018", "SELECT id FROM t ORDER BY id LIMIT 50000, 100"),
]

NEGATIVE = [
    ("SQD001", "SELECT count(*), a * b FROM t"),
    ("SQD001", "SELECT id FROM t WHERE EXISTS (SELECT * FROM u WHERE u.id = t.id)"),
    ("SQD001", "SELECT 'SELECT * FROM t' AS q -- SELECT * here too\nFROM t"),
    ("SQD002", "DELETE FROM events WHERE user_id = 7"),
    ("SQD002", "ALTER TABLE events DELETE WHERE event_date < '2020-01-01'"),
    ("SQD003", "SELECT count() FROM events WHERE event_date = today()"),
    ("SQD003", "SELECT event_id FROM events LIMIT 10"),
    ("SQD003", "SELECT count() FROM small_unknown_table"),
    ("SQD004", "SELECT id FROM t WHERE url LIKE '/checkout%'"),
    ("SQD004", "SELECT id FROM t WHERE note = '%not a like%' -- LIKE '%x'"),
    ("SQD005", "SELECT id FROM t WHERE ts >= toDateTime('2026-01-01') AND ts < now()"),
    ("SQD005", "SELECT id FROM t WHERE has(tags, 'x')"),
    ("SQD005", "SELECT id FROM t WHERE length(tags) > 0 AND notEmpty(name) = 1"),
    ("SQD006", "SELECT id FROM t WHERE status = 'a' OR kind = 'b' OR region = 'c'"),
    ("SQD006", "SELECT id FROM t WHERE status IN ('a', 'b', 'c')"),
    ("SQD007", "SELECT id FROM t ORDER BY ts LIMIT 10"),
    ("SQD007", "SELECT k, count() FROM t GROUP BY k ORDER BY k"),
    ("SQD007", "SELECT id, row_number() OVER (ORDER BY ts) FROM t"),
    ("SQD008", "SELECT DISTINCT country FROM users"),
    ("SQD009", "SELECT id FROM t WHERE id NOT IN (1, 2, 3)"),
    ("SQD009", "SELECT id FROM t WHERE NOT EXISTS (SELECT 1 FROM banned b WHERE b.id = t.id)"),
    ("SQD010", "SELECT a.x FROM a JOIN b ON a.id = b.id CROSS JOIN c"),
    ("SQD010", "SELECT id FROM t ARRAY JOIN tags"),
    ("SQD011", "SELECT uniq(user_id), count() FROM t"),
    ("SQD012", "SELECT plan FROM users FINAL WHERE id = 1"),
    ("SQD012", "SELECT id, argMax(plan, version) FROM users GROUP BY id"),
    ("SQD012", "SELECT plan FROM users WHERE id = 1 SETTINGS final = 1"),
    ("SQD013", "SELECT 1 FROM events e JOIN countries c ON e.country = c.code"),
    ("SQD013", "SELECT 1 FROM events e ARRAY JOIN e.tags"),
    ("SQD014", "UPDATE t SET deleted_at = NULL WHERE id = 1"),
    ("SQD014", "SELECT id FROM t WHERE deleted_at IS NULL"),
    ("SQD015", "SELECT event_id FROM events WHERE event_date = today() AND isNotNull(referrer)"),
    ("SQD015", "SELECT event_id FROM events WHERE event_date = today() AND user_id = 1"),
    ("SQD016", "SELECT count() FROM events WHERE event_date >= today() - 7"),
    ("SQD016", "SELECT count() FROM events AS e PREWHERE e.event_date = today()"),
    ("SQD017", "SELECT count() FROM events_all WHERE user_id GLOBAL IN (SELECT user_id FROM users_all)"),
    ("SQD017", "SELECT count() FROM events_all e GLOBAL JOIN users_all u ON u.user_id = e.user_id"),
    ("SQD017", "SELECT count() FROM events WHERE user_id IN (SELECT user_id FROM users_all)"),
    ("SQD018", "SELECT id FROM t ORDER BY id LIMIT 100 OFFSET 200"),
]


@pytest.mark.parametrize("rule_id,sql", POSITIVE, ids=[f"{r}-pos-{i}" for i, (r, _) in enumerate(POSITIVE)])
def test_rule_positive(rule_id, sql):
    assert rule_id in rule_ids(sql), f"{rule_id} should fire on: {sql}"


@pytest.mark.parametrize("rule_id,sql", NEGATIVE, ids=[f"{r}-neg-{i}" for i, (r, _) in enumerate(NEGATIVE)])
def test_rule_negative(rule_id, sql):
    assert rule_id not in rule_ids(sql), f"{rule_id} should not fire on: {sql}"


def test_every_rule_has_positive_and_negative_cases():
    ids = {r.id for r in all_rules()}
    assert ids == {r for r, _ in POSITIVE}
    assert ids == {r for r, _ in NEGATIVE}


def test_rule_metadata_is_complete():
    rules = all_rules()
    assert len(rules) >= 12
    for r in rules:
        assert r.id.startswith("SQD") and len(r.id) == 6
        assert r.name and r.summary and r.explanation and r.fix
        assert r.name == r.name.lower() and " " not in r.name
    assert len({r.name for r in rules}) == len(rules)


def test_comma_join_without_where_is_an_error():
    (finding,) = [f for f in lint("SELECT a.x FROM a, b") if f.rule_id == "SQD010"]
    assert finding.severity is Severity.ERROR


def test_count_distinct_escalates_on_high_cardinality_column():
    sql = "SELECT count(DISTINCT user_id), count(DISTINCT os) FROM events WHERE event_date = today()"
    hits = [f for f in lint(sql) if f.rule_id == "SQD011"]
    assert sorted(f.severity.value for f in hits) == ["info", "warning"]


def test_non_sargable_mentions_key_column():
    (finding,) = [f for f in lint("SELECT 1 FROM events WHERE toYYYYMM(event_date) = 202601") if f.rule_id == "SQD005"]
    assert "partition key of events" in finding.message


def test_or_chain_threshold_is_configurable():
    sql = "SELECT id FROM t WHERE s = 1 OR s = 2"
    assert "SQD006" not in rule_ids(sql)
    cfg = make_config(rules={"SQD006": {"options": {"min_terms": 2}}})
    assert "SQD006" in rule_ids(sql, cfg)


def test_clickhouse_rules_skipped_for_generic_dialect():
    sql = "SELECT plan FROM users WHERE id = 1"
    assert "SQD012" in rule_ids(sql)
    assert "SQD012" not in rule_ids(sql, make_config(dialect="generic"))


def test_findings_point_at_the_right_location():
    (finding,) = lint("SELECT id\nFROM t\nWHERE a = NULL")
    assert (finding.rule_id, finding.line, finding.column) == ("SQD014", 3, 9)
    assert finding.snippet == "WHERE a = NULL"
