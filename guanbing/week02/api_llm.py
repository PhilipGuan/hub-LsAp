import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI


_FILE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FILE_DIR.parent
ENV_CANDIDATES = [
    _FILE_DIR / ".env",
    _PROJECT_ROOT / ".env",
]


def load_env(env_path: Optional[Path] = None) -> Optional[Path]:
    if env_path is not None:
        if env_path.exists():
            load_dotenv(env_path)
            return env_path
        return None

    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            load_dotenv(candidate)
            return candidate
    return None


def build_provider_config(provider: str) -> Dict[str, Any]:
    provider = provider.lower().strip()

    if provider == "deepseek":
        return {
            "name": "deepseek",
            "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        }

    if provider == "qwen":
        return {
            "name": "qwen",
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "model": os.getenv("QWEN_MODEL", "qwen-plus"),
        }

    raise ValueError(f"暂不支持的 provider：{provider}")


def build_client(provider: str) -> tuple[OpenAI, Dict[str, Any]]:
    config = build_provider_config(provider)

    if not config.get("api_key"):
        raise ValueError(f"未在环境变量中找到 {config['name'].upper()}_API_KEY，请先配置 .env")

    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    return client, config


def build_create_kwargs(
    config: Dict[str, Any], model_override: Optional[str] = None, stream: bool = False,
    json_mode: bool = False,
    ) -> Dict[str, Any]:
    provider = config["name"]
    model = model_override or config["model"]

    kwargs: Dict[str, Any] = {
        "model": model,
        "stream": stream,
    }

    if provider == "deepseek":
        kwargs["reasoning_effort"] = "low"
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    return kwargs


def extract_text(provider: str, response: Any) -> str:
    provider = provider.lower().strip()

    try:
        if provider == "qwen":
            output_choices = getattr(response, "output", None) or getattr(response, "choices", None)
            if output_choices is not None:
                return output_choices[0].message.content

        return response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return ""


def chat(
    provider: str,
    prompt: str,
    system_prompt: str = "You are a helpful assistant",
    model_override: Optional[str] = None,
    json_mode: bool = False,
) -> str:
    client, config = build_client(provider)

    if json_mode and "json" not in system_prompt.lower():
        system_prompt = system_prompt.strip() + "\n\n你必须严格输出合法的 JSON，不要输出任何解释性文本。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    create_kwargs = build_create_kwargs(
        config,
        model_override=model_override,
        stream=False,
        json_mode=json_mode,
    )

    response = client.chat.completions.create(messages=messages, **create_kwargs)
    return extract_text(config["name"], response)


if __name__ == "__main__":
    load_env()

    provider = os.getenv("LLM_PROVIDER", "deepseek").lower().strip()
    prompt = "你好，帮我介绍CFA，包括考试等级以及每个等级需要考试通过的具体模块"

    answer = chat(provider, prompt)
    print(answer)
