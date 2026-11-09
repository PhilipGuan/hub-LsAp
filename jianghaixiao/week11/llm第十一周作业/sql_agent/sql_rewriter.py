"""第 5 步：SQL 改写（反思循环）。"""
from openai import OpenAI

from prompts import REWRITE_PROMPT
from sql_generator import clean_sql


def rewrite_sql(question: str, parser, tables: list[str],
                sql: str, error: str, history: list[dict],
                client: OpenAI, model: str = "qwen-max") -> str:
    """把错误信息回灌给模型，生成修正后的 SQL。"""
    schema = parser.get_schema_text(tables)
    history_text = "\n".join(
        f"- SQL: {h['sql']}\n  错误: {h['error']}" for h in history
    ) or "（无）"

    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": REWRITE_PROMPT.format(
                schema=schema,
                question=question,
                history=history_text,
                error=error,
            ),
        }],
        temperature=0,
    )
    return clean_sql(resp.choices[0].message.content)