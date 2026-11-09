"""S6/S7 单测：mock LLM 与搜索，验证主循环状态机、sid 映射与落盘兜底。"""
import json

from research import agent as agent_mod
from research.agent import ResearchAgent
from research.models import Extraction, Judgement, Keywords, Plan, Report, SearchHit


def _fake_chat(reflect_status: list[str]):
    """按预设顺序返回反思状态；其余阶段返回固定结构。"""
    counter = {"kw": 0, "reflect": 0}

    def fake_chat_json(system, user, schema, model=None, **kwargs):
        if schema is Plan:
            return Plan(subquestions=[
                {"id": "Q1", "question": "市场规模多大？"},
                {"id": "Q2", "question": "主要竞品是谁？"},
            ])
        if schema is Keywords:
            counter["kw"] += 1
            return Keywords(queries=[f"关键词{counter['kw']}"])
        if schema is Extraction:
            return Extraction(findings=[
                {"content": "抽取出的材料", "source_ids": ["S1"], "confidence": "high"}
            ])
        if schema is Judgement:
            status = reflect_status[min(counter["reflect"], len(reflect_status) - 1)]
            counter["reflect"] += 1
            return Judgement(sub_id="", status=status, reason="测试判定")
        if schema is Report:
            return Report(
                title="测试报告",
                summary="这是摘要。",
                sections=[{"heading": "章节", "body": "正文内容 [S1]", "refs": ["S1"]}],
                key_conclusions=[
                    {"text": "有源结论", "refs": ["S1"], "confidence": "high"},
                    {"text": "无源结论", "refs": [], "confidence": "low"},
                    {"text": "幻觉引用结论", "refs": ["S999"], "confidence": "high"},
                ],
                open_questions=["待确认的问题"],
                confidence_notes=["单一来源较多"],
                info_cutoff="未知",
            )
        raise AssertionError(f"意外调用：{schema}")

    return fake_chat_json


def test_agent_loop_and_outputs(tmp_path, monkeypatch):
    # Q1/Q2 各反思两次：第一轮 need_more，第二轮 resolved → 应跑 2 轮
    monkeypatch.setattr(agent_mod, "chat_json", _fake_chat(["need_more", "need_more", "resolved", "resolved"]))
    monkeypatch.setattr(
        agent_mod,
        "web_search",
        lambda query, **kw: [
            SearchHit(url=f"https://example.com/{query}", title=f"结果{query}", summary=f"{query} 的材料", date_published="2026-08-01")
        ],
    )

    agent = ResearchAgent("测试主题", out_dir=tmp_path / "out")
    result = agent.run()

    # 主循环状态机：2 轮后全部 resolved，多轮正确终止
    assert result.rounds == 2
    assert all(s.status == "resolved" for s in agent.plan.subquestions)
    assert len(result.registry.all_sources()) >= 2
    assert result.registry.findings, "应有抽取出的材料"

    # 落盘四件套：report.md + trace.jsonl
    report_md = (result.out_dir / "report.md").read_text(encoding="utf-8")
    assert "# 测试报告" in report_md
    assert "## 来源列表" in report_md
    assert "| S1 |" in report_md
    assert "（模型推断" in report_md, "无源结论应被标注"
    assert "S999" not in report_md, "幻觉引用应被剔除"
    assert "信息截止时间：2026-08-01" in report_md, "来源最新时间应覆盖 LLM 的'未知'"

    events = [json.loads(line) for line in (result.out_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    actions = [e["action"] for e in events]
    assert actions[0] == "plan" and actions[-1] == "synthesize"
    assert "search" in actions and "read" in actions and "reflect" in actions


def test_agent_terminates_on_round_budget(tmp_path, monkeypatch):
    # 反思永远 need_more → 应在 MAX_ROUNDS 轮强制收尾（need_more 被转为 abandoned）
    monkeypatch.setattr(agent_mod, "chat_json", _fake_chat(["need_more"]))
    monkeypatch.setattr(
        agent_mod,
        "web_search",
        lambda query, **kw: [SearchHit(url=f"https://example.com/{query}", title=f"结果{query}", summary="材料")],
    )
    import research.config as config
    monkeypatch.setattr(config, "MAX_ROUNDS", 2)

    agent = ResearchAgent("测试主题", out_dir=tmp_path / "out")
    result = agent.run()

    assert result.rounds == 2
    assert all(s.status in ("resolved", "abandoned") for s in agent.plan.subquestions)
