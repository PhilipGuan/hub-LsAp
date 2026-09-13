# -*- coding: utf-8 -*-
"""L1 测试：config.py envify-llm 3 条校验 + 级联 + OPENAI 三件套回写。
全用 monkeypatch.setenv 无网无文件。
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _reload_config():
    """保证每次用例清空后 config.py 重新 import（触发模块级 load_env_and_validate() 重算）。"""
    if "backend.config" in sys.modules:
        del sys.modules["backend.config"]
    return importlib.import_module("backend.config")


def test_key_empty_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        _reload_config()
    msg = str(exc.value).lower()
    assert "llm_provider=deepseek" in msg or "为空" in msg or "empty" in msg


def test_key_placeholder_prefix_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "your_deepseek_key_here")   # your_ 前缀占位
    with pytest.raises(RuntimeError) as exc:
        _reload_config()
    assert "占位符" in str(exc.value) or "your_" in str(exc.value).lower()


def test_openai_backward_compat_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """没设 LLM_PROVIDER，只传 OPENAI_* 三件套 → provider=openai，并对齐写回 os.environ。"""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-realvalue_notplaceholder_123")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    # 如果 qwen/deepseek key 也有占位 → 设成示例值 sk-xxx 也会被判定占位报错？先清掉
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    cfg = _reload_config()
    assert cfg.LLM_PROVIDER == "openai"
    assert os.environ["OPENAI_API_KEY"].startswith("sk-realvalue")
    assert os.environ["OPENAI_BASE_URL"] == "https://api.openai.com/v1"


def test_deepseek_three_components_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常 DeepSeek 场景：三键位齐全，Key 非占位。返回 (provider,key,base,model) 并写回 os.environ。"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-valid-key-for-test-only-abcdefghij")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _reload_config()
    assert cfg.LLM_PROVIDER == "deepseek"
    assert cfg.LLM_MODEL == "deepseek-v4-flash"
    assert cfg.LLM_BASE_URL == "https://api.deepseek.com/v1"
    # 对齐写回 openai-agents SDK：os.environ["OPENAI_API_KEY"] 应该= DEEPSEEK_API_KEY 真实值
    assert os.environ["OPENAI_API_KEY"] == "sk-valid-key-for-test-only-abcdefghij"
    assert os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"
