# terminal-mcp-server

A small FastMCP server that exposes terminal (PTY) management APIs over MCP.

Summary of what's new in this hotfix
- Added support for creating terminals with a specific working directory (`cwd`).
- `terminal_create` now always returns structured JSON: {"terminalId":..., "cwd":...}.
- `terminal_send` will create a terminal when called without `terminalId` and returns JSON including the created terminal's `cwd`.
- `terminal_list` now includes each PTY's `cwd`.
- Fixed the recommended virtualenv install and made examples clearer.

Features
- Create and manage local PTY-backed terminals from MCP clients.
- Non-blocking send/read of terminal output.
- Interrupt (Ctrl-C), clear, dispose terminals, and list active PTYs.

Available tools (summary)
- `terminal_create(name?, cwd?)` -> creates a PTY and returns JSON {terminalId, cwd}
- `terminal_send(terminalId?, text)` -> sends text to PTY; if `terminalId` omitted a PTY is created and returned as JSON
- `terminal_read(terminalId, {strip_ansi:true, lines:N})` -> read buffered output
- `terminal_interrupt(terminalId)` -> send Ctrl-C
- `terminal_clear(terminalId)` -> clear buffer
- `terminal_dispose(terminalId)` -> terminate and clean up
- `terminal_list()` -> list known PTYs (includes `cwd` now)

Quickstart
1. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]"
```

2. Run the server (from this folder):

```bash
python server.py
```

3. Use a FastMCP client or the MCP Inspector to call the tools above.

Usage examples

- Create a terminal in the repository directory and inspect the JSON return:

```py
# payload example sent to the MCP server
payload = {"name": "repo-pty", "cwd": "/path/to/repo"}
# server returns: {"terminalId": "repo-pty", "cwd": "/path/to/repo"}
```

- Send without an explicit terminalId (server will create one and return JSON):

```py
# calling terminal_send with only text
# returns: {"terminalId": "mcp-terminal-...,", "status": "created", "cwd": "/home/user"}
```

- Read output (strip ANSI sequences by default):

```py
# terminal_read(terminalId, {"strip_ansi": True, "lines": 200})
```

Example `mcp.json` fragment
Use this snippet in your MCP client configuration so the inspector or client knows how to run this server locally via stdio:

```jsonc
{
	"servers": {
		"terminal-mcp": {
			"command": "/path/to/your/venv/bin/python",
			"args": ["/path/to/terminal-mcp-server/server.py"],
			"type": "stdio",
			"description": "MCP stdio server exposing terminal commands",
			"dev": { "watch": "/path/to/terminal-mcp-server/app/**/*.py" }
		}
	}
}
```

Notes
- `cwd` is honored when starting the shell where supported; if the platform does not accept `cwd` for the Popen call the server will still store the requested `cwd` and return it (shell may start in the default directory).
- Reads are non-destructive by default; call `terminal_clear` to empty a buffer.
- This server intentionally only exposes terminal-related tools so it can be included or deployed separately from a larger MCP server.

Keywords: terminal, pty, mcp, fastmcp, shell, remote-shell, terminal-mcp
