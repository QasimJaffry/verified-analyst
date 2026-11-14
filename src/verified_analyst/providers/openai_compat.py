"""OpenAI-compatible chat-completions provider (OpenRouter, vLLM, Ollama, LM Studio, OpenAI...).

Standard library only (urllib), so the package has no HTTP dependency.
Env: VA_BASE_URL (default OpenRouter), VA_API_KEY or OPENROUTER_API_KEY, VA_MODEL.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import Turn, ToolCall, Usage


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            msg: dict = {"role": "assistant", "content": m.get("content")}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
    return out


class ProviderError(RuntimeError):
    pass


class OpenAICompatProvider:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 120.0) -> None:
        self.model = model or os.environ.get("VA_MODEL")
        if not self.model:
            raise ProviderError("set VA_MODEL (or pass --model) for the openai-compatible provider")
        self.base_url = (base_url or os.environ.get("VA_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("VA_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
        self.timeout = timeout
        self.name = f"openai-compat:{self.model}"

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": to_openai_messages(system, messages),
            "tools": [{"type": "function",
                       "function": {"name": t["name"], "description": t["description"],
                                    "parameters": t["parameters"]}} for t in tools],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ProviderError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"network error: {e.reason}") from e

        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"unexpected response shape: {str(data)[:300]}") from e

        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
            except ValueError:
                args = {"_unparsed_arguments": raw}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        u = data.get("usage") or {}
        return Turn(msg.get("content") or None, calls,
                    Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))
