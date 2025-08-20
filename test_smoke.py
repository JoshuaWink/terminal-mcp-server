#!/usr/bin/env python3
"""Smoke test for terminal-mcp-server cross-platform changes.
Creates a terminal via the tools, sends a command, reads output, and disposes.
"""
import time
import json
import sys
import app.tools as tools

class DummyServer:
    def __init__(self):
        self.tools = {}
    def tool(self, name=None, description=None):
        def deco(f):
            # register by the given name if provided, else function name
            key = name or getattr(f, '__name__', str(f))
            self.tools[key] = f
            return f
        return deco


def run_smoke():
    server = DummyServer()
    tools.register_tools(server)
    assert 'terminal_create' in server.tools
    create = server.tools['terminal_create']
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']
    dispose = server.tools['terminal_dispose']

    created = create()
    try:
        created_obj = json.loads(created)
        tid = created_obj.get('terminalId', created)
    except Exception:
        tid = created
    print('created:', tid)

    # send a command that prints a stable token
    token = 'SMOKE_TEST_OK'
    cmd = f'echo {token}'
    send(terminalId=tid, text=cmd)

    # wait for the background reader to collect output
    time.sleep(2.0)

    out = read(terminalId=tid, payload={'lines': 50})
    print('raw output length:', len(out) if out is not None else None)
    print('raw output repr:', repr(out))

    disposed = dispose(terminalId=tid)
    print('disposed:', disposed)

    if out and token in out:
        print('SMOKE TEST: PASS')
        return 0
    else:
        print('SMOKE TEST: FAIL')
        return 2

if __name__ == '__main__':
    rc = run_smoke()
    sys.exit(rc)
