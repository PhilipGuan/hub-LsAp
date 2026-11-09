"""第 8 步：把 7 个步骤串成完整 SQL Agent。"""
import time
import traceback

from openai import OpenAI

from config import MODEL_NAME, MAX_RETRIES, MAX_ROWS
from table_selector import select_tables
from sql_generator import generate_sql
from sql_validator import validate
from sql_rewriter import rewrite_sql
from executor import execute_sql
from summarizer import summarize


def run_sql_agent(question: str, parser, client: OpenAI,
                  model: str = MODEL_NAME,
                  max_retries: int = MAX_RETRIES,
                  verbose: bool = True) -> dict:
    """完整流程：选表 → 生成 → 校验 → 改写 → 执行 → 汇总。"""
    start = time.time()
    tables, sql, rows = [], "", []
    history = []
    last_error = None

    try:
        # ② 选表
        tables = select_tables(question, parser, client, model)
        if verbose:
            print(f"[2/7] 选中表: {tables}")

        # ③ 生成 SQL
        sql = generate_sql(question, parser, tables, client, model)
        if verbose:
            print(f"[3/7] 生成 SQL: {sql}")

        # ④ 校验 → ⑥ 执行 → ⑤ 改写
        for attempt in range(max_retries + 1):
            check = validate(sql, parser, parser.engine, tables)

            if check["ok"]:
                try:
                    exec_result = execute_sql(sql, parser.engine, MAX_ROWS)
                    rows = exec_result["rows"]
                    last_error = None
                    if verbose:
                        print(f"[6/7] 执行成功: {exec_result['row_count']} 行, "
                              f"{exec_result['elapsed_ms']} ms")
                    break
                except Exception as e:
                    last_error = f"执行失败: {e}"
            else:
                last_error = f"{check['stage']}: {check['error']}"

            if verbose:
                print(f"[4/7] 校验失败: {last_error}")

            history.append({"sql": sql, "error": last_error})

            if attempt < max_retries:
                sql = rewrite_sql(
                    question, parser, tables, sql, last_error, history,
                    client, model,
                )
                if verbose:
                    print(f"[5/7] 第 {attempt + 1} 次改写: {sql}")

        # ⑦ 汇总
        if last_error is None:
            answer = summarize(question, sql, rows, client, model)
            if verbose:
                print(f"[7/7] 汇总完成")
        else:
            answer = "抱歉，这个问题我暂时无法回答。"

    except Exception as e:
        traceback.print_exc()
        last_error = str(e)
        answer = "抱歉，处理过程中出现异常。"

    return {
        "question": question,
        "tables": tables,
        "sql": sql,
        "rows": rows,
        "answer": answer,
        "retries": len(history),
        "success": last_error is None,
        "last_error": last_error,
        "elapsed_ms": round((time.time() - start) * 1000, 2),
    }