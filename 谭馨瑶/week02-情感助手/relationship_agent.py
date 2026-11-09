# 人物关系抽取智能体
# 输入：一段描述人物关系的中文文本。
# 输出：包含 source、relation、target 的 JSON 数组。
# 规则：提取全部明确的爱慕、反感关系；没有匹配关系时返回 "未知".
import json
import os
from openai import OpenAI

client = OpenAI(
    api_key="sk-", 
    base_url="https://api.siliconflow.cn/v1",
)

# 调用模型：system 定义规则，user 提供待分析的文本
response = client.chat.completions.create(
    model="deepseek-ai/DeepSeek-V4-Flash",
    messages=[
        {
            "role": "system",
            "content": (
                '你是人物关系抽取助手。'
                '提取文本中全部明确表达的爱慕、反感关系。'
                '将表示恋爱情感的喜欢、暗恋、倾心、钟情等表达统一为“爱慕”。'
                '不喜欢、讨厌、厌恶等负面态度统一为“反感”。'
                '没有关系信息，或无法归入爱慕、反感的关系，统一为“未知”。'
                '输出格式为 JSON 数组。'
                '例如：用户输入文本为“小李喜欢小王，小王讨厌小赵。”，你应该输出：[{"source": "小李", "relation": "爱慕", "target": "小王"}, {"source": "小王", "relation": "反感", "target": "小赵"}]'
            ),
        },
        {
            "role": "user",
            "content": "小明喜欢小姚，但是小姚喜欢小王。",
        },
    ],
    stream=False,
    reasoning_effort="medium", # 思考能力。 高中低， 和 token 消耗，和 费用相关
        extra_body={"thinking": {"type": "enabled"}} # 是否打开思考
)

# 查看模型回复
print(response.choices[0].message.content)
