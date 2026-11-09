"""LLM 客户端 —— OpenAI 兼容接口 + 结构化输出的三层修复链。

**这个文件里的修复链是必需品，不是优化项。** DeepSeek（以及大多数 OpenAI 兼容
网关）只支持 ``response_format={"type": "json_object"}``，**不支持 ``json_schema``**
（传了会 400）。也就是说服务端不保证字段名、不保证类型，甚至不保证返回的是合法
JSON。实测会遇到：

* 空 content（模型只吐了 ``reasoning_content``）
* ```` ```json ```` 围栏包裹
* 前后带一句「好的，以下是结果：」
* 数字写成字符串 ``"count": "3"``
* 该给列表的地方给了单个值
* 该给对象的地方给了字符串化的 JSON

修复链分三层，逐层兜底：

    L1 清洗  → 去掉围栏 / 只取 content
    L2 解析  → 回灌错误重试 → 括号平衡扫描
    L3 校验  → Pydantic 宽容强制转换 → 仍失败抛 LLMJSONError

抛出的 ``LLMError`` 由各 agent 捕获并走模板兜底 —— **LLM 挂掉不能让研究挂掉**。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import types
from types import NoneType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .config import Settings
from .config import settings as default_settings

logger = logging.getLogger(__name__)

# ```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    """LLM 调用或解析失败。调用方应当捕获它并走兜底逻辑。"""


class LLMJSONError(LLMError):
    """三层修复链全部失败。"""


class LLMTruncatedError(LLMError):
    """推理模型的思考把 max_tokens 吃完了，content 一个字没吐。

    **必须和「模型就是不肯说话」分开**：这一种是纯粹的预算问题，
    同一份 prompt 换个更大的 max_tokens 重试就能过。当成普通
    ``LLMError`` 吞掉，等于把一篇完全读得下来的页面白白丢掉
    （实测 deepseek-flash 读 Rust 调度器长文要思考 2.3 万字，
    是默认预算 8192 的近两倍，5/30 条来源就这么没了）。
    """


def strip_fence(text: str) -> str:
    """L1：剥掉 markdown 代码围栏。

    只认「整段被围栏包住」的情况 —— 正文里出现的 ``` 不该被误伤。
    """
    if not text:
        return ""
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def extract_json_object(text: str) -> str | None:
    """L2：括号平衡扫描出第一个完整的 ``{...}``。

    比正则靠谱的地方在于它能正确处理字符串里的花括号 ——
    LLM 经常在 JSON 里塞代码片段或模板变量，正则会在那里截断。
    """
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return None


def _parse_json_lenient(text: str) -> Any:
    """L2：尽力把一段文本解析成 JSON。失败抛 JSONDecodeError。"""
    return json.loads(text)


# ══════════════════════════════════════════════════════════════
# L3：Pydantic 宽容强制转换
# ══════════════════════════════════════════════════════════════


def _unwrap_optional(annotation: Any) -> Any:
    """把 ``X | None`` / ``Optional[X]`` 拆成 ``X``。

    只能拆 Union —— 早先这里写成「单参数泛型就拆参数」，结果 ``list[str]``
    被拆成了 ``str``，于是「该给列表却给了单个值」的纠偏分支永远进不去。
    """
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not NoneType]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_list_annotation(annotation: Any) -> bool:
    return get_origin(annotation) is list


def _is_model_annotation(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


_TRUE_STRINGS = {"true", "yes", "y", "1", "是", "真"}
_FALSE_STRINGS = {"false", "no", "n", "0", "否", "假"}


def _coerce_scalar(value: Any, annotation: Any) -> Any:
    """把常见类型漂移掰回目标类型。改不动就原样返回，交给 Pydantic 报错。"""
    if annotation is bool and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    if annotation is int and isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group())
    if annotation is float and isinstance(value, (str, int)):
        try:
            return float(value)
        except (TypeError, ValueError):
            # "0.8（较高）" / "3 分" 这类带修饰的数字，抠出数字本身
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group())
    if annotation is str and not isinstance(value, str):
        # 列表/字典被塞进字符串字段时，序列化回去比丢掉更有信息量
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        if value is not None:
            return str(value)
    return value


def _maybe_json(value: Any, opener: str) -> Any:
    """字符串化的 JSON —— 只在开头对得上时才尝试，避免误伤普通文本。"""
    if isinstance(value, str) and value.lstrip().startswith(opener):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _coerce_value(value: Any, annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)

    if _is_model_annotation(annotation):
        value = _maybe_json(value, "{")
        if isinstance(value, dict):
            return coerce_for_model(annotation, value)
        return value

    args = get_args(annotation)
    if _is_list_annotation(annotation) and args:
        value = _maybe_json(value, "[")
        # 该给列表却给了单个值 —— 包成单元素列表
        if not isinstance(value, list):
            value = [] if value is None else [value]
        return [_coerce_value(v, args[0]) for v in value]

    return _coerce_scalar(value, annotation)


def coerce_for_model(model_cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """按模型字段声明逐字段纠偏，并丢掉模型不认识的键。"""
    fields = model_cls.model_fields
    coerced: dict[str, Any] = {}

    for name, field in fields.items():
        if name not in data:
            continue
        coerced[name] = _coerce_value(data[name], field.annotation)

    return coerced


def validate_lenient[T: BaseModel](model_cls: type[T], data: Any) -> T:
    """先用最宽松的方式校验，不行再上强制转换。"""
    if not isinstance(data, dict):
        raise LLMJSONError(f"期望 JSON 对象，实际是 {type(data).__name__}")

    # 第一遍：交给 Pydantic 自己转（它已经能把 "3" 转成 3）
    try:
        return model_cls.model_validate(data)
    except ValidationError as first_error:
        logger.debug("直接校验失败，尝试强制转换: %s", first_error)

    # 第二遍：按字段声明纠偏后重试
    try:
        return model_cls.model_validate(coerce_for_model(model_cls, data))
    except ValidationError as second_error:
        raise LLMJSONError(
            f"{model_cls.__name__} 校验失败（含强制转换）: {second_error}"
        ) from second_error


# ══════════════════════════════════════════════════════════════
# 客户端
# ══════════════════════════════════════════════════════════════


class LLMClient:
    """OpenAI 兼容的 chat 客户端，带并发上限与结构化输出修复链。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._semaphore = asyncio.Semaphore(self._settings.llm_max_concurrency)
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            if not self._settings.llm_api_key:
                raise LLMError("未配置 LLM_API_KEY，请在 .env 里填写后再运行")
            self._client = AsyncOpenAI(
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url,
                timeout=self._settings.llm_timeout_s,
                max_retries=2,
            )
        return self._client

    @property
    def retry_max_tokens(self) -> int:
        """被截断后重试用的更大的预算。见 ``LLMTruncatedError``。"""
        return self._settings.llm_retry_max_tokens

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """跑一次对话，返回清洗后的文本。网络/服务端错误抛 ``LLMError``。"""
        client = self._ensure_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": (self._settings.llm_temperature if temperature is None else temperature),
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
        }
        if json_mode:
            # DeepSeek 只认 json_object；且要求 prompt 里出现 "json" 字样
            kwargs["response_format"] = {"type": "json_object"}

        async with self._semaphore:
            try:
                response = await client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - SDK 的异常层级随版本变化
                raise LLMError(f"LLM 调用失败: {exc.__class__.__name__}: {exc}") from exc

        if not response.choices:
            raise LLMError("LLM 返回了空的 choices")

        choice = response.choices[0]
        message = choice.message
        # 推理模型会把思维链放在 reasoning_content 里，正文只在 content
        content = getattr(message, "content", None) or ""
        if not content.strip():
            # 一定把 finish_reason 带上：`length` 是「预算被思考吃完了」，
            # 和「模型就是不肯说话」是两种问题，处理方式完全不同，
            # 光看「空 content」这个说法分不出来。推理模型尤其容易踩这条。
            reasoning = getattr(message, "reasoning_content", None) or ""
            truncated = choice.finish_reason == "length"
            detail = (
                f"LLM 返回了空 content（finish_reason={choice.finish_reason}，"
                f"reasoning_content {len(reasoning)} 字"
                + ("，思考把 max_tokens 吃完了，请调大 llm_max_tokens" if truncated else "")
                + "）"
            )
            # 分成两个类型而不是靠字符串判断 —— 调用方要据此决定「重试」还是「放弃」
            raise LLMTruncatedError(detail) if truncated else LLMError(detail)

        return strip_fence(content)

    async def complete_json[T: BaseModel](
        self,
        prompt: str,
        model_cls: type[T],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> T:
        """跑一次对话并把结果解析成 ``model_cls``。三层修复链都在这里。"""
        raw = await self.complete(
            prompt,
            system=system,
            json_mode=True,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # L2：先直接解
        try:
            return validate_lenient(model_cls, _parse_json_lenient(raw))
        except (json.JSONDecodeError, LLMJSONError) as first_error:
            logger.debug("首轮 JSON 解析失败: %s", first_error)

        # L2 续：括号平衡扫描再试一次（应对「好的，以下是 json：{...}」这类前后缀）
        extracted = extract_json_object(raw)
        if extracted is not None and extracted != raw:
            try:
                return validate_lenient(model_cls, _parse_json_lenient(extracted))
            except (json.JSONDecodeError, LLMJSONError) as exc:
                logger.debug("括号扫描后仍失败: %s", exc)

        # L2 重试：把错误回灌给模型，让它自己修
        repair_prompt = (
            f"{prompt}\n\n"
            "─────\n"
            "你上一次的输出无法被解析为合法 json。请**只输出 json 对象本身**，"
            "不要任何解释文字、不要 markdown 围栏、不要前后缀。\n\n"
            f"上一次的输出是：\n{raw[:1500]}"
        )
        repaired = await self.complete(
            repair_prompt,
            system=system,
            json_mode=True,
            max_tokens=max_tokens,
            temperature=0.0,
        )

        try:
            return validate_lenient(model_cls, _parse_json_lenient(repaired))
        except (json.JSONDecodeError, LLMJSONError) as exc:
            salvaged = extract_json_object(repaired)
            if salvaged is not None:
                try:
                    return validate_lenient(model_cls, _parse_json_lenient(salvaged))
                except (json.JSONDecodeError, LLMJSONError):
                    pass
            raise LLMJSONError(
                f"三层修复链全部失败，无法解析为 {model_cls.__name__}: {exc}"
            ) from exc


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMJSONError",
    "LLMTruncatedError",
    "coerce_for_model",
    "extract_json_object",
    "strip_fence",
    "validate_lenient",
]
