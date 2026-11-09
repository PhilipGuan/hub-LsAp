"""跨模块共享的数据结构。

分两类（CLAUDE.md「命名与风格」）：
- **程序内部状态 / 产物** 用 dataclass——没有不可信输入要校验。
- **LLM 输出**用 Pydantic——模型给的结构必须过闸，且统一经 `BaseAgent.parse_model()`，
  它把 `ValidationError` 折成 `LLMJsonError`，保证编排器的降级表只认两种错。
"""

import asyncio
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field


@dataclass
class Source:
    """一条检索结果，由 search tool 产出。

    `id` 是**全局来源编号**（跨轮次、跨子问题唯一），报告里的 `[n]` 就是它——
    正因为全局唯一，各分节各自引用不同的来源也不会串号。

    `search` tool 拿到的响应里还没编号（一律填 0）：全局唯一意味着要对照"已经发过哪些号"，
    而只有编排器知道这件事，所以**编号由编排器统一分配**（含跨轮次按 URL 去重）。
    """

    id: int
    title: str
    url: str
    summary: str  # 博查返回的 summary，本项目以它当作"读到的内容"，不抓网页正文


@dataclass
class Section:
    """报告的一个分节。产物 JSON 里的 `report.sections[]` 就是它。"""

    sub_question: str
    text: str
    refs: list[int] = field(default_factory=list)


class SummaryResult(BaseModel):
    """`SummaryAgent` 的 LLM 输出（需求文档 5.2）。"""

    # min_length=1：空段落等于没写，直接判失败，让编排器走「只留来源标题与 URL」的降级
    section_text: str = Field(min_length=1)
    # 允许为空：一条来源都没用到时是「模型推断」，由置信度说明标注，不算错
    refs: list[int] = Field(default_factory=list)


class JudgeResult(BaseModel):
    """`JudgeAgent` 的 LLM 输出（需求文档 5.2）。"""

    # 只有 sufficient 是编排器真正据以行动的字段，缺了这条判定就没用，必须要有
    sufficient: bool
    # 其余三个是解释性字段，给默认值：模型偶尔漏一个，不该让整条判定降级成「视为已充分」
    missing_angles: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    new_sub_questions: list[str] = Field(default_factory=list)


class Conclusion(BaseModel):
    """关键结论的一条。

    行内 `[n]` 由**程序**按 `refs` 拼接（需求文档 3.2 要求关键结论必须标注引用），
    模型自己写的行内编号会被删掉——否则越界编号会绕过收口留在报告里。
    """

    text: str = Field(min_length=1)
    refs: list[int] = Field(default_factory=list)  # 空 = 纯模型推断，由置信度说明标注


class ReportResult(BaseModel):
    """`ReportAgent` 的 LLM 输出（需求文档 5.2）。只有这四块，**不含分节正文**（决策 8）。"""

    summary: str = Field(min_length=1)
    # 至少要有一条结论：报告结构里的「关键结论」章节不允许为空（验收清单第 2 项）
    conclusions: list[Conclusion] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)
    # 只写定性成因；具体数值由程序算完再渲染进去（决策 10）
    confidence_note: str = Field(min_length=1)


@dataclass
class SectionConfidence:
    """单个分节的置信度。验收项 7 要求每项都能由公式与来源数据复算得到。"""

    sub_question: str
    score: float


@dataclass
class Confidence:
    """置信度结果，由 `confidence.py` 按公式算出（决策 10）。

    渲染层只负责展示，不再改数——数值来源单一，才谈得上「可解释」。
    """

    overall: float
    level: str  # 高 / 中 / 低
    info_cutoff: str  # 信息截止时间 = 检索时间戳与来源最新发布时间的较大值
    unsourced_claims: int  # 无 refs 的结论条数，即「模型推断」条目
    per_section: list[SectionConfidence] = field(default_factory=list)


class Report(BaseModel):
    """artifact JSON 里的 `report` 子对象（需求文档第 7 节）。

    用 Pydantic 只为了一把 `model_dump()` 出可落盘的 dict；里面的 `Section` / `Source`
    仍是 dataclass——它们是产品结构，不是 LLM 输出，不需要过闸。
    """

    markdown: str
    sections: list[Section] = Field(default_factory=list)
    conclusions: list[Conclusion] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


@dataclass
class StepRecord:
    """`process.steps` 的一条埋点。由 `BaseAgent.run()` 统一产出，业务代码不要散打点。"""

    agent: str
    ok: bool
    elapsed_ms: int
    attempts: int  # 本次 run 内的 LLM 调用次数，重试会 >1，是重试行为唯一的观测口径
    error: str | None = None


@dataclass
class SearchRecord:
    """`process.search_queries` 的一条（验收项 5 靠它）。

    `queries` 是**实际发出去的检索词**：0 条时 search tool 会用改写后的词重试一次，
    记原始子问题就对不上真实检索行为了。
    """

    round: int
    sub_question: str
    queries: list[str] = field(default_factory=list)
    hits: int = 0
    error: str | None = None  # 调不通时的降级留痕，与「命中 0 条」是两回事


