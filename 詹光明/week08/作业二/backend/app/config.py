"""全局配置：从 .env / 环境变量读取，未设置时用这里的默认值。

运行 `python -m app.config` 可打印当前配置摘要（API Key 已脱敏）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/ -> 项目根（作业二/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent

# 两处都找：项目根放主 .env（与 .env.example 同级），backend/ 下可选放本地覆盖。
# 不使用相对路径，避免依赖启动时的 CWD。
_ENV_FILES = tuple(
    str(path) for path in (_BACKEND_DIR / ".env", _PROJECT_ROOT / ".env") if path.is_file()
)

# 反爬站点：这些域名直接不抓，降级用搜索摘要。
# 微信公众号 / 知乎 / 小红书等对非浏览器请求基本都返回验证页或空壳。
DEFAULT_BLOCKED_DOMAINS: tuple[str, ...] = (
    "mp.weixin.qq.com",
    "zhihu.com",
    "xiaohongshu.com",
    "weibo.com",
    "douban.com",
    "baijiahao.baidu.com",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 博查搜索 ───────────────────────────────────────────────
    bocha_api_key: str = ""
    bocha_base_url: str = "https://api.bocha.cn/v1/web-search"
    # 两次请求之间的最小间隔，避免触发 429
    bocha_min_interval_s: float = 1.0
    bocha_max_retries: int = 3
    bocha_timeout_s: float = 20.0

    # ── LLM（OpenAI 兼容）──────────────────────────────────────
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 120.0
    llm_max_concurrency: int = 4
    # 这是**上限不是目标**，调大几乎不花钱（模型不会为了凑满而多写），
    # 但调小会实打实地毁数据：deepseek-flash 这类推理模型把
    # reasoning_tokens 也算进 max_tokens，实测读一篇 4500 字的网页
    # 光思考就能吃掉 3072（finish_reason=length，content 为空，抽 0 条笔记），
    # 同一个来源给到 8192 就能正常抽出 11 条。
    llm_max_tokens: int = 8192
    # 被截断后的重试预算。思考长度随正文密度走，一个固定预算挡不住尾部：
    # 实测读 Rust 调度器、量化政策分析这类长文，思考能到 1.5~2.3 万字
    # （约 1.3 万 token），8192 照样吃满，该来源被整条丢掉。重试只在
    # 真被截断时发生，常态下不产生任何额外开销；接口接受最大 65536。
    llm_retry_max_tokens: int = 32768
    llm_temperature: float = 0.2

    # ── 检索 ───────────────────────────────────────────────────
    # 每个关键词向博查要多少条（接口上限 50）
    search_count_per_query: int = 10
    # 每轮最多执行几个检索词
    queries_per_round: int = 3
    max_rounds: int = 3
    max_sources: int = 30
    freshness: str = "noLimit"

    # ── 抓取 ───────────────────────────────────────────────────
    fetch_enabled: bool = True
    fetch_timeout_s: float = 12.0
    fetch_max_concurrency: int = 5
    fetch_max_bytes: int = 2 * 1024 * 1024
    # 同一域名两次抓取之间的最小间隔（域名级礼貌）
    fetch_domain_interval_s: float = 1.0
    max_content_chars: int = 6000
    blocked_domains: tuple[str, ...] = DEFAULT_BLOCKED_DOMAINS

    # ── 置信度 ─────────────────────────────────────────────────
    # 时效性因子的衰减窗口：来源发布超过这么多天，时效分归零
    freshness_window_days: int = 365

    # ── 迭代终止 ───────────────────────────────────────────────
    # 连续这么多轮没有新增有效来源就判定停滞
    stall_patience: int = 2
    max_elapsed_s: float = 600.0

    # ── 脱敏摘要（用于打印/日志，绝不泄露 Key）─────────────────
    def summary(self) -> dict[str, object]:
        def mask(value: str) -> str:
            if not value:
                return "(未设置)"
            return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "(已设置)"

        return {
            "搜索": {
                "base_url": self.bocha_base_url,
                "api_key": mask(self.bocha_api_key),
            },
            "LLM": {
                "base_url": self.llm_base_url,
                "model": self.llm_model,
                "api_key": mask(self.llm_api_key),
            },
            "检索": {
                "max_rounds": self.max_rounds,
                "queries_per_round": self.queries_per_round,
                "count_per_query": self.search_count_per_query,
            },
            "抓取": {
                "enabled": self.fetch_enabled,
                "max_concurrency": self.fetch_max_concurrency,
                "timeout_s": self.fetch_timeout_s,
            },
        }


settings = Settings()


if __name__ == "__main__":
    import json

    print(json.dumps(settings.summary(), ensure_ascii=False, indent=2))
