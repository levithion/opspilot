# Connecting MCP clients

The same server (`python -m opspilot.mcp_server`) works for every client. A stdio MCP server is spawned **per
user** by the client, so the acting user comes from environment variables and the tool layer applies that
user's RBAC. `OPSPILOT_MCP_CLIENT_ID` labels the client so the shadow-AI view attributes usage.

Environment used by the server:

| Variable | Meaning |
|---|---|
| `OPSPILOT_MCP_USER_EMAIL` | Directory user the tools act as (required in prod; demo default alice) |
| `OPSPILOT_MCP_CLIENT_ID` | `claude-desktop`, `cursor`, `vscode-copilot`, … |
| `OPSPILOT_DATA_DIR` | Shared data dir so MCP calls and the API see the same tickets/audit log |

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "opspilot": {
      "command": "/ABSOLUTE/PATH/TO/OpsPilot/.venv/bin/python",
      "args": ["-m", "opspilot.mcp_server"],
      "env": {
        "OPSPILOT_MCP_USER_EMAIL": "alice@opspilot.example",
        "OPSPILOT_MCP_CLIENT_ID": "claude-desktop",
        "OPSPILOT_DATA_DIR": "/ABSOLUTE/PATH/TO/OpsPilot/data"
      }
    }
  }
}
```

## Cursor

`.cursor/mcp.json` in the project (or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "opspilot": {
      "command": "/ABSOLUTE/PATH/TO/OpsPilot/.venv/bin/python",
      "args": ["-m", "opspilot.mcp_server"],
      "env": { "OPSPILOT_MCP_USER_EMAIL": "alice@opspilot.example", "OPSPILOT_MCP_CLIENT_ID": "cursor" }
    }
  }
}
```

## VS Code / GitHub Copilot (agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "opspilot": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "opspilot.mcp_server"],
      "env": { "OPSPILOT_MCP_USER_EMAIL": "alice@opspilot.example", "OPSPILOT_MCP_CLIENT_ID": "vscode-copilot" }
    }
  }
}
```

## OpenAI-based or custom clients

Use any MCP client SDK with the stdio parameters above, or skip MCP and call the HTTP gateway
(`POST /api/tools/{name}` with a bearer token), which enforces the identical policy. The agent itself can
run on OpenAI by setting `OPSPILOT_LLM_PROVIDER=openai`.

## Verify

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m opspilot.mcp_server
```

> **Verification status:** the server is tested in CI through the MCP protocol (in-memory and a real stdio
> subprocess using the official client), and was used from **Claude Desktop** (a `search_kb` call from a chat,
> recorded in the audit log with client id `claude-desktop`). Cursor and VS Code/Copilot are configured per the
> snippets above but have not been exercised; verify them before listing them on a resume.
