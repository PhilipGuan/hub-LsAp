"""方案 B：确定性 7 步 pipeline（对比/优化版）。

流程：
    选表 → 生成 SQL → 校验 → 改写 → 执行 → 汇总
"""
from config import MAX_RETRIES, MAX_ROWS
from executor import execute_sql
from sql_validator import validate


def run_pipeline(question: str, parser, chains: dict,
                 max_retries: int = MAX_RETRIES) -> dict:
    """固定流程，不依赖模型自主决策，准确率更高、可调试性更好。"""

    # ② 选表
    # TODO: 传入 schema_brief
    selection = chains["select"].invoke({
        "schema_brief": parser.get_schema_brief(),
        "question": question,
    })
    tables = [t for t in selection.tables if t in parser.table_names]

    # ③ 生成 SQL
    # TODO: 传入 schema
    sql = chains["generate"].invoke({
        "schema": parser.get_schema_text(tables),
        "question": question,
    }).strip()

    # ④⑤⑥ 校验 / 改写 / 执行
    history = []
    rows = []
    error = None

    for attempt in range(max_retries + 1):
        check = validate(sql, parser, parser.engine, tables)

        if check["ok"]:
            try:
                result = execute_sql(sql, parser.engine, MAX_ROWS)
                rows = result["rows"]
                error = None
                break
            except Exception as e:
                error = str(e)
        else:
            error = f"{check['stage']}: {check['error']}"

        history.append({"sql": sql, "error": error})

        if attempt < max_retries:
            # TODO: 传入 history_text
            sql = chains["rewrite"].invoke({
                "schema": parser.get_schema_text(tables),
                "question": question,
                "history": "\n".join(
                    f"- {h['sql']}  =>  {h['error']}" for h in history
                ),
                "error": error,
            }).strip()

    # ⑦ 汇总
    if error is None:
        # TODO: 传入 result / total / n
        answer = chains["summary"].invoke({
            "question": question,
            "sql": sql,
            "result": str(rows[:20]),
            "total": len(rows),
            "n": min(len(rows), 20),
        }).strip()
    else:
        answer = "抱歉，这个问题我暂时无法回答。"

    return {
        "question": question,
        "tables": tables,
        "sql": sql,
        "rows": rows,
        "answer": answer,
        "retries": len(history),
        "success": error is None,
        "last_error": error,
    }