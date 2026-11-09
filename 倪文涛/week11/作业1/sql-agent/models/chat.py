import json
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from models.workflow import QueryResult, Scalar, WorkflowError, WorkflowStep


class ChatRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ChatResponse(BaseModel):
    status: Literal["success", "needs_clarification", "failed"] = "failed"
    answer: str = ""
    selected_tables: list[str] = Field(default_factory=list)
    sql: str | None = None
    params: dict[str, Scalar] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Scalar]] = Field(default_factory=list)
    returned_rows: int = 0
    truncated: bool = False
    rewrite_count: int = 0
    summary_degraded: bool = False
    steps: list[WorkflowStep] = Field(default_factory=list)
    error: WorkflowError | None = None


class ResultEvent(QueryResult):
    selected_tables: list[str]
    sql: str
    params: dict[str, Scalar]


class AnswerDelta(BaseModel):
    delta: str


class ErrorEvent(BaseModel):
    stage: str
    code: str
    message: str
    fatal: bool


class ChatEvent(BaseModel):
    event: Literal["step", "result", "answer_delta", "error", "done"]
    data: WorkflowStep | ResultEvent | AnswerDelta | ErrorEvent | ChatResponse

    def encode(self) -> str:
        payload = json.dumps(self.data.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
        return f"event: {self.event}\ndata: {payload}\n\n"
