"""SQLAlchemy 连接与 ORM Session、结构反射、SQL 校验及只读查询。"""
from collections import deque
from contextlib import contextmanager
import math
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
from pathlib import Path
import sqlite3
from threading import Event
from time import monotonic

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.tokens import TokenType
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

import config
from models.workflow import (
    ColumnSchema, DatabaseSchema, ForeignKeySchema, QueryResult, SQLCandidate,
    SQLCheckResult, TableSchema, TableSelection, WorkflowFailure,
)

SAFE_FUNCTIONS = set("""
abs avg ceil ceiling char coalesce concat concat_ws count date datetime floor format
glob group_concat hex if ifnull iif instr julianday length like likelihood likely
lower ltrim max min mod nullif printf quote replace round rtrim sign strftime
substr substring sum time total trim typeof unicode unixepoch unlikely upper
row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value
last_value nth_value json json_array json_extract json_object json_type json_valid
json_array_length json_group_array json_group_object
""".split())

PORTABLE_FUNCTIONS = SAFE_FUNCTIONS | set("""
cast try_cast extract date_trunc to_char string_agg array_agg json_agg jsonb_agg
current_date current_time current_timestamp date_add date_sub date_diff timestamp_diff
time_to_str str_to_time ts_or_ds_to_date concat_ws
""".split())


