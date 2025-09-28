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
- `terminal_events({terminalId?, since_ts?, limit?, types?})` -> retrieve recent structured events (create, cmd, stdout, clear, interrupt, dispose)

Quality-of-Life (Verbose / Meta Responses)
You can opt in to structured guidance in responses by including one of the flags
`verbose: true`, `meta: true`, or `return_meta: true` inside the `payload` argument
you pass to a tool. This leaves the default (minimal) responses unchanged for
existing clients, while agents that benefit from hints can request richer JSON.

Examples

Create (verbose):
```json
{
	"terminalId": "[mcp] bright-otter",
	"cwd": "/Users/me",
	"created": true,
	"hint": "Use terminal_send to run commands, then terminal_read to fetch output.",
	"next": ["terminal_send", "terminal_read", "terminal_list"]
}
```

Send (auto-created + verbose):
```json
{
	"terminalId": "[mcp] bright-otter",
	"status": "created",
	"cwd": "/Users/me",
	"hint": "Use terminal_read to fetch output.",
	"next": ["terminal_read", "terminal_clear", "terminal_interrupt"]
}
```

Send (existing terminal, verbose):
```json
{
	"terminalId": "[mcp] bright-otter",
	"status": "sent",
	"bytes": 15,
	"hint": "Call terminal_read to view buffered output."
}
```

Read (verbose, non-empty):
```json
{
	"output": "HELLO_WORLD\n",
	"lines": 1,
	"terminalId": "[mcp] bright-otter",
	"empty": false,
	"hint": "Use lines parameter to tail recent output."
}
```

Read (verbose, empty buffer):
```json
{
	"output": "",
	"lines": 0,
	"terminalId": "[mcp] bright-otter",
	"empty": true,
	"hint": "Buffer empty. Use terminal_send to execute a command."
}
```

Error (verbose send without text):
```json
{
	"error": "text required",
	"hint": "Provide shell input in the \"text\" parameter."
}
```

These additions are backwards compatible: omit the flags to retain the original
concise string / plain output responses.

Event Stream & Introspection

The server maintains an in-memory circular buffer (size 2000) of recent events
also written to a newline-delimited JSON log at `.terminal-mcp/events.log` (can
disable with `TERMINAL_MCP_EVENT_LOG_ENABLED=0`).

Event Types:
- `create` – terminal created
- `cmd` – a command was written (before output appears)
- `stdout` – chunk of terminal output captured
- `clear` – buffer cleared via tool
- `interrupt` – Ctrl-C sent
- `dispose` – terminal disposed (now includes `exitCode` when available)

Use the `terminal_events` tool for polling:

```jsonc
// Example payload
{
	"terminalId": "[mcp] bright-otter",
	"since_ts": "2025-09-28T12:34:56.789Z",
	"limit": 100,
	"types": ["cmd", "stdout"]
}
```

Response:
```json
{
	"events": [
		{"terminalId": "[mcp] bright-otter", "type": "cmd", "text": "echo hi", "ts": "..."},
		{"terminalId": "[mcp] bright-otter", "type": "stdout", "text": "hi\n", "ts": "..."}
	],
	"count": 2
}
```

Dispose now returns exit code when verbose:
```json
{
	"terminalId": "[mcp] bright-otter",
	"disposed": true,
	"exitCode": 0
}
```


Quickstart
1. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run the server (from this folder):

```bash
python server.py
```

3. Use a FastMCP client or the MCP Inspector to call the tools above.

Testing
Run the test suite to validate functionality:

```bash
# Run smoke test (basic functionality)
python test_smoke.py

# Run comprehensive test suite
python -m pytest test_terminal.py -v

# Run all tests
python -m pytest -v
```

