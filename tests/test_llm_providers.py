"""Adapter tests with fake SDK clients: verifies message/tool translation without network access."""

import json
from types import SimpleNamespace as NS

import pytest

from opspilot.config import Settings
from opspilot.llm.providers import AnthropicClient, OpenAIClient, PaidProviderBlocked, _retry, build_llm
from opspilot.llm.types import ToolCall, ToolDef

TOOLS = [ToolDef("search_kb", "Search", {"type": "object", "properties": {"query": {"type": "string"}}})]
HISTORY = [
    {"role": "user", "content": "vpn broken"},
    {
        "role": "assistant",
        "content": "Checking",
        "tool_calls": [ToolCall("t1", "search_kb", {"query": "vpn"}), ToolCall("t2", "get_ticket", {"ticket_id": "HELP-1001"})],
    },
    {"role": "tool", "tool_call_id": "t1", "name": "search_kb", "content": '{"results": []}'},
    {"role": "tool", "tool_call_id": "t2", "name": "get_ticket", "content": '{"ok": false}'},
]


def make(cls, tmp_path, **kw):
    s = Settings(env="test", db_path=":memory:", data_dir=tmp_path, anthropic_api_key="k", openai_api_key="k", **kw)
    return cls(s)


def test_anthropic_message_conversion_merges_parallel_tool_results(tmp_path):
    msgs = AnthropicClient._convert(HISTORY)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["type"] for b in msgs[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["t1", "t2"]  # both results in ONE user turn


def test_anthropic_complete_parses_response(tmp_path):
    c = make(AnthropicClient, tmp_path)
    seen = {}

    def create(**kw):
        seen.update(kw)
        return NS(
            content=[NS(type="text", text="Let me look"), NS(type="tool_use", id="x1", name="search_kb", input={"query": "vpn"})],
            usage=NS(input_tokens=120, output_tokens=30),
        )

    c.client = NS(messages=NS(create=create))
    r = c.complete("sys", HISTORY[:1], TOOLS, max_tokens=300)
    assert seen["system"] == "sys" and seen["max_tokens"] == 300 and seen["tools"][0]["input_schema"]["type"] == "object"
    assert r.text == "Let me look" and r.tool_calls[0].arguments == {"query": "vpn"}
    assert (r.usage.input_tokens, r.usage.output_tokens, r.provider) == (120, 30, "anthropic")


def test_openai_message_conversion():
    msgs = OpenAIClient._convert("SYS", HISTORY)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"query": "vpn"})
    assert [m["role"] for m in msgs[3:]] == ["tool", "tool"] and msgs[3]["tool_call_id"] == "t1"


def test_openai_complete_parses_response_and_bad_json(tmp_path):
    c = make(OpenAIClient, tmp_path)
    msg = NS(
        content=None,
        tool_calls=[
            NS(id="c1", function=NS(name="search_kb", arguments='{"query": "wifi"}')),
            NS(id="c2", function=NS(name="get_ticket", arguments="{not json")),
        ],
    )
    c.client = NS(
        chat=NS(
            completions=NS(create=lambda **kw: NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=50, completion_tokens=9)))
        )
    )
    r = c.complete("sys", HISTORY[:1], TOOLS)
    assert r.tool_calls[0].arguments == {"query": "wifi"} and r.tool_calls[1].arguments == {}
    assert (r.usage.input_tokens, r.usage.output_tokens, r.provider) == (50, 9, "openai")


def test_retry_only_transient(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    class RateLimitError(Exception):
        pass

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError()
        return "ok"

    assert _retry(flaky, 3) == "ok" and calls["n"] == 3

    def fatal():
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        _retry(fatal, 3)


def test_factory_selects_provider(tmp_path):
    paid = {"allow_paid_llm": True}
    assert build_llm(Settings(env="test", data_dir=tmp_path, llm_provider="mock")).provider == "mock"
    assert build_llm(Settings(env="test", data_dir=tmp_path, anthropic_api_key="k", **paid), "anthropic").provider == "anthropic"
    assert build_llm(Settings(env="test", data_dir=tmp_path, openai_api_key="k", **paid), "openai").provider == "openai"


def test_paid_providers_are_blocked_by_default(tmp_path):
    """Zero-spend guard: a stray API key in the environment must not be enough to incur cost."""
    s = Settings(env="test", data_dir=tmp_path, anthropic_api_key="k", openai_api_key="k")
    for provider in ("anthropic", "openai"):
        with pytest.raises(PaidProviderBlocked, match="spend nothing"):
            build_llm(s, provider)


def test_ollama_is_free_and_allowed(tmp_path, svc):
    s = Settings(env="test", data_dir=tmp_path, llm_provider="ollama")
    c = build_llm(s)
    assert c.provider == "ollama" and c.model == s.ollama_model
    cost = svc.cost.record(
        team="IT",
        user_id="u",
        client_id="web",
        provider="ollama",
        model="llama3.2",
        input_tokens=5_000_000,
        output_tokens=5_000_000,
    )
    assert cost == 0.0
