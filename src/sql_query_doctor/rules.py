"""The rule catalogue.

Each rule is a plain function decorated with :func:`rule`. It receives a
:class:`RuleContext` for one statement and yields :class:`Hit` objects. The
engine turns hits into :class:`~sql_query_doctor.models.Finding` objects,
applying config severity overrides and inline suppressions.

Adding a rule is a matter of writing one function here plus tests; see
CONTRIBUTING.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .config import Config, TableMeta
from .models import Severity
from .parser import (
    COMPARISON_OPERATORS,
    SelectBlock,
    Statement,
    TableRef,
    call_end,
    column_refs,
    is_column_token,
    read_dotted_name,
    referenced_tables,
)
from .tokenizer import Token, TokenType


@dataclass(frozen=True)
class Hit:
    """A raw match produced by a rule.

    ``severity`` lets a rule escalate or soften a particular occurrence (e.g. a
    comma join with no WHERE at all is an error, one with a WHERE is only a
    warning). A severity set in the user's config always wins.
    """

    token: Token
    message: str
    severity: Optional[Severity] = None


@dataclass
class RuleContext:
    """Everything a rule needs to inspect one statement."""

    statement: Statement
    config: Config
    options: Dict[str, Any] = field(default_factory=dict)

    def table(self, name: Optional[str]) -> Optional[TableMeta]:
        """Shortcut for :meth:`Config.table`."""
        return self.config.table(name)

    def option(self, key: str, default: Any) -> Any:
        """Read a rule option from the config, falling back to ``default``."""
        return self.options.get(key, default)


CheckFn = Callable[[RuleContext], Iterable[Hit]]


@dataclass(frozen=True)
class Rule:
    """Metadata and implementation of one rule."""

    id: str
    name: str
    severity: Severity
    summary: str
    explanation: str
    fix: str
    check: CheckFn
    clickhouse_only: bool = False
    uses_metadata: bool = False
    category: str = "performance"
    options: Dict[str, str] = field(default_factory=dict)

    @property
    def tags(self) -> List[str]:
        """Tags used in SARIF output and the rules listing."""
        tags = [self.category]
        if self.clickhouse_only:
            tags.append("clickhouse")
        if self.uses_metadata:
            tags.append("needs-table-metadata")
        return tags


RULES: Dict[str, Rule] = {}


def rule(
    id: str,
    name: str,
    severity: Severity,
    summary: str,
    explanation: str,
    fix: str,
    clickhouse_only: bool = False,
    uses_metadata: bool = False,
    category: str = "performance",
    options: Optional[Dict[str, str]] = None,
) -> Callable[[CheckFn], CheckFn]:
    """Register a rule function in :data:`RULES`."""

    def decorator(fn: CheckFn) -> CheckFn:
        if id in RULES:
            raise ValueError(f"duplicate rule id {id}")
        RULES[id] = Rule(
            id=id,
            name=name,
            severity=severity,
            summary=summary,
            explanation=" ".join(explanation.split()),
            fix=" ".join(fix.split()),
            check=fn,
            clickhouse_only=clickhouse_only,
            uses_metadata=uses_metadata,
            category=category,
            options=dict(options or {}),
        )
        return fn

    return decorator


# -- small helpers ------------------------------------------------------------------


def _is_literal(tok: Token) -> bool:
    return tok.type in (TokenType.STRING, TokenType.NUMBER, TokenType.PARAMETER) or tok.is_keyword("TRUE", "FALSE")


def _display_table(ref: TableRef) -> str:
    if ref.is_function:
        return f"{ref.name}()"
    return ref.name or "(subquery)"


def _block_tables(ctx: RuleContext, block: SelectBlock) -> Iterator[Tuple[TableRef, TableMeta]]:
    for ref in block.tables:
        if ref.is_subquery or ref.is_function:
            continue
        meta = ctx.table(ref.name)
        if meta is not None:
            yield ref, meta


def _top_level(stmt: Statement) -> List[Token]:
    return [t for t in stmt.tokens if t.depth == 0]


def _source_text(toks: List[Token]) -> str:
    """Re-assemble tokens into compact text for messages."""
    out = ""
    for i, tok in enumerate(toks):
        prev = toks[i - 1] if i else None
        call = tok.is_punct("(") and prev is not None and prev.type is TokenType.IDENTIFIER
        if prev is not None and not (call or tok.is_punct(")", ",", ".") or prev.is_punct("(", ".")):
            out += " "
        out += tok.value
    return out


# -- rules ------------------------------------------------------------------------------


@rule(
    "SQD001",
    "select-star",
    Severity.WARNING,
    "SELECT * reads every column",
    """In a column store like ClickHouse every extra column is an extra file to
    read and decompress, so SELECT * is often the single most expensive part of
    a query. It also makes results change shape when the schema changes.""",
    """List only the columns you need. In ClickHouse, SELECT * EXCEPT (...) is
    still a full read of the remaining columns.""",
)
def check_select_star(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if block.context == "EXISTS":
            continue  # EXISTS (SELECT * ...) never materialises columns
        prev: Optional[Token] = block.keyword
        for tok in block.clause("SELECT"):
            if tok.depth != block.depth:
                continue
            if tok.is_op("*") and (prev is None or prev.is_keyword("SELECT", "DISTINCT") or prev.is_punct(",", ".")):
                if prev is not None and prev.is_punct("."):
                    yield Hit(tok, "qualified star (alias.*) reads every column of that table")
                else:
                    tables = ", ".join(_display_table(t) for t in block.tables) or "the source"
                    yield Hit(tok, f"SELECT * reads every column of {tables}")
            prev = tok


@rule(
    "SQD002",
    "unbounded-delete-update",
    Severity.ERROR,
    "DELETE/UPDATE without a real WHERE",
    """A DELETE or UPDATE without a WHERE clause (or with one that is always
    true, like WHERE 1 = 1) touches every row. In ClickHouse, ALTER TABLE ...
    DELETE/UPDATE is a mutation that rewrites every data part of the table,
    which is both slow and hard to undo.""",
    """Add a selective WHERE clause. To empty a table use TRUNCATE; to drop old
    data in ClickHouse prefer ALTER TABLE ... DROP PARTITION or a TTL.""",
    category="correctness",
)
def check_unbounded_write(ctx: RuleContext) -> Iterator[Hit]:
    stmt = ctx.statement
    top = _top_level(stmt)
    if not top:
        return
    verb: Optional[Token] = None
    if stmt.kind in ("DELETE", "UPDATE"):
        verb = next((t for t in top if t.is_keyword(stmt.kind)), top[0])
    elif stmt.kind == "ALTER":
        verb = next((t for t in top if t.is_keyword("DELETE", "UPDATE")), None)
    if verb is None:
        return

    after = top[top.index(verb) :]
    where_idx = next((i for i, t in enumerate(after) if t.is_keyword("WHERE")), None)
    if where_idx is None:
        yield Hit(verb, f"{verb.upper} without WHERE affects every row of the table")
        return
    cond = after[where_idx + 1 :]
    cond = cond[: next((i for i, t in enumerate(cond) if t.is_keyword("SETTINGS")), len(cond))]
    values = [t.upper for t in cond]
    trivially_true = values in (["1"], ["TRUE"]) or (
        len(cond) == 3 and cond[1].is_op("=", "==") and _is_literal(cond[0]) and values[0] == values[2]
    )
    if trivially_true:
        yield Hit(after[where_idx], f"{verb.upper} ... WHERE {_source_text(cond)} is always true and affects every row")


@rule(
    "SQD003",
    "unfiltered-large-table-scan",
    Severity.WARNING,
    "Large table read without any filter",
    """The table is declared large in the config, and this query block reads it
    without WHERE or PREWHERE. That is a full scan; on a billion-row table it
    is usually an accident rather than a plan.""",
    """Add a filter on the partition key or the leading sorting-key columns. If
    you really need every row, aggregate in a materialized view instead of
    scanning on every request.""",
    uses_metadata=True,
)
def check_unfiltered_large_table(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if block.is_filtered:
            continue
        bounded = block.has("LIMIT") and not block.has("ORDER BY") and not block.has("GROUP BY")
        if bounded:
            continue  # a bare LIMIT can stop reading early
        for ref, meta in _block_tables(ctx, block):
            if ctx.config.is_large(meta):
                size = f" (~{meta.rows:,} rows)" if meta.rows else ""
                yield Hit(ref.token, f"`{ref.name}`{size} is scanned without WHERE/PREWHERE")


@rule(
    "SQD004",
    "leading-wildcard-like",
    Severity.WARNING,
    "LIKE pattern starts with a wildcard",
    """A pattern like '%error' cannot use a primary-key or B-tree index because
    the prefix is unknown, so every value is checked. In ClickHouse it also
    defeats skip indexes such as tokenbf_v1 unless the pattern is tokenized.""",
    """Anchor the pattern ('error%'), search for whole tokens with hasToken(),
    add an ngrambf_v1 / tokenbf_v1 skip index, or store reverse(col) for
    suffix searches.""",
)
def check_leading_wildcard(ctx: RuleContext) -> Iterator[Hit]:
    toks = ctx.statement.tokens
    for i, tok in enumerate(toks[:-1]):
        if tok.is_keyword("LIKE", "ILIKE"):
            pattern = toks[i + 1]
            if pattern.type is TokenType.STRING and pattern.string_value.startswith(("%", "_")):
                yield Hit(pattern, f"{tok.upper} {pattern.value} has a leading wildcard and cannot use an index")


@rule(
    "SQD005",
    "non-sargable-predicate",
    Severity.WARNING,
    "Function applied to a filtered column",
    """Wrapping a column in a function (toDate(ts) = ..., lower(email) = ...)
    hides it from the index: the engine has to compute the function for every
    row before it can compare. ClickHouse can sometimes see through monotonic
    functions on the sorting key, but it is fragile and does not work for most
    functions.""",
    """Move the transformation to the constant side: replace toDate(ts) =
    '2024-05-01' with ts >= '2024-05-01' AND ts < '2024-05-02', or store a
    normalised copy of the column (a MATERIALIZED column) and filter on that.""",
    options={"ignore_functions": "extra function names to treat as harmless (default: none)"},
)
def check_non_sargable(ctx: RuleContext) -> Iterator[Hit]:
    skip = SARGABLE_FUNCTIONS | {str(f).lower() for f in ctx.option("ignore_functions", [])}
    for block in ctx.statement.blocks:
        keys: Dict[str, str] = {}
        for ref, meta in _block_tables(ctx, block):
            for col in meta.sort_columns:
                keys.setdefault(col, f"sorting key of {ref.name}")
            for col in meta.partition_columns:
                keys[col] = f"partition key of {ref.name}"
        for clause in ("WHERE", "PREWHERE"):
            yield from _non_sargable_in(block.clause(clause), keys, skip)


#: Functions that are cheap on ClickHouse subcolumns (``length(arr)`` reads
#: ``arr.size0``) or that skip indexes understand directly; wrapping a column
#: in them is not the anti-pattern this rule is about.
SARGABLE_FUNCTIONS = frozenset(
    {"length", "empty", "notempty", "has", "hasany", "hasall", "hastoken", "startswith", "position", "match", "mapcontains"}
)


def _non_sargable_in(toks: List[Token], keys: Dict[str, str], skip: frozenset) -> Iterator[Hit]:
    i = 0
    while i < len(toks) - 1:
        tok = toks[i]
        if tok.type is TokenType.IDENTIFIER and toks[i + 1].is_punct("(") and tok.name not in skip:
            end = call_end(toks, i)
            nxt = toks[end + 1] if end + 1 < len(toks) else None
            compared = nxt is not None and (nxt.is_op(*COMPARISON_OPERATORS) or nxt.is_keyword("BETWEEN", "IN"))
            cols = [name for name, _ in column_refs(toks[i + 2 : end])]
            if compared and cols:
                col = cols[0]
                where = f" ({keys[col]})" if col in keys else ""
                yield Hit(
                    tok, f"{_source_text(toks[i : end + 1])} wraps column `{col}`{where}; the comparison can't use an index"
                )
                i = end + 1
                continue
        i += 1


@rule(
    "SQD006",
    "or-chain-to-in",
    Severity.INFO,
    "OR chain of equalities on one column",
    """col = 1 OR col = 2 OR col = 3 is harder to read and, depending on the
    planner, may be evaluated as separate predicates instead of a single set
    lookup. In ClickHouse, IN builds a hash set and works with the primary key
    and skip indexes.""",
    """Rewrite as col IN (1, 2, 3).""",
    options={"min_terms": "how many OR-ed equalities make a chain (default 3)"},
)
def check_or_chain(ctx: RuleContext) -> Iterator[Hit]:
    toks = ctx.statement.tokens
    min_terms = int(ctx.option("min_terms", 3))

    def equality(i: int) -> Optional[Tuple[str, int]]:
        if i >= len(toks) or toks[i].type is not TokenType.IDENTIFIER:
            return None
        name, j = read_dotted_name(toks, i)
        if j + 1 < len(toks) and toks[j].is_op("=", "==") and _is_literal(toks[j + 1]):
            return name, j + 2
        return None

    i = 0
    while i < len(toks):
        first = equality(i)
        if first is None:
            i += 1
            continue
        col, j = first
        terms = 1
        depth = toks[i].depth
        while j < len(toks) and toks[j].is_keyword("OR") and toks[j].depth == depth:
            nxt = equality(j + 1)
            if nxt is None or nxt[0] != col:
                break
            terms += 1
            j = nxt[1]
        if terms >= min_terms:
            yield Hit(toks[i], f"{terms} OR-ed equality checks on `{col}` can be written as {col} IN (...)")
        i = max(j, i + 1)


@rule(
    "SQD007",
    "order-by-without-limit",
    Severity.INFO,
    "ORDER BY without LIMIT",
    """Sorting a raw, ungrouped result without a LIMIT forces a full sort of
    every matching row, often only for a client to read the first page. In a
    subquery the ORDER BY is usually discarded by the planner anyway.""",
    """Add a LIMIT, sort on the client if the result is small, or drop the
    ORDER BY from subqueries. Grouped queries are not reported because their
    output is normally small.""",
)
def check_order_without_limit(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if block.has("ORDER BY") and not block.has("LIMIT") and not block.has("GROUP BY"):
            yield Hit(block.clause_keywords["ORDER BY"], "ORDER BY without LIMIT sorts the entire result")


@rule(
    "SQD008",
    "distinct-as-dedupe",
    Severity.WARNING,
    "SELECT DISTINCT used to paper over duplicates",
    """DISTINCT on top of a JOIN usually hides a fan-out caused by a missing or
    incomplete join condition: the database still builds every duplicate row
    and then spends more work removing them. DISTINCT combined with GROUP BY is
    simply redundant.""",
    """Fix the join (add the missing key, or deduplicate the right side in a
    subquery), use EXISTS / a SEMI JOIN for membership checks, or drop DISTINCT
    when GROUP BY already makes rows unique.""",
)
def check_distinct_dedupe(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if not block.distinct:
            continue
        distinct_tok = block.clause("SELECT")[0]
        if block.has("GROUP BY"):
            yield Hit(distinct_tok, "DISTINCT is redundant: GROUP BY already returns unique rows")
        elif any(t.is_join or t.join == "COMMA" for t in block.tables):
            yield Hit(distinct_tok, "SELECT DISTINCT over a JOIN often masks row fan-out from the join")


@rule(
    "SQD009",
    "not-in-subquery",
    Severity.WARNING,
    "NOT IN with a subquery",
    """If the subquery returns a single NULL, x NOT IN (subquery) is never true
    in standard SQL and the query silently returns nothing. ClickHouse ignores
    NULLs here unless transform_null_in = 1, so the same SQL behaves
    differently across engines. NOT IN also materialises the whole subquery as
    a set in memory.""",
    """Use NOT EXISTS (correlated), a LEFT ANTI JOIN in ClickHouse, or add
    WHERE col IS NOT NULL to the subquery.""",
    category="correctness",
)
def check_not_in_subquery(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if block.context == "NOT IN" and block.context_token is not None:
            yield Hit(block.context_token, "NOT IN (subquery) returns no rows if the subquery yields a NULL")


@rule(
    "SQD010",
    "implicit-cross-join",
    Severity.WARNING,
    "Comma join or JOIN without a condition",
    """FROM a, b is a cross join; the join condition, if any, lives somewhere
    in WHERE where it is easy to lose. Without a condition at all the result is
    a Cartesian product: rows(a) x rows(b).""",
    """Use an explicit JOIN ... ON / USING. If you really want a Cartesian
    product, say so with CROSS JOIN.""",
)
def check_implicit_cross_join(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        for ref in block.tables:
            if ref.join == "COMMA":
                if block.is_filtered:
                    yield Hit(ref.token, f"comma join with `{_display_table(ref)}`; use an explicit JOIN ... ON")
                else:
                    yield Hit(
                        ref.token,
                        f"comma join with `{_display_table(ref)}` and no WHERE produces a Cartesian product",
                        Severity.ERROR,
                    )
            elif ref.is_join and not ref.has_condition and not any(w in ref.join for w in ("CROSS", "ARRAY")):
                yield Hit(ref.token, f"{ref.join} `{_display_table(ref)}` has no ON/USING condition", Severity.ERROR)


@rule(
    "SQD011",
    "exact-count-distinct",
    Severity.INFO,
    "Exact COUNT(DISTINCT) / uniqExact",
    """An exact distinct count must keep every distinct value in memory. On a
    high-cardinality column (user ids, session ids) that is gigabytes of hash
    table per query. ClickHouse maps COUNT(DISTINCT) to uniqExact by default.
    Columns listed under high_cardinality in the config raise this to a
    warning.""",
    """If a ~1% error is acceptable use uniq() / uniqCombined() in ClickHouse or
    APPROX_COUNT_DISTINCT elsewhere. For dashboards, pre-aggregate with
    uniqState() in an AggregatingMergeTree.""",
    uses_metadata=True,
)
def check_count_distinct(ctx: RuleContext) -> Iterator[Hit]:
    high_card = set()
    for block in ctx.statement.blocks:
        for _, meta in _block_tables(ctx, block):
            high_card |= meta.high_cardinality
    toks = ctx.statement.tokens
    for i, tok in enumerate(toks[:-1]):
        if tok.type is not TokenType.IDENTIFIER or not toks[i + 1].is_punct("("):
            continue
        fn = tok.name
        is_count_distinct = fn == "count" and i + 2 < len(toks) and toks[i + 2].is_keyword("DISTINCT")
        if not (is_count_distinct or fn == "uniqexact"):
            continue
        end = call_end(toks, i)
        cols = [name for name, _ in column_refs(toks[i + 2 : end])]
        label = _source_text(toks[i : end + 1])
        if any(c in high_card for c in cols):
            yield Hit(tok, f"{label} on a high-cardinality column keeps every distinct value in memory", Severity.WARNING)
        else:
            yield Hit(tok, f"{label} is an exact distinct count; consider an approximate one")


@rule(
    "SQD012",
    "missing-final",
    Severity.WARNING,
    "Deduplicating engine read without FINAL",
    """Replacing/Collapsing/Summing/AggregatingMergeTree only collapse rows when
    parts merge, which happens in the background at an unknown time. Reading
    such a table without FINAL (or an aggregation that does the collapsing)
    returns duplicates and stale versions. FINAL on a plain MergeTree is an
    error.""",
    """Add FINAL after the table name (fast since 23.x, especially with
    do_not_merge_across_partitions_select_final = 1), or aggregate explicitly
    with argMax(value, version) / GROUP BY key.""",
    clickhouse_only=True,
    uses_metadata=True,
)
def check_missing_final(ctx: RuleContext) -> Iterator[Hit]:
    settings_final = _has_final_setting(ctx.statement)
    for block in ctx.statement.blocks:
        for ref, meta in _block_tables(ctx, block):
            if not meta.engine:
                continue
            if meta.is_deduplicating and not ref.final and not block.has("GROUP BY") and not settings_final:
                engine = meta.engine.split("(")[0]
                yield Hit(ref.token, f"`{ref.name}` is a {engine}; without FINAL you may read unmerged duplicate rows")
            elif ref.final and not meta.is_deduplicating:
                yield Hit(ref.token, f"FINAL on `{ref.name}` ({meta.engine}) does nothing useful and may be rejected")


@rule(
    "SQD013",
    "large-right-join",
    Severity.WARNING,
    "Large table on the right side of a JOIN",
    """ClickHouse's default hash join loads the entire right-hand side into
    memory and streams the left side through it. Putting the big table on the
    right is the classic cause of "Memory limit exceeded" on joins.""",
    """Put the smaller table on the right (swap LEFT JOIN for RIGHT JOIN if you
    need to keep the semantics), pre-filter the right side in a subquery, use a
    dictionary / IN for lookups, or switch join_algorithm to 'grace_hash' or
    'partial_merge'.""",
    clickhouse_only=True,
    uses_metadata=True,
)
def check_large_right_join(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        if not block.tables:
            continue
        left = block.tables[0]
        left_meta = None if left.is_subquery else ctx.table(left.name)
        for ref in block.tables[1:]:
            if not ref.is_join or ref.is_subquery or "ARRAY" in ref.join:
                continue
            right_meta = ctx.table(ref.name)
            if not ctx.config.is_large(right_meta):
                continue
            assert right_meta is not None
            if ctx.config.is_large(left_meta):
                # Both sides are big: only complain when we know the right one is bigger.
                left_rows = left_meta.rows if left_meta is not None else None
                if left_rows is None or right_meta.rows is None or right_meta.rows <= left_rows:
                    continue
            size = f"~{right_meta.rows:,} rows" if right_meta.rows else "large"
            yield Hit(ref.token, f"`{ref.name}` ({size}) is on the right side of {ref.join}; the hash table is built from it")


@rule(
    "SQD014",
    "null-equality",
    Severity.ERROR,
    "Comparison with NULL using = or <>",
    """x = NULL and x <> NULL evaluate to NULL, never true, so the predicate
    filters out every row. It is almost always a bug.""",
    """Use x IS NULL / x IS NOT NULL (isNull(x) / isNotNull(x) in ClickHouse),
    or isNotDistinctFrom(a, b) for NULL-safe equality.""",
    category="correctness",
)
def check_null_equality(ctx: RuleContext) -> Iterator[Hit]:
    toks = ctx.statement.tokens
    assignment = _assignment_indices(ctx.statement)
    for i, tok in enumerate(toks):
        if not tok.is_op("=", "==", "!=", "<>") or i in assignment:
            continue
        left = toks[i - 1] if i else None
        right = toks[i + 1] if i + 1 < len(toks) else None
        if (left is not None and left.is_keyword("NULL")) or (right is not None and right.is_keyword("NULL")):
            suggestion = "IS NULL" if tok.value in ("=", "==") else "IS NOT NULL"
            yield Hit(tok, f"`{tok.value} NULL` is never true; use {suggestion}")


def _has_final_setting(stmt: Statement) -> bool:
    """True if the statement ends with ``SETTINGS final = 1``."""
    toks = stmt.tokens
    in_settings = False
    for i, tok in enumerate(toks):
        if tok.is_keyword("SETTINGS") and tok.depth == 0:
            in_settings = True
        elif in_settings and tok.upper == "FINAL" and i + 2 < len(toks) and toks[i + 1].is_op("="):
            return toks[i + 2].value not in ("0", "false")
    return False


def _assignment_indices(stmt: Statement) -> set:
    """Token indices that are SET assignments in UPDATE statements, not comparisons."""
    toks = stmt.tokens
    start = None
    if stmt.kind == "UPDATE":
        start = next((i for i, t in enumerate(toks) if t.is_keyword("SET") and t.depth == 0), None)
    elif stmt.kind == "ALTER":
        start = next((i for i, t in enumerate(toks) if t.is_keyword("UPDATE") and t.depth == 0), None)
    if start is None:
        return set()
    end = next((i for i in range(start, len(toks)) if toks[i].is_keyword("WHERE") and toks[i].depth == 0), len(toks))
    return set(range(start, end))


@rule(
    "SQD015",
    "nullable-equality",
    Severity.INFO,
    "Equality on a Nullable column",
    """Nullable columns in ClickHouse are stored with a separate null map that
    has to be read and checked for every comparison, and NULL keys never match
    in = or JOIN ON. Filtering or joining on them is both slower and a common
    source of "missing" rows.""",
    """Make the column non-Nullable with a DEFAULT sentinel, wrap it with
    ifNull()/assumeNotNull() when you know the semantics, or use
    isNotDistinctFrom() when NULL should match NULL.""",
    clickhouse_only=True,
    uses_metadata=True,
)
def check_nullable_equality(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        nullable = {}
        for ref, meta in _block_tables(ctx, block):
            for col in meta.nullable:
                nullable.setdefault(col, ref.name)
        if not nullable:
            continue
        regions = [block.filter_tokens()] + [ref.condition for ref in block.tables if ref.condition]
        for toks in regions:
            for i, tok in enumerate(toks):
                if not is_column_token(toks, i) or tok.name not in nullable:
                    continue
                before = toks[i - 1] if i else None
                after = toks[i + 1] if i + 1 < len(toks) else None
                other = None
                if after is not None and after.is_op("=", "=="):
                    other = toks[i + 2] if i + 2 < len(toks) else None
                elif before is not None and before.is_op("=", "=="):
                    other = toks[i - 2] if i >= 2 else None
                else:
                    continue
                if other is not None and other.is_keyword("NULL"):
                    continue  # SQD014 covers this
                yield Hit(tok, f"`{tok.name}` is Nullable in {nullable[tok.name]}; = never matches NULL and reads the null map")


@rule(
    "SQD016",
    "missing-partition-filter",
    Severity.WARNING,
    "No filter on the table's partition key",
    """The table declares a partition key in the config, but this query block
    never filters on it, so ClickHouse cannot prune partitions and has to open
    every one of them. On time-partitioned event tables this is usually the
    biggest single win available.""",
    """Add a predicate on the partition column (e.g. event_date >= today() - 7)
    in WHERE or PREWHERE, using the raw column so the pruning can kick in.""",
    uses_metadata=True,
)
def check_partition_filter(ctx: RuleContext) -> Iterator[Hit]:
    for block in ctx.statement.blocks:
        filtered_cols = {name for name, _ in column_refs(block.filter_tokens())}
        for ref, meta in _block_tables(ctx, block):
            part_cols = meta.partition_columns
            if part_cols and not (part_cols & filtered_cols):
                cols = ", ".join(sorted(part_cols))
                yield Hit(ref.token, f"`{ref.name}` is partitioned by {meta.partition_by} but there is no filter on {cols}")


@rule(
    "SQD017",
    "missing-global-in",
    Severity.WARNING,
    "Distributed subquery without GLOBAL",
    """When both the outer query and an IN/JOIN subquery read Distributed
    tables, a plain IN or JOIN makes every shard re-run the subquery against
    the whole cluster: N shards x N shards of work. GLOBAL runs it once on the
    initiator and ships the result to the shards.""",
    """Use GLOBAL IN / GLOBAL JOIN, or co-locate the data by sharding key and
    keep the local form intentionally (set distributed_product_mode).""",
    clickhouse_only=True,
    uses_metadata=True,
)
def check_global_in(ctx: RuleContext) -> Iterator[Hit]:
    stmt = ctx.statement

    def distributed(name: Optional[str]) -> bool:
        meta = ctx.table(name)
        return meta is not None and meta.distributed

    for block in stmt.blocks:
        outer_distributed = any(distributed(ref.name) for ref in block.tables[:1])
        if not outer_distributed:
            continue
        for ref in block.tables[1:]:
            if not ref.is_join or "GLOBAL" in ref.join:
                continue
            inner = [ref.name] if ref.name else [n for n, _ in referenced_tables(ref.subquery)]
            if any(distributed(n) for n in inner):
                yield Hit(ref.token, f"{ref.join} on a Distributed table runs on every shard; use GLOBAL {ref.join}")
        for sub in stmt.blocks:
            if sub.context not in ("IN", "NOT IN") or sub.global_in or sub.context_token is None:
                continue
            if sub.keyword.offset < block.keyword.offset or sub.depth != block.depth + 1:
                continue
            if sub.keyword.offset > block.tokens[-1].offset:
                continue
            if any(distributed(ref.name) for ref in sub.tables):
                message = f"{sub.context} (subquery) over Distributed tables runs on every shard; use GLOBAL {sub.context}"
                yield Hit(sub.context_token, message)


@rule(
    "SQD018",
    "deep-offset-pagination",
    Severity.INFO,
    "Large OFFSET for pagination",
    """OFFSET n still reads, sorts and throws away the first n rows, so every
    page is slower than the previous one.""",
    """Use keyset pagination: remember the last sort key you returned and query
    WHERE (ts, id) < (last_ts, last_id) ORDER BY ts DESC, id DESC LIMIT 100.""",
    options={"threshold": "smallest OFFSET that is reported (default 10000)"},
)
def check_deep_offset(ctx: RuleContext) -> Iterator[Hit]:
    threshold = int(ctx.option("threshold", 10_000))
    for block in ctx.statement.blocks:
        candidates: List[Token] = []
        offset = block.clause("OFFSET")
        if offset and offset[0].type is TokenType.NUMBER:
            candidates.append(offset[0])
        limit = block.clause("LIMIT")
        if len(limit) >= 3 and limit[1].is_punct(",") and limit[0].type is TokenType.NUMBER:
            candidates.append(limit[0])  # LIMIT offset, count
        for tok in candidates:
            try:
                value = int(float(tok.value.replace("_", "")))
            except ValueError:
                continue
            if value >= threshold:
                yield Hit(tok, f"OFFSET {value:,} reads and discards {value:,} rows per page")


def all_rules() -> List[Rule]:
    """All registered rules, sorted by id."""
    return [RULES[k] for k in sorted(RULES)]


def get_rule(rule_id: str) -> Optional[Rule]:
    """Look up a rule by id (case-insensitive) or by name."""
    key = rule_id.strip()
    if key.upper() in RULES:
        return RULES[key.upper()]
    return next((r for r in RULES.values() if r.name == key.lower()), None)
