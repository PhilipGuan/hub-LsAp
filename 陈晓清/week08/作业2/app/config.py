"""环境变量集中读取。所有配置只在这里出现一次，业务代码不得再读 os.environ。"""

import os

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-flash")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")
# 博查检索端点：属于「端点走环境变量」，不写死在 tools/search.py 里
BOCHA_BASE_URL = os.getenv("BOCHA_BASE_URL", "https://api.bocha.cn/v1/web-search")

# 每轮每个子问题的检索条数（需求文档 5.3）：首轮铺开广度，补检轮聚焦缺失角度
SEARCH_COUNT_INITIAL = int(os.getenv("SEARCH_COUNT_INITIAL", "10"))
SEARCH_COUNT_FOLLOWUP = int(os.getenv("SEARCH_COUNT_FOLLOWUP", "5"))

# 规划阶段子问题的条数上下限，与 app/prompts/plan_system.tmpl 里的硬性要求一致：
# 提示词负责让模型照着说，程序侧只负责收口（超 5 截断、不足 2 条回退原主题）。
MAX_SUB_QUESTIONS = int(os.getenv("MAX_SUB_QUESTIONS", "5"))
MIN_SUB_QUESTIONS = int(os.getenv("MIN_SUB_QUESTIONS", "2"))

# 喂给 SummaryAgent 的每条来源摘要的截断长度（需求文档 5.4）：每次调用只喂「一个问题 +
# 它的全部来源」，截断是防单次上下文爆掉的唯一手段。
SOURCE_SUMMARY_MAX_CHARS = int(os.getenv("SOURCE_SUMMARY_MAX_CHARS", "800"))

# 单轮补检的子问题条数上限（需求文档 6.1 说「2~3 个」）：直接影响下一轮的成本，
# 所以程序侧兜一道，不指望模型每次都听话。
MAX_NEW_SUB_QUESTIONS = int(os.getenv("MAX_NEW_SUB_QUESTIONS", "3"))

# 总轮次上限（决策 2）：首轮 + 最多 2 次补检。这是**唯一的成本边界**，不设 LLM 调用熔断。
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))

# 并发（需求文档 6.4）：同时最多几个研究任务；单个任务内检索/总结批次的并发上限。
# 这个数字是为防 LLM 限流定的，调大前先确认配额。
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "2"))
BATCH_CONCURRENCY = int(os.getenv("BATCH_CONCURRENCY", "2"))

# 任务产物落盘目录（需求文档 6.4）：失败的任务也要写，最坏情况是一份带 error 的部分结果
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")

# 置信度公式（CLAUDE.md 决策 10）：当前口径只看**真实来源数量**——域名多样性 / 权威来源 /
# 交叉印证三项暂不参与。基线 + 每条来源一档，3 条封顶，所以当前上限是 0.90（够到「高」）。
CONFIDENCE_BASE = float(os.getenv("CONFIDENCE_BASE", "0.30"))
CONFIDENCE_PER_SOURCE = float(os.getenv("CONFIDENCE_PER_SOURCE", "0.20"))
CONFIDENCE_SOURCE_CAP = int(os.getenv("CONFIDENCE_SOURCE_CAP", "3"))
# 数值只出现 0.05 的整数倍（需求文档 6.2），渲染层直接格式化两位小数即可
CONFIDENCE_ROUNDING = float(os.getenv("CONFIDENCE_ROUNDING", "0.05"))
CONFIDENCE_LEVEL_HIGH = float(os.getenv("CONFIDENCE_LEVEL_HIGH", "0.75"))
CONFIDENCE_LEVEL_MEDIUM = float(os.getenv("CONFIDENCE_LEVEL_MEDIUM", "0.50"))


if __name__ == "__main__":
    # 自测：验证 .env 已被找到并加载（缺 .env 时 LLM_BASE_URL 为空，此处会失败）
    assert LLM_BASE_URL.startswith("https://"), f"LLM_BASE_URL 未加载: {LLM_BASE_URL!r}"
    assert LLM_MODEL, "LLM_MODEL 为空"
    assert LLM_API_KEY, "LLM_API_KEY 为空"
    assert isinstance(BOCHA_API_KEY, str)
    print("config.py self-check ok")
