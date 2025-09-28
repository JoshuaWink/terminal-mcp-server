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
import fcntl
import sys
from collections import deque
from datetime import datetime, timezone
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
_event_seq_lock = threading.Lock()
_event_seq = 0  # monotonically increasing sequence id for events

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
    """Return RFC3339/ISO-8601 UTC timestamp with trailing Z (timezone-aware).

    Uses datetime.now(timezone.utc) instead of deprecated utcnow(). If anything
    goes wrong, falls back to a basic time.time() derived string.
    """
    try:
        # timespec='milliseconds' keeps log lines compact while preserving ordering fidelity
        return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    except Exception:
        try:
            return datetime.fromtimestamp(time.time(), timezone.utc).isoformat().replace('+00:00','Z')
        except Exception:
            return '1970-01-01T00:00:00Z'


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
    # assign monotonically increasing sequence number
    global _event_seq
    try:
        with _event_seq_lock:
            _event_seq += 1
            ev['seq'] = _event_seq
    except Exception:
        # best-effort; if it fails we still continue without seq
        pass
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
        # Make the fd non-blocking
        try:
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except Exception:
            pass
        try:
            while not stop_event.is_set():
                try:
                    data = os.read(master_fd, 4096)
                except OSError as e:
                    if e.errno == 11:  # EAGAIN, no data available
                        time.sleep(0.1)
                        continue
                    else:
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

    # For pipe-based terminals, make stdout non-blocking
    if sys.platform == 'darwin' and not isinstance(m.get('master_fd'), int):
        try:
            flags = fcntl.fcntl(proc.stdout.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(proc.stdout.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except Exception:
            pass

    # proc.stdout.read may block; it's fine because this runs in a daemon thread.
    try:
        while not stop_event.is_set():
            try:
                data = proc.stdout.read(4096)
            except (IOError, OSError) as e:
                # On non-blocking read, we might get an error indicating no data
                if e.errno == 11: # EAGAIN
                    time.sleep(0.1)
                    continue
                else:
                    break
            except Exception:
                # if the descriptor is closed or broken, stop
                break
            if not data:
                # EOF or no data on non-blocking read
                if proc.poll() is not None:
                    break # Process finished
                time.sleep(0.1) # Avoid busy-waiting
                continue
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
    @server.tool(
        name="terminal_create",
        description=(
            "Create a managed shell session (pty or pipe). Use first when you need a stable, reusable, non-blocking "
            "terminal to run multiple commands, long-lived servers, watchers or background processes. Returns JSON {terminalId,cwd}."
        ),
    )
    def terminal_create(name: str = None, cwd: str = None, payload: dict = None) -> str:
        """Create a terminal.

        QoL additions (opt-in): set any of payload.verbose | payload.meta | payload.return_meta to
        receive richer JSON with guidance and follow-up hints. Default remains minimal JSON
        for backward compatibility.
        """
        verbose = False
        if payload and isinstance(payload, dict):
            name = payload.get('name', name)
            cwd = payload.get('cwd', cwd)
            verbose = any(payload.get(k) for k in ('verbose','meta','return_meta'))
        # generate a human-friendly deterministic name when not provided
        term_name = name or _generate_name()
        # default to the user's home directory if cwd not provided
        if not cwd:
            try:
                cwd = os.path.expanduser('~')
            except Exception:
                cwd = None
        try:
            # Prefer a real pty on POSIX when available, but use pipe on macOS for compatibility
            shell = os.environ.get('SHELL', '/bin/sh') if os.name != 'nt' else (os.environ.get('ComSpec', 'cmd.exe'))
            if sys.platform == 'darwin':
                # Use pipe-backed for macOS compatibility
                shell = '/bin/sh'  # Use sh for better compatibility
                proc = subprocess.Popen([shell], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
                lid = term_name if term_name else f"local-subproc-{uuid.uuid4().hex[:8]}"
                buf = []
                lock = threading.Lock()
                stop_ev = threading.Event()
                _local_ptys[lid] = {
                    'master_fd': None,
                    'proc': proc,
                    'buffer': buf,
                    'lock': lock,
                    'stop': stop_ev,
                    'thread': None,  # will set after thread creation
                    'cwd': cwd
                }
                th = threading.Thread(target=_reader, args=(lid,), daemon=True)
                th.start()
                _local_ptys[lid]['thread'] = th
            else:
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
                    _local_ptys[lid] = {
                        'master_fd': master_fd,
                        'proc': proc,
                        'buffer': buf,
                        'lock': lock,
                        'stop': stop_ev,
                        'thread': None,  # will set after thread creation
                        'cwd': cwd
                    }
                    th = threading.Thread(target=_reader, args=(lid,), daemon=True)
                    th.start()
                    _local_ptys[lid]['thread'] = th
                except Exception:
                    # Fall back to pipe-backed subprocess (Windows or when pty isn't available).
                    # Use binary mode for stdout/stderr so the reader can decode consistently.
                    proc = subprocess.Popen([shell], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
                    lid = term_name if term_name else f"local-subproc-{uuid.uuid4().hex[:8]}"
                    buf = []
                    lock = threading.Lock()
                    stop_ev = threading.Event()
                    _local_ptys[lid] = {
                        'master_fd': None,
                        'proc': proc,
                        'buffer': buf,
                        'lock': lock,
                        'stop': stop_ev,
                        'thread': None,  # will set after thread creation
                        'cwd': cwd
                    }
                    th = threading.Thread(target=_reader, args=(lid,), daemon=True)
                    th.start()
                    _local_ptys[lid]['thread'] = th
            # publish a lifecycle 'create' event for watchers (best-effort)
            try:
                _publish_event({'terminalId': lid, 'type': 'create', 'cwd': cwd, 'pid': getattr(proc, 'pid', None)})
            except Exception:
                pass
            base = {'terminalId': lid, 'cwd': cwd}
            if verbose:
                base.update({
                    'created': True,
                    'hint': 'Use terminal_send to run commands, then terminal_read to fetch output.',
                    'next': ['terminal_send', 'terminal_read', 'terminal_list']
                })
            return json.dumps(base)
        except Exception:
            return term_name

    @server.tool(
        name="terminal_send",
        description=(
            "Write a command or input line to an existing terminal. Non-blocking; output is captured asynchronously. "
            "If no terminalId supplied a new terminal is auto-created (for quick one-offs). Pair with terminal_read to fetch output."
        ),
    )
    def terminal_send(terminalId: str = None, text: str = None, payload: dict = None) -> str:
        """Send text to a terminal.

        QoL additions (opt-in via verbose/meta/return_meta flags in payload):
        - Returns structured JSON with status, size, and hints instead of empty string.
        - Auto-created terminal responses always JSON and include hint.
        - Errors returned as JSON when verbose.
        """
        verbose = False
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
            text = payload.get('text', text)
            verbose = any(payload.get(k) for k in ('verbose','meta','return_meta'))
        # Require text for all sends
        if text is None:
            if verbose:
                return json.dumps({'error':'text required','hint':'Provide shell input in the "text" parameter.'})
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
                if verbose:
                    return json.dumps({'error':'failed to create terminal'})
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
                    resp = {'terminalId': terminalId, 'status': 'created', 'cwd': m.get('cwd'), 'hint':'Use terminal_read to fetch output.'}
                    if verbose:
                        resp['next'] = ['terminal_read','terminal_clear','terminal_interrupt']
                    return json.dumps(resp)
                if verbose:
                    return json.dumps({'terminalId': terminalId, 'status': 'sent', 'bytes': len(to_write), 'hint':'Call terminal_read to view buffered output.'})
                return ''
            except Exception as e:
                if verbose:
                    return json.dumps({'error': str(e)})
                return str(e)
        # If terminal not found, return an error
        if verbose:
            return json.dumps({'error':'terminal not found'})
        return 'Error: terminal not found'
    
    @server.tool(
        name="runCommand",
        description=(
            "Alias of terminal_send for compatibility with generic agent tooling expecting a 'runCommand' verb."
        ),
    )
    def runCommand(terminalId: str = None, text: str = None, payload: dict = None) -> str:
        return terminal_send(terminalId, text, payload)

    @server.tool(
        name="terminal_read",
        description=(
            "Retrieve accumulated stdout for a terminal without consuming it (idempotent). Use after terminal_send. "
            "Supports tailing via lines, ANSI stripping, and verbose metadata for agents."
        ),
    )
    def terminal_read(terminalId: str = None, payload: dict = None) -> str:
        """Read buffered output.

        QoL additions (opt-in via verbose/meta/return_meta):
        - Returns JSON with output, line counts, empty flag, and hints.
        - When lines parameter used, includes requested vs returned lines.
        - Legacy behavior (plain text) preserved when not verbose.
        """
        verbose = False
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
            verbose = any(payload.get(k) for k in ('verbose','meta','return_meta'))
        if not terminalId:
            if verbose:
                return json.dumps({'error':'terminalId required'})
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
                        return json.dumps({'output':'','lines':0,'hint':'No lines requested.'}) if verbose else ''
                    trimmed = ''.join(split_lines[-lines:])
                    if verbose:
                        return json.dumps({
                            'output': trimmed,
                            'lines': len(trimmed.splitlines()),
                            'requested': lines,
                            'terminalId': terminalId,
                            'hint': 'Send more commands with terminal_send or clear buffer with terminal_clear.' if not trimmed else 'Append more output by sending commands.'
                        })
                    return trimmed
                if verbose:
                    return json.dumps({
                        'output': out,
                        'lines': len(out.splitlines()) if out else 0,
                        'terminalId': terminalId,
                        'empty': not bool(out),
                        'hint': 'Buffer empty. Use terminal_send to execute a command.' if not out else 'Use lines parameter to tail recent output.'
                    })
                return out
            except Exception:
                if verbose:
                    return json.dumps({'error':'read failure'})
                return ''
        if verbose:
            return json.dumps({'error':'terminal not found'})
        return 'Error: terminal not found'

    @server.tool(
        name="terminal_interrupt",
        description=(
            "Send Ctrl-C (SIGINT) to an active terminal process. Use to abort a long-running command or gracefully stop a foreground server."
        ),
    )
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
                        try:
                            _publish_event({'terminalId': terminalId, 'type': 'interrupt', 'cwd': m.get('cwd')})
                        except Exception:
                            pass
                        return ''
                    proc = m.get('proc')
                    if proc:
                        # prefer sending a signal if available
                        try:
                            proc.send_signal(signal.SIGINT)
                            try:
                                _publish_event({'terminalId': terminalId, 'type': 'interrupt', 'cwd': m.get('cwd')})
                            except Exception:
                                pass
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
                                    try:
                                        _publish_event({'terminalId': terminalId, 'type': 'interrupt', 'cwd': m.get('cwd')})
                                    except Exception:
                                        pass
                                    return ''
                            except Exception:
                                pass
                except Exception as e:
                    return str(e)
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(
        name="terminal_clear",
        description=(
            "Clear the in-memory output buffer for a terminal (does not affect the underlying process). Use before benchmarks, or to reduce payload size."
        ),
    )
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
                try:
                    _publish_event({'terminalId': terminalId, 'type': 'clear', 'cwd': m.get('cwd')})
                except Exception:
                    pass
                return ''
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(
        name="terminal_dispose",
        description=(
            "Terminate and remove a managed terminal. Sends terminate+cleanup, joins reader, and emits a dispose event with exitCode. Always call when done to free resources."
        ),
    )
    def terminal_dispose(terminalId: str = None, payload: dict = None) -> str:
        if payload and isinstance(payload, dict):
            terminalId = payload.get('terminalId', terminalId)
        if not terminalId:
            return 'Error: terminalId required'
        if terminalId in _local_ptys:
            try:
                m = _local_ptys[terminalId]
                verbose = False
                if payload and isinstance(payload, dict):
                    verbose = any(payload.get(k) for k in ('verbose','meta','return_meta'))
                m['stop'].set()
                exit_code = None
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
                            try:
                                # give process brief time to exit
                                proc.wait(timeout=1.0)
                            except Exception:
                                pass
                            try:
                                exit_code = proc.poll()
                            except Exception:
                                exit_code = None
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
                    ev = {'terminalId': terminalId, 'type': 'dispose', 'cwd': m.get('cwd')}
                    if exit_code is not None:
                        ev['exitCode'] = exit_code
                    _publish_event(ev)
                except Exception:
                    pass
                del _local_ptys[terminalId]
                if verbose:
                    return json.dumps({'terminalId': terminalId, 'disposed': True, 'exitCode': exit_code})
                return ''
            except Exception:
                return ''
        return 'Error: terminal not found'

    @server.tool(
        name="terminal_list",
        description=(
            "List currently active managed terminals with basic stats (pid, buffer size, cwd). Use to discover or monitor existing sessions."
        ),
    )
    def terminal_list(payload: dict = None) -> str:
        # payload currently unused; reserved for future filters
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

    @server.tool(
        name="terminal_events",
        description=(
            "Query recent terminal lifecycle & activity events (create, cmd, stdout, clear, interrupt, dispose). Supports pagination (after), tailing (since_ts), and search (contains/regex)."
        ),
    )
    def terminal_events(payload: dict = None) -> str:
        terminal_id = None
        since_ts = None
        limit = 100
        types = None
        after_seq = None  # pagination cursor
        contains = None
        regex_pat = None
        truncated = False
        if payload and isinstance(payload, dict):
            terminal_id = payload.get('terminalId') or payload.get('terminal_id')
            since_ts = payload.get('since_ts') or payload.get('since')
            try:
                if 'limit' in payload:
                    limit = int(payload.get('limit'))
            except Exception:
                limit = 100
            maybe_types = payload.get('types')
            if isinstance(maybe_types, (list, tuple)):
                types = set(str(t) for t in maybe_types if t)
            if 'after' in payload:
                try:
                    after_seq = int(payload.get('after'))
                except Exception:
                    after_seq = None
            contains = payload.get('contains') if isinstance(payload.get('contains'), str) and payload.get('contains') else None
            regex_raw = payload.get('regex')
            if isinstance(regex_raw, str) and regex_raw:
                try:
                    # compile with IGNORECASE for user friendliness
                    regex_pat = re.compile(regex_raw, re.IGNORECASE)
                except Exception:
                    regex_pat = None
        if limit <= 0:
            return json.dumps({'events': [], 'count': 0, 'nextCursor': after_seq, 'hasMore': False, 'truncated': False})
        # snapshot current events to avoid mutation mid-iteration
        try:
            events_list = list(_recent_events)
        except Exception:
            events_list = []
        if not events_list:
            return json.dumps({'events': [], 'count': 0, 'nextCursor': after_seq, 'hasMore': False, 'truncated': False})
        oldest_seq = None
        newest_seq = None
        try:
            # sequences grow; earliest is first in deque
            for e in events_list:
                if 'seq' in e:
                    oldest_seq = e['seq']
                    break
            for e in reversed(events_list):
                if 'seq' in e:
                    newest_seq = e['seq']
                    break
        except Exception:
            pass
        # detect truncation: client asked for after_seq older than what we kept
        if after_seq is not None and oldest_seq is not None and after_seq < oldest_seq:
            truncated = True
        collected = []
        # iterate forward (natural order) and pick events beyond constraints
        for ev in events_list:
            seq = ev.get('seq')
            if after_seq is not None and seq is not None and seq <= after_seq:
                continue
            if terminal_id and ev.get('terminalId') != terminal_id:
                continue
            if since_ts and ev.get('ts') and ev['ts'] <= since_ts:
                continue
            if types and ev.get('type') not in types:
                continue
            txt_field = ev.get('text') or ev.get('hint') or ''
            if contains and contains.lower() not in txt_field.lower():
                continue
            if regex_pat and not regex_pat.search(txt_field):
                continue
            collected.append(ev)
            if len(collected) >= limit:
                break
        count = len(collected)
        next_cursor = collected[-1]['seq'] if count and 'seq' in collected[-1] else after_seq
        has_more = False
        if newest_seq is not None and next_cursor is not None and next_cursor < newest_seq:
            # More events exist after the last one we returned (even if filtered events ended early)
            has_more = True
        return json.dumps({
            'events': collected,
            'count': count,
            'nextCursor': next_cursor,
            'hasMore': has_more,
            'truncated': truncated,
            'oldestSeq': oldest_seq,
            'newestSeq': newest_seq,
            'applied': {
                'terminalId': terminal_id,
                'since_ts': since_ts,
                'after': after_seq,
                'types': list(types) if types else None,
                'contains': contains,
                'regex': regex_pat.pattern if regex_pat else None,
                'limit': limit
            }
        })
