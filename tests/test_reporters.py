import json

from conftest import lint
from sql_query_doctor.reporters import render_json, render_rules_text, render_sarif, render_text, should_color

SQL = "SELECT *\nFROM t\nWHERE deleted_at = NULL"


def results():
    return {"q.sql": lint(SQL, path="q.sql")}


def test_text_output_has_location_snippet_and_summary():
    out = render_text(results())
    assert "q.sql" in out
    assert "3:18" in out and "SQD014" in out
    assert "WHERE deleted_at = NULL" in out
    assert "fix:" in out
    assert "Found 2 problems (1 error, 1 warning, 0 info) in 1 file." in out
    assert "\033[" not in out


def test_text_output_color_and_clean_message():
    assert "\033[" in render_text(results(), color=True)
    assert "No problems found in 1 file." in render_text({"ok.sql": []})


def test_json_output_round_trips():
    data = json.loads(render_json(results()))
    assert data["summary"] == {"files": 1, "findings": 2, "error": 1, "warning": 1, "info": 0}
    assert {f["rule_id"] for f in data["findings"]} == {"SQD001", "SQD014"}
    assert data["findings"][0]["severity"] in ("error", "warning", "info")


def test_sarif_structure():
    log = json.loads(render_sarif(results()))
    assert log["version"] == "2.1.0"
    run = log["runs"][0]
    rules = run["tool"]["driver"]["rules"]
    assert len(rules) >= 12
    result = next(r for r in run["results"] if r["ruleId"] == "SQD014")
    assert result["level"] == "error"
    assert rules[result["ruleIndex"]]["id"] == "SQD014"
    region = result["locations"][0]["physicalLocation"]["region"]
    assert region == {"startLine": 3, "startColumn": 18}
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "q.sql"


def test_should_color_respects_tty_and_no_color(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert should_color(Tty())
    assert not should_color(object())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not should_color(Tty())
    assert should_color(object(), "always")


def test_rules_listing_contains_every_rule():
    out = render_rules_text()
    assert out.count("SQD0") >= 12
    assert "[clickhouse, metadata]" in out
