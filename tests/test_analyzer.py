from conftest import lint, make_config
from sql_query_doctor import Analyzer


def test_inline_ignore_for_one_statement():
    sql = "-- sqldoctor: ignore SQD001\nSELECT * FROM t;\nSELECT * FROM u;"
    findings = [f for f in lint(sql) if f.rule_id == "SQD001"]
    assert [f.line for f in findings] == [3]


def test_inline_ignore_all_rules():
    assert lint("SELECT * FROM t WHERE x = NULL -- sqldoctor: ignore") == []


def test_ignore_file():
    sql = "SELECT * FROM t;\nSELECT * FROM u;\n-- sqldoctor: ignore-file SQD001"
    assert [f for f in lint(sql) if f.rule_id == "SQD001"] == []


def test_select_and_ignore_filters():
    sql = "SELECT * FROM t WHERE x = NULL ORDER BY y"
    only = Analyzer(config=make_config(), select=["SQD014"]).analyze_sql(sql)
    assert {f.rule_id for f in only} == {"SQD014"}
    skipped = Analyzer(config=make_config(), ignore=["SQD014"]).analyze_sql(sql)
    assert "SQD014" not in {f.rule_id for f in skipped}


def test_findings_are_sorted_and_deduplicated():
    sql = "SELECT *\nFROM a, b\nWHERE x = NULL ORDER BY 1"
    findings = lint(sql)
    positions = [(f.line, f.column) for f in findings]
    assert positions == sorted(positions)
    assert len({(f.rule_id, f.line, f.column) for f in findings}) == len(findings)


def test_examples_clean_file_has_no_findings():
    from conftest import EXAMPLES
    from sql_query_doctor import load_config

    analyzer = Analyzer(config=load_config(EXAMPLES / "sqldoctor.json"))
    assert analyzer.analyze_file(EXAMPLES / "06_weekly_retention_clean.sql") == []
    assert analyzer.analyze_file(EXAMPLES / "03_support_search.sql")
