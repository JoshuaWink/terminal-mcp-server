#!/usr/bin/env python3
"""Simple tail viewer for terminal-mcp events.log

Run locally to watch all terminal commands and outputs.
"""
import argparse
import time
import os
import json
import hashlib
import re
import datetime

# comfortable color palette (ANSI 256-color codes)
PALETTE = ["\x1b[38;5;75m", "\x1b[38;5;136m", "\x1b[38;5;127m", "\x1b[38;5;81m"]
RESET = "\x1b[0m"
# modifiers
DIM = "\x1b[2m"
BOLD = "\x1b[1m"

# lighter/shade palette for CMD label (same length as PALETTE)
LIGHT_PALETTE = ["\x1b[38;5;225m", "\x1b[38;5;159m", "\x1b[38;5;189m", "\x1b[38;5;216m", "\x1b[38;5;183m"]

# global flag set from argparse
NO_COLOR = False

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEFAULT_LOG = os.path.join(REPO_ROOT, '.terminal-mcp', 'events.log')


def color_for_terminal(tid: str) -> str:
    if NO_COLOR:
        return ''
    h = hashlib.sha1(tid.encode()).digest()
    idx = h[0] % len(PALETTE)
    return PALETTE[idx]


def light_color_for_terminal(tid: str) -> str:
    if NO_COLOR:
        return ''
    h = hashlib.sha1(tid.encode()).digest()
    idx = h[0] % len(LIGHT_PALETTE)
    return LIGHT_PALETTE[idx]


# state to compress repeated blank output lines
_last_was_blank = False
# recent last command text per terminal to suppress the echoed stdout line
_last_cmd = {}
# timing state: start of feed and previous event timestamp
_start_ts = None
_prev_ts = None


def _clean_ansi(s: str) -> str:
    # remove carriage returns, common CSI/ANSI sequences, and trailing control chars
    if not s:
        return s
    s = s.replace('\r', '')
    # remove typical CSI sequences like \x1b[?2004h and other \x1b[...<letter>
    s = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', s)
    # remove stray BELs
    s = s.replace('\x07', '')
    # normalize trailing spaces
    return s.rstrip('\n')


def _format_ts(ts: str) -> str:
    # fallback formatting for standalone timestamp strings (not used when deltas enabled)
    if not ts:
        return ''
    try:
        if 'T' in ts:
            _, t = ts.split('T', 1)
            t = t.rstrip('Z')
            return t.split('.')[0]
        if ':' in ts:
            return ts.split('.')[0]
    except Exception:
        pass
    return ts


def _parse_epoch(ts: str) -> float:
    # parse ISO8601-ish timestamps to epoch seconds (float). If parse fails, return current time.
    if not ts:
        return time.time()
    try:
        # handle trailing Z
        if ts.endswith('Z'):
            ts2 = ts.replace('Z', '+00:00')
            dt = datetime.datetime.fromisoformat(ts2)
        else:
            dt = datetime.datetime.fromisoformat(ts)
        return dt.timestamp()
    except Exception:
        try:
            # last-resort: parse as float seconds
            return float(ts)
        except Exception:
            return time.time()


# stable short id mapping for compact display
_short_map = {}


def short_tid(tid: str) -> str:
    # return a stable short alias for tid (e.g., 'stream-repo' -> 'stream', 'mcp-terminal-123456' -> 'mcp-3456')
    if tid in _short_map:
        return _short_map[tid]
    # heuristics
    alias = tid
    if tid.startswith('mcp-terminal-'):
        # keep prefix and last 4 digits
        tail = ''.join(ch for ch in tid.split('-')[-1] if ch.isdigit())
        alias = f"mcp-{tail[-4:]}" if tail else 'mcp'
    else:
        parts = tid.split('-')
        if len(parts) >= 2:
            alias = parts[0]
        else:
            alias = tid[:8]
    _short_map[tid] = alias
    return alias


def follow(path: str):
    # tail -F like
    try:
        with open(path, 'r', encoding='utf-8') as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                yield line.rstrip('\n')
    except FileNotFoundError:
        print(f"Event log not found: {path}")
        return


