# Contributing

Thanks for taking the time to help. Bug reports with a small SQL snippet that
reproduces the problem (a false positive, a missed case, a crash) are the most
useful thing you can send.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

There are no runtime dependencies, and I'd like to keep it that way.

## Adding a rule

Rules live in `src/sql_query_doctor/rules.py`. A rule is one function that
receives a `RuleContext` (the parsed statement plus config) and yields `Hit`s.

1. **Pick the next free id** (`SQD019`, ...) and a short kebab-case name.
2. **Write the function** and register it with `@rule(...)`:

   ```python
   @rule(
       "SQD019",
       "my-new-rule",
       Severity.WARNING,
       "One-line summary shown in `sqldoctor rules`",
       """Why this pattern is slow or wrong. A few sentences.""",
       """What to do instead.""",
       clickhouse_only=False,  # True if it only makes sense for ClickHouse
       uses_metadata=False,  # True if it needs table metadata from the config
   )
   def check_my_new_rule(ctx: RuleContext) -> Iterator[Hit]:
       for block in ctx.statement.blocks:  # every SELECT, including subqueries
           for ref, meta in _block_tables(ctx, block):
               ...
               yield Hit(ref.token, "specific message about this occurrence")
   ```

   Work on tokens and `SelectBlock`s, never on raw text, so strings and
   comments can't cause false positives. `parser.py` has helpers for column
   references, dotted names and function-call boundaries.
3. **Add tests** in `tests/test_rules.py`: at least one entry in `POSITIVE`
   and one in `NEGATIVE`. `test_every_rule_has_positive_and_negative_cases`
   fails until you do. Negative cases that look *almost* like the
   anti-pattern are the valuable ones.
4. **Document it**: add a row to the rules table in `README.md` and a line
   under `[Unreleased]` in `CHANGELOG.md`.

Messages should say what is wrong with *this* query ("`events` (~4.2B rows) is
on the right side of LEFT JOIN"), while the explanation and fix stay generic.

## Pull requests

Keep them focused, make sure `pytest` and `ruff check .` pass, and describe a
real query that motivated the change if you can.
