from agents import Agent,Runner
from dotenv import load_dotenv
import json
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)
import os
load_dotenv()
model_id =os.getenv('OPENAI_MODEL_ID')
agent = Agent(
    name = "文本助手",
    instructions="""用户输入文本，如果是中文则翻译为英文，如果是非中文则翻译为中文，输出严格按照以下json格式：
    {
        "query_input": "",
        "language": "",
        "result": ""
    }
    """,
    model=model_id
)

user_input = input("请输入文本")

result = Runner.run_sync(agent,user_input)

raw = result.final_output
if raw.strip().startswith("```"):
    raw = raw.strip().split("\n", 1)[1].rsplit("```", 1)[0]
result = json.loads(raw)
print(raw)
