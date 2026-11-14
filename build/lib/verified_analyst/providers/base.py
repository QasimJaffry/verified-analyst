"""Provider interface. The agent speaks one internal message format; each provider converts it.

Internal messages:
  {"role": "user", "content": str}
  {"role": "assistant", "content": str | None, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Turn:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class Provider(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn: ...
