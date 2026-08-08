# ============================================================
# LangChain 入门教程 03：子智能体（Sub-Agent / Multi-Agent）
#
# 核心概念：一个「大总管 Agent」(Master) 负责接需求、拆解任务，
#          需要具体信息时，把任务分派给专门领域的「小弟 Agent」(Sub)。
#
# 生活类比：
#   你 = 用户（问"北京天气怎么样？"）
#   Master Agent = 你的助理（听懂问题，知道该派"天气专员"去查）
#   Weather Agent = 天气专员（专门负责整理天气信息的子智能体）
#
# 执行流：
#   用户 → Master Agent 决策："需要调用天气工具（即子 Agent）"
#       → 调用 get_weather_agent("北京")
#           → Weather Sub-Agent 整理天气数据
#       ← 返回天气报告
#   Master Agent 汇总结果 → 用户
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
from datetime import datetime              # 取当前日期，用于拼入系统提示词
from langchain.agents import create_agent  # LangChain 提供的「快速造 Agent」函数
from langchain.tools import tool           # 把普通 Python 函数升级为「工具」的装饰器
from langchain_openai import ChatOpenAI    # 大模型客户端


# ---------- 模块二：初始化大模型客户端 ----------
model = ChatOpenAI(
    model=MODEL,        # 模型名称（来自 .env）
    base_url=BASE_URL,  # API 地址（来自 .env）
    api_key=API_KEY     # API 密钥（来自 .env）
)


# ---------- 模块三：先创建 Weather Sub-Agent（子智能体） ----------
# ⚠️ 注意定义顺序！必须先有 weather_agent，get_weather_agent 工具里才能引用它
#
# create_agent 的核心参数：
#   model         → 用哪个大模型做决策
#   system_prompt → 给这个智能体的「角色设定」和「工作规范」
#   tools         → 它能调用哪些工具（这个子 agent 只做纯文本整理，所以没传工具）
weather_agent = create_agent(
    model=model,
    # 这个子 agent 的职责：把天气内容整理成「日期、城市、天气」的固定格式
    system_prompt=(
        f"今天的日期是 {datetime.now().strftime('%Y-%m-%d')}。" ##now容易和下文用户提问的其他时间冲突或对不上
        "你是一位天气信息整理专员，请将天气内容整理并汇总为如下格式："
        "【日期】、【城市】、【天气情况】。"
        "请用中文给出简洁的回答。"
    ),
)


# ---------- 模块四：把 Weather Sub-Agent 包装成「工具」供 Master 调用 ----------
# @tool 装饰器会让 Master Agent 能看到这个工具的「函数名+参数+说明」，
# 从而知道："当用户问天气时，我应该调用这个工具"。
@tool
def get_weather_agent(city: str) -> str:
    """
    Get weather information for a given city.
    （查询指定城市的天气信息。输入参数 city = 城市名称，如 "北京"。）
    """
    # 构造子 agent 要处理的查询
    # （教程里先用模拟数据；真实生产环境中这里应先调真实 API 再交给子 agent 整理）
    query = f"请整理 {city} 的天气信息：现在天气情况是'It's always sunny in {city}!'（永远是晴天）。"

    # 把查询扔给 Weather Sub-Agent，它会按 system_prompt 整理成固定格式
    # create_agent 返回的是 LangGraph，其标准调用方式是 .invoke({"messages": [...]})
    result = weather_agent.invoke({
        "messages": [{"role": "user", "content": query}]
    })

    # result["messages"] 是整个对话历史的消息列表；
    # [-1] 取「最后一条消息」，也就是子 Agent 最新的回复
    # 再用 .content 取出纯文本内容
    return result["messages"][-1].content


# ---------- 模块五：创建 Master Agent（主智能体，负责总调度） ----------
# 它拥有一个工具：get_weather_agent（就是上面那个子智能体包装成的工具）
# 当用户提问时，它会自己判断：
#   ① 直接回答（如果问题很简单，比如"你好"）
#   ② 或调用 get_weather_agent 工具（如果是问天气）
master_agent = create_agent(
    model=model,
    tools=[get_weather_agent],     # 可调用工具列表 = [包装成工具的子智能体]
    system_prompt="You are a helpful assistant. 你是一位乐于助人的助手，请用中文回答用户的问题。",
)


# ---------- 模块六：运行！让 Master Agent 处理用户请求 ----------
# 用户说："北京天气怎么样？"
# Master Agent 会识别 → 这是天气问题 → 调用 get_weather_agent(city="北京")
#                   → 拿到子 Agent 结果后 → 整理成最终回答
print()
print("=" * 60)
user_query = 'How is the weather in London tomorrow?'
print(f"💬 用户问题：{user_query}")
print("=" * 60)
result = master_agent.invoke(
    {"messages": [{"role": "user", "content": user_query}]}
)

# result["messages"] 是 Master Agent 完整对话历史（含它调用工具的中间过程）
# [-1] = 最后一条消息，也就是 Master Agent 给用户的最终回答
print()
print("=" * 60)
print("🎯 Master Agent 最终回答：")
print("=" * 60)
print(result["messages"][-1].content)
