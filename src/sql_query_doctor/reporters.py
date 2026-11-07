"""Output formats: human-readable text, JSON and SARIF 2.1.0."""

from __future__ import annotations

import json
import os
import textwrap
from collections import Counter
from pathlib import Path
from typing import IO, Dict, List, Mapping, Optional

from . import __version__
from .models import Finding, Severity
from .rules import Rule, all_rules, get_rule

FORMATS = ("text", "json", "sarif")
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
PROJECT_URL = "https://github.com/s78519300-creator/sql-query-doctor"

_SARIF_LEVELS = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "note"}


class Style:
    """Tiny ANSI helper; every method is a no-op when colour is off."""

    CODES = {"bold": "1", "dim": "2", "red": "31", "yellow": "33", "blue": "34", "cyan": "36", "green": "32", "magenta": "35"}

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        codes = ";".join(self.CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"

    def severity(self, sev: Severity, text: Optional[str] = None) -> str:
        """Colour ``text`` (default: the severity name) by severity."""
        colour = {Severity.ERROR: "red", Severity.WARNING: "yellow", Severity.INFO: "blue"}[sev]
        return self(text if text is not None else sev.value, colour, "bold")


def should_color(stream: IO[str], mode: str = "auto") -> bool:
    """Decide whether to emit ANSI colours.

    ``auto`` honours ``NO_COLOR`` / ``FORCE_COLOR`` and otherwise colours only
    when ``stream`` is a TTY.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def summarize(results: Mapping[str, List[Finding]]) -> Dict[str, int]:
    """Count findings by severity across all files."""
    counts = Counter(f.severity.value for findings in results.values() for f in findings)
    return {
        "files": len(results),
        "findings": sum(counts.values()),
        "error": counts.get("error", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# -- text ---------------------------------------------------------------------------


def render_text(results: Mapping[str, List[Finding]], color: bool = False, show_fix: bool = True) -> str:
    """Render findings grouped by file, with source context and a fix hint."""
    s = Style(color)
    out: List[str] = []
    for path, findings in results.items():
        if not findings:
            continue
        out.append(s(path, "bold", "magenta"))
        for f in findings:
            loc = f"{f.line}:{f.column}"
            sev = s.severity(f.severity, f.severity.value.ljust(7))
            out.append(f"  {s(loc.ljust(7), 'dim')} {sev}  {s(f.rule_id, 'bold')}  {f.message}")
            if f.snippet:
                gutter = str(f.line).rjust(5)
                caret_pad = " " * (f.column - 1)
                out.append(s(f"{gutter} | ", "dim") + f.snippet)
                out.append(s("      | ", "dim") + caret_pad + s.severity(f.severity, "^"))
            if show_fix:
                r = get_rule(f.rule_id)
                if r is not None:
                    wrapped = textwrap.wrap(r.fix, width=92) or [""]
                    out.append(s("      = fix: ", "cyan") + wrapped[0])
                    out.extend("             " + line for line in wrapped[1:])
            out.append("")

    summary = summarize(results)
    if summary["findings"] == 0:
        out.append(s(f"No problems found in {_plural(summary['files'], 'file')}.", "green", "bold"))
    else:
        parts = [
            s.severity(Severity.ERROR, _plural(summary["error"], "error")),
            s.severity(Severity.WARNING, _plural(summary["warning"], "warning")),
            s.severity(Severity.INFO, f"{summary['info']} info"),
        ]
        out.append(
            s(f"Found {_plural(summary['findings'], 'problem')}", "bold")
            + f" ({', '.join(parts)}) in {_plural(summary['files'], 'file')}."
        )
    return "\n".join(out) + "\n"


def render_rules_text(rules: Optional[List[Rule]] = None, color: bool = False) -> str:
    """Render the rule catalogue as an aligned table."""
    s = Style(color)
    rules = rules if rules is not None else all_rules()
    name_w = max(len(r.name) for r in rules)
    lines = []
    for r in rules:
        flags = []
        if r.clickhouse_only:
            flags.append("clickhouse")
        if r.uses_metadata:
            flags.append("metadata")
        flag_text = s(f" [{', '.join(flags)}]", "dim") if flags else ""
        sev = s.severity(r.severity, r.severity.value.ljust(7))
        lines.append(f"{s(r.id, 'bold')}  {sev}  {r.name.ljust(name_w)}  {r.summary}{flag_text}")
    return "\n".join(lines) + "\n"


def render_rule_explanation(r: Rule, color: bool = False) -> str:
    """Long-form description of one rule (``sqldoctor explain``)."""
    s = Style(color)
    parts = [
        f"{s(r.id, 'bold')} {r.name} ({s.severity(r.severity)})",
        "",
        s(r.summary, "bold"),
        "",
        textwrap.fill(r.explanation, width=88),
        "",
        s("How to fix: ", "cyan") + textwrap.fill(r.fix, width=88, initial_indent=" " * 12)[12:],
    ]
    if r.options:
        parts += ["", s("Options:", "bold")]
        parts += [f"  {name}: {text}" for name, text in r.options.items()]
    if r.clickhouse_only:
        parts += ["", s("Runs only with dialect = clickhouse.", "dim")]
    if r.uses_metadata:
        parts += ["", s("Uses table metadata from the config file.", "dim")]
    return "\n".join(parts) + "\n"


# -- json -----------------------------------------------------------------------------


def render_json(results: Mapping[str, List[Finding]]) -> str:
    """Machine-readable JSON report."""
    payload = {
        "tool": "sql-query-doctor",
        "version": __version__,
        "summary": summarize(results),
        "findings": [f.to_dict() for findings in results.values() for f in findings],
    }
    return json.dumps(payload, indent=2) + "\n"


def rule_to_dict(r: Rule) -> Dict[str, object]:
    """Serialise rule metadata."""
    return {
        "id": r.id,
        "name": r.name,
        "severity": r.severity.value,
        "summary": r.summary,
        "explanation": r.explanation,
        "fix": r.fix,
        "clickhouse_only": r.clickhouse_only,
        "uses_metadata": r.uses_metadata,
        "options": r.options,
        "tags": r.tags,
    }


# -- sarif ------------------------------------------------------------------------------


def _artifact_uri(path: str) -> str:
    """SARIF wants repo-relative URIs; make absolute paths relative to the CWD when possible."""
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return p.as_uri()


def render_sarif(results: Mapping[str, List[Finding]], rules: Optional[List[Rule]] = None) -> str:
    """SARIF 2.1.0 log, suitable for ``github/codeql-action/upload-sarif``."""
    rules = rules if rules is not None else all_rules()
    index = {r.id: i for i, r in enumerate(rules)}
    driver_rules = [
        {
            "id": r.id,
            "name": r.name,
            "shortDescription": {"text": r.summary},
            "fullDescription": {"text": r.explanation},
            "help": {
                "text": f"{r.explanation}\n\nHow to fix: {r.fix}",
                "markdown": f"{r.explanation}\n\n**How to fix:** {r.fix}",
            },
            "helpUri": f"{PROJECT_URL}#rules",
            "defaultConfiguration": {"level": _SARIF_LEVELS[r.severity]},
            "properties": {"tags": r.tags, "precision": "medium"},
        }
        for r in rules
    ]
    sarif_results = []
    for findings in results.values():
        for f in findings:
            item: Dict[str, object] = {
                "ruleId": f.rule_id,
                "level": _SARIF_LEVELS[f.severity],
                "message": {"text": f.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": _artifact_uri(f.path), "uriBaseId": "%SRCROOT%"},
                            "region": {"startLine": f.line, "startColumn": f.column},
                        }
                    }
                ],
                "partialFingerprints": {"primaryLocationLineHash": f"{f.rule_id}:{f.snippet.strip()}"},
            }
            if f.rule_id in index:
                item["ruleIndex"] = index[f.rule_id]
            sarif_results.append(item)

    log = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "sql-query-doctor",
                        "version": __version__,
                        "semanticVersion": __version__,
                        "informationUri": PROJECT_URL,
                        "rules": driver_rules,
                    }
                },
                "results": sarif_results,
            }
        ],
    }
    return json.dumps(log, indent=2) + "\n"


def render(fmt: str, results: Mapping[str, List[Finding]], color: bool = False, show_fix: bool = True) -> str:
    """Dispatch to the renderer for ``fmt``."""
    if fmt == "text":
        return render_text(results, color=color, show_fix=show_fix)
    if fmt == "json":
        return render_json(results)
    if fmt == "sarif":
        return render_sarif(results)
    raise ValueError(f"unknown format {fmt!r}")
