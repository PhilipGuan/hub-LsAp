from pyexpat.errors import messages
import re
from unittest import result
from urllib import response

from openai import OpenAI
import json

from sympy import content

client = OpenAI(
    api_key="sk-ws-H.EDPxxxabuxxxxxxxxnU",
    base_url="https://ws-09qg8sou349yp1mg.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)

def clean_json(text: str) -> str:
    # 匹配 ```json ... ``` 或 ``` ... ``` 或纯 JSON
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    match = re.search(pattern, text)
    if match:
        return match.group(1).strip()
    return text.strip()

def safe_json_parse(text: str) -> dict | list | None:
    if not text or not text.strip():
        print("模型返回为空")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"json解析失败：{e}")
        print("尝试清理文本")
        cleaned = clean_json(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"json解析失败：{e}")
            return None
        
system_prompt = """
从用户的商品描述中提取人物关系列表，以 JSON 数组格式输出,如果无人物关系，JSON输出为[]。
每个人物关系包含：source（主体对象）、relation（关系）、target（目标对象）。

输入示例：
小明喜欢小姚，但是小姚喜欢小王。

JSON 输出示例：
[
    {
        "source": "小明",
        "relation": "爱慕",
        "target": "小姚"
    },
    {
        "source": "小姚",
        "relation": "爱慕",
        "target": "小王"
    }
]
"""

user_prompt = """
小王讨厌小明，小明不讨厌小张，小张喜欢小王
"""

response = client.chat.completions.create(
    model = "qwen-flash",
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    response_format={"type": "json_object"},
    max_tokens = 600,
    temperature = 0.0
)

content = response.choices[0].message.content
result = safe_json_parse(content)

if result:
    items = result if isinstance(result, list) else result.get("items", [result])
    for item in items:
        source = item.get("source", "")
        relation = item.get("relation", "")
        target = item.get("target", "")
        print(f"{source}-->{relation}-->{target}")
    print(f"\n原始输出:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
else:
    print("无人物关系")

