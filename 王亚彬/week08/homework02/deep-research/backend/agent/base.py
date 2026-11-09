"""所有 Agent 的基础类：统一管理 system 提示词、用户 prompt 模板、LLM 调用与 JSON 解析。"""
from __future__ import annotations

import logging
from typing import Any

from backend.llm import chat, chat_json

logger = logging.getLogger(__name__)


class BaseAgent:
    """Agent 基类。

    子类声明 ``name``、``system_prompt``、``template``，可选重写 ``run``。
    ``template`` 支持 Python ``str.format`` 占位符（如 ``{topic}``）。
    """

    name: str = "base"
    system_prompt: str = ""
    template: str = ""
    json_mode: bool = True
    temperature: float = 0.2
    max_tokens: int = 4000

    def render(self, **kwargs: Any) -> str:
        """渲染 user prompt。子类的 ``run`` 把它需要的所有参数都通过 kwargs 传进来。"""
        if not self.template:
            return ""
        try:
            return self.template.format(**kwargs)
        except KeyError as exc:
            raise ValueError(f"[{self.name}] template 缺少参数：{exc}")

    def call(self, **kwargs: Any) -> Any:
        """走 LLM，自动 JSON 解析。"""
        user = self.render(**kwargs)
        if not user:
            raise ValueError(f"[{self.name}] 渲染后的 user prompt 为空")
        if self.json_mode:
            return chat_json(self.system_prompt, user, temperature=self.temperature, max_tokens=self.max_tokens)
        return chat(self.system_prompt, user, temperature=self.temperature, max_tokens=self.max_tokens, json_mode=False)

    def call_text(self, **kwargs: Any) -> str:
        """走 LLM，返回纯文本（json_mode=False 也可改用此方法）。"""
        user = self.render(**kwargs)
        return chat(self.system_prompt, user, temperature=self.temperature, max_tokens=self.max_tokens, json_mode=False)

    def run(self, **kwargs: Any) -> Any:
        """业务入口。子类按需重写。"""
        return self.call(**kwargs)


if __name__ == "__main__":
    from backend.config import setup_logging

    setup_logging()

    class Echo(BaseAgent):
        name = "echo"
        system_prompt = "你是复读机。"
        template = "请复述：{text}"
        json_mode = False

    e = Echo()
    out = e.run(text="你好，世界")
    print("Echo.render ->", repr(e.render(text="你好")))
    print("Echo.run    ->", out)
