"""第 7 步：把查询结果汇总成自然语言。"""
import json

from openai import OpenAI

from prompts import SUMMARY_PROMPT


def summarize(question: str, sql: str, rows: list[dict],
              client: OpenAI, model: str = "qwen-max",
              max_preview: int = 20) -> str:
    """把 SQL 结果翻译成中文结论。"""
    if not rows:
        return "没有查询到符合条件的数据。"

    preview = rows[:max_preview]
    result_text = json.dumps(preview, ensure_ascii=False, default=str)

    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": SUMMARY_PROMPT.format(
                question=question,
                sql=sql,
                result=result_text,
                total=len(rows),
                n=len(preview),
            ),
        }],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()