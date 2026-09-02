# 输入：小明喜欢小姚，但是小姚喜欢小王。
# 输出：人物关系图谱

# [
#     {
#         "source": "小明",
#         "relation": "爱慕",
#         "target": "小姚"
#     }
# ]
from openai import OpenAI
import json

client = OpenAI(
    api_key = "sk-e924eeaaxxxabu28bed754",
    base_url = "https://api.deepseek.com"
)

def safe_json_parse(text:str)->dict|list|None:
    '''
    安全解析json文本,处理可能的异常
    '''
    if not text or not text.strip():
        print("input text is invalid")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"json 解析失败：{e}")
        #尝试修复常见格式问题,删除首尾空格，删除首尾注释标记，再去除首尾空格
        cleaned = text.strip().removeprefix("'''json").removeprefix("'''").removesuffix("'''").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"json 解析失败：{e}")
            return None
    except Exception as e:
        print(f"JSON解析失败: {e}")
        return None


SYS_PROMPT = '''
你是一个关系图谱生成器。请根据用户输入的文本，提取出人物之间的关系，并以JSON格式输出。
每个关系应包含以下字段：
- source:
- relation: 
- target: 
'''

USER_PROMPT = "小明喜欢小姚，但是小姚喜欢小王。请帮我生成人物关系图谱。"

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role":"system", "content": SYS_PROMPT},
        {"role":"user", "content": USER_PROMPT},
    ],
    response_format = {"type":"json_object"},
    temperature = 0.0
)

msg = response.choices[0].message.content
result = safe_json_parse(msg)

if result:
    items = result if isinstance(result,list) else result.get("items")
    print(f"共提取到{len(items)}条人物图谱：\n")

    for i,item in enumerate(items):
        print(f"第{i+1}条人物图谱：")
        print(f"source:{item.get("source","")}")
        print(f"relation:{item.get("relation","")}")
        print(f"target:{item.get("target","")}")
        print("\n")
