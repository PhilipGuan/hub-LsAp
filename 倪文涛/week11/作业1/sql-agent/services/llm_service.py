import asyncio
from collections.abc import AsyncIterator
import json

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from openai import AsyncOpenAI, AuthenticationError, PermissionDeniedError
from openai.types.responses import ResponseTextDeltaEvent
from pydantic import BaseModel, ValidationError

import config
from models.workflow import WorkflowFailure


class LLMService:
    def __init__(self):
        if not config.LLM_API_KEY.strip():
            raise WorkflowFailure("model", "config_error", "请先在 config.py 中填写 LLM_API_KEY。")
        self.client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL,
                                  timeout=60, max_retries=0)
        self.model = OpenAIChatCompletionsModel(model=config.LLM_MODEL, openai_client=self.client)
        self.run_config = RunConfig(tracing_disabled=True)

    async def close(self):
        await self.client.close()

    def make_agent(self, name: str, instructions: str, output_type: type[BaseModel] | None = None):
        extra_body = {"enable_thinking": config.LLM_ENABLE_THINKING}
        if output_type:
            instructions += "\n只返回符合以下 JSON Schema 的 JSON 对象，不要输出 Markdown：\n"
            instructions += json.dumps(output_type.model_json_schema(), ensure_ascii=False)
            extra_body["response_format"] = {"type": "json_object"}
        return Agent(name=name, instructions=instructions, model=self.model,
                     model_settings=ModelSettings(temperature=0.1, max_tokens=3000,
                                                  extra_body=extra_body))

    @staticmethod
    def _input(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, allow_nan=False)

    async def run_agent(self, agent, input_data: dict, output_type: type[BaseModel]):
        try:
            async with asyncio.timeout(60):
                result = await Runner.run(agent, input=self._input(input_data),
                                          max_turns=2, run_config=self.run_config)
            if isinstance(result.final_output, output_type):
                return result.final_output
            return output_type.model_validate_json(result.final_output)
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise WorkflowFailure("model", "model_auth_error", "模型认证或权限校验失败，请检查模型配置。") from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise WorkflowFailure("model", "model_timeout", "模型响应超时，请稍后重新提交。") from exc
        except ValidationError as exc:
            raise WorkflowFailure("model", "model_output_error", "模型返回的数据格式不符合约定。") from exc
        except WorkflowFailure:
            raise
        except Exception as exc:
            raise WorkflowFailure("model", "model_error", "模型请求失败，请检查服务地址、模型名称和网络连接。") from exc

    async def stream_agent(self, agent, input_data: dict) -> AsyncIterator[str]:
        stream = None
        try:
            async with asyncio.timeout(60):
                stream = Runner.run_streamed(agent, input=self._input(input_data),
                                             max_turns=2, run_config=self.run_config)
                text = ""
                async for event in stream.stream_events():
                    if event.type != "raw_response_event":
                        continue
                    if isinstance(event.data, ResponseTextDeltaEvent):
                        text += event.data.delta
                        yield event.data.delta
                if not text.strip():
                    raise WorkflowFailure("summary", "model_output_error", "汇总模型未返回回答。")
        except asyncio.CancelledError:
            raise
        except WorkflowFailure:
            raise
        except Exception as exc:
            raise WorkflowFailure("summary", "summary_error", "回答汇总失败，已保留查询结果。") from exc
        finally:
            if stream is not None:
                stream.cancel()
