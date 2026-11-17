"""方案 B：openai-agents 实现（设计文档 §4.3）。

进程1 = SentimentAnalyzer Agent（output_type=SentimentResult 结构化输出）；
条件路由 = 编排层显式 if（复用 prompts.LABEL_TO_BRANCH，与 LangGraph 版等价）；
进程2 = 按分支动态配置 instructions 的 Responder Agent。

第三方模型兼容（GLM/DeepSeek 等）：
- 显式使用 OpenAIChatCompletionsModel——兼容端点只支持 Chat Completions，
  不支持 OpenAI Responses API（否则 404 /v4/responses）；
- client「每次调用创建、用完即关」：AsyncOpenAI 绑定创建时的事件循环，
  模块级单例在多事件循环场景（pytest / TestClient）会触发
  "Event loop is closed"，每次独立创建并在退出时关闭，
  生产（uvicorn 单循环）与测试均安全。
"""

import time

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from config import MODEL_NAME, OPENAI_API_KEY, OPENAI_BASE_URL, validate_settings
from prompts import BRANCH_PROMPTS, LABEL_TO_BRANCH, SENTIMENT_SYSTEM_PROMPT
from schemas import PipelineResult, SentimentResult

# 非 OpenAI 端点无法上传 trace，关闭以免报错（纯进程级开关，导入期设置安全）
set_tracing_disabled(True)


async def run_pipeline(text: str) -> PipelineResult:
    validate_settings()  # 配置缺失时给出明确报错（设计文档 §7）
    start = time.perf_counter()

    async with AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY) as client:
        model = OpenAIChatCompletionsModel(model=MODEL_NAME, openai_client=client)

        # ---- 进程1：情感识别 Agent（结构化输出） ----
        # 分类任务 temperature=0，保证识别确定性（避免边界样例判定抖动）
        s = await Runner.run(
            Agent(
                name="SentimentAnalyzer",
                instructions=SENTIMENT_SYSTEM_PROMPT,
                output_type=SentimentResult,
                model=model,
                model_settings=ModelSettings(temperature=0),
            ),
            text,
        )
        sentiment: SentimentResult = s.final_output

        # ---- 条件路由：label → branch（幻觉兜底同方案 A） ----
        branch = LABEL_TO_BRANCH.get(sentiment.label, "neutral_chat")

        # ---- 进程2：按情感分支动态配置 instructions ----
        r = await Runner.run(
            Agent(
                name="Responder",
                instructions=BRANCH_PROMPTS[branch],
                model=model,
                model_settings=ModelSettings(temperature=0.3),
            ),
            text,
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return PipelineResult(
        user_input=text,
        sentiment=sentiment,
        reply=r.final_output,
        branch=branch,
        engine="openai-agents",
        elapsed_ms=elapsed_ms,
    )