The test suite covers:
- Terminal creation and disposal
- Command execution and output reading
- Buffer management (clear, read options)
- Interrupt functionality
- Multiple terminal management
- Error handling

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

	## Adding This Server To `mcp.json` (Expanded Guide)

	Below are several common configuration patterns you can paste into your client-side `mcp.json` (used by MCP Inspector, compatible agents, or orchestration layers). Adjust paths to match your system.

	### 1. Local Clone + Virtualenv (Recommended for Development)
	Clone the repository and create a virtual environment in the project root:

	```bash
	git clone https://github.com/your-org/terminal-mcp-server.git
	cd terminal-mcp-server
	python -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```

	Then point `mcp.json` at the python executable and `server.py`:

	```jsonc
	{
		"servers": {
			"terminal-mcp": {
				"command": "/absolute/path/to/terminal-mcp-server/.venv/bin/python",
				"args": ["/absolute/path/to/terminal-mcp-server/server.py"],
				"type": "stdio",
				"description": "Local terminal management MCP server (dev virtualenv)",
				"dev": { "watch": "/absolute/path/to/terminal-mcp-server/app/**/*.py" }
			}
		}
	}
	```

	Tip: Run `pwd` inside the repo to copy the absolute path; then append `/.venv/bin/python` and `/server.py` accordingly.

	### 2. Editable Install in a Shared Environment
	If this repo is part of a monorepo or shared dev environment you may prefer an editable install:

	```bash
	pip install -e /absolute/path/to/terminal-mcp-server
	```

	Then you can invoke using module-style execution (optional). Update `mcp.json`:

	```jsonc
	{
		"servers": {
			"terminal-mcp": {
				"command": "/path/to/python",
				"args": ["/absolute/path/to/terminal-mcp-server/server.py"],
				"type": "stdio",
				"description": "Terminal MCP (editable install)"
			}
		}
	}
	```

	### 3. Windows (PowerShell) Example
	Paths differ and the virtualenv python lives under `Scripts`:

	```jsonc
	{
		"servers": {
			"terminal-mcp": {
				"command": "C:/path/to/terminal-mcp-server/.venv/Scripts/python.exe",
				"args": ["C:/path/to/terminal-mcp-server/server.py"],
				"type": "stdio",
				"description": "Terminal MCP on Windows"
			}
		}
	}
	```

	### 4. Using `uv` (Fast Python Installer)
	If you prefer `uv` for environment management (https://github.com/astral-sh/uv):

	```bash
	uv venv .venv
	source .venv/bin/activate
	uv pip install -r requirements.txt
	```

	Then the `mcp.json` is the same as (1) – just ensure you still reference the venv Python path.

	### 5. Multiple Terminal Servers (Namespacing)
	You can register more than one server (e.g. one for local machine and one for a remote wrapper) by adding multiple entries under `servers`:

	```jsonc
	{
		"servers": {
			"terminal-local": { "command": "/local/venv/bin/python", "args": ["/local/terminal-mcp/server.py"], "type": "stdio" },
			"terminal-alt":   { "command": "/alt/venv/bin/python",   "args": ["/alt/terminal-mcp/server.py"],   "type": "stdio" }
		}
	}
	```

	### 6. Environment Variables (Optional)
	You can influence runtime behavior via environment variables in future expansions or by wrapping the command. For event log control today:

	| Variable | Purpose | Default |
	|----------|---------|---------|
	| `TERMINAL_MCP_EVENT_LOG_ENABLED` | Enable/disable persistent event log | `1` |
	| `TERMINAL_MCP_EVENT_DIR` | Custom directory for the event log | `.terminal-mcp/` under repo |
	| `TERMINAL_MCP_EVENT_LOG` | Override full path to event log file | `<EVENT_DIR>/events.log` |

	If your MCP host supports per-server environment injection you may extend the `servers.terminal-mcp` object with an `env` field (syntax depends on the host). Example (conceptual):

	```jsonc
	{
		"servers": {
			"terminal-mcp": {
				"command": "/venv/bin/python",
				"args": ["/repo/server.py"],
				"type": "stdio",
				"env": {
					"TERMINAL_MCP_EVENT_LOG_ENABLED": "0"
				}
			}
		}
	}
	```

	### 7. Verifying Setup
	After editing `mcp.json`, restart your MCP-enabled client / inspector and look for a server named "terminal-mcp" (or the name you chose). List tools; you should see:

	```
	terminal_create, terminal_send, runCommand, terminal_read, terminal_interrupt, terminal_clear, terminal_dispose, terminal_list, terminal_events
	```

	Run a quick sanity cycle:
	1. Call `terminal_create` (optionally with `{ "payload": { "verbose": true } }`).
	2. Call `terminal_send` with a simple command (e.g. `echo hello`).
	3. Call `terminal_read` to confirm output buffering.
	4. Poll `terminal_events` to observe `create`, `cmd`, and `stdout` entries.
	5. Call `terminal_dispose` (verbose) to verify `exitCode` appears.

	### 8. Troubleshooting
	| Symptom | Likely Cause | Fix |
	|---------|--------------|-----|
	| No tools appear | Wrong path to python or server file | Verify absolute paths in `command` and `args` |
	| Empty output after send | Command still running / buffering | Add a short sleep then `terminal_read`; or run simpler command |
	| Missing events | Pagination cursor set too high | Remove `after` or lower it; check `oldestSeq/newestSeq` in response |
	| Permission error launching | CWD or python not accessible | Use a directory you own; ensure executable bit | 

	If issues persist, run the server manually (`python server.py`) and observe stderr/stdout for import or runtime errors.

	---

	This section aims to make integration copy/paste friendly. If you use a different MCP host that requires a slightly different schema, adapt the key names but keep the `command`, `args`, and `type: stdio` trio intact.


Notes
- `cwd` is honored when starting the shell where supported; if the platform does not accept `cwd` for the Popen call the server will still store the requested `cwd` and return it (shell may start in the default directory).
- Reads are non-destructive by default; call `terminal_clear` to empty a buffer.
- This server intentionally only exposes terminal-related tools so it can be included or deployed separately from a larger MCP server.

Keywords: terminal, pty, mcp, fastmcp, shell, remote-shell, terminal-mcp