def render_event(ev: dict):
    t = ev.get('type', 'stdout')
    tid = ev.get('terminalId', '<unknown>')
    color = color_for_terminal(tid)
    global _last_was_blank
    global _start_ts, _prev_ts
    ts = _format_ts(ev.get('ts', ''))
    # compute epoch and delta for all event types so both branches can use them
    ev_epoch = _parse_epoch(ev.get('ts', ''))
    if _start_ts is None:
        _start_ts = ev_epoch
        delta = 0.0
    else:
        delta = ev_epoch - (_prev_ts if _prev_ts is not None else _start_ts)
    rel = ev_epoch - _start_ts
    rel_str = f"{rel:.3f}s"
    delta_str = f"+{delta:.3f}s" if delta >= 0 else f"{delta:.3f}s"
    _prev_ts = ev_epoch
    cwd = ev.get('cwd', '')
    # lifecycle / control events: show a short disposed notice for disposals
    if t in ('dispose', 'disposed'):
        notice = 'DISPOSED'
        if color:
            print(f"{color}{DIM}{delta_str} {tid} | {RESET}{BOLD}{notice}{RESET}")
        else:
            print(f"{delta_str} {tid} | {notice}")
        _last_was_blank = False
        return
    if t == 'cmd':
        text = ev.get('text', '')
        # normalize command text: strip ANSI, collapse internal newlines/whitespace
        try:
            clean_text = _clean_ansi(text)
            # collapse any internal newlines or runs of whitespace into single spaces
            clean_text = re.sub(r"\s+", ' ', clean_text).strip()
        except Exception:
            clean_text = text.strip()

        # remember last submitted (cleaned) command so we can suppress the echoed stdout line
        _last_cmd[tid] = clean_text

        # show only the delta (time since previous event) for compactness
        ts_part = f"{DIM}{delta_str}{RESET}" if color else f"{delta_str}"
        cmd_text = f"{BOLD}{color}{clean_text}{RESET}" if color else clean_text

        # use a lighter shade for the CMD label to visually separate it from the terminal id
        light = light_color_for_terminal(tid)
        if color:
            prefix = f"{color}{ts_part} {tid} {light}CMD ({cwd}) > {RESET}"
        else:
            prefix = f"{delta_str} {tid} CMD ({cwd}) > "
        print(f"{prefix}{cmd_text}")
        _last_was_blank = False
        return

    # stdout / other textual output
    raw = ev.get('text', '')
    cleaned = _clean_ansi(raw)
    lines = cleaned.splitlines()
    # if the first meaningful stdout line equals the last submitted command, skip it
    last_cmd = _last_cmd.get(tid)
    if last_cmd and lines:
        # find first non-blank/non-prompt line index
        first_idx = 0
        while first_idx < len(lines) and (lines[first_idx].strip() == '' or set(lines[first_idx].strip()) <= set('%')):
            first_idx += 1
        if first_idx < len(lines) and lines[first_idx].strip() == last_cmd:
            # drop that echoed line
            del lines[first_idx]
            # clear remembered command so we don't suppress unrelated output
            try:
                del _last_cmd[tid]
            except Exception:
                _last_cmd.pop(tid, None)

    # split into lines and print each with a prefix; compress purely-blank lines
    for l in lines:
        # treat lines that are only whitespace or only percent/whitespace as a single '%'
        if l.strip() == '' or set(l.strip()) <= set('%'):
            if _last_was_blank:
                # skip consecutive blanks
                continue
            if color:
                print(f"{color}{DIM}{delta_str} {tid} | {RESET}")
            else:
                print(f"{delta_str} {tid} | ")
            _last_was_blank = True
            continue

        if color:
            print(f"{color}{DIM}{delta_str} {tid} | {RESET}{l}")
        else:
            print(f"{delta_str} {tid} | {l}")
        _last_was_blank = False


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', '-f', default=DEFAULT_LOG)
    p.add_argument('--no-color', action='store_true', help='Disable ANSI color output')
    args = p.parse_args()
    global NO_COLOR
    NO_COLOR = bool(args.no_color)
    for raw in follow(args.file):
        try:
            ev = json.loads(raw)
        except Exception:
            print(raw)
            continue
        render_event(ev)


if __name__ == '__main__':
    main()
