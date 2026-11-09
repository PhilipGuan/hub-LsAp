from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    name: str
    category: str
    message: str
    retryable: bool
    action: str
    error_class: int


# 10 类用户可见错误；error_class 与 02 §7 的分类一一对应。
ERRORS: dict[str, ErrorInfo] = {
    "E1001": ErrorInfo("E1001", "EMPTY_INPUT", "输入", "请输入一个具体的数据问题", False, "补充问题", 1),
    "E1002": ErrorInfo("E1002", "INPUT_TOO_LONG", "输入", "问题过长，请精简到 500 字以内", False, "精简问题", 1),
    "E1003": ErrorInfo("E1003", "NOT_ANALYTICS", "意图", "这个问题不需要查询数据库", False, "返回能力说明/示例", 2),
    "E1004": ErrorInfo("E1004", "OUT_OF_SCOPE", "意图", "我只能做只读查询，不能修改数据", False, "明确拒答", 2),
    "E1005": ErrorInfo("E1005", "AMBIGUOUS", "意图", "请补充时间范围或指标口径", False, "不生成 SQL；给补充建议", 3),
    "E1006": ErrorInfo("E1006", "SESSION_NOT_FOUND", "会话", "会话不存在，已新建", False, "自动创建", 1),
    "E1007": ErrorInfo("E1007", "UNSUPPORTED_ANALYSIS", "意图", "本期不做因果/预测分析，可以看趋势或对比", False, "不生成 SQL；给替代", 2),
    "E2001": ErrorInfo("E2001", "DB_NOT_FOUND", "Catalog", "数据库不存在或无法只读打开", False, "检查配置", 10),
    "E2002": ErrorInfo("E2002", "CATALOG_PARSE_FAILED", "Catalog", "数据库结构解析失败", True, "重建缓存", 10),
    "E2003": ErrorInfo("E2003", "CACHE_INVALID", "Catalog", "缓存已失效，正在重建", True, "自动重建", 10),
    "E3001": ErrorInfo("E3001", "SELECTOR_LLM_FAILED", "选表", "选表失败，已使用兜底策略", True, "关键词/全表回退", 10),
    "E3002": ErrorInfo("E3002", "NO_TABLE_SELECTED", "选表", "未能确定相关数据表", True, "回退 + 告警", 4),
    "E3003": ErrorInfo("E3003", "UNKNOWN_TABLE_SELECTED", "选表", "模型选择了不存在的表", True, "丢弃 + 回退", 4),
    "E3004": ErrorInfo("E3004", "FALLBACK_ALL_TABLES", "选表", "已使用全部表，结果可能较慢", True, "记录 fallback 率", 4),
    "E3005": ErrorInfo("E3005", "FANOUT_RISK", "选表", "检测到一对多关联，可能重复计数", False, "去重提示/告警", 9),
    "E3006": ErrorInfo("E3006", "ENTITY_UNMAPPED", "映射", "未找到中文实体对应的库内值", True, "保留原文/低置信", 4),
    "E3007": ErrorInfo("E3007", "ENTITY_AMBIGUOUS", "映射", "实体存在多个匹配值", False, "低置信并标注", 3),
    "E4001": ErrorInfo("E4001", "LLM_CALL_FAILED", "生成", "调用大模型失败，请稍后重试", True, "预算内重试/终止", 10),
    "E4002": ErrorInfo("E4002", "NO_SQL_IN_RESPONSE", "生成", "模型未能生成查询，请换个问法", True, "修复/改写", 10),
    "E4003": ErrorInfo("E4003", "CANNOT_ANSWER", "生成", "当前数据无法回答该问题", False, "说明/替代问法", 2),
    "E4004": ErrorInfo("E4004", "LLM_TIMEOUT", "生成", "模型响应超时", True, "预算内重试/E8004", 7),
    "E5001": ErrorInfo("E5001", "EMPTY_SQL", "校验", "查询为空", False, "终止/改写", 6),
    "E5002": ErrorInfo("E5002", "MULTI_STATEMENT", "校验", "只能执行一条查询", False, "拦截", 6),
    "E5003": ErrorInfo("E5003", "STATEMENT_TYPE", "校验", "只能做只读查询", False, "拦截", 6),
    "E5004": ErrorInfo("E5004", "FORBIDDEN_KEYWORD", "校验", "检测到不允许的操作", False, "拦截 + 审计", 6),
    "E5005": ErrorInfo("E5005", "SYNTAX_ERROR", "校验", "查询语法有误，系统将尝试修复", True, "改写", 6),
    "E5006": ErrorInfo("E5006", "UNKNOWN_TABLE", "校验", "未找到相关数据表，或该表不在本次查询范围", True, "改写/回退", 4),
    "E5007": ErrorInfo("E5007", "UNKNOWN_COLUMN", "校验", "未找到相关字段", True, "改写", 4),
    "E5008": ErrorInfo("E5008", "UNKNOWN_ALIAS", "校验", "查询别名有误", True, "改写", 4),
    "E5009": ErrorInfo("E5009", "EXPLAIN_FAILED", "校验", "查询无法执行，系统将尝试修复", True, "改写", 6),
    "E5010": ErrorInfo("E5010", "READ_ONLY_VIOLATION", "校验", "只允许只读查询", False, "拦截 + 告警", 6),
    "E5011": ErrorInfo("E5011", "FUNCTION_NOT_ALLOWED", "校验", "使用了不允许的函数", False, "拦截", 5),
    "E5012": ErrorInfo("E5012", "STAR_PROJECTION", "校验", "不允许使用 SELECT *，请明确列出字段", False, "拦截 + 改写", 5),
    "E5013": ErrorInfo("E5013", "GROUP_BY_MISMATCH", "校验", "聚合查询中的非聚合字段必须出现在 GROUP BY 中", True, "改写", 6),
    "E5014": ErrorInfo("E5014", "CARTESIAN_JOIN", "校验", "多表查询缺少明确的连接条件", True, "改写", 6),
    "E5015": ErrorInfo("E5015", "TOO_MANY_JOINS", "校验", "关联表过多，请缩小查询范围", False, "拦截", 7),
    "E5016": ErrorInfo("E5016", "SQL_TOO_LONG", "校验", "查询语句过长", False, "拦截", 7),
    "E6001": ErrorInfo("E6001", "QUERY_TIMEOUT", "执行", "查询超时，请缩小时间范围", True, "中断；可改写", 7),
    "E6002": ErrorInfo("E6002", "SQLITE_ERROR", "执行", "查询执行失败，系统将尝试修复", True, "改写", 10),
    "E6003": ErrorInfo("E6003", "CONNECTION_FAILED", "执行", "数据库连接失败，请检查配置", False, "终止", 10),
    "E6004": ErrorInfo("E6004", "RESULT_TRUNCATED", "执行", "结果已截断，仅展示前 100 行", False, "truncated=true", 8),
    "E6005": ErrorInfo("E6005", "RESOURCE_LIMIT", "执行", "查询过大，请缩小范围", True, "终止/降级", 7),
    "E6010": ErrorInfo("E6010", "REWRITE_NOOP", "改写", "修复未产生新查询，已停止", False, "停止改写", 6),
    "E7001": ErrorInfo("E7001", "SUMMARY_FAILED", "汇总", "结果已返回，但自然语言解释失败", True, "模板答案", 9),
    "E7002": ErrorInfo("E7002", "GROUNDING_FAILED", "质量", "答案中的数字无法核对，已改用模板", True, "模板答案", 9),
    "E7003": ErrorInfo("E7003", "EMPTY_RESULT", "质量", "没有查询到符合条件的数据", False, "固定话术 + data_until", 8),
    "E7004": ErrorInfo("E7004", "TRUNCATED_RESULT", "质量", "结果已截断，仅展示前 100 行", False, "明确提示", 8),
    "E7005": ErrorInfo("E7005", "METRIC_UNDEFINED", "质量", "当前未定义该指标", False, "说明 + 替代指标", 9),
    "E7006": ErrorInfo("E7006", "METRIC_AMBIGUOUS", "质量", "指标存在多种口径", False, "默认 + 标注；V1 澄清", 3),
    "E7007": ErrorInfo("E7007", "TIME_RANGE_UNSPECIFIED", "质量", "未指定时间范围，已使用全量数据", False, "标注时间范围", 9),
    "E7008": ErrorInfo("E7008", "SANITY_CHECK_FAILED", "质量", "结果量级/重复度异常", True, "低置信/改写/复核", 9),
    "E7009": ErrorInfo("E7009", "GOLD_SQL_FAILED", "评测", "评测 gold SQL 执行失败", False, "该题判失败", 9),
    "E8001": ErrorInfo("E8001", "LLM_CONFIG_MISSING", "系统", "未配置 API Key", False, "启动阻断", 10),
    "E8002": ErrorInfo("E8002", "LLM_RATE_LIMIT", "系统", "模型限流，请稍后重试", True, "退避一次/终止", 10),
    "E8003": ErrorInfo("E8003", "BUDGET_EXCEEDED", "系统", "本次查询超出预算，已终止", False, "终止/降级", 7),
    "E8004": ErrorInfo("E8004", "DEADLINE_EXCEEDED", "系统", "查询超时，已返回已完成部分", False, "终止", 7),
    "E8005": ErrorInfo("E8005", "INTERNAL_ERROR", "系统", "系统繁忙，请稍后重试", True, "记录 + 通用提示", 10),
    "E8006": ErrorInfo("E8006", "CONFIG_INVALID", "系统", "配置值无效，已使用默认值", False, "默认值 + 告警", 10),
    "E8007": ErrorInfo("E8007", "PII_DETECTED", "安全", "检测到敏感信息，已阻断", False, "脱敏/阻断", 5),
    "E8008": ErrorInfo("E8008", "STORAGE_FULL", "系统", "审计存储已满，正在清理", False, "清理/告警", 10),
    "E8009": ErrorInfo("E8009", "AUDIT_WRITE_FAILED", "系统", "审计暂不可用，本次回答仍有效", True, "本地日志兜底", 10),
}

# 安全红线：不进入改写重试。
REDLINE_CODES = frozenset(
    {
        "E5002",
        "E5003",
        "E5004",
        "E5010",
        "E5011",
        "E5012",
        "E8007",
    }
)


def error_info(code: str) -> ErrorInfo:
    return ERRORS.get(code, ERRORS["E8005"])


def user_message(code: str) -> str:
    return error_info(code).message


def error_class(code: str) -> int:
    return error_info(code).error_class


def error_dict(code: str, detail: str = "") -> dict:
    info = error_info(code)
    return {
        "code": info.code,
        "name": info.name,
        "category": info.category,
        "message": info.message,
        "detail": detail,
        "retryable": info.retryable,
        "action": info.action,
        "error_class": info.error_class,
    }


class AgentError(RuntimeError):
    """带稳定错误码的内部异常。"""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail or error_info(code).message
        super().__init__(f"{code}: {self.detail}")

    @property
    def info(self) -> ErrorInfo:
        return error_info(self.code)

    def to_dict(self) -> dict:
        return error_dict(self.code, self.detail)
