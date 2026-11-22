"""边界层的失败话术（D18），中英双语。

统一放在 `server/` 而不是 `sqlagent/`，是为了守住 `DESIGN.md` §4 的「核心库不动」。
约定：每条 ≤ 2 句，尽量附「你可以这样问」的示例，让用户知道下一步怎么走。

本模块只放**字符串**，不放逻辑——判定（哪个分支用哪条）在 `app.py` 里。

话术分两处（批 2 落地后定稿）：
- **边界层才知道的失败**收在本文件（模型侧不可用 / 会话过期 / 口令 / 配额）；
- **核心库自己的业务分支话术**留在核心库、不在这里覆盖（范围外 `OUT_OF_SCOPE_TEMPLATE`、
  改写到上限 `FAIL_ANSWER`、澄清超限 `CLARIFY_LIMIT_ANSWER`）——边界层只在 envelope 里
  标 `degraded`，供前端加一条非侵入式标注。

**双语**：模块级常量保持原样（= 中文，既有导入与测试依赖它们）；英文版在 `_EN` 表里，
通过 `text(name, lang)` 取用。`app.py` 按「会话语言 → `X-Lang` 请求头 → 默认 `zh`」解析后传入。
"""

from __future__ import annotations

from sqlagent.i18n import DEFAULT_LANG, normalise

# —— 不可预期失败（D6）——
# 兜底话术：用户能复述、日志能对上。trace_id 由调用方拼在响应里，这里不写死。
INTERNAL_ERROR = (
    "服务出错了，本轮没有拿到结果。请稍后再试；若反复出现，"
    "请把响应里的 trace_id 一并反馈（可在服务端日志里对到具体那一轮）。"
)

# 会话不存在 / 已过期（进程重启，或超过 §1 D8 的保留期）。
SESSION_NOT_FOUND = "会话不存在或已过期，请点击「新建会话」重新开始。"

# —— 可预期失败 · 准入与额度（D10 / D11）——
# 没带有效 cookie 就来调接口（口令页还没过）。
PASSCODE_REQUIRED = "这是需要口令的内测链接：请先在口令页输入访问口令。"
# 口令本身错了。
PASSCODE_INVALID = "口令不对，请确认后重试（口令首尾空格会被忽略）。"
# 每日全局额度（D11）。作品集链接会漂到全网，额度让最坏损失可算。
QUOTA_EXCEEDED_GLOBAL = (
    "今天的体验额度已经用完了（全站每天 200 轮），明天再来吧。"
    "你也可以在本机 clone 仓库、用自己的 Key 跑一份。"
)
# 单会话额度（D11）。额度只在「会话」这一层用完，换个会话即可继续。
QUOTA_EXCEEDED_SESSION = (
    "这个会话已经问了 20 轮了，换一个会话继续吧——点右上角「新建会话」。"
)

# —— 可预期失败 · 模型侧不可用（D6 / D11 / D12）——
# 单次请求超时（`request_timeout=30`）、429、连接失败，或主 + 兜底模型全挂。
# SDK 会先按 `max_retries` 重试，重试仍失败才走到这里：本轮没有结论，但服务本身是好的。
LLM_UNAVAILABLE = (
    "模型这会儿没能给出结果（超时或被限流），本轮先跳过。"
    "稍等一会儿再问一次通常就好了，比如「统计每个国家的客户数」。"
)

# 英文版（键与下面 `_ZH` 一一对应；`assert` 保证不漏翻）。
_EN = {
    "internal_error": (
        "Something went wrong on our side and this round produced no result. Please try again "
        "shortly; if it keeps happening, share the trace_id from the response — it maps to the "
        "exact round in the server logs."
    ),
    "session_not_found": (
        "This session doesn't exist or has expired. Click \"New session\" to start again."
    ),
    "passcode_required": (
        "This is a private preview link that requires a passcode. "
        "Please enter it on the passcode page first."
    ),
    "passcode_invalid": (
        "Wrong passcode — please check and try again (leading/trailing spaces are ignored)."
    ),
    "quota_exceeded_global": (
        "Today's demo quota is used up (200 rounds per day site-wide). Come back tomorrow, or "
        "clone the repo and run it locally with your own API key."
    ),
    "quota_exceeded_session": (
        "This session has reached 20 rounds. Start a new one — click \"New session\" at the "
        "top right."
    ),
    "llm_unavailable": (
        "The model couldn't return a result this time (timeout or rate limit), so this round was "
        "skipped. Waiting a moment and asking again usually works — e.g. \"How many customers "
        "are there in each country?\""
    ),
}

_ZH = {
    "internal_error": INTERNAL_ERROR,
    "session_not_found": SESSION_NOT_FOUND,
    "passcode_required": PASSCODE_REQUIRED,
    "passcode_invalid": PASSCODE_INVALID,
    "quota_exceeded_global": QUOTA_EXCEEDED_GLOBAL,
    "quota_exceeded_session": QUOTA_EXCEEDED_SESSION,
    "llm_unavailable": LLM_UNAVAILABLE,
}

assert set(_EN) == set(_ZH), "中英话术必须一一对应"


def text(name: str, lang: str = DEFAULT_LANG) -> str:
    """按语言取一条边界层话术；未知语言回落到默认 `zh`（与核心库 `i18n` 一致）。"""
    lang = normalise(lang)
    return _EN[name] if lang == "en" else _ZH[name]
