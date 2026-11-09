from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field

from .catalog import DBCatalog
from .privacy import projection_violations

try:
    from sqlglot import exp, parse
    HAS_SQLGLOT = True
except ImportError:  # pragma: no cover
    exp = None
    parse = None
    HAS_SQLGLOT = False

DANGEROUS_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "attach",
    "detach",
    "pragma",
    "vacuum",
    "reindex",
    "analyze",
)

ALLOWED_FUNCTIONS = frozenset(
    {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "TOTAL",
        "GROUP_CONCAT",
        "ROUND",
        "ABS",
        "COALESCE",
        "IFNULL",
        "NULLIF",
        "IIF",
        "IF",
        "LENGTH",
        "LOWER",
        "UPPER",
        "SUBSTRING",
        "SUBSTR",
        "TRIM",
        "LTRIM",
        "RTRIM",
        "REPLACE",
        "INSTR",
        "PRINTF",
        "FORMAT",
        "STRFTIME",
        "DATE",
        "TIME",
        "DATETIME",
        "JULIANDAY",
        "UNIXEPOCH",
        "CAST",
        "CHAR",
        "HEX",
        "QUOTE",
        "UNICODE",
        "ROW_NUMBER",
        "RANK",
        "DENSE_RANK",
        "NTILE",
        "LAG",
        "LEAD",
        "FIRST_VALUE",
        "LAST_VALUE",
        "NTH_VALUE",
        "CUME_DIST",
        "PERCENT_RANK",
    }
)

BLOCKED_FUNCTIONS = frozenset(
    {
        "LOAD_EXTENSION",
        "READFILE",
        "WRITEFILE",
        "EDIT",
        "EVAL",
        "FTS3",
        "FTS4",
        "FTS5",
        "RANDOMBLOB",
        "ZEROBLOB",
    }
)


@dataclass
class ValidationResult:
    ok: bool
    stage: str = "ok"
    error: str = ""
    code: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class _Source:
    table: str | None
    columns: set[str] = field(default_factory=set)


@dataclass
class _Scope:
    sources: dict[str, _Source] = field(default_factory=dict)
    output_aliases: set[str] = field(default_factory=set)
    parent: "_Scope | None" = None

    def columns(self) -> set[str]:
        values: set[str] = set()
        for source in self.sources.values():
            values |= source.columns
        if self.parent is not None:
            values |= self.parent.columns()
        return values

    def lookup(self, qualifier: str) -> _Source | None:
        key = qualifier.lower()
        if key in self.sources:
            return self.sources[key]
        if self.parent is not None:
            return self.parent.lookup(key)
        return None

    def has_column(self, name: str) -> bool:
        lname = name.lower()
        if any(lname in source.columns for source in self.sources.values()):
            return True
        return self.parent.has_column(name) if self.parent is not None else False


def strip_code_fence(sql: str) -> str:
    sql = sql.strip()
    fenced = re.match(r"^```(?:sql)?\s*(.*?)```$", sql, re.S | re.I)
    return fenced.group(1).strip() if fenced else sql


def _strip_literals(sql: str) -> str:
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    sql = re.sub(r'"(?:""|[^"])*"', '""', sql)
    return sql


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _function_name(node: object) -> str:
    text = node.sql(dialect="sqlite") if hasattr(node, "sql") else ""
    match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
    return match.group(1).upper() if match else ""


def _owned_columns(select: object) -> list[object]:
    columns = []
    for column in select.find_all(exp.Column):
        parent = column.parent
        while parent is not None and not isinstance(parent, exp.Select):
            parent = parent.parent
        if parent is select:
            columns.append(column)
    return columns


def _owned_aggregates(select: object) -> list[object]:
    aggregates = []
    for node in select.find_all(exp.AggFunc):
        parent = node.parent
        while parent is not None and not isinstance(parent, exp.Select):
            parent = parent.parent
        if parent is select:
            aggregates.append(node)
    return aggregates