@dataclass
class JudgeRecord:
    """`process.judge_history` 的一条。**第 3 轮的判定同样入列**（决策 3）。"""

    round: int
    sufficient: bool
    missing_angles: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    new_sub_questions: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ResearchProcess:
    """产物 JSON 里的 `process` 子对象（研究过程记录 = 第 3 项交付物）。"""

    rounds: int = 0
    elapsed_ms: int = 0
    search_queries: list[SearchRecord] = field(default_factory=list)
    judge_history: list[JudgeRecord] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)


@dataclass
class Event:
    """研究过程中实时发出的一条事件，SSE 直接推它（验收项 8）。

    与 `process.*` 那三份记录的区别：那三份是**跑完之后的存档**，这份是**过程中就要发出去的**。
    两者内容有重叠是刻意的——存档要能独立说明全过程，不该逼着读者去翻事件流。

    `stage` 取值：`plan` 规划完成 ｜ `round` 本轮开始 ｜ `search` 检索完一个子问题 ｜
    `section` 分节完成 ｜ `judge` 判定结论 ｜ `compose` 开始综合 ｜ `done` 任务结束（**必为最后一条**）。
    """

    seq: int  # 从 1 自增、全任务唯一：SSE 断线重连按它续传
    stage: str
    message: str  # 给人看的一句话，客户端不用自己拼
    data: dict = field(default_factory=dict)


@dataclass
class TaskState:
    """一个研究任务的全部状态。**只由 `ResearchOrchestrator` 持有与修改**（决策：状态单一持有者）。

    `sections` / `sources` / `by_url` / `source_seq` / `events` 都是编排过程用的内部字段，
    `to_artifact()` 只挑契约里的键落盘，它们不会出现在产物里。
    """

    task_id: str
    topic: str
    status: str = "pending"  # pending / running / succeeded / failed
    sections: list[Section] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    by_url: dict[str, Source] = field(default_factory=dict)  # 跨轮次按 URL 复用编号
    source_seq: int = 0
    process: ResearchProcess = field(default_factory=ResearchProcess)
    report: Report | None = None
    confidence: Confidence | None = None
    error: str | None = None
    events: list[Event] = field(default_factory=list)
    # 「有新事件了」的信号：SSE 端点 await 它，而不是隔一会儿查一次。每次 _emit 都会 set。
    wakeup: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_artifact(self) -> dict:
        """需求文档第 7 节的产物结构。

        `report` 走 Pydantic 的 `model_dump()`，其余是 dataclass 递归——`asdict()` 会自动
        钻进 `ResearchProcess` 里的每一条记录。
        """
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "status": self.status,
            "report": self.report.model_dump(mode="json") if self.report else None,
            "process": asdict(self.process),
            "confidence": asdict(self.confidence) if self.confidence else None,
            "error": self.error,
        }


if __name__ == "__main__":
    # 纯本地逻辑（无网络），按约定用假数据自检并打印结果
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 cp936，中文输出会乱码

    print("正常解析:", SummaryResult(section_text="一段结论", refs=[2, 5]))
    print("编号是字符串也会归成 int:", SummaryResult(section_text="x", refs=["2"]).refs)
    print("不给 refs 视为空数组:", SummaryResult(section_text="x").refs)

    for bad in ('{"refs": [1]}', '{"section_text": ""}', '{"section_text": []}'):
        try:
            SummaryResult.model_validate_json(bad)
        except Exception as exc:  # pydantic.ValidationError
            print(f"按预期拒绝 {bad} → {type(exc).__name__}")
        else:
            raise AssertionError(f"应拒绝: {bad}")

    print("只给 sufficient 也成立:", JudgeResult(sufficient=True))
    try:
        JudgeResult.model_validate_json('{"reasons": ["x"]}')
    except Exception as exc:  # pydantic.ValidationError
        print(f"按预期拒绝缺 sufficient → {type(exc).__name__}")
    else:
        raise AssertionError("缺 sufficient 应拒绝")

    report = ReportResult(
        summary="摘要", conclusions=[{"text": "结论一", "refs": ["2"]}], confidence_note="说明"
    )
    print("报告四块解析:", report.conclusions[0], "| 遗留问题默认:", report.open_questions)
    for bad in ('{"summary": "s", "confidence_note": "c"}', '{"summary": "s", "conclusions": [], "confidence_note": "c"}'):
        try:
            ReportResult.model_validate_json(bad)
        except Exception as exc:  # pydantic.ValidationError
            print(f"按预期拒绝 {bad} → {type(exc).__name__}")
        else:
            raise AssertionError(f"应拒绝: {bad}")

    # 产物结构：内部字段不落盘，且整份 artifact 必须能直接 json.dumps（落盘那一跳的前提）
    import json

    state = TaskState(task_id="demo", topic="主题")
    state.process.search_queries.append(SearchRecord(round=1, sub_question="q", queries=["q"], hits=3))
    state.process.steps.append(StepRecord(agent="plan", ok=True, elapsed_ms=1200, attempts=1))
    artifact = state.to_artifact()
    assert set(artifact) == {"task_id", "topic", "status", "report", "process", "confidence", "error"}
    assert set(artifact["process"]) == {"rounds", "elapsed_ms", "search_queries", "judge_history", "steps"}
    assert "by_url" not in artifact and "sections" not in artifact  # 内部字段不外泄
    print("空任务的产物:", json.dumps(artifact, ensure_ascii=False))

    print("models.py 自检 ok：SummaryResult / JudgeResult / ReportResult 的必填与默认值 / 产物结构")
