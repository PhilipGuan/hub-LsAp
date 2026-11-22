"""结构守卫 `is_readonly()` 的回归测试（A3 验收线）。"""

from __future__ import annotations

import pytest

from sqlagent.guard import is_readonly

ALLOWED = [
    "SELECT COUNT(*) FROM tracks;",
    "SELECT * FROM albums",
    "WITH t AS (SELECT 1) SELECT * FROM t",
    "select 1",
    "SELECT 'DROP TABLE albums' AS note FROM tracks;",  # 字符串字面量里的关键字不算
    "SELECT 1 -- drop table x",                         # 行注释里的关键字不算
    "SELECT /* delete from x */ 1",                     # 块注释里的关键字不算
]

BLOCKED = [
    ("", "空 SQL"),
    ("   ", "空 SQL"),
    ("DROP TABLE albums;", "只允许 SELECT / WITH"),
    ("DELETE FROM albums;", "只允许 SELECT / WITH"),
    ("UPDATE albums SET Title='x';", "只允许 SELECT / WITH"),
    ("INSERT INTO albums VALUES (1);", "只允许 SELECT / WITH"),
    ("PRAGMA table_info(albums);", "只允许 SELECT / WITH"),
    ("SELECT 1; DROP TABLE albums;", "多条语句"),
    ("ATTACH DATABASE 'x.db' AS x;", "只允许 SELECT / WITH"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_readonly(sql):
    ok, msg = is_readonly(sql)
    assert ok, f"应放行：{sql}（{msg}）"


@pytest.mark.parametrize("sql", [s for s, _ in BLOCKED])
def test_blocks_writes(sql):
    ok, _ = is_readonly(sql)
    assert not ok, f"应拦截：{sql}"


def test_reason_is_specific():
    """拦截原因要是可读的、带关键字信息的，便于定位。"""
    ok, msg = is_readonly("DROP TABLE albums;")
    assert not ok
    assert "SELECT" in msg or "drop" in msg.lower()
