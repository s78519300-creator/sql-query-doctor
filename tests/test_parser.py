from sql_query_doctor.parser import split_statements


def test_split_ignores_semicolons_in_strings_and_comments():
    stmts = split_statements("SELECT ';' FROM t; -- a; b\nSELECT 2;")
    assert len(stmts) == 2
    assert stmts[1].comments and "a; b" in stmts[1].comments[0].value


def test_clauses_detected_at_block_depth_only():
    (stmt,) = split_statements("SELECT row_number() OVER (ORDER BY ts) AS rn, extract(YEAR FROM ts) FROM t WHERE x = 1")
    (block,) = stmt.blocks
    assert set(block.clauses) == {"SELECT", "FROM", "WHERE"}
    assert [r.name for r in block.tables] == ["t"]


def test_from_clause_joins_aliases_and_final():
    (stmt,) = split_statements("SELECT * FROM db.a AS x FINAL GLOBAL ANY LEFT JOIN b y ON x.id = y.id, c ARRAY JOIN arr")
    tables = stmt.blocks[0].tables
    assert [(t.name, t.alias, t.join) for t in tables] == [
        ("db.a", "x", "FROM"),
        ("b", "y", "GLOBAL ANY LEFT JOIN"),
        ("c", None, "COMMA"),
        ("arr", None, "ARRAY JOIN"),
    ]
    assert tables[0].final and not tables[1].final
    assert tables[1].has_condition


def test_subquery_contexts():
    sql = (
        "WITH cte AS (SELECT 1) SELECT * FROM (SELECT a FROM t) s "
        "WHERE a IN (SELECT a FROM u) AND b NOT IN (SELECT b FROM v) "
        "AND EXISTS (SELECT 1 FROM w) AND c GLOBAL IN (SELECT c FROM z)"
    )
    (stmt,) = split_statements(sql)
    contexts = [b.context for b in stmt.blocks]
    assert contexts == ["CTE", None, "FROM", "IN", "NOT IN", "EXISTS", "IN"]
    assert stmt.blocks[-1].global_in and not stmt.blocks[3].global_in


def test_statement_kind():
    sql = "WITH x AS (SELECT 1) SELECT * FROM x; DELETE FROM t; ALTER TABLE t DELETE WHERE 1"
    kinds = [s.kind for s in split_statements(sql)]
    assert kinds == ["SELECT", "DELETE", "ALTER"]


def test_union_splits_blocks():
    (stmt,) = split_statements("SELECT a FROM t WHERE a > 1 UNION ALL SELECT b FROM u LIMIT 1")
    first, second = stmt.blocks
    assert [t.name for t in first.tables] == ["t"] and first.has("WHERE")
    assert [t.name for t in second.tables] == ["u"] and second.has("LIMIT")
