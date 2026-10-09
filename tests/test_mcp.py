import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from opspilot.mcp_server import create_server, resolve_principal

EXPECTED = {
    "search_kb",
    "create_ticket",
    "get_ticket",
    "list_my_tickets",
    "get_user_access",
    "notify_channel",
    "get_ai_spend_report",
    "reset_request",
    "get_approval_status",
}


def test_lists_all_tools_with_schemas(svc, alice):
    async def go():
        async with create_connected_server_and_client_session(create_server(svc, alice)._mcp_server) as s:
            tools = (await s.list_tools()).tools
            assert {t.name for t in tools} == EXPECTED
            reset = next(t for t in tools if t.name == "reset_request")
            assert "APPROVAL-GATED" in reset.description and set(reset.inputSchema["required"]) == {"system", "reason"}
            assert next(t for t in tools if t.name == "notify_channel").inputSchema["properties"]["channel"]["enum"]

    anyio.run(go)


def test_call_tool_through_protocol(svc, alice):
    async def go():
        async with create_connected_server_and_client_session(create_server(svc, alice)._mcp_server) as s:
            res = await s.call_tool("search_kb", {"query": "Which ports does the VPN need open?"})
            data = json.loads(res.content[0].text)
            assert not res.isError and data["results"][0]["doc_id"] == "vpn-troubleshooting"
            res = await s.call_tool("reset_request", {"system": "vpn", "reason": "profile corrupted"})
            assert json.loads(res.content[0].text)["status"] == "pending_approval"

    anyio.run(go)


def test_rbac_denial_surfaces_as_mcp_error_and_is_audited(svc, alice):
    async def go():
        async with create_connected_server_and_client_session(create_server(svc, alice)._mcp_server) as s:
            res = await s.call_tool("get_ai_spend_report", {})
            assert res.isError and "permission_denied" in res.content[0].text

    anyio.run(go)
    assert any(r["outcome"].startswith("denied:") for r in svc.audit.recent(5))


def test_client_id_labels_spend_and_audit(svc):
    p = resolve_principal(svc, "alice@opspilot.example", "cursor")
    assert p.client_id == "cursor"

    async def go():
        async with create_connected_server_and_client_session(create_server(svc, p)._mcp_server) as s:
            await s.call_tool("search_kb", {"query": "wifi password"})

    anyio.run(go)
    assert svc.audit.recent(1)[0]["client_id"] == "cursor"


def test_resolve_principal_rejects_unknown_user(svc):
    with pytest.raises(RuntimeError):
        resolve_principal(svc, "ghost@nowhere.example")


def test_real_stdio_subprocess(tmp_path):
    """Spawn the server exactly as Claude Desktop / Cursor would and talk MCP over stdio."""
    root = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "OPSPILOT_ENV": "test",
        "OPSPILOT_DB_PATH": str(tmp_path / "t.db"),
        "OPSPILOT_DATA_DIR": str(tmp_path),
        "OPSPILOT_MCP_USER_EMAIL": "bob@opspilot.example",
        "OPSPILOT_MCP_CLIENT_ID": "claude-desktop",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "opspilot.mcp_server"], env=env, cwd=str(root))

    async def go():
        with anyio.fail_after(60):
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                assert {t.name for t in (await s.list_tools()).tools} == EXPECTED
                res = await s.call_tool("get_ai_spend_report", {})  # team lead: allowed
                assert not res.isError and json.loads(res.content[0].text)["team"] == "Engineering"

    anyio.run(go)