def _output_columns(
    node: object, cte_columns: dict[str, set[str]], *, include_bare: bool = True
) -> set[str]:
    if isinstance(node, exp.Union):
        node = node.this
    if not isinstance(node, exp.Select):
        return set()
    columns: set[str] = set()
    for projection in node.expressions:
        if isinstance(projection, exp.Alias):
            columns.add(projection.alias.lower())
        elif isinstance(projection, exp.Column):
            if include_bare:
                columns.add(projection.name.lower())
        elif isinstance(projection, exp.Star):
            for source in _scope_sources(node, cte_columns).values():
                columns |= source.columns
    return columns


def _scope_sources(
    select: object, cte_columns: dict[str, set[str]]
) -> dict[str, _Source]:
    sources: dict[str, _Source] = {}
    from_node = select.args.get("from_")
    nodes = []
    if from_node is not None and from_node.this is not None:
        nodes.append(from_node.this)
    for join in select.args.get("joins") or []:
        if join.this is not None:
            nodes.append(join.this)
    for node in nodes:
        if isinstance(node, exp.Table):
            name = node.name
            alias = (node.alias_or_name or name).lower()
            if name.lower() in cte_columns:
                source = _Source(None, set(cte_columns[name.lower()]))
            else:
                source = _Source(name, set())
            sources[alias] = source
            sources[name.lower()] = source
        elif isinstance(node, exp.Subquery):
            alias = (node.alias_or_name or "").lower()
            columns = _output_columns(node.this, cte_columns)
            if alias:
                sources[alias] = _Source(None, columns)
    return sources


