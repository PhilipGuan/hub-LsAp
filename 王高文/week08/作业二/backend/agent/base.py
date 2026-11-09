"""Agent 基类 + SDK 全局初始化 + JSON 解析兜底。

- SDK 全局初始化（`set_default_openai_api("chat_completions")`、`set_tracing_disabled(True)`）
  在本模块**模块级**执行；有 DeepSeek key 时设置默认 client（base_url 指向 DeepSeek）。
- `BaseAgent._run`：以 base model 发起一次 LLM 调用，DeepSeek 偶发 `200 OK` 却空输出，
  自动重试 `LLM_RETRIES` 次（退避 1s）。
- `parse_json`：pydantic 直接解析 + ```json``` 代码块正则兜底。
"""
import asyncio
import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel
from agents import (
    Agent,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_default_openai_key,
    set_tracing_disabled,
)

from ..config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

LLM_RETRIES = max(1, settings.llm_retries)
TEMPLATES_DIR = PROJECT_ROOT / "backend" / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    trim_blocks=True,
    lstrip_blocks=True,
)

# ---------------- SDK 全局初始化（模块级） ----------------
set_default_openai_api("chat_completions")
set_tracing_disabled(True)
if settings.deepseek_api_key:
    try:
        from openai import AsyncOpenAI

        set_default_openai_key(settings.deepseek_api_key, use_for_tracing=False)
        set_default_openai_client(
            AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url_normalized,
            ),
            use_for_tracing=False,
        )
        logger.info(
            "已配置 DeepSeek LLM model=%s base=%s",
            settings.deepseek_model,
            settings.deepseek_base_url_normalized,
        )
    except Exception as e:  # pragma: no cover
        logger.warning("DeepSeek client 初始化失败（本地 demo 可忽略）: %s", e)
else:
    logger.warning("未配置 DEEPSEEK_API_KEY（.env），LLM 相关 demo/请求不可用")


# ---------------- JSON 解析兜底 ----------------

def parse_json(text, output_cls: type[BaseModel]) -> BaseModel:
    """把 LLM 文本输出解析为 pydantic 模型：直解 → 代码块正则 → 首尾花括号。"""
    if isinstance(text, BaseModel):
        return text
    text = str(text).strip()
    if not text:
        raise ValueError(f"{output_cls.__name__} 输出为空")

    # 1) 直接解析
    try:
        return output_cls.model_validate_json(text)
    except Exception:
        pass

    # 2) ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        try:
            return output_cls.model_validate_json(m.group(1).strip())
        except Exception:
            pass

    # 3) 取首个 { 到最后一个 } 之间
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return output_cls.model_validate_json(text[start : end + 1])
        except Exception:
            pass

    raise ValueError(f"无法从输出解析 {output_cls.__name__}: {text[:300]!r}...")


# ---------------- BaseAgent ----------------

class BaseAgent:
    """所有角色 agent 的基类：无工具、单次 LLM 调用、提示词从 templates/ 渲染。"""

    name = "base"
    model = settings.deepseek_model

    def _render(self, template: str, variables: dict) -> str:
        return _env.get_template(template).render(**variables)

    def _agent(self, prompt: str) -> Agent:
        return Agent(name=self.name, model=self.model, instructions=prompt)

    async def _run(self, template: str, system_vars: dict, user_input: str) -> str:
        """渲染模板并调用一次 LLM；对空输出自动重试，返回原始文本。"""
        prompt = self._render(template, {**system_vars, "user_input": user_input})
        agent = self._agent(prompt)
        for attempt in range(1, LLM_RETRIES + 1):
            logger.debug("LLM %s 调用 attempt=%d prompt_len=%d", self.name, attempt, len(prompt))
            result = await Runner.run(agent, prompt)
            out = (result.final_output or "").strip()
            logger.info("LLM %s 完成 attempt=%d out_len=%d", self.name, attempt, len(out))
            if out:
                return out
            logger.warning("%s 返回空输出，重试 %d/%d", self.name, attempt, LLM_RETRIES)
            await asyncio.sleep(1)
        raise RuntimeError(f"{self.name} 连续 {LLM_RETRIES} 次返回空输出")

    async def call_json(self, template: str, system_vars: dict, user_input: str, output_cls: type[BaseModel]):
        """调用一次 LLM 并解析为结构化 JSON。"""
        text = await self._run(template, system_vars, user_input)
        return parse_json(text, output_cls)


if __name__ == "__main__":
    import logging

    from ..models import KeywordOutput, JudgeDecision

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=== parse_json + 模板渲染 自检（不调用 LLM） ===")

    # 直接 JSON
    o1 = parse_json('{"keywords": ["a", "b"]}', KeywordOutput)
    # ```json``` 代码块
    o2 = parse_json('```json\n{"keywords": ["c"]}\n```', KeywordOutput)
    # 前置杂文字 + JSON
    o3 = parse_json('好的，以下是结果：{"keywords": ["d"]}', KeywordOutput)
    # 纯列表无法解析 -> 应抛错
    try:
        parse_json("not json at all", JudgeDecision)
        print("异常：应抛错却未抛")
    except ValueError:
        print("非法输入正确抛出 ValueError")

    assert o1.keywords == ["a", "b"]
    assert o2.keywords == ["c"] and o3.keywords == ["d"]

    # 模板渲染（keyword_agent.jinja2 存在性 + 变量注入）
    ba = BaseAgent()
    rendered = ba._render("keyword_agent.jinja2", {"user_input": "天空为什么是蓝色的"})
    assert "天空为什么是蓝色的" in rendered
    print("parse_json 3 种场景通过；keyword 模板渲染通过")
    print("输出示例:", o1.keywords, o2.keywords, o3.keywords)