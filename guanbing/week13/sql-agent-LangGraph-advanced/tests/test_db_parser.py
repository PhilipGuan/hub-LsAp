"""`DBParser` 移植回归：确认「真的在连库读文件」（A2 验收线，tracks=3503）。"""

from __future__ import annotations

import sqlite3

from sqlagent.db import DBParser


def test_reflects_tables(parser):
    assert "tracks" in parser.table_names
    assert {"albums", "artists", "invoices", "customers", "employees"} <= set(parser.table_names)


def test_counts_match_native_sqlite(parser, tmp_path):
    """双引擎一致性：SQLAlchemy 链路 == 原生 sqlite3 直连（证明不是模型编的数）。"""
    cols, rows = parser.execute_sql("SELECT COUNT(*) FROM tracks;")
    assert rows == [(3503,)]
    assert cols == ["COUNT(*)"]

    con = sqlite3.connect(parser.db_url.split("sqlite:///")[-1])
    try:
        native = con.execute("SELECT COUNT(*) FROM tracks;").fetchone()
    finally:
        con.close()
    assert native == rows[0]


def test_check_sql_reports_failure(parser):
    ok, _ = parser.check_sql("SELECT nope_column FROM tracks;")
    assert not ok
    ok, msg = parser.check_sql("SELECT COUNT(*) FROM tracks;")
    assert ok and msg == "ok"


def test_table_fields_and_relations(parser):
    fields = parser.get_table_fields("tracks")
    assert "TrackId" in fields.index
    rels = parser.get_data_relations()
    assert len(rels) > 0
