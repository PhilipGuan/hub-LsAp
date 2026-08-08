# ============================================================
# LangChain 入门教程 01：调用大语言模型
# 功能演示：如何连接模型、发送消息、构建多轮对话
# ============================================================

# ---------- 模块零：从 .env 加载配置（隐藏 API Key） ----------
# os: 读取环境变量
# pathlib: 处理文件路径（跨平台，不会出错）
# load_dotenv: 把 .env 文件里的配置加载到环境变量中
import os
from pathlib import Path
from dotenv import load_dotenv

# 定位 .env 文件的位置
# 当前脚本在:   .../Week03-课程代码/01_langchain教程/01_调用模型.py
# .env 文件在:  .../Week03-课程代码/.env
# 所以需要从当前脚本往上走一层目录去找
dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=dotenv_path)
print(f"✅ .env loaded from the path as follows: {dotenv_path}")

# 读取默认使用的模型供应商（deepseek 或 qwen），在 .env 的 LLM_PROVIDER 中配置
llm_provider = os.getenv("LLM_PROVIDER", "deepseek").lower()

# 根据 LLM_PROVIDER 读取对应的配置
if llm_provider == "deepseek":
    API_KEY  = os.getenv("DEEPSEEK_API_KEY")
    BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
    MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    print(f"🚀 Model selected: DeepSeek ({MODEL})")
elif llm_provider == "qwen":
    API_KEY  = os.getenv("QWEN_API_KEY")
    BASE_URL = os.getenv("QWEN_BASE_URL")
    MODEL    = os.getenv("QWEN_MODEL", "qwen-plus")
    print(f"🚀 Model selected: Qwen ({MODEL})")
else:
    raise ValueError(f"❌ .env 中 LLM_PROVIDER={llm_provider} 不支持，请改为 deepseek 或 qwen")

# 安全检查：确保 API Key 没有遗漏（防止配了 .env 但忘写 Key 的情况）
if not API_KEY or not BASE_URL:
    raise ValueError(f"❌ 无法从 .env 中读取 {llm_provider.upper()}_API_KEY 或 {llm_provider.upper()}_BASE_URL，请检查配置")


# ---------- 模块一：导入依赖 ----------
# ChatOpenAI: 连接兼容 OpenAI 格式的大模型客户端（支持 Qwen、DeepSeek 等）
from langchain_openai import ChatOpenAI
# HumanMessage: 表示「用户说的话」的消息对象
# AIMessage:   表示「AI 说的话」的消息对象
from langchain_core.messages import HumanMessage, AIMessage


# ---------- 模块二：初始化大模型客户端 ----------
# 用从 .env 读取到的配置来创建模型客户端（不再硬编码 API Key！）
llm = ChatOpenAI(
    model=MODEL,       # 模型名称（来自 .env）
    base_url=BASE_URL, # API 接口地址（来自 .env）
    api_key=API_KEY    # API 密钥（来自 .env，安全不泄露）
)


# ---------- 模块三：基础调用方式 ----------
# 方式 1：最简单的调用 —— 直接传字符串
# LangChain 会自动把字符串包装成「用户消息」

human_input = "Please explain LangChain and its core features and benefits in plain language."
response = llm.invoke(human_input)    # invoke = 调用模型，参数就是你问的问题
print(response.content)          # response 是对象，用 .content 取出 AI 返回的文本

# 方式 2：手动构造消息对象（更灵活，可指定用户名）
msg = HumanMessage(content=human_input)   # content=消息内容，name=说话人
messages = [msg, msg]                              # 列表可以装多条消息
response = llm.invoke(messages)                    # 把多条消息一次性发给模型
print(response.content)


# ---------- 模块四：构建多轮对话历史 ----------
# 要点：模型本身是「健忘」的，每次调用都要把之前的对话全部传过去，
#      模型才能理解上下文，像真的在「聊天」一样。
messages = [
    # 第 1 条消息：AI 问的问题
    AIMessage(content=f"{human_input}", name="Model")
]
# 用 append() 逐条追加后续对话
messages.append(HumanMessage(content=f"Yes, that's right.", name="Lance"))
messages.append(AIMessage(content=f"Great, what would you like to learn about.", name="Model"))
messages.append(HumanMessage(content=f"LangChain application examples in AI native and Agentic apps.", name="Lance"))

# pretty_print() = 美化打印
# 会用不同的颜色/格式区分 AI 和用户的消息，方便调试时查看
for m in messages:
    m.pretty_print()
