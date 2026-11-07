"""Command-line interface.

Usage::

    sqldoctor check queries/*.sql --config sqldoctor.json --format text
    sqldoctor check - < query.sql
    sqldoctor rules
    sqldoctor explain SQD005

Exit codes: 0 = clean (below the ``--fail-on`` threshold), 1 = findings at or
above the threshold, 2 = usage, config or I/O error.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import __version__
from .analyzer import Analyzer
from .config import Config, ConfigError, discover_config, load_config
from .models import Finding, Severity
from .reporters import FORMATS, render, render_rule_explanation, render_rules_text, rule_to_dict, should_color
from .rules import RULES, all_rules, get_rule

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def _csv(value: str) -> List[str]:
    return [v.strip().upper() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="sqldoctor",
        description="Find slow-query anti-patterns in SQL (with ClickHouse-specific rules) and explain how to fix them.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    check = sub.add_parser("check", help="analyze SQL files")
    check.add_argument("paths", nargs="+", help="SQL files, directories (searched for *.sql) or globs; '-' reads stdin")
    check.add_argument("-c", "--config", help="JSON config file (default: ./sqldoctor.json if present)")
    check.add_argument("--no-config", action="store_true", help="ignore any sqldoctor.json in the current directory")
    check.add_argument("-f", "--format", choices=FORMATS, default="text", help="output format (default: text)")
    check.add_argument("-o", "--output", help="write the report to this file instead of stdout")
    check.add_argument("--select", type=_csv, help="only run these rule ids (comma separated)")
    check.add_argument("--ignore", type=_csv, default=[], help="skip these rule ids (comma separated)")
    check.add_argument("--dialect", choices=("clickhouse", "generic"), help="override the dialect from the config")
    check.add_argument(
        "--fail-on",
        choices=("error", "warning", "info", "never"),
        default="error",
        help="lowest severity that makes the exit code non-zero (default: error)",
    )
    check.add_argument("--color", choices=("auto", "always", "never"), default="auto", help="colorize text output")
    check.add_argument("--no-fix", action="store_true", help="hide the fix hints in text output")

    rules = sub.add_parser("rules", help="list available rules")
    rules.add_argument("-f", "--format", choices=("text", "json"), default="text")
    rules.add_argument("--color", choices=("auto", "always", "never"), default="auto")

    explain = sub.add_parser("explain", help="show the full explanation for a rule")
    explain.add_argument("rule", help="rule id (SQD005) or name (non-sargable-predicate)")
    explain.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    return parser


def expand_paths(paths: Sequence[str]) -> List[Path]:
    """Expand directories and glob patterns into a sorted, de-duplicated file list.

    Shells expand globs on POSIX, but not on Windows; doing it here too keeps
    ``sqldoctor check "queries/**/*.sql"`` working everywhere.
    """
    files: List[Path] = []
    seen = set()
    for raw in paths:
        candidates: List[Path]
        if any(ch in raw for ch in "*?["):
            candidates = [Path(p) for p in sorted(glob.glob(raw, recursive=True))]
            if not candidates:
                raise FileNotFoundError(f"no files match {raw!r}")
        else:
            p = Path(raw)
            if p.is_dir():
                candidates = sorted(p.rglob("*.sql"))
            elif p.exists():
                candidates = [p]
            else:
                raise FileNotFoundError(f"no such file or directory: {raw}")
        for c in candidates:
            if c.is_file() and c not in seen:
                seen.add(c)
                files.append(c)
    return files


def _load_config(args: argparse.Namespace) -> Config:
    known = set(RULES)
    if args.config:
        config = load_config(args.config, known_rules=known)
    elif not args.no_config and (found := discover_config()) is not None:
        config = load_config(found, known_rules=known)
    else:
        config = Config()
    if args.dialect:
        config.dialect = args.dialect
    return config


def _exit_code(results: Dict[str, List[Finding]], fail_on: str) -> int:
    if fail_on == "never":
        return EXIT_OK
    threshold = Severity.parse(fail_on).rank
    worst = max((f.severity.rank for fs in results.values() for f in fs), default=0)
    return EXIT_FINDINGS if worst >= threshold else EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    unknown = [r for r in (args.select or []) + args.ignore if r not in RULES]
    if unknown:
        print(f"sqldoctor: unknown rule id(s): {', '.join(unknown)}", file=sys.stderr)
        return EXIT_ERROR
    try:
        config = _load_config(args)
    except ConfigError as exc:
        print(f"sqldoctor: config error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    analyzer = Analyzer(config=config, select=args.select, ignore=args.ignore)
    results: Dict[str, List[Finding]] = {}
    try:
        if args.paths == ["-"]:
            results["<stdin>"] = analyzer.analyze_sql(sys.stdin.read(), path="<stdin>")
        else:
            for path in expand_paths(args.paths):
                results[path.as_posix()] = analyzer.analyze_file(path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"sqldoctor: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.output:
        report = render(args.format, results, color=False, show_fix=not args.no_fix)
        Path(args.output).write_text(report, encoding="utf-8")
    else:
        color = args.format == "text" and should_color(sys.stdout, args.color)
        sys.stdout.write(render(args.format, results, color=color, show_fix=not args.no_fix))
    return _exit_code(results, args.fail_on)


def _cmd_rules(args: argparse.Namespace) -> int:
    if args.format == "json":
        sys.stdout.write(json.dumps([rule_to_dict(r) for r in all_rules()], indent=2) + "\n")
    else:
        sys.stdout.write(render_rules_text(color=should_color(sys.stdout, args.color)))
    return EXIT_OK


def _cmd_explain(args: argparse.Namespace) -> int:
    r = get_rule(args.rule)
    if r is None:
        print(f"sqldoctor: unknown rule {args.rule!r}; run 'sqldoctor rules' for the list", file=sys.stderr)
        return EXIT_ERROR
    sys.stdout.write(render_rule_explanation(r, color=should_color(sys.stdout, args.color)))
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the ``sqldoctor`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "rules":
        return _cmd_rules(args)
    if args.command == "explain":
        return _cmd_explain(args)
    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
