"""Thin Anthropic client wrapper. One method for free-form, one for JSON."""
import json
import os

import anthropic


class LLM:
    def __init__(self, model: str = "claude-sonnet-5", api_key: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model

    def complete(self, prompt: str, system: str = "", max_tokens: int = 2048) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "You are a precise research assistant.",
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    def complete_json(self, prompt: str, system: str = "", max_tokens: int = 2048) -> dict:
        # Ask explicitly for JSON; strip a stray ```json fence if the model adds one.
        # Ponytail: skipping pydantic / instructor — when fields grow / parse errors become common, add it.
        text = self.complete(prompt + "\n\nReturn valid JSON only. No prose.", system, max_tokens).strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("`").strip()
        return json.loads(text)