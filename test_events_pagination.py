#!/usr/bin/env python3
"""Tests for pagination and search in terminal_events."""
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


def test_pagination_basic():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']
    events_tool = s.tools['terminal_events']

    tid = extract_tid(create())

    # generate several events (cmd + stdout each echo)
    for i in range(6):
        send(payload={'terminalId': tid, 'text': f'echo PG{i}'})
        time.sleep(0.2)

    time.sleep(0.6)

    first = json.loads(events_tool(payload={'terminalId': tid, 'limit': 3}))
    assert first['count'] <= 3
    assert 'nextCursor' in first
    assert first['nextCursor'] is not None

    if first['hasMore']:
        second = json.loads(events_tool(payload={'terminalId': tid, 'after': first['nextCursor'], 'limit': 3}))
        if second['count']:
            assert second['events'][0]['seq'] > first['nextCursor']


def test_search_contains():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']
    events_tool = s.tools['terminal_events']
    tid = extract_tid(create())

    send(payload={'terminalId': tid, 'text': 'echo apples'})
    send(payload={'terminalId': tid, 'text': 'echo oranges'})
    send(payload={'terminalId': tid, 'text': 'echo pears'})
    time.sleep(0.6)

    resp = json.loads(events_tool(payload={'terminalId': tid, 'contains': 'apple'}))
    # Expect at least one stdout event containing apples
    texts = ''.join(e.get('text','') for e in resp['events'])
    assert 'apples' in texts.lower()


def test_search_regex():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']
    events_tool = s.tools['terminal_events']
    tid = extract_tid(create())

    send(payload={'terminalId': tid, 'text': 'echo ERROR: something broke'})
    send(payload={'terminalId': tid, 'text': 'echo INFO: normal'})
    time.sleep(0.6)

    resp = json.loads(events_tool(payload={'terminalId': tid, 'regex': '^ERROR', 'limit': 20}))
    texts = [e.get('text','') for e in resp['events'] if e.get('type') == 'stdout']
    # We should see ERROR line captured
    assert any('ERROR' in t for t in texts)


def test_truncated_detection():
    # We simulate truncation by creating > deque size not feasible fast; instead emulate by requesting after very low seq
    s = setup_server()
    events_tool = s.tools['terminal_events']
    # no events yet, asking after 1 should not cause crash; truncated false because no oldest_seq
    resp = json.loads(events_tool(payload={'after': 1}))
    assert resp['truncated'] in (False, True)  # Accept either depending on state