class SQLValidator:
    """静态（sqlglot AST 允许列表/作用域）+ 动态（EXPLAIN）双层校验。"""

    def __init__(
        self,
        catalog: DBCatalog,
        *,
        max_sql_chars: int = 20_000,
        max_tables: int = 8,
    ):
        self.catalog = catalog
        self.max_sql_chars = max(1000, int(max_sql_chars))
        self.max_tables = max(1, int(max_tables))

    def validate(
        self,
        sql: str,
        *,
        allowed_tables: list[str] | None = None,
        check_pii: bool = True,
    ) -> ValidationResult:
        body = strip_code_fence(sql).rstrip().rstrip(";").strip()
        if not body:
            return self._fail("empty", "SQL 为空", "E5001")
        if len(body) > self.max_sql_chars:
            return self._fail("too_long", "查询语句过长", "E5016")

        cleaned = _strip_literals(_strip_comments(body))
        if ";" in cleaned:
            return self._fail("multi_statement", "只允许执行一条 SQL 语句", "E5002")
        if not re.match(r"^\s*(select|with)\b", cleaned, re.I):
            return self._fail("statement_type", "只允许 SELECT 查询", "E5003")

        lowered = cleaned.lower()
        for keyword in DANGEROUS_KEYWORDS:
            if re.search(rf"\b{keyword}\b", lowered):
                return self._fail(
                    "forbidden", f"检测到禁止的关键字: {keyword.upper()}", "E5004"
                )

        static = self._static_validate(
            body, allowed_tables=allowed_tables, check_pii=check_pii
        )
        if not static.ok:
            return static
        return self._dynamic_validate(body)

    def _static_validate(
        self,
        body: str,
        *,
        allowed_tables: list[str] | None,
        check_pii: bool,
    ) -> ValidationResult:
        if not HAS_SQLGLOT:
            return ValidationResult(True, "skipped", "sqlglot 未安装，跳过静态校验")

        try:
            statements = [s for s in parse(body, read="sqlite") if s is not None]
        except Exception as exc:  # sqlglot 解析异常
            return self._fail("syntax", f"SQL 解析失败: {exc}", "E5005")

        if len(statements) != 1:
            return self._fail("multi_statement", "只允许一条 SQL 语句", "E5002")

        expr = statements[0]
        allowed_roots = (exp.Select, exp.Union, exp.Intersect, exp.Except)
        if not isinstance(expr, allowed_roots):
            return self._fail(
                "statement_type", f"只允许 SELECT，实际为 {type(expr).__name__}", "E5003"
            )

        # CTE 输出列
        cte_columns: dict[str, set[str]] = {}
        cte_names: set[str] = set()
        for cte in expr.find_all(exp.CTE):
            name = (cte.alias_or_name or "").lower()
            if not name:
                continue
            cte_names.add(name)
            explicit = {column.lower() for column in cte.alias_column_names if column}
            cte_columns[name] = explicit or _output_columns(cte.this, cte_columns)

        allowed_set = (
            {name.lower() for name in allowed_tables}
            if allowed_tables is not None
            else {name.lower() for name in self.catalog.table_names}
        )

        real_tables: set[str] = set()
        for table in expr.find_all(exp.Table):
            raw_name = table.name
            if not raw_name or raw_name.lower() in cte_names:
                continue
            real = self.catalog.resolve_table(raw_name)
            if not real:
                return self._fail("unknown_table", f"表不存在: {raw_name}", "E5006")
            if real.lower() not in allowed_set:
                return self._fail(
                    "table_not_allowed",
                    f"表不在本次查询白名单中: {raw_name}",
                    "E5006",
                )
            real_tables.add(real.lower())
        if len(real_tables) > self.max_tables:
            return self._fail(
                "too_many_joins",
                f"关联表超过 {self.max_tables} 张",
                "E5015",
            )

        # 函数白名单（EXISTS/IN 等谓词不是可调用函数）
        for node in expr.walk():
            if isinstance(node, (exp.Func, exp.Cast)) and not isinstance(
                node, (exp.Exists, exp.In, exp.Between, exp.Like)
            ):
                name = _function_name(node)
                if not name:
                    continue
                if name in BLOCKED_FUNCTIONS or name.startswith(("PRAGMA_", "SQLITE_")):
                    return self._fail(
                        "function_not_allowed", f"禁止的函数: {name}", "E5011"
                    )
                if name not in ALLOWED_FUNCTIONS:
                    return self._fail(
                        "function_not_allowed", f"不允许的函数: {name}", "E5011"
                    )

        # PII / SELECT *
        if check_pii:
            violations = projection_violations(body)
            if violations:
                star = any("SELECT *" in item for item in violations)
                return self._fail(
                    "star_projection" if star else "pii",
                    "；".join(violations),
                    "E5012" if star else "E8007",
                )

        # 作用域与列校验（含 CTE）
        scopes: dict[int, _Scope] = {}
        for select in expr.find_all(exp.Select):
            parent_scope = _parent_scope(select, scopes)
            scope = self._build_scope(select, cte_columns, parent_scope)
            scopes[id(select)] = scope
            problem = self._validate_columns(select, scope)
            if problem is not None:
                return problem
            problem = self._validate_cartesian(select)
            if problem is not None:
                return problem
            problem = self._validate_group_by(select, scope)
            if problem is not None:
                return problem
        return ValidationResult(True, "ok", "静态校验通过")

    def _build_scope(
        self,
        select: object,
        cte_columns: dict[str, set[str]],
        parent: _Scope | None,
    ) -> _Scope:
        sources = _scope_sources(select, cte_columns)
        for key, source in list(sources.items()):
            if source.table is not None:
                source.columns = self.catalog.column_names(source.table)
        scope = _Scope(sources=sources, parent=parent)
        scope.output_aliases = _output_columns(
            select, cte_columns, include_bare=False
        )
        return scope

    def _validate_columns(
        self, select: object, scope: _Scope
    ) -> ValidationResult | None:
        for column in _owned_columns(select):
            if isinstance(column.this, exp.Star):
                continue
            name = column.name
            if not name:
                continue
            lname = name.lower()
            if lname in scope.output_aliases:
                continue
            qualifier = column.table.lower()
            if qualifier:
                source = scope.lookup(qualifier)
                if source is None:
                    return self._fail(
                        "unknown_alias", f"未知的表别名: {qualifier}", "E5008"
                    )
                if source.columns and lname not in source.columns:
                    label = source.table or qualifier
                    return self._fail(
                        "unknown_column", f"列不存在: {label}.{name}", "E5007"
                    )
            else:
                if scope.has_column(lname):
                    continue
                # 无 FROM 的常量查询允许
                if not scope.sources:
                    continue
                return self._fail("unknown_column", f"列不存在: {name}", "E5007")
        return None

    def _validate_cartesian(self, select: object) -> ValidationResult | None:
        from_node = select.args.get("from_")
        joins = select.args.get("joins") or []
        source_count = int(from_node is not None and from_node.this is not None) + len(joins)
        if source_count <= 1:
            return None
        for join in joins:
            if join.args.get("on") or join.args.get("using") or join.args.get("natural"):
                continue
            return self._fail(
                "cartesian_join",
                "多表查询缺少明确的 JOIN ... ON 条件",
                "E5014",
            )
        return None

    def _validate_group_by(
        self, select: object, scope: _Scope
    ) -> ValidationResult | None:
        has_aggregate = bool(_owned_aggregates(select))
        if not has_aggregate:
            return None
        group = select.args.get("group")
        group_expressions = list(group.expressions) if group is not None else []
        if not any(not _is_constant(item) for item in select.expressions):
            return None

        group_sql = {
            item.sql(dialect="sqlite").lower() for item in group_expressions
        }
        group_names = {
            item.name.lower()
            for item in group_expressions
            if isinstance(item, exp.Column)
        }
        aliases = {
            projection.alias.lower(): projection.this
            for projection in select.expressions
            if isinstance(projection, exp.Alias)
        }
        allow_by_pk = self._group_by_primary_key(group_expressions, aliases, scope)

        for projection in select.expressions:
            if _is_constant(projection):
                continue
            if any(isinstance(node, exp.AggFunc) for node in projection.walk()):
                continue
            base = projection.this if isinstance(projection, exp.Alias) else projection
            base_sql = base.sql(dialect="sqlite").lower()
            if base_sql in group_sql:
                continue
            # GROUP BY 输出别名（SQLite 允许按 SELECT 别名分组）
            if isinstance(projection, exp.Alias) and projection.alias.lower() in group_names:
                continue
            if isinstance(base, exp.Column):
                if base.name.lower() in group_names:
                    continue
            # GROUP BY 别名
            if isinstance(base, exp.Column) and base.name.lower() in aliases:
                aliased = aliases[base.name.lower()]
                if aliased.sql(dialect="sqlite").lower() in group_sql:
                    continue
            if allow_by_pk:
                continue
            return self._fail(
                "group_by_mismatch",
                f"非聚合字段未出现在 GROUP BY 中: {base.sql(dialect='sqlite')}",
                "E5013",
            )
        return None

    def _group_by_primary_key(
        self,
        group_expressions: list[object],
        aliases: dict[str, object],
        scope: _Scope,
    ) -> bool:
        for item in group_expressions:
            if isinstance(item, exp.Literal):
                continue
            if isinstance(item, exp.Column):
                if not item.table and item.name.lower() in aliases:
                    item = aliases[item.name.lower()]
                if not isinstance(item, exp.Column):
                    continue
                source = scope.lookup(item.table) if item.table else None
                if source and source.table:
                    info = self.catalog.column_info(source.table, item.name)
                    if info and info.primary_key:
                        return True
                elif not item.table:
                    for source in scope.sources.values():
                        if source.table:
                            info = self.catalog.column_info(source.table, item.name)
                            if info and info.primary_key:
                                return True
        return False

    def _dynamic_validate(self, body: str) -> ValidationResult:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.catalog.db_path.as_uri() + "?mode=ro", uri=True)
            conn.execute("PRAGMA query_only = ON")
            deadline = time.monotonic() + 5.0
            conn.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 10000
            )
            conn.execute("EXPLAIN " + body)
            return ValidationResult(True, "ok", "静态/动态校验通过")
        except sqlite3.Error as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                return self._fail("explain_timeout", "查询计划编译超时", "E5009")
            return self._fail("sqlite", f"SQLite 校验失败: {exc}", "E5009")
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def _fail(stage: str, message: str, code: str) -> ValidationResult:
        return ValidationResult(False, stage, message, code)


def _parent_scope(select: object, scopes: dict[int, _Scope]) -> _Scope | None:
    parent = select.parent
    while parent is not None:
        if isinstance(parent, exp.Select) and id(parent) in scopes:
            return scopes[id(parent)]
        parent = parent.parent
    return None


def _is_constant(node: object) -> bool:
    if isinstance(node, exp.Literal):
        return True
    if isinstance(node, exp.Alias):
        return _is_constant(node.this)
    if isinstance(node, (exp.Boolean, exp.Null)):
        return True
    if isinstance(node, exp.Neg):
        return _is_constant(node.this)
    return False
