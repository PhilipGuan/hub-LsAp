# ============================================================
# LangChain 入门教程 02：模型的「工具调用」(Tool Calling)
# 核心概念：让大模型不只会「聊天」，还会「用工具」——查天气、算数学、查数据库...
# 执行流程 = 让模型做3件事：
#   Step 1. 理解用户问题 → 判断该调用什么工具 + 传什么参数
#   Step 2. 我们在本地真正运行工具，拿到结果
#   Step 3. 把工具结果丢回模型 → 让它汇总成自然语言回答
# ============================================================

# ---------- 模块零：从 .env 加载配置（隐藏 API Key） ----------
import os
from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)
print(f"✅ 已加载 .env 配置文件: {dotenv_path}")

llm_provider = os.getenv("LLM_PROVIDER", "deepseek").lower()
if llm_provider == "deepseek":
    API_KEY  = os.getenv("DEEPSEEK_API_KEY")
    BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    print(f"🚀 当前使用模型: DeepSeek ({MODEL})")
elif llm_provider == "qwen":
    API_KEY  = os.getenv("QWEN_API_KEY")
    BASE_URL = os.getenv("QWEN_BASE_URL")
    MODEL    = os.getenv("QWEN_MODEL", "qwen-plus")
    print(f"🚀 当前使用模型: Qwen ({MODEL})")
else:
    raise ValueError(f"❌ .env 中 LLM_PROVIDER={llm_provider} 不支持")
if not API_KEY or not BASE_URL:
    raise ValueError(f"❌ 无法读取 {llm_provider.upper()} 的 API_KEY/BASE_URL")


# ---------- 模块一：导入依赖 ----------
# create_agent: 本文件未直接使用（下一课 subagent 会用到）
from langchain.agents import create_agent
# ChatOpenAI: 大模型客户端
from langchain_openai import ChatOpenAI
# 三种消息角色（虽然本文件用字典传消息，但这是标准类）
from langchain.messages import HumanMessage, AIMessage, SystemMessage
# @tool 装饰器：把一个普通的 Python 函数「升级」成大模型能调用的工具
from langchain.tools import tool


# ---------- 模块二：定义工具（核心！） ----------
# @tool 装饰器会自动读取函数的 ①函数名 ②参数类型 ③docstring，
# 把它们转换成大模型能理解的「工具描述 JSON Schema」。
# 模型就是看这些信息来判断：什么时候调用、传什么参数。
@tool
def get_weather(location: str) -> str:
    """Get the weather at a location.（获取指定城市的天气）"""
    # ⚠️ 真实环境中这里应该调用真实的天气 API（如和风天气、高德地图等）
    # 教程里先用模拟数据代替
    if location == "北京":
        return "北京下雪了，明天还是会下雪～"
    if location == "上海":
        return "上海下冰雹了，明天晴天～"
    if location == "武汉":
        return "武汉有雾霾，明天晴天～"
    if location == "Dubai":
        return "It's extremely hot in Dubai! 35°C or even higher!"

    return f"It's sunny in {location}."
    

# ---------- 模块三：初始化模型，并把工具「绑定」到模型上 ----------
model = ChatOpenAI(
    model=MODEL,        # 模型名称（来自 .env）
    base_url=BASE_URL,  # API 地址（来自 .env）
    api_key=API_KEY     # API 密钥（来自 .env）
)

# bind_tools = 「告诉模型：你有这些工具可以用哦」
# 绑定后，模型就学会了：当用户问天气时，应该调用 get_weather()
model_with_tools = model.bind_tools([get_weather])

# —— bind_tools 的两个高级用法（原代码注释里有）——
# ① tool_choice="any"   → 强制模型「必须用至少一个工具」，不许直接回答
# ② tool_choice="get_weather" → 强制模型「只能用 get_weather 这个工具」
# 常用场景：你确定用户的问题 100% 应该走某个工具流程时，减少模型决策失误
# model_with_tools = model.bind_tools([get_weather], tool_choice="any")
# model_with_tools = model.bind_tools([get_weather], tool_choice="get_weather")


