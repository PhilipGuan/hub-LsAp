from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | None


class ColumnSchema(BaseModel):
    name: str
    data_type: str
    nullable: bool
    primary_key: bool


class ForeignKeySchema(BaseModel):
    column: str
    target_table: str
    target_column: str


class TableSchema(BaseModel):
    name: str
    ddl: str
    columns: list[ColumnSchema]
    foreign_keys: list[ForeignKeySchema]


class DatabaseSchema(BaseModel):
    dialect: str = "sqlite"
    schema_name: str | None = None
    tables: list[TableSchema]


class TableSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[str]
    reason: str
    clarification: str | None = None


class SQLCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(max_length=30000)
    params: dict[str, Scalar] = Field(default_factory=dict)


class WorkflowError(BaseModel):
    stage: str
    code: str
    message: str
    retryable: bool = False


class WorkflowFailure(Exception):
    def __init__(self, stage: str, code: str, message: str, retryable: bool = False):
        self.error = WorkflowError(stage=stage, code=code, message=message, retryable=retryable)
        super().__init__(message)


class SQLCheckResult(BaseModel):
    passed: bool
    error: WorkflowError | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Scalar]]
    returned_rows: int
    truncated: bool
    elapsed_ms: float


class WorkflowStep(BaseModel):
    stage: str
    status: Literal["running", "completed", "failed"]
    attempt: int = 0
    message: str
    elapsed_ms: float = 0
