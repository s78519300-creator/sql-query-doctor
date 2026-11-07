"""A small, forgiving SQL tokenizer.

The rules in this project never look at raw SQL text. They work on tokens, so
a ``SELECT *`` inside a string literal or a ``LIKE '%x'`` inside a comment can't
trigger a finding. The tokenizer understands the parts of SQL that matter for
that: string literals (with ``''`` and backslash escapes), quoted identifiers
(``"col"`` and `` `col` ``), line and block comments, numbers, ClickHouse query
parameters (``{name:Type}``) and multi-character operators.

It never raises on malformed input. An unterminated string or comment simply
runs to the end of the input, which is what you want from a linter that is
pointed at half-written queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, List


class TokenType(str, Enum):
    """Coarse token categories. ``str`` mixin keeps them JSON friendly."""

    KEYWORD = "keyword"
    IDENTIFIER = "identifier"
    STRING = "string"
    NUMBER = "number"
    OPERATOR = "operator"
    PUNCTUATION = "punctuation"
    PARAMETER = "parameter"
    COMMENT = "comment"
    WHITESPACE = "whitespace"
    UNKNOWN = "unknown"


#: Words treated as structural keywords. Deliberately not exhaustive: anything
#: that is commonly also used as a column name (``date``, ``user``, ``value``)
#: stays an identifier so column-level rules keep working.
KEYWORDS = frozenset(
    """
    ALL ALTER AND ANTI ANY ARRAY AS ASC ASOF BETWEEN BY CASE CLUSTER CREATE CROSS
    DELETE DESC DISTINCT DROP ELSE END EXCEPT EXISTS FALSE FINAL FIRST FORMAT FROM
    FULL GLOBAL GROUP HAVING ILIKE IN INNER INSERT INTERSECT INTERVAL INTO IS JOIN
    LAST LEFT LIKE LIMIT NOT NULL NULLS OFFSET ON OR ORDER OUTER OVER PREWHERE
    QUALIFY RIGHT SAMPLE SELECT SEMI SET SETTINGS TABLE THEN TRUE TRUNCATE UNION
    UPDATE USING VALUES VIEW WHEN WHERE WINDOW WITH
    """.split()
)

#: Keywords that double as function names (ClickHouse ``any(x)``, ``if(c, a, b)``,
#: MySQL ``left(s, 3)``). When followed by ``(`` they are tokenized as identifiers.
FUNCTION_KEYWORDS = frozenset({"ANY", "ALL", "LEFT", "RIGHT", "FIRST", "LAST", "FORMAT", "ARRAY"})

_MULTI_CHAR_OPERATORS = ("<=>", "<>", "<=", ">=", "!=", "==", "||", "::", "->", "=>")
_SINGLE_CHAR_OPERATORS = set("=<>+-*/%^&|~!")
_PUNCTUATION = set("(),;.[]")


@dataclass(frozen=True)
class Token:
    """A single lexical token.

    Attributes:
        type: The token category.
        value: The exact source text of the token.
        line: 1-based line number of the first character.
        column: 1-based column number of the first character.
        offset: 0-based character offset into the source.
        depth: Parenthesis nesting depth. An opening paren carries the depth
            *outside* it, its contents are one level deeper, and the matching
            closing paren is back at the outer depth.
    """

    type: TokenType
    value: str
    line: int
    column: int
    offset: int
    depth: int = 0

    @property
    def upper(self) -> str:
        """The value upper-cased; handy for keyword comparisons."""
        return self.value.upper()

    @property
    def is_trivia(self) -> bool:
        """True for whitespace and comments, which rules usually skip."""
        return self.type in (TokenType.WHITESPACE, TokenType.COMMENT)

    def is_keyword(self, *words: str) -> bool:
        """Return True if this is a keyword matching any of ``words``."""
        return self.type is TokenType.KEYWORD and self.upper in words

    def is_punct(self, *chars: str) -> bool:
        """Return True if this is punctuation matching any of ``chars``."""
        return self.type is TokenType.PUNCTUATION and self.value in chars

    def is_op(self, *ops: str) -> bool:
        """Return True if this is an operator matching any of ``ops``."""
        return self.type is TokenType.OPERATOR and self.value in ops

    @property
    def name(self) -> str:
        """Identifier text without quoting, lower-cased for comparisons."""
        value = self.value
        if self.type is TokenType.IDENTIFIER and len(value) >= 2 and value[0] in '"`' and value[-1] == value[0]:
            value = value[1:-1]
        return value.lower()

    @property
    def string_value(self) -> str:
        """Contents of a string literal without the surrounding quotes."""
        if self.type is not TokenType.STRING:
            return self.value
        body = self.value[1:]
        if body.endswith("'"):
            body = body[:-1]
        return body.replace("''", "'")


class Tokenizer:
    """Turns SQL text into a list of :class:`Token` objects."""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.pos = 0
        self.line = 1
        self.column = 1
        self.depth = 0

    def tokenize(self) -> List[Token]:
        """Tokenize the whole input, including whitespace and comments."""
        return list(self._iter_tokens())

    # -- internals ---------------------------------------------------------

    def _iter_tokens(self) -> Iterator[Token]:
        sql = self.sql
        n = len(sql)
        while self.pos < n:
            ch = sql[self.pos]
            nxt = sql[self.pos + 1] if self.pos + 1 < n else ""

            if ch.isspace():
                yield self._take(TokenType.WHITESPACE, self._scan_while(str.isspace))
            elif ch == "-" and nxt == "-":
                yield self._take(TokenType.COMMENT, self._scan_line_comment())
            elif ch == "/" and nxt == "*":
                yield self._take(TokenType.COMMENT, self._scan_block_comment())
            elif ch == "'":
                yield self._take(TokenType.STRING, self._scan_quoted("'"))
            elif ch in '"`':
                yield self._take(TokenType.IDENTIFIER, self._scan_quoted(ch))
            elif ch.isdigit() or (ch == "." and nxt.isdigit()):
                yield self._take(TokenType.NUMBER, self._scan_number())
            elif ch.isalpha() or ch == "_":
                yield self._word()
            elif ch == "{":
                yield self._take(TokenType.PARAMETER, self._scan_until("}"))
            elif ch == "?" or (ch in ":@$" and (nxt.isalnum() or nxt == "_")):
                yield self._take(TokenType.PARAMETER, self._scan_parameter())
            elif ch == "(":
                tok = self._take(TokenType.PUNCTUATION, ch)
                self.depth += 1
                yield tok
            elif ch == ")":
                self.depth = max(0, self.depth - 1)
                yield self._take(TokenType.PUNCTUATION, ch)
            elif ch in _PUNCTUATION:
                yield self._take(TokenType.PUNCTUATION, ch)
            else:
                op = next((o for o in _MULTI_CHAR_OPERATORS if sql.startswith(o, self.pos)), None)
                if op is not None:
                    yield self._take(TokenType.OPERATOR, op)
                elif ch in _SINGLE_CHAR_OPERATORS:
                    yield self._take(TokenType.OPERATOR, ch)
                else:
                    yield self._take(TokenType.UNKNOWN, ch)

    def _take(self, kind: TokenType, text: str) -> Token:
        token = Token(kind, text, self.line, self.column, self.pos, self.depth)
        self.pos += len(text)
        newlines = text.count("\n")
        if newlines:
            self.line += newlines
            self.column = len(text) - text.rfind("\n")
        else:
            self.column += len(text)
        return token

    def _scan_while(self, predicate) -> str:  # type: ignore[no-untyped-def]
        end = self.pos
        while end < len(self.sql) and predicate(self.sql[end]):
            end += 1
        return self.sql[self.pos : end]

    def _scan_line_comment(self) -> str:
        end = self.sql.find("\n", self.pos)
        return self.sql[self.pos :] if end == -1 else self.sql[self.pos : end]

    def _scan_block_comment(self) -> str:
        end = self.sql.find("*/", self.pos + 2)
        return self.sql[self.pos :] if end == -1 else self.sql[self.pos : end + 2]

    def _scan_until(self, closing: str) -> str:
        end = self.sql.find(closing, self.pos + 1)
        return self.sql[self.pos :] if end == -1 else self.sql[self.pos : end + 1]

    def _scan_quoted(self, quote: str) -> str:
        sql = self.sql
        i = self.pos + 1
        while i < len(sql):
            ch = sql[i]
            if ch == "\\" and quote == "'":
                i += 2
                continue
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:  # doubled quote escape
                    i += 2
                    continue
                return sql[self.pos : i + 1]
            i += 1
        return sql[self.pos :]

    def _scan_number(self) -> str:
        sql = self.sql
        i = self.pos
        if sql.startswith(("0x", "0X"), i):
            i += 2
            while i < len(sql) and sql[i] in "0123456789abcdefABCDEF":
                i += 1
            return sql[self.pos : i]
        while i < len(sql) and (sql[i].isdigit() or sql[i] in "._"):
            i += 1
        if i < len(sql) and sql[i] in "eE":
            j = i + 1
            if j < len(sql) and sql[j] in "+-":
                j += 1
            if j < len(sql) and sql[j].isdigit():
                i = j
                while i < len(sql) and sql[i].isdigit():
                    i += 1
        return sql[self.pos : i]

    def _scan_parameter(self) -> str:
        if self.sql[self.pos] == "?":
            return "?"
        i = self.pos + 1
        while i < len(self.sql) and (self.sql[i].isalnum() or self.sql[i] == "_"):
            i += 1
        return self.sql[self.pos : i]

    def _word(self) -> Token:
        word = self._scan_while(lambda c: c.isalnum() or c in "_$")
        upper = word.upper()
        kind = TokenType.IDENTIFIER
        if upper in KEYWORDS:
            kind = TokenType.KEYWORD
            if upper in FUNCTION_KEYWORDS and self._next_char_after(len(word)) == "(":
                kind = TokenType.IDENTIFIER
        return self._take(kind, word)

    def _next_char_after(self, length: int) -> str:
        i = self.pos + length
        while i < len(self.sql) and self.sql[i].isspace():
            i += 1
        return self.sql[i] if i < len(self.sql) else ""


def tokenize(sql: str) -> List[Token]:
    """Tokenize ``sql``, keeping whitespace and comments."""
    return Tokenizer(sql).tokenize()


def significant(tokens: Iterable[Token]) -> List[Token]:
    """Drop whitespace and comments."""
    return [t for t in tokens if not t.is_trivia]
