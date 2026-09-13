# -*- coding: utf-8 -*-
"""M6：5 Agent 共用 BaseAgent（继承综合案例-02 骨架）。
统一：Jinja2 Prompt 渲染 + openai SDK LLM 调用 + tenacity 3 次重试 + max_rounds 收敛循环。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import LLM_MODEL, LLM_TEMPERATURE, RESEARCH_MAX_ROUNDS

logger = logging.getLogger(__name__)

MAX_ROUNDS: int = RESEARCH_MAX_ROUNDS

ProgressCb = Callable[[str, str], None]

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=(), default_for_string=False),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


def _style_include_prefix(style: str) -> str:
    style = (style or "analyst").lower()
    if style not in ("analyst", "institutional", "explainer"):
        style = "analyst"
    return f"style_snippets/{style}.jinja2"


class BaseAgent:
    """5 Agent 公共基类：统一 Prompt → LLM → Parse 流水线。"""

    agent_name: str = "base"
    template_path: str = "base.jinja2"

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        on_progress: ProgressCb | None = None,
    ) -> None:
        self.model = model or LLM_MODEL
        self.temperature = LLM_TEMPERATURE if temperature is None else float(temperature)
        self.on_progress: ProgressCb = on_progress or (lambda *a, **kw: None)

    # ---------- 给子类重写 ----------
    def build_context(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def parse_response(self, raw: str) -> Any:  # pragma: no cover
        raise NotImplementedError

    # ---------- 渲染 / LLM ----------
    def render_prompt(self, context: dict[str, Any]) -> str:
        tmpl = _jinja_env.get_template(self.template_path)
        style = str(context.get("style") or "analyst").lower()
        ctx = dict(context)
        ctx.setdefault("style_include", _style_include_prefix(style))
        ctx.setdefault("agent_name", self.agent_name)
        ctx.setdefault("model", self.model)
        ctx.setdefault("temperature", self.temperature)
        return tmpl.render(**ctx)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
        retry=retry_if_exception_type((Exception,)),
    )
    def _llm_chat(self, prompt: str) -> str:
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - 环境缺包
            raise RuntimeError(f"openai SDK 未安装：{exc!s}") from exc
        client = OpenAI()  # 自动读 os.environ OPENAI_*（config.py 已对齐）
        messages = [
            {"role": "system", "content": "You are an ETF research assistant. Always respond precisely with the requested format. Never include extra commentary outside the requested structure."},
            {"role": "user", "content": prompt},
        ]
        logger.info("[Agent::%s] calling model=%s prompt_len=%d", self.agent_name, self.model, len(prompt))
        self.on_progress(self.agent_name, f"calling LLM model={self.model}")
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            temperature=self.temperature,
            max_tokens=4096,
            timeout=120.0,
        )
        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:  # pragma: no cover - 结构异常
            raise RuntimeError(f"LLM 返回结构异常：{exc!s}") from exc
        logger.info("[Agent::%s] response_len=%d", self.agent_name, len(content))
        return content.strip()

    # ---------- 公共入口 ----------
    def generate(self, *args: Any, max_rounds: int = MAX_ROUNDS, **kwargs: Any) -> Any:
        """Loop：build_context → render → llm → parse，成功即返回或达到 max_rounds。"""
        max_rounds = max(1, int(max_rounds or MAX_ROUNDS))
        last_error: Exception | None = None
        last_raw: str = ""
        for r in range(1, max_rounds + 1):
            self.on_progress(
                self.agent_name,
                f"round={r}/{max_rounds} building_prompt",
            )
            try:
                ctx = self.build_context(*args, **kwargs)
                prompt = self.render_prompt(ctx)
                try:
                    raw = self._llm_chat(prompt)
                except RetryError as exc:  # pragma: no cover - tenacity 重试 3 次后
                    last_error = RuntimeError(f"LLM 3次重试均失败：{exc!s}")
                    last_raw = ""
                    continue
                last_raw = raw
                parsed = self.parse_response(raw)
                self.on_progress(
                    self.agent_name,
                    f"round={r}/{max_rounds} parse_ok",
                )
                return parsed
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "[Agent::%s] round=%d FAILED: %s",
                    self.agent_name, r, exc,
                )
                self.on_progress(
                    self.agent_name,
                    f"round={r}/{max_rounds} FAILED: {exc!s}",
                )
                continue
        # 全轮次失败：抛最后错误（CLI/Engine 捕获后走 degrade）
        raise RuntimeError(
            f"[Agent::{self.agent_name}] {max_rounds} 轮次全部失败。"
            f"last_error={last_error!s}; last_raw_preview={last_raw[:400]!r}"
        ) from last_error


def _debug_dump(obj: Any) -> str:  # pragma: no cover - 给 parse_response 内部调试 helper
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)
