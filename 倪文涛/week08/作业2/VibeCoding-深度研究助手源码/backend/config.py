"""配置加载:密钥与研究循环参数,全部读自项目根目录 .env。"""
import os


def load_env(path: str = ".env") -> None:
    """零依赖的 .env 解析:KEY=VALUE,忽略注释与空行,不覆盖已有环境变量。"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


load_env()


class Config:
    """密钥与研究循环参数(全部 .env 可调)。"""

    def __init__(self) -> None:
        self.bocha_api_key = os.environ.get("BOCHA_API_KEY", "")
        self.llm_base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")
        self.llm_model = os.environ.get("LLM_MODEL", "deepseek-chat")
        self.max_rounds = int(os.environ.get("MAX_ROUNDS", 3))
        self.num_subquestions = int(os.environ.get("NUM_SUBQUESTIONS", 4))
        self.max_queries_per_round = int(os.environ.get("MAX_QUERIES_PER_ROUND", 4))
        self.search_count = int(os.environ.get("SEARCH_COUNT", 8))
        self.fetch_top_k = int(os.environ.get("FETCH_TOP_K", 3))
        self.page_char_limit = int(os.environ.get("PAGE_CHAR_LIMIT", 6000))
        self.max_pages_per_round = int(os.environ.get("MAX_PAGES_PER_ROUND", 12))
        self.overall_timeout_seconds = int(os.environ.get("OVERALL_TIMEOUT_SECONDS", 300))


CONFIG = Config()
