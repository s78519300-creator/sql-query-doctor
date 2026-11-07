from sql_query_doctor.tokenizer import TokenType, significant, tokenize


def kinds(sql):
    return [(t.type, t.value) for t in significant(tokenize(sql))]


def test_basic_select_tokens():
    toks = kinds("SELECT a, b FROM t WHERE x >= 10")
    assert toks[0] == (TokenType.KEYWORD, "SELECT")
    assert (TokenType.IDENTIFIER, "a") in toks
    assert (TokenType.OPERATOR, ">=") in toks
    assert (TokenType.NUMBER, "10") in toks


def test_strings_with_escapes_are_single_tokens():
    toks = significant(tokenize(r"SELECT 'it''s', 'a\'b', 'SELECT * FROM x'"))
    strings = [t for t in toks if t.type is TokenType.STRING]
    assert [s.string_value for s in strings][0] == "it's"
    assert len(strings) == 3
    assert strings[2].string_value == "SELECT * FROM x"


def test_comments_are_recognised():
    toks = tokenize("SELECT 1 -- trailing * comment\n/* block\n * comment */ FROM t")
    comments = [t for t in toks if t.type is TokenType.COMMENT]
    assert len(comments) == 2
    assert all(t.type is not TokenType.OPERATOR for t in significant(toks) if t.value == "*")


def test_quoted_identifiers_and_names():
    toks = significant(tokenize('SELECT "User Id", `order` FROM t'))
    assert toks[1].type is TokenType.IDENTIFIER and toks[1].name == "user id"
    assert toks[3].type is TokenType.IDENTIFIER and toks[3].name == "order"


def test_paren_depth_tracking():
    toks = significant(tokenize("SELECT count(x) FROM (SELECT 1)"))
    depth = {t.value: t.depth for t in toks}
    assert depth["count"] == 0
    assert depth["x"] == 1
    inner_select = [t for t in toks if t.upper == "SELECT"][1]
    assert inner_select.depth == 1
    assert toks[-1].value == ")" and toks[-1].depth == 0


def test_keyword_used_as_function_becomes_identifier():
    toks = significant(tokenize("SELECT any(x), left(s, 2) FROM t LEFT JOIN u ON 1"))
    assert toks[1].type is TokenType.IDENTIFIER  # any(
    left_join = [t for t in toks if t.upper == "LEFT"]
    assert left_join[0].type is TokenType.IDENTIFIER
    assert left_join[1].type is TokenType.KEYWORD


def test_parameters_and_casts():
    toks = kinds("SELECT x::Int32 FROM t WHERE id = {id:UInt64} AND y = ? AND z = :name")
    assert (TokenType.OPERATOR, "::") in toks
    assert (TokenType.PARAMETER, "{id:UInt64}") in toks
    assert (TokenType.PARAMETER, "?") in toks
    assert (TokenType.PARAMETER, ":name") in toks


def test_unterminated_input_does_not_raise():
    toks = tokenize("SELECT 'oops /* still going")
    assert toks[-1].type is TokenType.STRING


def test_line_and_column_positions():
    toks = significant(tokenize("SELECT\n  a,\n  b"))
    b = toks[-1]
    assert (b.line, b.column) == (3, 3)
