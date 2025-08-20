"""
Minimal terminal-only tools for a dedicated Terminal MCP server.
Expose a small subset of the original terminal utilities: create, send, read,
interrupt, clear, dispose, list.
"""

import os
import select
import signal
import threading
import time
import uuid
import subprocess
import json
import re
from collections import deque
from datetime import datetime
import hashlib

_local_ptys = {}
_MAX_BUFFER_CHARS = 200000
_EVENT_DEQUE_MAX = 2000

# Event log configuration: write newline-delimited JSON events to a local file.
# Can be disabled by setting TERMINAL_MCP_EVENT_LOG_ENABLED=0 in the env.
# default event log stored inside the repository under .terminal-mcp/events.log
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_EVENT_LOG_DIR = os.environ.get('TERMINAL_MCP_EVENT_DIR', os.path.join(_REPO_ROOT, '.terminal-mcp'))
_EVENT_LOG_FILE = os.environ.get('TERMINAL_MCP_EVENT_LOG', os.path.join(_EVENT_LOG_DIR, 'events.log'))
_EVENT_LOG_ENABLED = os.environ.get('TERMINAL_MCP_EVENT_LOG_ENABLED', '1') == '1'

# in-memory recent events for poll-style queries
_recent_events = deque(maxlen=_EVENT_DEQUE_MAX)

# lightweight word lists used to create deterministic human-friendly ids
# picked by indices derived from the current timestamp (time_ns) so rapid
# calls still vary.
ADJECTIVES = [
    'quick', 'brave', 'clever', 'rusty', 'silent', 'golden', 'husky', 'lucky', 'fuzzy', 'bright', 'calm', 'sly'
]

NOUNS = [
    'fox', 'otter', 'panda', 'tiger', 'beetle', 'hawk', 'lark', 'walrus', 'badger', 'otter', 'koala', 'moose'
]


def _generate_name(seed: int = None) -> str:
    """Return a deterministic "mcp-<adj>-<noun>" name derived from seed/time.

    If seed is None the function uses time.time_ns() so names change rapidly.
    """
    if seed is None:
        seed = time.time_ns()
    try:
        a = ADJECTIVES[seed % len(ADJECTIVES)]
        b = NOUNS[(seed >> 16) % len(NOUNS)]
        return f"[mcp] {a}-{b}"
    except Exception:
        # fallback to numeric timestamp-like id
        return f"[mcp]-{int(time.time() * 1000)}"


def _now_ts():
    return datetime.utcnow().isoformat() + 'Z'


