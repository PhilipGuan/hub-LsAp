"""第 2 步：根据问题选择需要用到的表。"""
import json
import re

from openai import OpenAI

from prompts import SELECT_PROMPT


def select_tables(question: str, parser, client: OpenAI,
                  model: str = "qwen-max") -> list[str]:
    """让 LLM 从所有表里挑 1~4 张；解析失败时退化为前 3 张。"""
    schema_brief = parser.get_schema_brief()

    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": SELECT_PROMPT.format(
                schema_brief=schema_brief, question=question
            ),
        }],
        temperature=0,
    )
    text = resp.choices[0].message.content or ""

    # 模型可能带 ```json 围栏或解释，抠出第一个 {...}
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return parser.table_names[:3]

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return parser.table_names[:3]

    tables = [
        t for t in data.get("tables", [])
        if t in parser.table_names
    ]
    return tables or parser.table_names[:3]