class DatabaseService:
    def __init__(self, path=None, max_rows=100, timeout=5, cancel: Event | None = None, url=None):
        self.max_rows = max_rows
        self.timeout = timeout
        self.cancel = cancel or Event()
        self.path = None
        self.schema_name = None
        if self.max_rows < 1 or self.timeout <= 0:
            raise WorkflowFailure("database", "config_error", "查询行数与超时必须大于零。")
        if path is not None and url is not None:
            raise WorkflowFailure("database", "config_error", "数据库路径与 URL 只能指定一个。")
        try:
            database_url = make_url(url if url is not None else config.DATABASE_URL)
            if path is not None:
                database_url = URL.create("sqlite+pysqlite", database=str(path))
            self.dialect = database_url.get_backend_name()
            self.sql_dialect = "postgres" if self.dialect == "postgresql" else self.dialect
            if self.dialect not in {"sqlite", "postgresql", "mysql"}:
                raise WorkflowFailure("database", "config_error", "目前支持 SQLite、PostgreSQL 和 MySQL。")
            connect_args = {}
            if self.dialect == "sqlite":
                raw = Path(database_url.database or "").expanduser()
                self.path = (raw if raw.is_absolute() else config.PROJECT_ROOT / raw).resolve()
                # 无论配置的 SQLite URL 是否带 mode，都强制只读，禁止自动创建空库。
                database_url = URL.create("sqlite+pysqlite", database="file:" + self.path.as_posix(),
                                          query={"mode": "ro", "uri": "true"})
                connect_args = {"timeout": self.timeout}
            elif database_url.drivername == "postgresql+psycopg":
                connect_args = {"connect_timeout": 5}
            elif database_url.drivername == "mysql+pymysql":
                connect_args = {"connect_timeout": 5, "read_timeout": max(1, math.ceil(self.timeout) + 1)}
            # 每次操作在工作线程中创建、关闭连接，避免将 SQLite 连接带到其他线程。
            self.engine = create_engine(database_url, poolclass=NullPool, connect_args=connect_args, hide_parameters=True)
        except WorkflowFailure:
            raise
        except (SQLAlchemyError, ImportError, ValueError) as exc:
            raise WorkflowFailure("database", "config_error", "数据库 URL 或驱动配置无效，请检查 DATABASE_URL 并安装对应驱动。") from exc

    @contextmanager
    def connection(self, stage: str, allowed_tables: list[str] | None = None):
        if self.path is not None and not self.path.is_file():
            raise WorkflowFailure(stage, "config_error", "数据库文件不存在，请检查 config.py 中的 DATABASE_URL。")
        if self.cancel.is_set():
            raise WorkflowFailure(stage, "cancelled", "请求已取消。")
        deadline = monotonic() + self.timeout
        raw = None
        try:
            with self.engine.connect() as con:
                if self.dialect == "sqlite":
                    raw = con.connection.driver_connection
                    con.exec_driver_sql("PRAGMA query_only = ON")
                    raw.set_progress_handler(lambda: int(self.cancel.is_set() or monotonic() >= deadline), 1000)
                    if allowed_tables is not None:
                        allowed = {name.casefold() for name in allowed_tables}

                        def authorize(action, arg1, arg2, database, source):
                            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                                return sqlite3.SQLITE_OK
                            if action == sqlite3.SQLITE_READ:
                                return sqlite3.SQLITE_OK if database in ("main", None) and (arg1 or "").casefold() in allowed else sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_FUNCTION:
                                return sqlite3.SQLITE_OK if (arg2 or "").casefold() in SAFE_FUNCTIONS else sqlite3.SQLITE_DENY
                            return sqlite3.SQLITE_DENY

                        raw.set_authorizer(authorize)
                elif self.dialect == "postgresql":
                    con.exec_driver_sql("SET TRANSACTION READ ONLY")
                    con.exec_driver_sql(f"SET LOCAL statement_timeout = {max(1, int(self.timeout * 1000))}")
                else:
                    con.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
                    con.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {max(1, int(self.timeout * 1000))}")
                try:
                    with Session(bind=con, autoflush=False) as session:
                        yield session
                finally:
                    if raw is not None:
                        raw.set_authorizer(None)
                        raw.set_progress_handler(None, 0)
        except DBAPIError as exc:
            message = str(exc.orig)
            if self.cancel.is_set():
                raise WorkflowFailure(stage, "cancelled", "请求已取消。") from exc
            if monotonic() >= deadline or any(word in message.lower() for word in
                    ("interrupted", "statement timeout", "maximum statement execution time", "query execution was interrupted")):
                raise WorkflowFailure(stage, "timeout", "SQL 执行超时，已中断查询。") from exc
            if any(word in message.lower() for word in ("not authorized", "prohibited", "permission denied", "read-only", "read only")):
                raise WorkflowFailure(stage, "policy_denied", "SQL 访问了未授权的表、操作或函数。") from exc
            if stage == "schema" or exc.connection_invalidated:
                raise WorkflowFailure(stage, "database_error", "数据库不可访问，请检查 URL、网络、权限及驱动。") from exc
            raise WorkflowFailure(stage, "execution_error", message[:1000], retryable=True) from exc
        except SQLAlchemyError as exc:
            raise WorkflowFailure(stage, "database_error", "数据库连接或会话操作失败。") from exc

    def parse_schema(self) -> DatabaseSchema:
        with self.connection("schema") as session:
            con = session.connection()
            inspector = inspect(con)
            self.schema_name = inspector.default_schema_name
            tables = []
            metadata = MetaData()
            for name in sorted(inspector.get_table_names()):
                if name.startswith("sqlite_"):
                    continue
                table = Table(name, metadata, autoload_with=con)
                columns = [ColumnSchema(name=c.name, data_type=str(c.type.compile(dialect=con.dialect)),
                           nullable=bool(c.nullable and not c.primary_key), primary_key=c.primary_key) for c in table.columns]
                keys = []
                for key in inspector.get_foreign_keys(name):
                    if key.get("referred_schema") not in (None, self.schema_name):
                        continue  # 本项目仅开放当前默认 schema 的表。
                    keys.extend(ForeignKeySchema(column=column, target_table=key["referred_table"], target_column=target)
                                for column, target in zip(key["constrained_columns"], key["referred_columns"]))
                tables.append(TableSchema(name=name, ddl=str(CreateTable(table).compile(dialect=con.dialect)),
                                          columns=columns, foreign_keys=keys))
            if not tables:
                raise WorkflowFailure("schema", "database_error", "数据库没有可查询的业务表。")
            return DatabaseSchema(dialect=self.sql_dialect, schema_name=self.schema_name, tables=tables)

    @staticmethod
    def complete_selection(selection: TableSelection, schema: DatabaseSchema) -> TableSelection:
        names = {t.name.casefold(): t.name for t in schema.tables}
        chosen = []
        for name in selection.tables:
            if name.casefold() not in names:
                raise WorkflowFailure("table_selection", "selection_error", f"选择的表不存在：{name}")
            canonical = names[name.casefold()]
            if canonical not in chosen:
                chosen.append(canonical)
        if not chosen:
            raise WorkflowFailure("table_selection", "selection_error", "没有选择可查询的数据表。")
        graph = {t.name: set() for t in schema.tables}
        for table in schema.tables:
            for key in table.foreign_keys:
                target = names.get(key.target_table.casefold())
                if target:
                    graph[table.name].add(target)
                    graph[target].add(table.name)
        connected = [chosen[0]]
        for target in chosen[1:]:
            queue = deque([[name] for name in connected])
            seen = set(connected)
            path = None
            while queue:
                route = queue.popleft()
                if route[-1] == target:
                    path = route
                    break
                for neighbor in sorted(graph[route[-1]]):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(route + [neighbor])
            if path is None:
                raise WorkflowFailure("table_selection", "selection_error", "所选表缺少明确的关联路径，请补充查询口径。")
            connected.extend(n for n in path if n not in connected)
        return selection.model_copy(update={"tables": connected})

    @staticmethod
    def validate_sql(candidate: SQLCandidate, schema: DatabaseSchema, selected_tables: list[str]):
        try:
            trees = [tree for tree in sqlglot.parse(candidate.sql, read=schema.dialect) if tree is not None]
        except sqlglot.errors.SqlglotError as exc:
            raise WorkflowFailure("sql_check", "invalid_sql", f"{schema.dialect} 语法解析失败：{exc}", True) from exc
        if len(trees) != 1:
            raise WorkflowFailure("sql_check", "policy_denied", "仅允许一条只读查询。")
        tree = trees[0]
        if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise WorkflowFailure("sql_check", "policy_denied", "仅支持 SELECT 或只读 WITH 查询。")
        if any(isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into)) for node in tree.walk()):
            raise WorkflowFailure("sql_check", "policy_denied", "查询中包含不允许的操作。")
        if tree.find(exp.Lock):
            raise WorkflowFailure("sql_check", "policy_denied", "只读查询不允许加写锁。")
        if schema.dialect != "sqlite":
            for function in tree.find_all(exp.Func):
                name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
                if name.casefold() not in PORTABLE_FUNCTIONS or isinstance(function.parent, exp.Dot):
                    raise WorkflowFailure("sql_check", "policy_denied", f"不开放此数据库函数：{name}")
        placeholders = list(tree.find_all(exp.Placeholder))
        keys = {node.name for node in placeholders}
        if any(not node.name for node in placeholders) or list(tree.find_all(exp.Parameter)):
            raise WorkflowFailure("sql_check", "invalid_sql", "仅支持 :name 形式的命名参数。", True)
        if keys != set(candidate.params):
            raise WorkflowFailure("sql_check", "invalid_sql", "SQL 命名占位符与 params 不一致。", True)
        existing = {t.name.casefold() for t in schema.tables}
        allowed = {n.casefold() for n in selected_tables}
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if isinstance(source, exp.Table):
                    name = source.name.casefold()
                    if source.catalog or (source.db and source.db.casefold() != (schema.schema_name or "main").casefold()):
                        raise WorkflowFailure("sql_check", "policy_denied", "只能查询当前数据库的业务表。")
                    if name.startswith("sqlite_") or name.startswith("pragma_"):
                        raise WorkflowFailure("sql_check", "policy_denied", "不开放系统表或 PRAGMA 表函数。")
                    if name not in existing:
                        raise WorkflowFailure("sql_check", "invalid_sql", f"数据表不存在：{source.name}", True)
                    if name not in allowed:
                        raise WorkflowFailure("sql_check", "selection_error", f"查询使用了未选择的表：{source.name}", True)

    def statement(self, candidate: SQLCandidate, prefix=""):
        # SQLAlchemy text() 也会识别字符串/注释中的 :word。只保留真正占位符，
        # 对其他冒号转义，保证时间文本、URL、PostgreSQL ::cast 等按原文执行。
        tokens = sqlglot.tokenize(candidate.sql, read=self.sql_dialect)
        positions = {token.start for token, following in zip(tokens, tokens[1:])
                     if token.token_type == TokenType.COLON and following.start == token.end + 1
                     and following.text in candidate.params}
        sql = "".join("\\:" if char == ":" and index not in positions else char
                      for index, char in enumerate(candidate.sql))
        return text(prefix + sql)

    def check_sql(self, candidate: SQLCandidate, schema: DatabaseSchema, selected_tables: list[str]) -> SQLCheckResult:
        try:
            self.validate_sql(candidate, schema, selected_tables)
            with self.connection("sql_check", selected_tables) as session:
                prefix = "EXPLAIN QUERY PLAN " if self.dialect == "sqlite" else "EXPLAIN "
                session.execute(self.statement(candidate, prefix), candidate.params).close()
                with session.execute(self.statement(candidate), candidate.params) as result:
                    result.fetchmany(self.max_rows + 1)
            return SQLCheckResult(passed=True)
        except WorkflowFailure as exc:
            return SQLCheckResult(passed=False, error=exc.error)

    def execute_sql(self, candidate: SQLCandidate, allowed_tables: list[str]) -> QueryResult:
        started = monotonic()
        with self.connection("sql_execution", allowed_tables) as session:
            with session.execute(self.statement(candidate), candidate.params) as result:
                columns = list(result.keys())
                fetched = result.fetchmany(self.max_rows + 1)

        def serializable(value):
            if isinstance(value, (datetime, date, time)):
                return value.isoformat()
            if isinstance(value, (Decimal, UUID)):
                return str(value)
            if isinstance(value, bytes):
                return value.hex()
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            return value

        rows = [[serializable(v) for v in row] for row in fetched[:self.max_rows]]
        return QueryResult(columns=columns, rows=rows, returned_rows=len(rows),
                           truncated=len(fetched) > self.max_rows, elapsed_ms=round((monotonic() - started) * 1000, 2))
