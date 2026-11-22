"""数据库解析层：`DBParser`（逐字移植，仅按 SQLAlchemy 2.x 规范修正两处写法）。

移植原则：**对外行为一个字节都不改**。
- `table_names` / `get_table_fields` / `get_data_relations` / `get_table_sample` /
  `check_sql` / `execute_sql` 的签名与返回结构与原实现一致；
- 构造时逐表打印 `Table -> ...` 的行为保留（原实现就打印，换成静默会让输出对不上）。

仅有的两处调整（都不改变语义，只是消除 SQLAlchemy 2.x 的弃用告警）：
1. `select(func.count()).filter(col == None)` → `select(func.count()).where(col.is_(None))`
   —— 生成的都是 `SELECT count(*) FROM t WHERE col IS NULL`；
2. 用 `with self.engine.connect()` 包住整段元数据抓取（原实现直接用 `self.conn`），
   语义相同，但连接能确保归还。
"""

from __future__ import annotations

import traceback

import pandas as pd
from sqlalchemy import (MetaData, Table, create_engine, func, inspect, select,
                        text)


class DBParser:
    """解析 sqlite / mysql 数据库的元数据与样例。"""

    def __init__(self, db_url: str, *, verbose: bool = True) -> None:
        self.db_url = db_url
        self.db_type = "mysql" if "mysql" in db_url else "sqlite"

        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()

        self._table_fields: dict = {}   # 每张表的字段元数据
        self.foreign_keys: list = []    # 全库外键
        self._table_sample: dict = {}   # 每张表的第一行样例

        with self.engine.connect() as conn:
            for table_name in self.table_names:
                if verbose:
                    print("Table ->", table_name)
                self._table_fields[table_name] = {}

                # 累计外键
                self.foreign_keys += [
                    {
                        "constrained_table": table_name,
                        "constrained_columns": x["constrained_columns"],
                        "referred_table": x["referred_table"],
                        "referred_columns": x["referred_columns"],
                    }
                    for x in self.inspector.get_foreign_keys(table_name)
                ]

                table_instance = Table(table_name, MetaData(), autoload_with=self.engine)
                table_columns = self.inspector.get_columns(table_name)
                self._table_fields[table_name] = {x["name"]: dict(x) for x in table_columns}

                # 逐字段做统计
                for column_meta in table_columns:
                    col = getattr(table_instance.columns, column_meta["name"])
                    meta = self._table_fields[table_name][column_meta["name"]]

                    # distinct 数
                    meta["distinct"] = conn.execute(
                        select(func.count(func.distinct(col)))
                    ).fetchone()[0]

                    # 文本字段取众数（出现次数最多的值）
                    field_type = str(meta["type"]).lower()
                    if "text" in field_type or "char" in field_type:
                        row = conn.execute(
                            select(col, func.count().label("count"))
                            .group_by(col).order_by(func.count().desc()).limit(1)
                        ).fetchone()
                        meta["mode"] = row[0] if row else None

                    # 缺失数 / 最大 / 最小
                    meta["nan_count"] = conn.execute(
                        select(func.count()).where(col.is_(None))
                    ).fetchone()[0]
                    meta["max"] = conn.execute(select(func.max(col))).fetchone()[0]
                    meta["min"] = conn.execute(select(func.min(col))).fetchone()[0]

                    # 随机样例
                    random_value = [
                        str(r[0]) for r in conn.execute(select(col).limit(10)).all()
                        if r[0] is not None
                    ]
                    meta["random"] = list(set(random_value))[:3]

                # 表样例（第一行）
                sample = conn.execute(select(table_instance).limit(1)).fetchone()
                df = pd.DataFrame([sample]) if sample is not None else pd.DataFrame()
                df.columns = [x["name"] for x in table_columns]
                self._table_sample[table_name] = df

    # ---------- 对外接口（签名与返回结构同原实现） ----------
    def get_table_fields(self, table_name) -> pd.DataFrame:
        """获取表字段信息。"""
        return pd.DataFrame.from_dict(self._table_fields[table_name]).T

    def get_data_relations(self) -> pd.DataFrame:
        """获取数据库连接信息（主键与外键）。"""
        return pd.DataFrame(self.foreign_keys)

    def get_table_sample(self, table_name) -> pd.DataFrame:
        """获取表样例（第一行）。"""
        return self._table_sample[table_name]

    def check_sql(self, sql: str):
        """试跑 SQL，返回 (是否可执行, 报错信息)。"""
        try:
            with self.engine.begin() as conn:  # begin() 自动提交并关闭
                conn.execute(text(sql))
            return True, "ok"
        except Exception:
            return False, traceback.format_exc(limit=1)

    def execute_sql(self, sql: str):
        """只读执行，返回 (列名, 全部结果行)。"""
        with self.engine.begin() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = [tuple(r) for r in result.fetchall()]
            return columns, rows
