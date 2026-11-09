"""全链路冒烟：直接跑研究引擎，不起服务。

用法：
    python3 scripts/smoke.py "研究主题"
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.engine import DeepResearch, new_record  # noqa: E402
from backend import storage  # noqa: E402


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "开源大模型与闭源大模型的企业选型对比"
    record = new_record(topic)

    async def on_progress(record_dict: dict) -> None:
        storage.save_record(record_dict)
        status = record_dict.get("status", "")
        rounds = len(record_dict.get("rounds", []))
        sources = len(record_dict.get("sources", []))
        print(f"[progress] status={status} rounds={rounds} sources={sources}")

    print(f"开始研究：{topic}")
    result = await DeepResearch(record, on_progress).run()

    print(f"\n===== 结果（status={result.status}） =====")
    if result.status == "failed":
        print("错误：", result.error)
        sys.exit(1)
    print("报告标题：", result.report.title)
    print("摘要：", result.report.summary[:200])
    print("关键结论：")
    for c in result.report.key_conclusions:
        print(f"  - [{c.confidence}] {c.text}（来源: {','.join(c.source_ids) or '无'}）")
    print("遗留问题：")
    for q in result.report.open_questions:
        print("  -", q)
    print(f"来源数：{len(result.sources)}  迭代轮数：{result.iterations}")
    print("信息截止：", result.confidence.info_cutoff)
    print(f"\n完整记录已落盘：data/{result.research_id}.json")
    print(json.dumps(result.rounds[-1].model_dump(), ensure_ascii=False, indent=2) if result.rounds else "")


if __name__ == "__main__":
    asyncio.run(main())
