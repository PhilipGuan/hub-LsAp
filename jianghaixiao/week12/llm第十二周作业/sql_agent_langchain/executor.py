"""第 6 步：安全执行 SQL。"""
import time

from sqlalchemy import text


def execute_sql(sql: str, engine, max_rows: int = 100) -> dict:
    """只读 + 限行执行，返回结果与耗时。"""
    start = time.time()

    with engine.connect() as conn:
        # 只读模式：即使 SQL 里混入写操作也会被 SQLite 拒绝
        conn.execute(text("PRAGMA query_only = ON"))
        result = conn.execute(text(sql))
        rows = [dict(r._mapping) for r in result.fetchmany(max_rows)]

    return {
        "rows": rows,
        "row_count": len(rows),
        "elapsed_ms": round((time.time() - start) * 1000, 2),
    }