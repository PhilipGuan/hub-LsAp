"""第 3 步：根据问题和 schema 生成 SQL。"""
import re

from openai import OpenAI

from prompts import GEN_PROMPT


def clean_sql(text: str) -> str:
    """去掉 markdown 代码围栏和多余空白。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:sql)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def generate_sql(question: str, parser, tables: list[str],
                 client: OpenAI, model: str = "qwen-max") -> str:
    """生成一条 SELECT 语句。"""
    schema = parser.get_schema_text(tables)

    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": GEN_PROMPT.format(schema=schema, question=question),
        }],
        temperature=0,   # 降低随机性
    )
    return clean_sql(resp.choices[0].message.content)