"""Lightweight structural parsing on top of the token stream.

This is not a full SQL grammar, and it doesn't try to be. It recovers just
enough structure for the rules to reason about queries:

* statements, split on top-level semicolons;
* every ``SELECT`` block (including subqueries and CTE bodies) with its
  clauses (``FROM``, ``WHERE``, ``PREWHERE``, ``GROUP BY``, ``ORDER BY``, ...);
* the tables a block reads from, how they are joined, aliases and ``FINAL``;
* the syntactic context of a subquery (``IN (...)``, ``EXISTS (...)``, ...).

Because clause detection only considers tokens at the block's own paren
depth, ``ORDER BY`` inside a window function or ``FROM`` inside
``EXTRACT(YEAR FROM ts)`` is never mistaken for a clause of the outer query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from .tokenizer import Token, TokenType, tokenize

#: Clause keywords recognised at the block's own depth, in the form they are
#: stored under. Two-word clauses are keyed by their first word.
_CLAUSE_STARTS = {
    "SELECT": "SELECT",
    "FROM": "FROM",
    "PREWHERE": "PREWHERE",
    "WHERE": "WHERE",
    "GROUP": "GROUP BY",
    "HAVING": "HAVING",
    "ORDER": "ORDER BY",
    "LIMIT": "LIMIT",
    "OFFSET": "OFFSET",
    "SETTINGS": "SETTINGS",
    "FORMAT": "FORMAT",
    "WINDOW": "WINDOW",
    "QUALIFY": "QUALIFY",
}
_SET_OPERATORS = ("UNION", "INTERSECT", "EXCEPT")
_JOIN_MODIFIERS = ("GLOBAL", "ANY", "ALL", "ASOF", "SEMI", "ANTI", "INNER", "LEFT", "RIGHT", "FULL", "OUTER", "CROSS", "ARRAY")

COMPARISON_OPERATORS = ("=", "==", "!=", "<>", "<", ">", "<=", ">=")


@dataclass
class TableRef:
    """A table (or derived table) referenced in a ``FROM`` clause.

    Attributes:
        name: Lower-cased, dotted table name (``analytics.events``); ``None``
            for a derived table / subquery.
        token: The token where the reference starts (used for locations).
        alias: Alias if one was given.
        join: How the table enters the query: ``"FROM"`` for the first table,
            ``"COMMA"`` for an implicit join, or the join keywords such as
            ``"LEFT JOIN"`` or ``"GLOBAL ANY INNER JOIN"``.
        final: Whether ``FINAL`` follows the table.
        condition: Tokens of the ``ON``/``USING`` condition, if any.
        has_condition: True if an ``ON`` or ``USING`` clause was found.
        is_function: True for table functions like ``numbers(10)``.
        subquery: Tokens inside the parentheses of a derived table.
    """

    name: Optional[str]
    token: Token
    alias: Optional[str] = None
    join: str = "FROM"
    final: bool = False
    condition: List[Token] = field(default_factory=list)
    has_condition: bool = False
    is_function: bool = False
    subquery: List[Token] = field(default_factory=list)

    @property
    def is_join(self) -> bool:
        """True for tables brought in by an explicit ``JOIN`` keyword."""
        return self.join.endswith("JOIN")

    @property
    def is_subquery(self) -> bool:
        """True for derived tables."""
        return self.name is None


@dataclass
class SelectBlock:
    """One ``SELECT ...`` query block.

    Attributes:
        tokens: Significant tokens from the ``SELECT`` keyword to the end of
            the block, nested subqueries included.
        depth: Paren depth of the ``SELECT`` keyword.
        clauses: Clause name -> tokens following the clause keyword(s).
        clause_keywords: Clause name -> the keyword token that opened it.
        tables: Tables referenced in this block's ``FROM`` clause.
        distinct: True for ``SELECT DISTINCT``.
        context: How the block is used: ``None`` for a top-level query,
            ``"IN"``, ``"NOT IN"``, ``"EXISTS"``, ``"FROM"`` (derived table)
            or ``"CTE"``.
        context_token: The token that establishes the context (e.g. ``IN``).
        global_in: True when the ``IN`` is a ``GLOBAL IN``.
    """

    tokens: List[Token]
    depth: int
    clauses: Dict[str, List[Token]] = field(default_factory=dict)
    clause_keywords: Dict[str, Token] = field(default_factory=dict)
    tables: List[TableRef] = field(default_factory=list)
    distinct: bool = False
    context: Optional[str] = None
    context_token: Optional[Token] = None
    global_in: bool = False

    @property
    def keyword(self) -> Token:
        """The ``SELECT`` keyword token."""
        return self.tokens[0]

    def has(self, clause: str) -> bool:
        """Return True if the block contains ``clause``."""
        return clause in self.clauses

    def clause(self, clause: str) -> List[Token]:
        """Tokens of ``clause`` (empty if absent)."""
        return self.clauses.get(clause, [])

    def filter_tokens(self) -> List[Token]:
        """Tokens of the ``WHERE`` and ``PREWHERE`` clauses combined."""
        return self.clause("PREWHERE") + self.clause("WHERE")

    @property
    def is_filtered(self) -> bool:
        """True when the block has a ``WHERE`` or ``PREWHERE`` clause."""
        return self.has("WHERE") or self.has("PREWHERE")


@dataclass
class Statement:
    """A single SQL statement.

    Attributes:
        index: 0-based position of the statement in its file.
        tokens: Significant tokens (no whitespace / comments).
        comments: Comment tokens that belong to the statement, including the
            ones directly above it.
        blocks: All ``SELECT`` blocks, outermost first.
    """

    index: int
    tokens: List[Token]
    comments: List[Token] = field(default_factory=list)
    blocks: List[SelectBlock] = field(default_factory=list)

    @property
    def kind(self) -> str:
        """The statement verb: ``SELECT``, ``INSERT``, ``DELETE``, ``ALTER``..."""
        if not self.tokens:
            return ""
        first = self.tokens[0]
        if first.is_keyword("WITH"):
            for tok in self.tokens:
                if tok.depth == 0 and tok.type is TokenType.KEYWORD and tok.upper in ("SELECT", "INSERT", "DELETE", "UPDATE"):
                    return tok.upper
            return "SELECT"
        if first.is_punct("("):
            return "SELECT"
        return first.upper

    @property
    def line(self) -> int:
        """Line number where the statement starts."""
        return self.tokens[0].line if self.tokens else 1


# -- statement splitting ------------------------------------------------------


def split_statements(sql: str) -> List[Statement]:
    """Tokenize ``sql`` and split it into statements with parsed blocks."""
    statements: List[Statement] = []
    sig: List[Token] = []
    comments: List[Token] = []
    for tok in tokenize(sql):
        if tok.type is TokenType.WHITESPACE:
            continue
        if tok.type is TokenType.COMMENT:
            comments.append(tok)
            continue
        if tok.is_punct(";") and tok.depth == 0:
            if sig:
                statements.append(_build_statement(len(statements), sig, comments))
            sig, comments = [], []
            continue
        sig.append(tok)
    if sig:
        statements.append(_build_statement(len(statements), sig, comments))
    elif comments and statements:
        statements[-1].comments.extend(comments)  # trailing comments after the last ';'
    return statements


def _build_statement(index: int, tokens: List[Token], comments: List[Token]) -> Statement:
    stmt = Statement(index=index, tokens=tokens, comments=comments)
    stmt.blocks = [_parse_block(tokens, i) for i, tok in enumerate(tokens) if tok.is_keyword("SELECT")]
    return stmt


# -- select blocks --------------------------------------------------------------


def _parse_block(tokens: List[Token], start: int) -> SelectBlock:
    depth = tokens[start].depth
    end = start + 1
    while end < len(tokens):
        tok = tokens[end]
        if tok.depth < depth:
            break
        if tok.depth == depth and tok.type is TokenType.KEYWORD and tok.upper in _SET_OPERATORS:
            break
        end += 1

    block = SelectBlock(tokens=tokens[start:end], depth=depth)
    _fill_clauses(block)
    _fill_context(block, tokens, start)
    select = block.clause("SELECT")
    block.distinct = bool(select) and select[0].is_keyword("DISTINCT")
    block.tables = parse_from_clause(block.clause("FROM"), depth)
    return block


def _fill_clauses(block: SelectBlock) -> None:
    current: Optional[str] = None
    toks = block.tokens
    i = 0
    while i < len(toks):
        tok = toks[i]
        name = _CLAUSE_STARTS.get(tok.upper) if tok.type is TokenType.KEYWORD and tok.depth == block.depth else None
        if name is not None and (name != "SELECT" or i == 0) and name not in block.clauses:
            current = name
            block.clauses[current] = []
            block.clause_keywords[current] = tok
            # Two-word clauses: swallow the BY.
            if name in ("GROUP BY", "ORDER BY") and i + 1 < len(toks) and toks[i + 1].is_keyword("BY"):
                i += 1
            i += 1
            continue
        if current is not None:
            block.clauses[current].append(tok)
        i += 1


def _fill_context(block: SelectBlock, tokens: List[Token], start: int) -> None:
    if start == 0 or not tokens[start - 1].is_punct("("):
        return
    if start < 2:
        block.context = "FROM"
        return
    before = tokens[start - 2]
    if before.is_keyword("IN"):
        negated = start >= 3 and tokens[start - 3].is_keyword("NOT")
        block.context = "NOT IN" if negated else "IN"
        block.context_token = tokens[start - 3] if negated else before
        g = start - 4 if negated else start - 3
        block.global_in = g >= 0 and tokens[g].is_keyword("GLOBAL")
    elif before.is_keyword("EXISTS"):
        block.context = "EXISTS"
        block.context_token = before
    elif before.is_keyword("AS"):
        block.context = "CTE"
    else:
        block.context = "FROM"
        block.context_token = before


# -- FROM clause ----------------------------------------------------------------


def parse_from_clause(tokens: List[Token], depth: int) -> List[TableRef]:
    """Split a ``FROM`` clause into :class:`TableRef` items.

    Only tokens at ``depth`` are treated as structure; anything deeper is the
    body of a subquery or table-function call.
    """
    items: List[Tuple[str, List[Token]]] = []
    current: List[Token] = []
    join = "FROM"
    for tok in tokens:
        if tok.depth == depth and tok.is_punct(","):
            items.append((join, current))
            current, join = [], "COMMA"
        elif tok.depth == depth and tok.is_keyword("JOIN"):
            modifiers: List[str] = []
            while current and current[-1].depth == depth and current[-1].is_keyword(*_JOIN_MODIFIERS):
                modifiers.insert(0, current.pop().upper)
            items.append((join, current))
            current, join = [], " ".join(modifiers + ["JOIN"])
        else:
            current.append(tok)
    items.append((join, current))
    return [ref for ref in (_parse_table_item(kind, toks, depth) for kind, toks in items) if ref is not None]


def _parse_table_item(join: str, toks: List[Token], depth: int) -> Optional[TableRef]:
    if not toks:
        return None
    first = toks[0]
    i = 0
    ref: TableRef
    if first.is_punct("("):
        close = _matching_paren(toks, 0)
        ref = TableRef(name=None, token=first, join=join, subquery=toks[1:close])
        i = close + 1
    elif first.type is TokenType.IDENTIFIER or first.type is TokenType.KEYWORD:
        name, i = read_dotted_name(toks, 0)
        ref = TableRef(name=name, token=first, join=join)
        if i < len(toks) and toks[i].is_punct("("):
            ref.is_function = True
            i = _matching_paren(toks, i) + 1
    else:
        return None

    while i < len(toks):
        tok = toks[i]
        if tok.depth != depth:
            i += 1
            continue
        if tok.is_keyword("FINAL"):
            ref.final = True
        elif tok.is_keyword("AS") and i + 1 < len(toks):
            ref.alias = toks[i + 1].name
            i += 1
        elif tok.is_keyword("ON", "USING"):
            ref.has_condition = True
            ref.condition = toks[i + 1 :]
            break
        elif tok.type is TokenType.IDENTIFIER and ref.alias is None:
            ref.alias = tok.name
        i += 1
    return ref


def _matching_paren(toks: List[Token], open_index: int) -> int:
    depth = toks[open_index].depth
    for j in range(open_index + 1, len(toks)):
        if toks[j].is_punct(")") and toks[j].depth == depth:
            return j
    return len(toks) - 1


# -- helpers shared by rules ------------------------------------------------------


def read_dotted_name(toks: List[Token], i: int) -> Tuple[str, int]:
    """Read ``a.b.c`` starting at ``i``; return the lower-cased name and next index."""
    parts = [toks[i].name]
    i += 1
    while i + 1 < len(toks) and toks[i].is_punct(".") and toks[i + 1].type in (TokenType.IDENTIFIER, TokenType.KEYWORD):
        parts.append(toks[i + 1].name)
        i += 2
    return ".".join(parts), i


def is_column_token(toks: List[Token], i: int) -> bool:
    """True if ``toks[i]`` is an identifier used as a column (not a function call)."""
    tok = toks[i]
    if tok.type is not TokenType.IDENTIFIER:
        return False
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    return nxt is None or not nxt.is_punct("(", ".")


def column_refs(toks: List[Token]) -> Iterator[Tuple[str, Token]]:
    """Yield ``(column_name, token)`` for every column reference in ``toks``.

    Qualified references (``e.user_id``) yield the last segment only, which is
    what metadata lookups need.
    """
    for i, tok in enumerate(toks):
        if is_column_token(toks, i):
            yield tok.name, tok


def call_end(toks: List[Token], name_index: int) -> int:
    """Index of the closing paren of the function call whose name is at ``name_index``."""
    return _matching_paren(toks, name_index + 1)


def referenced_tables(toks: List[Token]) -> List[Tuple[str, Token]]:
    """Every dotted name directly after a ``FROM`` or ``JOIN`` keyword, at any depth."""
    found = []
    for i, tok in enumerate(toks[:-1]):
        if tok.is_keyword("FROM", "JOIN") and toks[i + 1].type is TokenType.IDENTIFIER:
            name, end = read_dotted_name(toks, i + 1)
            if end < len(toks) and toks[end].is_punct("("):
                continue  # table function
            found.append((name, toks[i + 1]))
    return found
