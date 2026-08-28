from openai import OpenAI






SYS_PROMPT = '''
你是一个关系图谱生成器。请根据用户输入的文本，提取出人物之间的关系，并以JSON格式输出。
每个关系应包含以下字段：
- source:
- relation: 
- target: 
'''

USER_PROMPT = "小明喜欢小姚，但是小姚喜欢小王,小王喜欢小明。请帮我生成人物关系图谱。"

client = OpenAI(
    api_key="sk-dc8c095735cxxxabu277f19b16136",
    base_url="https://api.deepseek.com")



response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role":"system", "content": SYS_PROMPT},
        {"role":"user", "content": USER_PROMPT},
    ],
    # response_format = {"type":"json_object"},
    response_format = {
    "type": "json_object",
    "schema": {
      "type": "object",
      "properties": {
        "source": {"type": "string"},
        "relation": {"type": "string"},
        "target": {"type": "string"}
      },
      "required": ["sentiment", "confidence"]
    }
  },
    temperature = 0.0
)
print(response)


print(response.choices[0].message.content)

