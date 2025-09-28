#!/usr/bin/env python3
"""Tests for verbose/meta QoL responses in terminal tools."""
import json
import time
import app.tools as tools

class DummyServer:
    def __init__(self):
        self.tools = {}
    def tool(self, name=None, description=None):
        def deco(f):
            key = name or getattr(f, '__name__', str(f))
            self.tools[key] = f
            return f
        return deco


def setup_server():
    s = DummyServer()
    tools.register_tools(s)
    return s


def extract_tid(resp):
    try:
        obj = json.loads(resp)
        return obj.get('terminalId', resp)
    except Exception:
        return resp


def test_verbose_create():
    s = setup_server()
    create = s.tools['terminal_create']
    resp = create(payload={'verbose': True})
    obj = json.loads(resp)
    assert obj['created'] is True
    assert 'hint' in obj
    assert 'next' in obj


def test_verbose_send_and_read():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']
    read = s.tools['terminal_read']

    tid = extract_tid(create())
    send_resp = send(payload={'terminalId': tid, 'text': 'echo VERBOSE_TOKEN', 'verbose': True})
    send_obj = json.loads(send_resp)
    assert send_obj['status'] in ('sent','created')
    assert 'hint' in send_obj

    time.sleep(1.0)
    read_resp = read(payload={'terminalId': tid, 'verbose': True})
    read_obj = json.loads(read_resp)
    assert 'output' in read_obj
    assert 'lines' in read_obj
    assert 'hint' in read_obj
    assert 'VERBOSE_TOKEN' in read_obj['output']


def test_verbose_error_send_missing_text():
    s = setup_server()
    send = s.tools['terminal_send']
    resp = send(payload={'terminalId': 'does-not-exist', 'verbose': True})
    obj = json.loads(resp)
    # Should report error about text required OR terminal not found (text missing precedes)
    assert 'error' in obj


def test_verbose_read_empty_buffer_hint():
    s = setup_server()
    create = s.tools['terminal_create']
    read = s.tools['terminal_read']
    tid = extract_tid(create())
    # Immediately read before any output
    resp = read(payload={'terminalId': tid, 'verbose': True})
    obj = json.loads(resp)
    assert obj['empty'] is True
    assert 'hint' in obj

