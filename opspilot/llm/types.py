"""Provider-neutral message / tool-call types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""


@dataclass
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]


# Neutral messages are plain dicts:
#   {"role": "user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Message = dict[str, Any]


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        *,
        max_tokens: int = 800,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse: ...