# ---------- 模块四：Step 1 - 让模型判断「该调什么工具 + 传什么参数」 ----------
# 用户一次问了 3 个城市的天气，还要求「总结」
# 理想情况下模型会调用 3 次 get_weather() 工具
original_messages = [
    {"role": "user", "content": "上海和迪拜最近天气怎么样？ 今天是26年8月1日，总结天气。"}
]

# ---- 方式 A：普通（非流式）调用 ----
# invoke 后，模型不会直接回答北京的天气是啥，
# 而是返回「我要调用 get_weather 工具，参数是 location=北京」这样的指令
ai_response = model_with_tools.invoke(original_messages)
print("=" * 50)
print("🔍 Step 1A. 模型的工具调用决策（非流式输出）：")
print("=" * 50)
# ai_response.tool_calls 是一个列表，里面装了模型决定调用的所有工具
for tool_call in ai_response.tool_calls:
    print(f"  Tool(工具名):  {tool_call['name']}")
    print(f"  Args(传入参数): {tool_call['args']}")
    print()


# ---- 方式 B：流式（Streaming）调用 ----
# 上面是等模型「想完整了」一次性返回；流式是「边想边返回」，体验更像 ChatGPT
# 适合需要打字机效果的前端
# ⚠️ 重要：这里传入的是 original_messages（未被 1A 污染的原始列表）！
#    如果传了 1A 追加过 assistant[tool_calls] 的 messages，API 会报错：
#    "带 tool_calls 的 assistant 消息后面必须跟着对应 ID 的 tool 消息"
print("=" * 50)
print("🔍 Step 1B. 模型的工具调用决策（流式输出，逐块打印）：")
print("=" * 50)
for chunk in model_with_tools.stream(original_messages):
    # chunk.tool_call_chunks = 流式的工具调用分片
    for tool_chunk in chunk.tool_call_chunks:
        # 用海象运算符 := 快速判断字段是否存在 + 赋值
        if name := tool_chunk.get("name"):
            print(f"  Tool: {name}")
        if id_ := tool_chunk.get("id"):
            print(f"  ID:   {id_}")
        if args := tool_chunk.get("args"):
            print(f"  Args: {args}")
print()

# —— 构建后续流程使用的工作消息列表 ——
# 顺序必须正确：用户问题 → AI 的工具调用决策
# 后面 Step 2 会追加 tool 的执行结果，Step 3 再交给模型总结
messages = original_messages + [ai_response]


# ---------- 模块五：Step 2 - 真正执行工具，拿到结果 ----------
# ⚠️ 重要！模型只是「说要调用工具」，它自己不会真的执行 Python 函数！
# 执行工具必须在我们（开发者）这边做，然后把结果回传给模型。
# 这一步就是 大模型 ↔ 外部世界 的桥梁
print("=" * 50)
print("🛠  Step 2. 本地执行工具函数：")
print("=" * 50)
for tool_call in ai_response.tool_calls:
    # get_weather.invoke(tool_call) = 以模型给出的参数调用工具函数
    # 返回的 tool_result 是一条 ToolMessage（工具结果消息），会带上 tool_call_id
    tool_result = get_weather.invoke(tool_call)
    location = tool_call["args"].get("location", "?")
    print(f"  调用 get_weather({location}) → {tool_result.content[:40]}...")
    # 把工具结果追加到对话历史，模型下一次调用时就能看到
    messages.append(tool_result)
print()


# ---------- 模块六：Step 3 - 把工具结果丢回模型，让它生成最终自然语言回答 ----------
# 现在 messages 列表里有：
#   [用户问题] → [模型: 我要调3次工具] → [工具结果1] → [工具结果2] → [工具结果3]
# 模型看到所有工具结果后，就能像人类一样「整理成一段通顺的回答」了
print("=" * 50)
print("💬 Step 3. 模型基于工具结果生成最终回答：")
print("=" * 50)
final_response = model_with_tools.invoke(messages)
# 注：LangChain 中 .text 和 .content 都能拿到内容，.content 更标准
print(final_response.text)
