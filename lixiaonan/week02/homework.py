
from openai import OpenAI

client = OpenAI(
    api_key="sk-************",
    base_url="https://api.deepseek.com",
)


#1,tools
import json
import re
import jieba.posseg as pseg

# 使用jieba分词和词性标注提取主谓宾
def extract_spo_local(text: str) -> str:
    """
    使用jieba分词和词性标注提取主谓宾（简单版本）

    Args:
        text: 输入句子

    Returns:
        output:返回结果
    """
    words = pseg.cut(text)

    subject, predicate, object_ = None, None, None

    for word, flag in words:
        # 主语：名词/人名/代词
        if flag in ['nr', 'n', 'r'] and subject is None:
            subject = word
        # 谓语：动词
        elif flag.startswith('v') and predicate is None:
            predicate = word
        # 宾语：名词/人名
        elif flag in ['nr', 'n'] and subject != word and object_ is None:
            object_ = word
    output = "source为:{source}, relation为:{relation}, target为:{target}".format(
        source=subject, relation=predicate, target=object_
    )
    return output

#获取所有实体关系
def get_relation_func(text:str)-> str:
    """
    """
    sentences = re.split('[，,、。；;！!？?]', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    result = ""
    for sentence in sentences:
        result = result + extract_spo_local(sentence) + "\n"

    return result

text = "小明喜欢小姚，小姚喜欢小王"
get_relation_func(text)

TOOLS = [{
        "type": "function",
        "function": {
            "name": "get_relation_func",
            "description": "根据文本输入解析出关系图谱数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "输入文本内容",
                    }
                },
                "required": ["text"]
            },
        },
    }]

# 工具名 → 本地函数映射
FUNCTION_MAP = {
    "get_relation_func": get_relation_func
}

def run_tool_call(tc) -> str:
    """执行一次工具调用，返回结果字符串。"""
    name = tc.function.name
    args = json.loads(tc.function.arguments)
    print(f"    → 调用工具: {name}({json.dumps(args, ensure_ascii=False)})")
    result = FUNCTION_MAP[name](**args)
    print(f"    ← 结果: {result}")
    return result

system_prompt = """
用户会提供一些人物关系的描述，请从中解析出 "source"、"relation"、"target" 并以 JSON 格式输出。
"""
user_prompt = "小明喜欢小姚，小姚喜欢小王"

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    tools=TOOLS,
    temperature=0.0,
)

choice = response.choices[0]
msg = choice.message

# 模型可能直接回复（不需要工具），也可能发起工具调用
if msg.tool_calls:
    for tc in msg.tool_calls:
        result = run_tool_call(tc)
        messages.append(msg)                     # 保留 assistant 的 tool_calls
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })

    # 把工具结果发回模型，让其生成最终回复
    final = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
        tools=TOOLS,
        temperature=0.0,
    )
    print(f"\n最终回复: {final.choices[0].message.content}")
else:
    print(f"直接回复: {msg.content}")

#2, json mode
system_prompt = """
用户会提供一些人物关系的描述，请从中解析出 "source"、"relation"、"target" 并以 JSON 格式输出。

输入示例：大王讨厌大李，大赵信任大孙

JSON 输出示例：
{
    "source":"大王"
    , "relation":"讨厌"
    , "target":"大李"
}
, {
    "source":"大赵"
    , "relation":"信任"
    , "target":"大孙"
}
"""
user_prompt = "小明喜欢小姚，小姚喜欢小王"

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt},
]

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    response_format={"type": "json_object"},
    max_tokens=200,
    temperature=0.0
)

content = response.choices[0].message.content
print(content)

