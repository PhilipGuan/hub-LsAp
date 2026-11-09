import os
import asyncio
import json
from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled,
)

# ---------- DeepSeek setup ----------
client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY") or "sk-bf4240833ba44d6ea4e6b33dacd6be11",
    base_url="https://api.deepseek.com",
)
set_default_openai_client(client, use_for_tracing=False)
set_default_openai_api("chat_completions")
set_tracing_disabled(True)


# ---------- Agents (no output_type) ----------
language_detector_agent = Agent(
    name="language_detector_agent",
    model="deepseek-flash",
    instructions=(
        "Detect the language of the given text.\n"
        "You must return a valid JSON object with exactly these two fields:\n"
        '{"language": "language name", "is_chinese": true/false}\n'
        "Only output the JSON, nothing else."
    ),
)

to_english_agent = Agent(
    name="to_english_agent",
    model="deepseek-flash",
    instructions=(
        "Translate the given Chinese text into natural English.\n"
        "Return a JSON object like: {\"translation\": \"the english text\"}\n"
        "Only output the JSON, nothing else."
    ),
)

to_chinese_agent = Agent(
    name="to_chinese_agent",
    model="deepseek-flash",
    instructions=(
        "Translate the given text into natural Chinese.\n"
        "Return a JSON object like: {\"translation\": \"the chinese text\"}\n"
        "Only output the JSON, nothing else."
    ),
)


def parse_json(text: str) -> dict:
    """Safely extract JSON from the model response."""
    text = text.strip()
    # Remove possible markdown code block
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    return json.loads(text)


async def main():
    user_input = input("Enter text: ").strip()
    if not user_input:
        print("Empty input, exiting.")
        return

    # 1. Detect language
    detection_result = await Runner.run(language_detector_agent, user_input)
    detection = parse_json(detection_result.final_output)

    print(f"Detected language: {detection['language']} (is_chinese={detection['is_chinese']})")

    # 2. Translate
    if detection["is_chinese"]:
        translation_result = await Runner.run(to_english_agent, user_input)
    else:
        translation_result = await Runner.run(to_chinese_agent, user_input)

    translation = parse_json(translation_result.final_output)

    # 3. Final JSON
    final_output = {
        "query_input": user_input,
        "language": detection["language"],
        "result": translation["translation"],
    }

    print(json.dumps(final_output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
