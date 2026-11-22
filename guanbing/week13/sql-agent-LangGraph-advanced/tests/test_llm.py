"""模型接入层的「硬参数」测试：只钉 D11 的阈值，**不发任何网络请求**。

`build_raw_model()` 是三条调用路线（纯文本 / 结构化输出 / `create_agent`）唯一的模型构造处，
所以「单次请求超时」在这一个函数上断言即可覆盖全部路径。
"""

from __future__ import annotations

from sqlagent import llm
from sqlagent.config import LLMSettings

SETTINGS = LLMSettings(provider="deepseek", model="test-model",
                       api_key="test-key", base_url="https://example.invalid/v1")


def test_raw_model_carries_request_timeout_and_retries():
    """默认带上 `request_timeout`（D11），且重试次数仍是原实现的 3 次。"""
    model = llm.build_raw_model(SETTINGS)

    assert model.request_timeout == llm.DEFAULT_REQUEST_TIMEOUT
    assert model.max_retries == llm.DEFAULT_ATTEMPTS


def test_request_timeout_is_overridable():
    """超时是「参数化上限」而非写死的常量，调用方可以按后端能力收紧。"""
    assert llm.build_raw_model(SETTINGS, request_timeout=5).request_timeout == 5
