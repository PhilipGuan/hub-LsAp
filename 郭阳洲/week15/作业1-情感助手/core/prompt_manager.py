"""作业1 · 核心层：Jinja2 提示词模板管理。

所有提示词集中在 ../prompts/*.j2，本模块负责加载与渲染：
- StrictUndefined 保证模板变量缺失时立即报错，而不是静默输出空串；
- 模板文件遵循统一五段式：角色 / 任务 / 约束 / 变量 / 输出格式。
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_env = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(name: str, **kwargs) -> str:
    """按名称渲染提示词模板（prompts/<name>.j2）。

    参数缺失会直接抛 UndefinedError，避免提示词静默劣化。
    """
    return _env.get_template(f"{name}.j2").render(**kwargs)


def list_templates() -> list[str]:
    """列出可用模板名（自检/调试用）。"""
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.j2"))