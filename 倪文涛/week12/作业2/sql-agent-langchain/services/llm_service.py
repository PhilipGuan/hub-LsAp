import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
import json

import httpx

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

import config
from models.workflow import WorkflowFailure


class LLMService:
    def __init__(self):
        if not config.LLM_API_KEY.strip():
            raise WorkflowFailure("model", "config_error", "请先在 config.py 中填写 LLM_API_KEY。")
        # 每次问答独立持有 HTTP 连接，结束时统一关闭。
        self.client = httpx.AsyncClient()
        self.sync_client = httpx.Client()
        self.model = ChatOpenAI(
            api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL, model=config.LLM_MODEL,
            temperature=0.1, timeout=60, max_retries=0, stream_usage=False,
            use_responses_api=False, http_socket_options=(),
            http_client=self.sync_client, http_async_client=self.client,
            # 保留兼容接口的 max_tokens 参数。
            extra_body={"enable_thinking": config.LLM_ENABLE_THINKING, "max_tokens": 3000},
        )

    async def close(self):
        await self.client.aclose()
        self.sync_client.close()

    def make_agent(self, name: str, instructions: str, output_type: type[BaseModel] | None = None):
        model = self.model
        if output_type:
            instructions += "\n只返回符合以下 JSON Schema 的 JSON 对象，不要输出 Markdown：\n"
            instructions += json.dumps(output_type.model_json_schema(), ensure_ascii=False)
            model = model.with_structured_output(output_type, method="json_mode")
        # SystemMessage 保留提示词中的 JSON 花括号，不将其解释为模板变量。
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=instructions), ("human", "{input}"),
        ])
        chain = prompt | model
        if output_type is None:
            chain = chain | StrOutputParser()
        return chain.with_config(run_name=name)

    @staticmethod
    def _input(data: dict) -> dict[str, str]:
        return {"input": json.dumps(data, ensure_ascii=False, allow_nan=False)}

    async def run_agent(self, agent, input_data: dict, output_type: type[BaseModel]):
        try:
            async with asyncio.timeout(60):
                result = await agent.ainvoke(self._input(input_data))
            return output_type.model_validate(result)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise WorkflowFailure("model", "model_timeout", "模型响应超时，请稍后重新提交。") from exc
        except (ValidationError, OutputParserException) as exc:
            raise WorkflowFailure("model", "model_output_error", "模型返回的数据格式不符合约定。") from exc
        except WorkflowFailure:
            raise
        except Exception as exc:
            # 只读取异常的 HTTP 状态与底层原因，不依赖模型 SDK 的异常类型。
            if getattr(exc, "status_code", None) in (401, 403):
                raise WorkflowFailure("model", "model_auth_error", "模型认证或权限校验失败，请检查模型配置。") from exc
            if isinstance(exc.__cause__, httpx.TimeoutException):
                raise WorkflowFailure("model", "model_timeout", "模型响应超时，请稍后重新提交。") from exc
            raise WorkflowFailure("model", "model_error", "模型请求失败，请检查服务地址、模型名称和网络连接。") from exc

    async def stream_agent(self, agent, input_data: dict) -> AsyncIterator[str]:
        try:
            async with asyncio.timeout(60):
                async with aclosing(agent.astream(self._input(input_data))) as stream:
                    has_text = False
                    async for delta in stream:
                        if not delta:
                            continue
                        has_text = has_text or bool(delta.strip())
                        yield delta
                    if not has_text:
                        raise WorkflowFailure("summary", "model_output_error", "汇总模型未返回回答。")
        except asyncio.CancelledError:
            raise
        except WorkflowFailure:
            raise
        except Exception as exc:
            raise WorkflowFailure("summary", "summary_error", "回答汇总失败，已保留查询结果。") from exc
