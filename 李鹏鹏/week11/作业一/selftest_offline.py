"""离线自测：不调用真实大模型，验证工作流编排与 SQL 安全校验。

运行：
    python selftest_offline.py

通过注入 ScriptedLLM，模拟：
    表选择 -> 生成一条错误 SQL -> 校验失败 -> 改写 -> 执行 -> 汇总
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一改用 UTF-8，避免中文/符号输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from sql_agent.config import Settings
from sql_agent.db_parser import DBParser
from sql_agent.workflow import SQLAgentWorkflow, extract_json, extract_sql

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "chinook.db"


class ScriptedLLM:
    """按 prompt 内容返回预设答案的假模型。"""

    def __init__(self, tables: tuple[str, ...] = ("genres", "tracks")) -> None:
        self.calls = 0
        self.repaired = False
        self.tables = list(tables)

    def complete(self, messages) -> str:
        self.calls += 1
        prompt = messages[-1]["content"]

        if "只输出如下 JSON" in prompt:
            payload = {"tables": self.tables, "reason": "按流派统计曲目数"}
            return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

        if "没有通过校验或执行失败" in prompt:
            self.repaired = True
            return """```sql
SELECT g.Name AS genre, COUNT(t.TrackId) AS track_count
FROM genres AS g
JOIN tracks AS t ON t.GenreId = g.GenreId
GROUP BY g.GenreId, g.Name
ORDER BY track_count DESC
LIMIT 5
```"""

        if "生成一条可直接执行的只读 SQL" in prompt:
            # 故意使用不存在的字段，触发“校验 -> 改写”
            return "```sql\nSELECT GenreName FROM genres\n```"

        if "根据 SQL 的执行结果" in prompt:
            return "Rock 是曲目数量最多的流派，其后依次是 Latin、Metal 等。"

        raise AssertionError(f"未预期的 prompt：{prompt[:80]}")

    def usage(self) -> dict:
        return {
            "model": "scripted",
            "calls": self.calls,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  ✓ {message}")


def test_extractors() -> None:
    print("\n[1] 解析工具")
    assert_true(
        extract_sql("解释如下：\n```sql\nSELECT 1;\n```\n以上。") == "SELECT 1",
        "从 Markdown 代码块中提取 SQL",
    )
    assert_true(
        extract_sql("SELECT * FROM albums;") == "SELECT * FROM albums",
        "直接提取裸 SQL 并去掉分号",
    )
    payload = extract_json('```json\n{"tables": ["albums"]}\n```')
    assert_true(payload == {"tables": ["albums"]}, "解析带围栏的 JSON")


def test_validation() -> None:
    print("\n[2] SQL 校验（只读 / 单语句 / 字段存在）")
    with DBParser(DB_PATH) as parser:
        ok, _ = parser.validate_sql("SELECT COUNT(*) FROM albums")
        assert_true(ok, "合法查询通过")

        ok, _ = parser.validate_sql("DELETE FROM albums")
        assert_true(not ok, "拒绝 DELETE")

        ok, _ = parser.validate_sql("SELECT 1; SELECT 2")
        assert_true(not ok, "拒绝多条语句")

        ok, _ = parser.validate_sql("SELECT ';' AS semi_colon FROM genres")
        assert_true(ok, "字符串中的分号不误伤")

        ok, _ = parser.validate_sql("SELECT 1; -- 结尾注释")
        assert_true(ok, "允许结尾分号 + 注释")

        ok, _ = parser.validate_sql(
            "WITH x AS (SELECT 1) DELETE FROM genres"
        )
        assert_true(not ok, "拒绝 CTE 包装的写操作")

        ok, _ = parser.validate_sql("SELECT NotExistColumn FROM albums")
        assert_true(not ok, "拒绝不存在的字段")

        columns, rows, truncated = parser.execute_sql(
            "SELECT Name FROM genres ORDER BY GenreId", max_rows=5
        )
        assert_true(columns == ["Name"] and len(rows) == 5 and truncated,
                    "执行查询并按 max_rows 截断")


def test_workflow() -> None:
    print("\n[3] 完整工作流（假模型）")
    llm = ScriptedLLM()
    settings = Settings(api_key="scripted", max_rows=100, max_repair=2)

    with SQLAgentWorkflow(DB_PATH, settings=settings, llm=llm) as workflow:
        result = workflow.run("哪个流派的曲目数量最多？列出前 5 个。")

    steps = [trace.name for trace in result.traces]
    print(f"    执行步骤：{' -> '.join(steps)}")
    print(f"    最终 SQL：{result.sql}")

    assert_true(result.ok, "工作流成功完成")
    assert_true(result.tables == ["genres", "tracks"], "table 选择命中 genres/tracks")
    assert_true(result.attempts == 2, "首次校验失败后改写成功")
    assert_true(llm.repaired, "确实执行了 SQL 改写")
    assert_true(len(result.rows) == 5, "执行返回 5 行")
    assert_true(result.rows[0][1] >= result.rows[1][1], "结果按曲目数降序")
    assert_true(
        steps == [
            "数据库解析", "table 选择", "SQL 生成",
            "SQL 校验", "SQL 改写", "SQL 校验", "SQL 执行", "结果汇总",
        ],
        "步骤顺序符合预期",
    )


def main() -> int:
    try:
        test_extractors()
        test_validation()
        test_workflow()
    except AssertionError as exc:
        print(f"\n自测失败：{exc}", file=sys.stderr)
        return 1

    print("\n全部离线自测通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
