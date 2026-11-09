"""第 1 步：数据库解析。

读取表名、字段、主键、外键、样例数据，生成给大模型看的 schema。
"""
import json
from sqlalchemy import create_engine, inspect, text

# 中文别名：帮助模型把中文问题映射到英文表名
TABLE_CN = {
    "artists": "歌手/艺术家",
    "albums": "专辑",
    "tracks": "曲目/歌曲",
    "genres": "音乐流派",
    "media_types": "媒体类型",
    "playlists": "播放列表",
    "playlist_track": "播放列表-曲目关联",
    "invoices": "发票/订单",
    "invoice_items": "订单明细",
    "customers": "客户",
    "employees": "员工",
}


class DBParser:
    """解析数据库结构，产出供 LLM 使用的 schema 说明。"""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.engine = create_engine(db_url, echo=False)
        self.inspector = inspect(self.engine)
        self.table_names = self.inspector.get_table_names()
        self.tables = {}
        self.foreign_keys = []
        self._parse()

    def _parse(self):
        with self.engine.connect() as conn:
            for t in self.table_names:
                columns = self.inspector.get_columns(t)
                pk = self.inspector.get_pk_constraint(t).get("constrained_columns", [])
                fks = self.inspector.get_foreign_keys(t)
                sample_rows = conn.execute(
                    text(f'SELECT * FROM "{t}" LIMIT 3')
                ).mappings().all()

                self.tables[t] = {
                    "columns": [
                        {
                            "name": c["name"],
                            "type": str(c["type"]),
                            "nullable": c.get("nullable", True),
                        }
                        for c in columns
                    ],
                    "pk": list(pk),
                    "fks": [
                        {
                            "column": fk["constrained_columns"][0],
                            "ref_table": fk["referred_table"],
                            "ref_column": fk["referred_columns"][0],
                        }
                        for fk in fks
                    ],
                    "sample": [dict(r) for r in sample_rows],
                }

                for fk in self.tables[t]["fks"]:
                    self.foreign_keys.append({"table": t, **fk})

    def get_schema_brief(self) -> str:
        """一行一张表，用于 table selection。"""
        lines = []
        for t in self.table_names:
            cols = ", ".join(c["name"] for c in self.tables[t]["columns"])
            lines.append(f"- {t} ({TABLE_CN.get(t, '')}): {cols}")
        return "\n".join(lines)

    def get_schema_text(self, table_names=None) -> str:
        """选中表的完整 schema，用于 SQL 生成。"""
        names = table_names or self.table_names
        lines = []
        for t in names:
            if t not in self.tables:
                continue
            info = self.tables[t]
            cols = ", ".join(
                f"{c['name']} {c['type']}" for c in info["columns"]
            )
            lines.append(f"表 {t} ({TABLE_CN.get(t, '')}): {cols}")
            for fk in info["fks"]:
                lines.append(
                    f"  外键: {t}.{fk['column']} -> "
                    f"{fk['ref_table']}.{fk['ref_column']}"
                )
            if info["sample"]:
                lines.append(f"  样例: {info['sample'][0]}")
            lines.append("")
        return "\n".join(lines)

    def dump(self, path: str = "schema.json"):
        """把解析结果落盘，后续步骤复用。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "tables": self.tables,
                    "foreign_keys": self.foreign_keys,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )