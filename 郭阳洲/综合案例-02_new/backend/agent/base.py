# -*- coding: utf-8 -*-
"""角色 agent 的基类：SDK 初始化 + 提示词渲染 + 单次 LLM 调用 + JSON 解析。

规格见 README.md / 任务说明书.md（T5）。
所有角色 agent 都继承 BaseAgent，且都是**无工具**的单次 LLM 调用，不直接调搜索。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TypeVar

import jinja2
from agents import Agent, Runner, set_default_openai_api, set_tracing_disabled
from pydantic import BaseModel, ValidationError

from .. import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- SDK 初始化
# 必须在模块顶层执行：任何 Agent 被创建前就生效。
set_default_openai_api("chat_completions")  # DeepSeek 是 OpenAI 兼容的 chat 接口
set_tracing_disabled(True)  # 关闭 tracing 上报

BASE_MODEL = config.MODEL_NAME
_env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(config.TEMPLATE_DIR)))

# 代码块围栏：```json {...} ``` / ``` {...} ```
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------- JSON 解析
def _extract_fence(text: str) -> str | None:
    """从模型输出里抽出 ```json ... ``` 代码块内容；没有代码块返回 None。"""
    if not text:
        return None
    match = _FENCE_RE.search(text)
    return match.group(1) if match else None


def _describe_validation_error(exc: ValidationError) -> str:
    """把 pydantic 校验错误整理成一行一条的可读诊断。"""
    lines: list[str] = []
    for err in exc.errors():
        etype = err.get("type", "")
        msg = err.get("msg", "")
        if etype == "json_invalid":
            # JSON 语法错误：从消息里抽出行列位置
            found = re.search(r"at line (\d+) column (\d+)", msg)
            where = f"第 {found.group(1)} 行第 {found.group(2)} 列" if found else "位置未知"
            lines.append(f"- [JSON 语法错误] {where}: {msg}")
        else:
            loc = ".".join(str(p) for p in err.get("loc", ())) or "(根)"
            lines.append(f"- [字段校验失败] {loc}: {msg}")
    return "\n".join(lines) if lines else "- (未提供错误明细)"


def parse_json(text: str, output_cls: type[T]) -> T:
    """把模型输出的 JSON 文本解析成 output_cls 实例。

    候选顺序很重要：**先抽 ```json 代码块**（正常路径一次成功），失败再回退整段原始输出。
    全部失败时抛 ValueError，附诊断（错误明细 + 输出片段前 200 字符）。
    """
    candidates: list[tuple[str, str]] = []
    fenced = _extract_fence(text)
    if fenced is not None:
        candidates.append(("```json 代码块", fenced))
    candidates.append(("原始输出", text or ""))

    errors: list[str] = []
    for label, candidate in candidates:
        if not candidate.strip():
            errors.append(f"- [{label}] 内容为空，跳过")
            continue
        try:
            return output_cls.model_validate_json(candidate)
        except ValidationError as exc:
            errors.append(f"- [{label}]\n{_describe_validation_error(exc)}")
        except Exception as exc:  # noqa: BLE001 - 非校验类异常也要记进诊断
            errors.append(f"- [{label}] 非校验异常: {exc!r}")

    snippet = (text or "")[:200]
    raise ValueError(
        f"无法把模型输出解析成 {output_cls.__name__}（已尝试 {len(candidates)} 个候选）。\n"
        + "\n".join(errors)
        + f"\n输出片段（前 200 字符）: {snippet!r}"
    )


