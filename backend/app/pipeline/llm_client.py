from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_APP_NAME,
    OPENROUTER_APP_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
)


class LLMConfigurationError(RuntimeError):
    pass


class LLMCallError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


def openrouter_configured() -> bool:
    return bool(OPENROUTER_API_KEY and OPENROUTER_MODEL)


def call_openrouter_json(
    messages: list[LLMMessage],
    json_schema: dict[str, Any],
    schema_name: str,
    temperature: float = 0.0,
    max_tokens: int = 2500,
) -> dict[str, Any]:
    if not OPENROUTER_API_KEY:
        raise LLMConfigurationError("OPENROUTER_API_KEY is not set.")
    if not OPENROUTER_MODEL:
        raise LLMConfigurationError("OPENROUTER_MODEL is not set.")

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [message.__dict__ for message in messages],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            },
        },
    }
    request = urllib.request.Request(
        f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": OPENROUTER_APP_URL,
            "X-Title": OPENROUTER_APP_NAME,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise LLMCallError(f"OpenRouter request failed with HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise LLMCallError(f"OpenRouter request failed: {error.reason}") from error

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMCallError(f"OpenRouter response did not contain message content: {body}") from error

    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise LLMCallError(f"OpenRouter returned unsupported content type: {type(content).__name__}")

    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise LLMCallError(f"OpenRouter returned non-JSON content: {content}") from error
