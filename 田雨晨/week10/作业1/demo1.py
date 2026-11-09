import os, json, asyncio

os.environ.setdefault("OPENAI_API_KEY", "xxx")
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled

set_default_openai_api("chat_completions")   # 国内兼容端点走 Chat Completions，不走 Responses
set_tracing_disabled(True)

MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")

agent = Agent(
    name="Translate Agent",
    model=MODEL,
    instructions="""你是语种识别与翻译助手。只输出一个 JSON 对象，不要解释、不要代码块：
{"language": "<输入文本的语言，用中文名，如 英语/日语/中文/法语>", "result": "<译文>"}
规则：language 不是中文 → result 为简体中文译文；language 是中文 → result 为英文译文。""",
)


def _parse(text: str) -> dict:
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(t)


async def handle(query: str) -> dict:
    out = await Runner.run(agent, query)
    data = _parse(out.final_output)          # 模型只给 language / result
    return {"query_input": query, **{k: data[k] for k in ("language", "result")}}


if __name__ == "__main__":
    for q in ["The quick brown fox jumps over the lazy dog.", "今天天气真好，我们去爬山吧。"]:
        print(json.dumps(asyncio.run(handle(q)), ensure_ascii=False, indent=4))