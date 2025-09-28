#!/usr/bin/env python3
"""Tests for terminal_events tool and lifecycle event emission."""
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


def get_events(s, **payload):
    ev = s.tools['terminal_events'](payload=payload if payload else None)
    return json.loads(ev)['events']


def test_event_flow_basic():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']
    read = s.tools['terminal_read']
    clear = s.tools['terminal_clear']
    interrupt = s.tools['terminal_interrupt']
    dispose = s.tools['terminal_dispose']
    # events tool accessible via s.tools['terminal_events']

    # create terminal
    created = create(payload={'verbose': True})
    tid = extract_tid(created)

    # send command
    send(payload={'terminalId': tid, 'text': 'echo EVT_ONE'})
    time.sleep(0.5)
    # read to allow stdout event to accumulate
    _ = read(payload={'terminalId': tid, 'lines': 10})

    # clear buffer
    clear(terminalId=tid)

    # send long running and interrupt
    send(payload={'terminalId': tid, 'text': 'sleep 5'})
    time.sleep(0.3)
    interrupt(terminalId=tid)

    time.sleep(0.8)

    # dispose
    dispose_resp = dispose(payload={'terminalId': tid, 'verbose': True})
    dispose_obj = json.loads(dispose_resp)
    assert dispose_obj.get('disposed') is True
    assert 'exitCode' in dispose_obj  # may be None if process hasn't exited but usually integer

    # fetch all events
    all_events = get_events(s, limit=500)
    # Filter by terminal id
    term_events = [e for e in all_events if e.get('terminalId') == tid]

    # Expect at least these event types
    types = {e.get('type') for e in term_events}
    assert 'create' in types
    assert 'cmd' in types
    assert 'stdout' in types  # echo output
    assert 'clear' in types
    assert 'interrupt' in types
    assert 'dispose' in types


def test_events_filtering():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']

    t1 = extract_tid(create())
    t2 = extract_tid(create())

    send(payload={'terminalId': t1, 'text': 'echo ONE'})
    send(payload={'terminalId': t2, 'text': 'echo TWO'})

    time.sleep(0.6)

    events_t1 = get_events(s, terminalId=t1)
    ids_t1 = {e.get('terminalId') for e in events_t1}
    assert ids_t1 == {t1} or ids_t1 == {t1, None}

    events_cmd = get_events(s, types=['cmd'])
    assert all(ev.get('type') == 'cmd' for ev in events_cmd)


def test_events_limit_and_since():
    s = setup_server()
    create = s.tools['terminal_create']
    send = s.tools['terminal_send']

    tid = extract_tid(create())
    for i in range(5):
        send(payload={'terminalId': tid, 'text': f'echo MSG_{i}'})
        time.sleep(0.2)

    time.sleep(0.6)
    all_ev = get_events(s, terminalId=tid)
    assert len(all_ev) >= 5
    if all_ev:
        midpoint_ts = all_ev[len(all_ev)//2]['ts']
        since_ev = get_events(s, terminalId=tid, since_ts=midpoint_ts)
        # All returned events should have ts > midpoint_ts
        assert all(e['ts'] > midpoint_ts for e in since_ev)

    limited = get_events(s, terminalId=tid, limit=2)
    assert len(limited) <= 2
