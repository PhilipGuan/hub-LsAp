"""LLM 三层修复链。

阶段 3 的验收要求是「故意注入非法 JSON / 空 content / 数字写成字符串，
验证修复链全兜住」。这些测试就是那条验收 —— 用假的 transport 注入畸形响应，
不依赖真实 API。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.config import settings as default_settings
from app.llm import (
    LLMClient,
    LLMError,
    LLMJSONError,
    extract_json_object,
    strip_fence,
    validate_lenient,
)


class Plan(BaseModel):
    """模仿 planner 的输出契约。"""

    sub_questions: list[str]
    queries: list[str] = []
    rationale: str = ""


class Nested(BaseModel):
    name: str
    score: float = 0.0


class WithNested(BaseModel):
    items: list[Nested] = []


# ══════════════════════════════════════════════════════════════
# L1 清洗
# ══════════════════════════════════════════════════════════════


class TestStripFence:
    @pytest.mark.parametrize(
        "raw",
        [
            '```json\n{"a": 1}\n```',
            '```JSON\n{"a": 1}\n```',
            '```\n{"a": 1}\n```',
            '  ```json\n{"a": 1}\n```  ',
        ],
    )
    def test_removes_wrapping_fence(self, raw: str) -> None:
        assert strip_fence(raw) == '{"a": 1}'

    def test_leaves_plain_text_alone(self) -> None:
        assert strip_fence('{"a": 1}') == '{"a": 1}'

    def test_inner_fence_not_touched(self) -> None:
        """正文里出现的 ``` 不该被当成围栏剥掉。"""
        raw = '{"code": "```py\\npass\\n```"}'
        assert strip_fence(raw) == raw

    def test_empty(self) -> None:
        assert strip_fence("") == ""


# ══════════════════════════════════════════════════════════════
# L2 括号平衡扫描
# ══════════════════════════════════════════════════════════════


class TestExtractJsonObject:
    def test_pulls_object_out_of_prose(self) -> None:
        raw = '好的，以下是结果：\n{"a": 1}\n希望有帮助！'
        assert extract_json_object(raw) == '{"a": 1}'

    def test_handles_braces_inside_strings(self) -> None:
        """LLM 经常在 JSON 里塞代码片段 —— 正则会在这里截断。"""
        raw = '{"tpl": "f(x) = {x}", "n": 2}'
        assert extract_json_object(raw) == raw

    def test_handles_escaped_quote_before_brace(self) -> None:
        raw = '{"s": "say \\"hi\\" {now}", "n": 1}'
        assert extract_json_object(raw) == raw

    def test_nested_objects(self) -> None:
        raw = '{"a": {"b": {"c": 1}}}'
        assert extract_json_object(raw) == raw

    def test_unterminated_returns_none(self) -> None:
        assert extract_json_object('{"a": 1') is None

    def test_no_brace_returns_none(self) -> None:
        assert extract_json_object("完全没有对象") is None


# ══════════════════════════════════════════════════════════════
# L3 宽容转换
# ══════════════════════════════════════════════════════════════


class TestValidateLenient:
    def test_clean_input_passes(self) -> None:
        plan = validate_lenient(Plan, {"sub_questions": ["a", "b"]})
        assert plan.sub_questions == ["a", "b"]

    def test_missing_optional_fields_use_defaults(self) -> None:
        plan = validate_lenient(Plan, {"sub_questions": ["a"]})
        assert plan.queries == []
        assert plan.rationale == ""

    def test_single_value_wrapped_into_list(self) -> None:
        """该给列表却给了单个字符串 —— 实测常见。"""
        plan = validate_lenient(Plan, {"sub_questions": "只有一个子问题"})
        assert plan.sub_questions == ["只有一个子问题"]

    def test_numeric_field_as_string(self) -> None:
        nested = validate_lenient(WithNested, {"items": [{"name": "a", "score": "0.75"}]})
        assert nested.items[0].score == 0.75

    def test_number_with_unit_extracted(self) -> None:
        nested = validate_lenient(WithNested, {"items": [{"name": "a", "score": "3 分"}]})
        assert nested.items[0].score == 3.0

    def test_stringified_nested_json(self) -> None:
        """整个对象被序列化成一个字符串塞进来。"""
        raw = {"items": '[{"name": "a", "score": 1}]'}
        assert validate_lenient(WithNested, raw).items[0].name == "a"

    def test_unknown_keys_dropped(self) -> None:
        plan = validate_lenient(Plan, {"sub_questions": ["a"], "额外交付": "忽略我"})
        assert plan.sub_questions == ["a"]

    def test_nested_single_value_wrapped(self) -> None:
        nested = validate_lenient(WithNested, {"items": {"name": "a"}})
        assert nested.items[0].name == "a"

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(LLMJSONError):
            validate_lenient(Plan, {"queries": ["a"]})

    def test_non_object_raises(self) -> None:
        with pytest.raises(LLMJSONError):
            validate_lenient(Plan, ["a", "b"])

    def test_list_for_str_field_is_serialized(self) -> None:
        plan = validate_lenient(Plan, {"sub_questions": ["a"], "rationale": ["x", "y"]})
        assert plan.rationale == '["x", "y"]'


# ══════════════════════════════════════════════════════════════
# 端到端：假的 chat.completions
# ══════════════════════════════════════════════════════════════


class _FakeMessage:
    def __init__(self, content: str | None, reasoning: str | None = None) -> None:
        self.content = content
        self.reasoning_content = reasoning


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(
        self, content: str | None, reasoning: str | None = None, finish_reason: str = "stop"
    ) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content, reasoning), finish_reason)]


class _FakeCompletions:
    """按脚本依次吐响应；脚本用完后一直重复最后一个。"""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, script: list[Any]) -> None:
        self.chat = type("_Chat", (), {"completions": _FakeCompletions(script)})()

    async def close(self) -> None:
        pass


def _client(script: list[Any]) -> LLMClient:
    """造一个走假 transport 的 client。

    settings 必须复制一份 —— 直接改 ``default_settings`` 会把 key 泄漏给后续
    测试，之前就出现过 case 之间互相污染。
    """
    settings = default_settings.model_copy(update={"llm_api_key": "test-key"})
    client = LLMClient(settings)
    client._client = _FakeClient(script)
    return client


class TestCompleteJsonEndToEnd:
    async def test_plain_json(self) -> None:
        client = _client([_FakeResponse('{"sub_questions": ["a"]}')])
        plan = await client.complete_json("给我 json", Plan)

        assert plan.sub_questions == ["a"]

    async def test_fenced_json(self) -> None:
        client = _client([_FakeResponse('```json\n{"sub_questions": ["a"]}\n```')])
        plan = await client.complete_json("给我 json", Plan)

        assert plan.sub_questions == ["a"]

    async def test_json_with_prose_wrapper(self) -> None:
        client = _client([_FakeResponse('好的：\n{"sub_questions": ["a"]}\n完毕。')])
        plan = await client.complete_json("给我 json", Plan)

        assert plan.sub_questions == ["a"]

    async def test_empty_content_raises_llm_error(self) -> None:
        """只吐了 reasoning_content、content 为空 —— 实测会遇到。"""
        client = _client([_FakeResponse("", reasoning="我在想……")])

        with pytest.raises(LLMError, match="空 content"):
            await client.complete_json("给我 json", Plan)

    async def test_transport_error_becomes_llm_error(self) -> None:
        client = _client([RuntimeError("connection reset")])

        with pytest.raises(LLMError, match="LLM 调用失败"):
            await client.complete_json("给我 json", Plan)

    async def test_repair_retry_recovers(self) -> None:
        """第一次返回彻底不是 JSON，重试时模型改对了。"""
        client = _client(
            [
                _FakeResponse("我无法回答这个问题。"),
                _FakeResponse('{"sub_questions": ["a"]}'),
            ]
        )
        plan = await client.complete_json("给我 json", Plan)

        assert plan.sub_questions == ["a"]
        assert len(client._client.chat.completions.calls) == 2

    async def test_repair_retry_also_broken_raises(self) -> None:
        client = _client([_FakeResponse("还是不是 json")])

        with pytest.raises(LLMJSONError):
            await client.complete_json("给我 json", Plan)

    async def test_type_drift_survives_without_retry(self) -> None:
        """数字写成字符串这类漂移不该浪费一次重试 —— L3 直接兜住。"""
        client = _client(
            [_FakeResponse('{"sub_questions": "唯一子问题", "queries": "唯一检索词"}')]
        )
        plan = await client.complete_json("给我 json", Plan)

        assert plan.sub_questions == ["唯一子问题"]
        assert plan.queries == ["唯一检索词"]
        assert len(client._client.chat.completions.calls) == 1

    async def test_json_mode_flag_sent(self) -> None:
        client = _client([_FakeResponse('{"sub_questions": ["a"]}')])
        await client.complete_json("给我 json", Plan)

        assert client._client.chat.completions.calls[0]["response_format"] == {
            "type": "json_object"
        }

    async def test_missing_api_key_raises(self) -> None:
        """必须显式造一个空 key 的 settings。

        早先这里写的是 ``LLMClient()``，读的是真实 .env —— 当时 .env 里
        LLM_API_KEY 是空的，所以它「碰巧」通过；一旦填上真 key，这条用例
        就会去打 DeepSeek 的真实接口。测试变成依赖本机环境是最糟的一种坏法：
        它在本机绿、在别人机器红，而且**把不联网这条性质悄悄弄丢了**。
        """
        client = LLMClient(default_settings.model_copy(update={"llm_api_key": ""}))

        with pytest.raises(LLMError, match="LLM_API_KEY"):
            await client.complete_json("给我 json", Plan)


class TestEmptyContentDiagnosis:
    """空 content 必须能一眼看出是「预算被思考吃完了」还是「模型没说话」。

    实测踩到的：推理模型把 reasoning_tokens 也算进 max_tokens，
    读一篇 4500 字的网页光思考就吃满 3072，content 一个字没吐。
    两个原因是两种处理方式（调预算 / 换 prompt），日志里不能含糊过去。
    """

    async def test_truncated_by_length_points_at_max_tokens(self) -> None:
        client = _client([_FakeResponse("", reasoning="想" * 800, finish_reason="length")])

        with pytest.raises(LLMError) as excinfo:
            await client.complete("读这一页")

        message = str(excinfo.value)
        assert "finish_reason=length" in message
        assert "llm_max_tokens" in message
        assert "800" in message

    async def test_stop_with_empty_content_does_not_blame_max_tokens(self) -> None:
        """模型正常跑完却没说话 —— 让用户去调 max_tokens 是误诊。"""
        client = _client([_FakeResponse("", reasoning="", finish_reason="stop")])

        with pytest.raises(LLMError) as excinfo:
            await client.complete("读这一页")

        message = str(excinfo.value)
        assert "finish_reason=stop" in message
        assert "llm_max_tokens" not in message

    async def test_whitespace_only_content_counts_as_empty(self) -> None:
        client = _client([_FakeResponse("   \n  ", finish_reason="length")])

        with pytest.raises(LLMError, match="空 content"):
            await client.complete("读这一页")

    async def test_reasoning_content_alone_is_not_an_answer(self) -> None:
        """思维链不是产出 —— 拿它当正文会让报告里混进模型的草稿。"""
        client = _client(
            [
                _FakeResponse(
                    None, reasoning="我觉得这一页讲了通义灵码的定价", finish_reason="length"
                )
            ]
        )

        with pytest.raises(LLMError):
            await client.complete("读这一页")
