"""Anthropic (primary) and OpenAI (second provider) clients behind one interface."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from opspilot.config import Settings
from opspilot.llm.types import LLMResponse, Message, ToolCall, Usage

log = logging.getLogger("opspilot.llm")


def _retry(fn, retries: int):
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # SDKs raise typed errors; we retry transient ones only
            name = type(exc).__name__
            transient = any(t in name for t in ("RateLimit", "Timeout", "Connection", "InternalServer", "Overloaded"))
            if not transient or attempt == retries:
                raise
            last = exc
            time.sleep(0.5 * 2**attempt)
    raise last  # pragma: no cover


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic

        self.model = settings.anthropic_model
        self.retries = settings.llm_max_retries
        self.client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None, timeout=settings.llm_timeout_s, max_retries=0
        )

    @staticmethod
    def _convert(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)  # parallel tool results share one user turn
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def complete(self, system, messages, tools=None, *, max_tokens=800, meta=None) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._convert(messages),
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
        resp = _retry(lambda: self.client.messages.create(**kwargs), self.retries)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text, calls, Usage(resp.usage.input_tokens, resp.usage.output_tokens), self.model, self.provider)


class OpenAIClient:
    provider = "openai"

    def __init__(self, settings: Settings):
        import openai

        self.model = settings.openai_model
        self.retries = settings.llm_max_retries
        self.client = openai.OpenAI(api_key=settings.openai_api_key or None, timeout=settings.llm_timeout_s, max_retries=0)

    @staticmethod
    def _convert(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return out

    def complete(self, system, messages, tools=None, *, max_tokens=800, meta=None) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": self.model, "messages": self._convert(system, messages), "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}}
                for t in tools
            ]
        resp = _retry(lambda: self.client.chat.completions.create(**kwargs), self.retries)
        choice = resp.choices[0].message
        calls = []
        for tc in choice.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        u = resp.usage
        return LLMResponse(
            choice.content or "", calls, Usage(u.prompt_tokens, u.completion_tokens) if u else Usage(), self.model, self.provider
        )


class OllamaClient(OpenAIClient):
    """Local, free models via Ollama's OpenAI-compatible endpoint (no API key, no cost)."""

    provider = "ollama"

    def __init__(self, settings: Settings):
        import openai

        self.model = settings.ollama_model
        self.retries = settings.llm_max_retries
        self.client = openai.OpenAI(
            base_url=settings.ollama_base_url, api_key="ollama", timeout=settings.llm_timeout_s, max_retries=0
        )


class PaidProviderBlocked(RuntimeError):
    pass


def build_llm(settings: Settings, provider: str | None = None):
    provider = provider or settings.llm_provider
    if provider in ("anthropic", "openai") and not settings.allow_paid_llm:
        raise PaidProviderBlocked(
            f"LLM provider '{provider}' bills per token. This project is configured to spend nothing: use "
            "OPSPILOT_LLM_PROVIDER=mock or ollama, or set OPSPILOT_ALLOW_PAID_LLM=true to opt in deliberately."
        )
    if provider == "ollama":
        return OllamaClient(settings)
    if provider == "anthropic":
        return AnthropicClient(settings)
    if provider == "openai":
        return OpenAIClient(settings)
    from opspilot.llm.mock import MockLLM

    return MockLLM()
