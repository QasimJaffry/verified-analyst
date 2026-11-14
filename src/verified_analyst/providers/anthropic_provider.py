"""Anthropic Messages API provider (tool use)."""

from __future__ import annotations

import os
from typing import Any

from .base import Turn, ToolCall, Usage

DEFAULT_MODEL = "claude-sonnet-5-5"


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert internal messages. All tool_results for one assistant turn must share one user message."""
    out: list[dict] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            out.append({"role": "user", "content": list(pending)})
            pending.clear()

    for m in messages:
        if m["role"] == "tool":
            pending.append({"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]})
            continue
        flush()
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        else:
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]})
            out.append({"role": "assistant", "content": blocks})
    flush()
    return out


class AnthropicProvider:
    def __init__(self, model: str | None = None, client: Any = None, max_tokens: int = 2048) -> None:
        self.model = model or os.environ.get("VA_MODEL", DEFAULT_MODEL)
        self.name = f"anthropic:{self.model}"
        self.max_tokens = max_tokens
        if client is None:
            import anthropic  # lazy: optional dependency

            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.client = client

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=to_anthropic_messages(messages),
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                   for t in tools],
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input)))
        usage = Usage(getattr(resp.usage, "input_tokens", 0), getattr(resp.usage, "output_tokens", 0))
        return Turn("\n".join(text_parts) or None, calls, usage)
