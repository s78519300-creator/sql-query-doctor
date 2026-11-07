import json
import subprocess
import sys

from conftest import EXAMPLES, ROOT
from sql_query_doctor.cli import main

CONFIG = str(EXAMPLES / "sqldoctor.json")


def test_check_examples_exits_nonzero_on_errors(capsys):
    code = main(["check", str(EXAMPLES), "--config", CONFIG, "--color", "never"])
    out = capsys.readouterr().out
    assert code == 1
    assert "SQD002" in out and "Found" in out


def test_clean_file_exits_zero(capsys):
    code = main(["check", str(EXAMPLES / "06_weekly_retention_clean.sql"), "--config", CONFIG])
    assert code == 0
    assert "No problems found" in capsys.readouterr().out


def test_fail_on_threshold(capsys):
    target = str(EXAMPLES / "03_support_search.sql")  # warnings and info only
    assert main(["check", target, "--config", CONFIG]) == 0
    assert main(["check", target, "--config", CONFIG, "--fail-on", "warning"]) == 1
    assert main(["check", str(EXAMPLES), "--config", CONFIG, "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_json_and_sarif_to_file(tmp_path, capsys):
    out = tmp_path / "report.sarif"
    main(["check", str(EXAMPLES), "--config", CONFIG, "--format", "sarif", "--output", str(out)])
    assert json.loads(out.read_text())["runs"][0]["results"]
    main(["check", str(EXAMPLES / "02_gdpr_cleanup.sql"), "--config", CONFIG, "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["error"] == 3


def test_stdin_and_glob(monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("DELETE FROM t"))
    assert main(["check", "-", "--no-config"]) == 1
    assert "<stdin>" in capsys.readouterr().out
    assert main(["check", str(EXAMPLES / "0[1-2]_*.sql"), "--config", CONFIG, "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["files"] == 2


def test_usage_errors_exit_2(tmp_path, capsys):
    assert main(["check", str(tmp_path / "missing.sql")]) == 2
    assert main(["check", str(EXAMPLES), "--select", "SQD999"]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"rules": {"SQD001": "loud"}}')
    assert main(["check", str(EXAMPLES), "--config", str(bad)]) == 2
    assert "config error" in capsys.readouterr().err


def test_rules_and_explain_commands(capsys):
    assert main(["rules", "--format", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) >= 12
    assert main(["explain", "non-sargable-predicate", "--color", "never"]) == 0
    assert "SQD005" in capsys.readouterr().out
    assert main(["explain", "SQD000"]) == 2


def test_python_dash_m_entry_point():
    proc = subprocess.run(
        [sys.executable, "-m", "sql_query_doctor", "rules", "--color", "never"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert proc.returncode == 0
    assert "SQD001" in proc.stdout