# ---------------------------------------------------------------- 基类
class BaseAgent:
    """角色 agent 基类：渲染提示词 → 单次 LLM 调用（无工具）→ 返回文本或解析成结构。"""

    agent_name = "BaseAgent"
    template_name = ""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or BASE_MODEL

    # -------------------------------------------------- 提示词
    def _render(self, template_name: str | None = None, **vars) -> str:
        """渲染 templates/ 下的 Jinja2 提示词模板。"""
        name = template_name or self.template_name
        if not name:
            raise ValueError(f"{type(self).__name__} 未指定 template_name")
        return _env.get_template(name).render(**vars)

    # -------------------------------------------------- 调用
    async def _run(
        self,
        system_vars: dict,
        user_input: str,
        template_name: str | None = None,
    ) -> str:
        """执行一次无工具的 LLM 调用，返回原始文本输出。

        空输出自动重试（DeepSeek 偶发 200 但 content 为空），最多尝试
        config.LLM_RETRIES 次，每次退避 1 秒。
        """
        instructions = self._render(template_name, **system_vars)
        agent = Agent(
            model=self.model,
            name=self.agent_name,
            instructions=instructions,
        )
        logger.debug(
            "%s: system_vars=%s user_input=%s", self.agent_name, system_vars, user_input
        )

        max_attempts = max(1, config.LLM_RETRIES)
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "%s: LLM 调用开始（第 %d/%d 次，model=%s）",
                self.agent_name,
                attempt,
                max_attempts,
                self.model,
            )
            # 绝不用 run_sync：这里是 async 上下文
            result = await Runner.run(agent, user_input, max_turns=1)
            final = result.final_output
            text = final if isinstance(final, str) else ("" if final is None else str(final))

            if text.strip():
                logger.info("%s: LLM 调用完成，输出 %d 字符", self.agent_name, len(text))
                logger.debug("%s: final_output=%s", self.agent_name, text)
                return text

            logger.warning(
                "%s: 第 %d/%d 次调用返回空输出%s",
                self.agent_name,
                attempt,
                max_attempts,
                "，1 秒后重试" if attempt < max_attempts else "，不再重试",
            )
            if attempt < max_attempts:
                await asyncio.sleep(1)

        raise RuntimeError(
            f"{self.agent_name}: 连续 {max_attempts} 次调用均返回空输出"
            f"（可调大 .env 的 LLM_RETRIES）"
        )

    async def call_json(
        self,
        system_vars: dict,
        user_input: str,
        output_cls: type[T],
        template_name: str | None = None,
    ) -> T:
        """调用 LLM 并把输出解析成 output_cls 实例。"""
        text = await self._run(system_vars, user_input, template_name)
        try:
            return parse_json(text, output_cls)
        except Exception:
            logger.exception(
                "%s: 输出解析失败（目标结构 %s）", self.agent_name, output_cls.__name__
            )
            raise


# ---------------------------------------------------------------- 自检 demo
if __name__ == "__main__":
    import json

    from ..models import KeywordOutput

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    print("=== 1) parse_json 直接解析 JSON ===")
    direct = parse_json('{"keywords": ["a", "b"]}', KeywordOutput)
    print(f"解析结果: {direct.keywords}")
    assert direct.keywords == ["a", "b"]

    print("=== 2) parse_json 解析 ```json 代码块包裹的文本（fence 兜底）===")
    fenced_text = '好的，结果如下：\n```json\n{"keywords": ["x", "y", "z"]}\n```\n以上。'
    fenced = parse_json(fenced_text, KeywordOutput)
    print(f"抽取到代码块: {_extract_fence(fenced_text)}")
    print(f"解析结果: {fenced.keywords}")
    assert fenced.keywords == ["x", "y", "z"]

    print("=== 3) 子类渲染提示词（纯本地，不调 LLM）===")

    class _Dummy(BaseAgent):
        agent_name = "_Dummy"
        template_name = "keyword_agent.jinja2"

    dummy = _Dummy()
    prompt = dummy._render(topic="测试主题", today=config.today_str())
    print(f"提示词长度: {len(prompt)} 字符")
    assert prompt.strip(), "提示词不应为空"
    assert "测试主题" in prompt, "模板变量 topic 未渲染进提示词"
    assert config.TEMPLATE_DIR.exists()

    print("=== 4) 解析失败诊断（预期失败，仅展示诊断文案）===")
    bad_cases = [
        ("语法错误", "这不是 JSON，只是一段普通文字。"),
        ("字段类型错误", '```json\n{"keywords": "不是数组"}\n```'),
    ]
    for label, bad in bad_cases:
        try:
            parse_json(bad, KeywordOutput)
        except ValueError as exc:
            print(f"[{label}] 已按预期抛出 ValueError：")
            print(exc)
        else:
            raise AssertionError(f"[{label}] 本应解析失败却成功了")

    print("=== 5) 配置项 ===")
    print(f"BASE_MODEL   = {BASE_MODEL}")
    print(f"TEMPLATE_DIR = {config.TEMPLATE_DIR}")
    print(f"LLM_RETRIES  = {config.LLM_RETRIES}")
    print(f"示例模型 dump = {json.dumps(direct.model_dump(), ensure_ascii=False)}")

    print("base 自检 OK")
