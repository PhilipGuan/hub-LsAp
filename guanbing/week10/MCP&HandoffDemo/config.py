"""统一配置层：环境变量 → DeepSeek 接入 → Agents SDK 全局设置 → MCP server 参数。

⚠️ 不变量（拆文件后最容易踩的坑）：
    任何需要 `import agents` 的模块，都必须先 import config。
    原因：Agents SDK 是「运行时」才读 OPENAI_API_KEY 和 set_default_openai_api 的全局开关，
    而配置必须在它读之前就位。Python 导入一个模块时会先把它整个执行完，
    所以只要 config 是 import 链的第一环，顺序就一定正确。

    main.py 里 `from config import MODEL, SERVER_PARAMS` 写在第一行，就是为了守住这条不变量——
    你不能「忘记」import config，因为 MODEL 正是从它那里来的。

对照物：fin_triage_example/main.py 里这段代码是内联在文件顶部的。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------- 1. 找 .env（从本文件所在目录逐级向上）----------

_HERE = Path(__file__).resolve().parent

for _p in [_HERE, *_HERE.parents]:
    if (_p / ".env").exists():
        load_dotenv(_p / ".env", override=True)
        break
else:
    # 注意：这里只打印「运行时」的搜索起点，不在源码里硬编码任何机器相关的路径。
    # 报错信息必须自解释，但不该把开发者的本地目录结构固化进仓库。
    raise RuntimeError(
        f"没找到 .env。已从 {_HERE} 逐级向上查找到文件系统根目录。\n"
        "请在项目根目录创建 .env，并至少写入一行：\n"
        "    DEEPSEEK_API_KEY=你的密钥"
    )

# ---------- 2. 把 DeepSeek 的 Key 伪装成 OpenAI 的 ----------

os.environ["OPENAI_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
os.environ["OPENAI_BASE_URL"] = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ---------- 3. Agents SDK 的两个全局开关（必须在导入后立刻设置）----------

from agents import set_default_openai_api, set_tracing_disabled  # noqa: E402

set_default_openai_api("chat_completions")  # 国内模型只有 Chat Completions
set_tracing_disabled(True)  # Key 不是 OpenAI 的，必须关掉 tracing

# ---------- 4. MCP server 启动参数 ----------

SERVER_PARAMS = {"command": "npx", "args": ["-y", "github:CohenD/fin-data-mcp"]}
