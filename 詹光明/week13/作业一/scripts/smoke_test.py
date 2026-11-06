"""离线冒烟测试：验证 schema 解析、SQL 校验与只读执行，不调用大模型。

用法：
    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sql_agent.config import settings  # noqa: E402
from sql_agent.db.catalog import get_catalog  # noqa: E402
from sql_agent.db.executor import SQLExecutor  # noqa: E402
from sql_agent.db.validator import SQLValidator  # noqa: E402


def main() -> None:
    print(f"数据库：{settings.db_path}")
    catalog = get_catalog()
    print(f"表数量：{len(catalog.table_names)}")
    print("表名：" + ", ".join(catalog.table_names))

    schema = catalog.render_schema(["Artist", "Album"])
    print(f"Artist + Album schema 字符数：{len(schema)}")
    assert catalog.is_connected(["Artist", "Album"])

    validator = SQLValidator(settings.db_path)
    good = validator.validate("SELECT Name FROM Artist ORDER BY ArtistId LIMIT 3;")
    print(f"合法 SQL：ok={good.ok} normalized={good.normalized_sql!r}")
    assert good.ok

    unsafe = validator.validate("DELETE FROM Artist")
    print(f"写操作拦截：ok={unsafe.ok} code={unsafe.code}")
    assert not unsafe.ok

    broken = validator.validate("SELECT Nope FROM Artist")
    print(f"未知列：ok={broken.ok} code={broken.code}")
    assert not broken.ok

    execution = SQLExecutor(settings.db_path).execute(good.normalized_sql)
    print(f"执行结果：{execution.columns} {execution.rows}")
    assert execution.row_count == 3

    print("冒烟测试通过。")


if __name__ == "__main__":
    main()