def _write_event_to_log(ev: dict) -> None:
    if not _EVENT_LOG_ENABLED:
        return
    try:
        os.makedirs(_EVENT_LOG_DIR, exist_ok=True)
        # ensure a compact JSON object per line
        with open(_EVENT_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _publish_event(ev: dict) -> None:
    # attach timestamp if missing
    if 'ts' not in ev:
        ev['ts'] = _now_ts()
    try:
        _recent_events.append(ev)
    except Exception:
        pass
    _write_event_to_log(ev)


def _reader(pty_id: str):
    """
    Generic reader that supports both Unix pty masters (master_fd) and
    subprocess pipe-backed terminals (proc.stdout). The thread reads data
    and appends to the in-memory buffer while publishing stdout events.
    """
    m = _local_ptys.get(pty_id)
    if not m:
        return
    stop_event = m.get('stop')
    lock = m.get('lock')
    buffer = m.get('buffer')

    # If this entry has a numeric master_fd we treat it as a pty master
    if isinstance(m.get('master_fd'), int):
        master_fd = m.get('master_fd')
        try:
            while not stop_event.is_set():
                r, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in r:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    text = data.decode(errors='replace')
                    try:
                        _publish_event({'terminalId': pty_id, 'type': 'stdout', 'text': text, 'cwd': m.get('cwd')})
                    except Exception:
                        pass
                    with lock:
                        buffer.append(text)
                        try:
                            total = sum(len(s) for s in buffer)
                            if total > _MAX_BUFFER_CHARS:
                                joined = ''.join(buffer)
                                kept = joined[-_MAX_BUFFER_CHARS:]
                                buffer.clear()
                                buffer.append(kept)
                        except Exception:
                            pass
        except Exception:
            pass
        return

    # Otherwise, if a subprocess is present, read from its stdout.
    proc = m.get('proc')
    if not proc:
        return
    # proc.stdout.read may block; it's fine because this runs in a daemon thread.
    try:
        while not stop_event.is_set():
            try:
                data = proc.stdout.read(4096)
            except Exception:
                # if the descriptor is closed or broken, stop
                break
            if not data:
                # EOF
                break
            # proc.stdout.read returns bytes on Python when opened in binary,
            # but the Popen created below uses binary mode. Handle both.
            if isinstance(data, bytes):
                text = data.decode(errors='replace')
            else:
                text = data
            try:
                _publish_event({'terminalId': pty_id, 'type': 'stdout', 'text': text, 'cwd': m.get('cwd')})
            except Exception:
                pass
            with lock:
                buffer.append(text)
                try:
                    total = sum(len(s) for s in buffer)
                    if total > _MAX_BUFFER_CHARS:
                        joined = ''.join(buffer)
                        kept = joined[-_MAX_BUFFER_CHARS:]
                        buffer.clear()
                        buffer.append(kept)
                except Exception:
                    pass
    except Exception:
        pass


def register_tools(server):
    @server.tool(name="terminal_create", description="Create a terminal (pty-backed). Returns terminal id and cwd.")
    def terminal_create(name: str = None, cwd: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            name = payload.get('name', name)
            cwd = payload.get('cwd', cwd)
        # generate a human-friendly deterministic name when not provided
        term_name = name or _generate_name()
        # default to the user's home directory if cwd not provided
        if not cwd:
            try:
                cwd = os.path.expanduser('~')
            except Exception:
                cwd = None
        try:
            # Prefer a real pty on POSIX when available.
            shell = os.environ.get('SHELL', '/bin/sh') if os.name != 'nt' else (os.environ.get('ComSpec', 'cmd.exe'))
            try:
                master_fd, slave_fd = os.openpty()
                # Start the shell attached to the pty slave
                try:
                    proc = subprocess.Popen([shell], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True, cwd=cwd)
                except TypeError:
                    proc = subprocess.Popen([shell], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
                os.close(slave_fd)
                lid = term_name if term_name else f"local-pty-{uuid.uuid4().hex[:8]}"
                buf = []
                lock = threading.Lock()
                stop_ev = threading.Event()
                th = threading.Thread(target=_reader, args=(lid,), daemon=True)
                th.start()
                _local_ptys[lid] = {
                    'master_fd': master_fd,
                    'proc': proc,
                    'buffer': buf,
                    'lock': lock,
                    'stop': stop_ev,
                    'thread': th,
                    'cwd': cwd
                }
            except Exception:
                # Fall back to pipe-backed subprocess (Windows or when pty isn't available).
                # Use binary mode for stdout/stderr so the reader can decode consistently.
                proc = subprocess.Popen([shell], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
                lid = term_name if term_name else f"local-subproc-{uuid.uuid4().hex[:8]}"
                buf = []
                lock = threading.Lock()
                stop_ev = threading.Event()
                th = threading.Thread(target=_reader, args=(lid,), daemon=True)
                th.start()
                _local_ptys[lid] = {
                    'master_fd': None,
                    'proc': proc,
                    'buffer': buf,
                    'lock': lock,
                    'stop': stop_ev,
                    'thread': th,
                    'cwd': cwd
                }
            # publish a lifecycle 'create' event for watchers (best-effort)
            try:
                _publish_event({'terminalId': lid, 'type': 'create', 'cwd': cwd, 'pid': getattr(proc, 'pid', None)})
            except Exception:
                pass
            # Always return a JSON object with terminalId and cwd
            return json.dumps({'terminalId': lid, 'cwd': cwd})
        except Exception:
            return term_name

    @server.tool(name="terminal_send", description="Send text to a terminal (shell or pty). Non-blocking.")
    def terminal_send(terminalId: str = None, text: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
            text = payload.get('text', text)
        # Require text for all sends
        if text is None:
            return 'Error: text required'

        created_new = False
        # If no terminalId supplied, create a new local pty and use it
        if not terminalId:
            try:
                created = terminal_create()
                created_new = True
                # terminal_create may return JSON or a plain id; handle both
                try:
                    created_obj = json.loads(created)
                    terminalId = created_obj.get('terminalId', created)
                except Exception:
                    terminalId = created
            except Exception:
                return 'Error: failed to create terminal'

        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                to_write = text if text.endswith('\n') else text + '\n'
                # publish cmd event for watchers
                try:
                    _publish_event({'terminalId': terminalId, 'type': 'cmd', 'text': text, 'cwd': m.get('cwd'), 'pid': getattr(m.get('proc', None), 'pid', None)})
                except Exception:
                    pass
                # If a pty master_fd is present, write to it. Otherwise write to proc.stdin.
                try:
                    if isinstance(m.get('master_fd'), int) and m.get('master_fd') is not None:
                        os.write(m['master_fd'], to_write.encode())
                    else:
                        proc = m.get('proc')
                        if proc and proc.stdin:
                            try:
                                # write bytes to stdin
                                proc.stdin.write(to_write.encode())
                                proc.stdin.flush()
                            except TypeError:
                                # maybe stdin is text-mode
                                proc.stdin.write(to_write)
                                proc.stdin.flush()
                except Exception as e:
                    return str(e)
                # If we created the terminal for this send, return the new id in JSON
                if created_new:
                    try:
                        return json.dumps({'terminalId': terminalId, 'status': 'created', 'cwd': m.get('cwd')})
                    except Exception:
                        return terminalId
                return ''
            except Exception as e:
                return str(e)
        # If terminal not found, return an error
        return 'Error: terminal not found'
    
    @server.tool(name="runCommand", description="Send text to a terminal (pty). Non-blocking.")
    def runCommand(terminalId: str = None, text: str = None, payload: dict = None) -> str:
        return terminal_send(terminalId, text, payload)

    @server.tool(name="terminal_read", description="Read buffered output from a pseudoterminal created by terminal_create.")
    def terminal_read(terminalId: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
        if not terminalId:
            return 'Error: terminalId required'
        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                strip_ansi = True
                lines = None
                if payload and isinstance(payload, dict):
                    if 'strip_ansi' in payload:
                        strip_ansi = bool(payload.get('strip_ansi'))
                    if 'lines' in payload:
                        try:
                            lines = int(payload.get('lines'))
                        except Exception:
                            lines = None
                with m['lock']:
                    raw = ''.join(m['buffer'])
                    if lines is not None:
                        parts = raw.splitlines(True)
                        if lines <= 0:
                            consumed_raw = ''
                        else:
                            if lines >= len(parts):
                                consumed_raw = ''.join(parts)
                            else:
                                consumed_raw = ''.join(parts[-lines:])
                    else:
                        consumed_raw = raw
                out = consumed_raw
                if strip_ansi and out:
                    ansi_csi_re = re.compile(r"\x1b\[[0-9;?=><]*[A-Za-z]")
                    out = ansi_csi_re.sub('', out)
                    out = re.sub(r"\x1b\[\?[0-9;]*[hl]", '', out)
                    osc_re = re.compile(r"\x1b\][^\x07]*\x07")
                    out = osc_re.sub('', out)
                    out = out.replace('\x1b', '')
                    out = out.replace('\r\n', '\n').replace('\r', '\n')
                    out = ''.join(ch for ch in out if ch == '\n' or ch == '\t' or ord(ch) >= 32)
                if lines is not None:
                    split_lines = out.splitlines(True)
                    if lines <= 0:
                        return ''
                    return ''.join(split_lines[-lines:])
                return out
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(name="terminal_interrupt", description="Send an interrupt (Ctrl-C) to a terminal created by these tools.")
    def terminal_interrupt(terminalId: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
        if not terminalId:
            return 'Error: terminalId required'
        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                # Try platform-appropriate interrupt: write Ctrl-C to master or stdin, or send SIGINT to the process.
                try:
                    if isinstance(m.get('master_fd'), int) and m.get('master_fd') is not None:
                        os.write(m['master_fd'], b'\x03')
                        return ''
                    proc = m.get('proc')
                    if proc:
                        # prefer sending a signal if available
                        try:
                            proc.send_signal(signal.SIGINT)
                            return ''
                        except Exception:
                            # fall back to writing Ctrl-C to stdin
                            try:
                                if proc.stdin:
                                    try:
                                        proc.stdin.write(b'\x03')
                                        proc.stdin.flush()
                                    except TypeError:
                                        proc.stdin.write('\x03')
                                        proc.stdin.flush()
                                    return ''
                            except Exception:
                                pass
                except Exception as e:
                    return str(e)
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(name="terminal_clear", description="Clear the buffered output for a terminal created by terminal_create.")
    def terminal_clear(terminalId: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
        if not terminalId:
            return 'Error: terminalId required'
        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                with m['lock']:
                    m['buffer'].clear()
                return ''
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(name="terminal_dispose", description="Dispose a terminal created by these tools.")
    def terminal_dispose(terminalId: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
        if not terminalId:
            return 'Error: terminalId required'
        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                m['stop'].set()
                try:
                    if isinstance(m.get('master_fd'), int) and m.get('master_fd') is not None:
                        os.close(m['master_fd'])
                except Exception:
                    pass
                try:
                    proc = m.get('proc')
                    if proc:
                        try:
                            # Close stdin if present to encourage graceful exit
                            if proc.stdin:
                                try:
                                    proc.stdin.close()
                                except Exception:
                                    pass
                            proc.terminate()
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    m['thread'].join(timeout=1.0)
                except Exception:
                    pass
                # publish a lifecycle 'dispose' event for watchers (best-effort)
                try:
                    _publish_event({'terminalId': terminalId, 'type': 'dispose', 'cwd': m.get('cwd')})
                except Exception:
                    pass
                del _local_ptys[terminalId]
                return ''
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(name="terminal_list", description="List terminals created or registered (returns JSON array).")
    def terminal_list(payload: dict = None) -> str:
        include_remote = False
        if payload and isinstance(payload, dict):
            if 'include_remote' in payload:
                try:
                    include_remote = bool(payload.get('include_remote'))
                except Exception:
                    include_remote = False
        results = []
        try:
            for tid, m in _local_ptys.items():
                try:
                    pid = getattr(m.get('proc', None), 'pid', None)
                except Exception:
                    pid = None
                buf_len = None
                try:
                    with m['lock']:
                        buf_len = sum(len(s) for s in m.get('buffer', []))
                except Exception:
                    buf_len = None
                results.append({'id': tid, 'type': 'pty', 'pid': pid, 'buffer_chars': buf_len, 'cwd': m.get('cwd')})
            return json.dumps(results)
        except Exception:
            return '[]'
