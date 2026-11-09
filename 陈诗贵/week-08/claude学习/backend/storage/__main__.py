"""demo：python -m backend.storage 用样例记录落盘（无需 API key）。"""
from ..models import (
    Confidence,
    Evidence,
    ResearchRecord,
    ResearchProcess,
    SearchRound,
    Section,
    Source,
    SubQuestion,
)
from . import writer


def main() -> None:
    record = ResearchRecord(
        id="demo",
        topic="示例主题",
        summary="这是一段摘要。",
        sections=[Section(heading="背景", content="正文内容，引用 [来源0]。")],
        key_conclusions=["结论一"],
        open_questions=["遗留问题一"],
        sources=[Source(url="https://example.com", title="示例来源", site="示例站")],
        process=ResearchProcess(
            sub_questions=[SubQuestion(question="子问题", rationale="理由")],
            rounds=[
                SearchRound(
                    round_no=1,
                    purpose="首轮检索",
                    queries=["关键词"],
                    extracted=[Evidence(claim="一条结论", source_index=0)],
                )
            ],
            iterations=1,
        ),
        confidence=Confidence(level="high", cutoff="2026-09-10", notes=["说明"]),
    )
    d = writer.write(record)
    print(f"已写入：{d}")


if __name__ == "__main__":
    main()
