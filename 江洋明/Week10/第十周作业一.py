"""
第十周作业一：基于 OpenAI Agents 框架的语种识别 + 双向翻译 Agent
================================================
业务需求：
    用户输入一段文本，系统判断文本的语种，
    - 如果是中文，翻译成英文
    - 如果不是中文，翻译成中文
    最终输出 JSON: {"query_input": ..., "language": ..., "result": ...}

架构（模仿 03_基础使用.py 的多 Agent + handoff 模式）：
    user input
        ↓
    language_detect_agent   (识别语种)
        ↓
    triage_agent            (根据语种选择翻译专家)
        ├──→ zh_to_en_agent       (中文  -> 英文)
        └──→ to_zh_agent          (其它  -> 中文)
        ↓
    组装成 FinalOutput JSON 输出
"""

# ============================================================
# 1. 配置 OpenAI 兼容接口（这里用 SiliconFlow 跑开源 Qwen 模型）
# ============================================================
import os
os.environ["OPENAI_API_KEY"] = "sk-khlzqgggbrjyvlltopabudsmpmswdhbxlddbfvehormwgdn"
os.environ["OPENAI_BASE_URL"] = "https://api.siliconflow.cn/v1"

# ============================================================
# 2. 导入依赖
# ============================================================
import asyncio
import json
from pydantic import BaseModel
from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel
from agents import set_default_openai_api, set_tracing_disabled


# ============================================================
# 3. 创建异步 OpenAI 客户端
#    - base_url 指向 SiliconFlow 的兼容接口
#    - timeout 拉长，开源模型高峰期慢
#    - max_retries 失败时自动重试 2 次
# ============================================================
sf_client = AsyncOpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
    timeout=120.0,
    max_retries=2,
)


# ============================================================
# 4. 构造唯一的模型对象
#    OpenAIChatCompletionsModel 必须传对象（不是模型名字符串）
# ============================================================
sf_model = OpenAIChatCompletionsModel(
    model="Qwen/Qwen3-32B",   # SiliconFlow 上真实存在的开源模型
    openai_client=sf_client,
)


# ============================================================
# 5. 配置 SDK 全局行为
#    - set_default_openai_api("chat_completions")
#         让 SDK 使用 Chat Completions API，而不是默认的 Responses API
#    - set_tracing_disabled(True)
#         关闭 SDK 的链路追踪，避免向外部服务上报链路数据
# ============================================================
set_default_openai_api("chat_completions")
set_tracing_disabled(True)


# ============================================================
# 6. 定义最终输出 JSON 的结构（Pydantic BaseModel，方便序列化与类型校验）
# ============================================================
class FinalOutput(BaseModel):
    """最终输出给用户的 JSON 结构"""
    query_input: str   # 用户原始输入
    language: str      # 识别出来的语种
    result: str        # 翻译后的结果


# ============================================================
# 7. 语种识别 Agent
#    任务：判断输入文本的语种，仅返回语种名称
# ============================================================
language_detect_agent = Agent(
    name="Language Detect Agent",
    model=sf_model,
    instructions=(
        "你是语种识别专家。请准确判断用户输入文本的语种，"
        "只输出一个简短的语种名称（如：中文、英文、日语、法语、德语、韩语、西班牙语、俄语等），"
        "不要输出任何其它内容，也不要加标点符号。"
    ),
)


# ============================================================
# 8. 中文 -> 英文 翻译 Agent
#    任务：将中文文本翻译为自然、地道的英文
# ============================================================
zh_to_en_agent = Agent(
    name="Chinese to English Translator",
    model=sf_model,
    handoff_description="负责把中文文本翻译成英文的专家。",
    instructions=(
        "你是专业的中译英翻译官。请将用户提供的中文文本翻译为自然、地道的英文。"
        "只输出翻译结果本身，不要附加任何解释或额外文字。"
    ),
)


# ============================================================
# 9. 其它语种 -> 中文 翻译 Agent
#    任务：将非中文的外语翻译为通顺、自然的中文
# ============================================================
to_zh_agent = Agent(
    name="Other Language to Chinese Translator",
    model=sf_model,
    handoff_description="负责把非中文的外语翻译成中文的专家。",
    instructions=(
        "你是专业的外译中翻译官。请将用户提供的非中文文本翻译为通顺、自然的中文。"
        "只输出翻译结果本身，不要附加任何解释或额外文字。"
    ),
)


# ============================================================
# 10. 调度 Agent（编排入口）
#     任务：识别语种后，把请求 handoff 给对应的翻译专家
#     这里 handoffs 的使用方式与 03_基础使用.py 中 triage_agent 一致
# ============================================================
triage_agent = Agent(
    name="Translation Triage Agent",
    model=sf_model,
    instructions=(
        "你是翻译任务调度员。请严格按下面两步处理用户的输入：\n"
        "1) 先调用 'Language Detect Agent' 判断语种；\n"
        "2) 如果是中文，请交给 'Chinese to English Translator' 处理；\n"
        "3) 如果是其它语种，请交给 'Other Language to Chinese Translator' 处理。"
    ),
    handoffs=[zh_to_en_agent, to_zh_agent],
)


# ============================================================
# 11. 工具函数
# ============================================================
def normalize_language(raw: str) -> str:
    """
    把大模型输出的语种名称归一化，方便后续判断是否为中文。
    例：'Chinese' / '中文' / '汉语 / Mandarin' 都视为 '中文'。
    """
    s = raw.strip()
    lowered = s.lower()
    if ("中文" in s) or ("汉语" in s) or ("chinese" in lowered) or ("mandarin" in lowered):
        return "中文"
    return s


async def detect_language(text: str) -> str:
    """调用 language_detect_agent，识别文本语种"""
    print(f"  [1/2 语种识别] 输入: {text}")
    result = await Runner.run(language_detect_agent, text)
    language = normalize_language(result.final_output)
    print(f"  [1/2 语种识别] 结果: {language}")
    return language


async def translate_text(text: str, language: str) -> str:
    """根据语种，调用对应的翻译 Agent 执行翻译"""
    if language == "中文":
        print("  [2/2 翻译] 语种为中文 -> 调用 zh_to_en_agent (中 -> 英)")
        result = await Runner.run(zh_to_en_agent, text)
    else:
        print(f"  [2/2 翻译] 语种为 {language} -> 调用 to_zh_agent (外 -> 中)")
        result = await Runner.run(to_zh_agent, text)
    translated = result.final_output.strip()
    print(f"  [2/2 翻译] 结果: {translated}")
    return translated


async def process(query: str) -> dict:
    """单条完整流程：识别语种 -> 翻译 -> 组装 JSON"""
    print(f"\n>>> 处理用户输入: {query}")
    language = await detect_language(query)
    translated = await translate_text(query, language)
    output = FinalOutput(query_input=query, language=language, result=translated)
    return output.model_dump()


# ============================================================
# 12. 主流程：演示若干测试样例（中文 / 英文 / 法语 / 日语）
# ============================================================
async def main():
    print("=" * 60)
    print("       语种识别 + 双向翻译 Agent 系统  ")
    print("=" * 60)

    # 测试数据：覆盖中文、英文、法语、日语
    test_inputs = [
        "今天天气真好，我想去公园散步。",
        "Hello, how are you doing today?",
        "Bonjour, comment allez-vous ?",
        "こんにちは、お元気ですか？",
    ]

    for query in test_inputs:
        result = await process(query)
        print("**✅ 最终 JSON 输出:**")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())

# 运行命令：python 第十周作业一.py
