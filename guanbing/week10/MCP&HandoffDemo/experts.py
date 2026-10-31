"""四个金融专家的声明式定义 —— 纯数据，零逻辑，不 import 任何东西。

这张表是「泛化」的抓手：新增/删除一个专家 = 改这张表，不需要动 main.py 里的任何逻辑。
  - prefixes：该专家能看到哪些 MCP 工具（前缀匹配，决定 tool_filter 的结果）
  - handoff_description：写给 Triage Agent 看的路由线索
  - instructions：接手后给专家自己看的作业指导

注意：这里没有 model 字段。模型由 config.py 统一决定（MODEL），
      build_triage() 在组装 Agent 时统一注入——避免「某个专家偷偷用了别的模型」。
"""

EXPERTS: dict[str, dict[str, object]] = {
    "FX Analyst": {
        "prefixes": ("fx_",),
        "handoff_description": "法定货币汇率：最新汇率、指定日历史汇率、汇率时间序列、支持的货币清单。",
        "instructions": (
            "你是外汇分析师，只处理法定货币之间的汇率问题（如 USD/CNY、EUR/JPY）。"
            "用 fx_latest_rates 取最新汇率、fx_historical_rate 取某一天、fx_timeseries 取区间序列。"
            "必须引用工具返回的原文数字，禁止凭记忆估算汇率。"
        ),
    },
    "Crypto Analyst": {
        "prefixes": ("crypto_",),
        "handoff_description": "加密资产：BTC/ETH 等现货与永续合约价格、K线、资金费率、多空比、市场情绪指数。",
        "instructions": (
            "你是加密资产分析师，只处理加密货币问题。"
            "现货用形如 BTC-USDT 的 id，永续合约用 BTC-USDT-SWAP；"
            "用 crypto_ticker 取最新价、crypto_candles 取K线、crypto_fear_greed_index 取市场情绪。"
            "必须引用工具返回的原文数字。"
        ),
    },
    "Market Analyst": {
        "prefixes": ("yahoo_", "cboe_"),
        "handoff_description": "股指与商品行情：纳斯达克/标普/道指等指数、黄金、原油、白银，以及个股与期权。",
        "instructions": (
            "你是股指与商品行情分析师。用 yahoo_quote / yahoo_chart 取行情，符号要写对："
            "纳斯达克综合指数 ^IXIC、标普500 ^GSPC、道琼斯 ^DJI；"
            "黄金期货 GC=F、WTI 原油 CL=F、布伦特原油 BZ=F、白银 SI=F。"
            "如果 Yahoo 被限流（429），改用 cboe_quote / cboe_history 作为备胎。"
            "必须引用工具返回的原文数字。"
        ),
    },
    "Macro Analyst": {
        "prefixes": ("worldbank_", "oecd_", "dbnomics_"),
        "handoff_description": "宏观经济数据：各国 GDP、通胀、失业率、人口等长期宏观指标。",
        "instructions": (
            "你是宏观分析师。先用 worldbank_search_indicators / dbnomics_search 找到指标代码，"
            "再用 worldbank_indicator / dbnomics_series 取数。"
            "回答时必须说明数据对应的年份与国家，禁止把不同年份的数据混为一谈。"
        ),
    },
}
