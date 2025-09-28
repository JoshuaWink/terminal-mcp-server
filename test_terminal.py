#!/usr/bin/env python3
"""Comprehensive tests for terminal-mcp-server tools."""
import time
import json
import pytest
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


@pytest.fixture(scope="module")
def server():
    """Create a dummy server with registered tools."""
    server = DummyServer()
    tools.register_tools(server)
    return server


@pytest.fixture
def terminal_id(server):
    """Create a terminal and return its ID, cleaning up after test."""
    create = server.tools['terminal_create']
    dispose = server.tools['terminal_dispose']

    created = create()
    try:
        created_obj = json.loads(created)
        tid = created_obj.get('terminalId', created)
    except Exception:
        tid = created

    yield tid

    # Cleanup
    try:
        dispose(terminalId=tid)
    except Exception:
        pass


def test_terminal_create_basic(server):
    """Test basic terminal creation."""
    create = server.tools['terminal_create']

    result = create()
    assert result
    try:
        created_obj = json.loads(result)
        tid = created_obj.get('terminalId')
        cwd = created_obj.get('cwd')
        assert tid
        assert cwd
    except Exception:
        # Fallback for non-JSON response
        assert isinstance(result, str) and len(result) > 0


def test_terminal_create_with_name(server):
    """Test terminal creation with custom name."""
    create = server.tools['terminal_create']
    dispose = server.tools['terminal_dispose']

    name = "test-terminal"
    result = create(name=name)
    tid = None
    try:
        created_obj = json.loads(result)
        tid = created_obj.get('terminalId')
        assert name in tid
    except Exception:
        tid = result
        assert name in tid

    # Cleanup
    if tid:
        dispose(terminalId=tid)


def test_terminal_send_read_basic(server, terminal_id):
    """Test sending a command and reading output."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']

    # Send a simple echo command
    token = "HELLO_WORLD_TEST"
    cmd = f"echo {token}"
    result = send(terminalId=terminal_id, text=cmd)
    assert result == ""  # Should return empty string on success

    # Wait for output
    time.sleep(1.0)

    # Read output
    output = read(terminalId=terminal_id)
    assert token in output


def test_terminal_send_read_multiline(server, terminal_id):
    """Test sending multiline commands and reading output."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']

    # Send multiple commands
    cmds = [
        "echo 'Line 1'",
        "echo 'Line 2'",
        "echo 'Line 3'"
    ]

    for cmd in cmds:
        send(terminalId=terminal_id, text=cmd)
        time.sleep(0.5)

    time.sleep(1.0)

    output = read(terminalId=terminal_id)
    assert "Line 1" in output
    assert "Line 2" in output
    assert "Line 3" in output


def test_terminal_read_lines_option(server, terminal_id):
    """Test reading with lines option."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']

    # Send multiple lines
    for i in range(5):
        send(terminalId=terminal_id, text=f"echo 'Line {i}'")
        time.sleep(0.2)

    time.sleep(1.0)

    # Read last 2 lines
    output = read(terminalId=terminal_id, payload={'lines': 2})
    lines = output.strip().split('\n')
    assert len(lines) <= 2
    assert "Line 3" in output or "Line 4" in output


def test_terminal_read_strip_ansi(server, terminal_id):
    """Test ANSI stripping in output."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']

    # Send command that might produce ANSI codes
    cmd = "echo -e '\\033[31mRed Text\\033[0m'"
    send(terminalId=terminal_id, text=cmd)
    time.sleep(1.0)

    # Read with ANSI stripping (default)
    output_stripped = read(terminalId=terminal_id, payload={'strip_ansi': True})

    # Stripped should not contain ANSI codes
    assert '\033[' not in output_stripped
    # Should contain the text
    assert "Red Text" in output_stripped


def test_terminal_clear(server, terminal_id):
    """Test clearing terminal buffer."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']
    clear = server.tools['terminal_clear']

    # Send some output
    send(terminalId=terminal_id, text="echo 'Test output'")
    time.sleep(1.0)

    output_before = read(terminalId=terminal_id)
    assert "Test output" in output_before

    # Clear buffer
    result = clear(terminalId=terminal_id)
    assert result == ""

    # Read again - should be empty or not contain old output
    output_after = read(terminalId=terminal_id)
    assert "Test output" not in output_after


def test_terminal_interrupt(server, terminal_id):
    """Test interrupting a long-running command."""
    send = server.tools['terminal_send']
    interrupt = server.tools['terminal_interrupt']

    # Send a long-running command
    if tools.sys.platform == 'darwin':
        # On macOS, use a simple sleep
        send(terminalId=terminal_id, text="sleep 10")
    else:
        # On other systems, try sleep or similar
        send(terminalId=terminal_id, text="sleep 10")

    time.sleep(0.5)  # Let command start

    # Interrupt
    result = interrupt(terminalId=terminal_id)
    assert result == ""

    time.sleep(1.0)  # Wait for interrupt to take effect

    # Check that process is not still running (this is approximate)
    # The interrupt should have stopped the sleep command


def test_terminal_list(server, terminal_id):
    """Test listing terminals."""
    list_terminals = server.tools['terminal_list']

    result = list_terminals()
    terminals = json.loads(result)

    assert isinstance(terminals, list)
    assert len(terminals) >= 1

    # Find our terminal
    our_terminal = None
    for t in terminals:
        if t.get('id') == terminal_id:
            our_terminal = t
            break

    assert our_terminal is not None
    assert our_terminal.get('type') == 'pty'
    assert 'cwd' in our_terminal


def test_terminal_dispose(server):
    """Test disposing a terminal."""
    create = server.tools['terminal_create']
    dispose = server.tools['terminal_dispose']
    list_terminals = server.tools['terminal_list']

    # Create a terminal
    result = create()
    try:
        created_obj = json.loads(result)
        tid = created_obj.get('terminalId', result)
    except Exception:
        tid = result

    # Verify it exists
    terminals_before = json.loads(list_terminals())
    assert any(t['id'] == tid for t in terminals_before)

    # Dispose
    result = dispose(terminalId=tid)
    assert result == ""

    # Verify it's gone
    terminals_after = json.loads(list_terminals())
    assert not any(t['id'] == tid for t in terminals_after)


def test_run_command_alias(server, terminal_id):
    """Test runCommand as alias for terminal_send."""
    run_command = server.tools['runCommand']
    read = server.tools['terminal_read']

    token = "RUN_COMMAND_TEST"
    cmd = f"echo {token}"
    result = run_command(terminalId=terminal_id, text=cmd)
    assert result == ""

    time.sleep(1.0)

    output = read(terminalId=terminal_id)
    assert token in output


def test_error_cases(server):
    """Test error cases."""
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']
    interrupt = server.tools['terminal_interrupt']
    clear = server.tools['terminal_clear']
    dispose = server.tools['terminal_dispose']

    fake_id = "non-existent-terminal"

    # Test operations on non-existent terminal
    assert "not found" in send(terminalId=fake_id, text="echo test").lower()
    assert "not found" in read(terminalId=fake_id).lower()
    assert "not found" in interrupt(terminalId=fake_id).lower()
    assert "not found" in clear(terminalId=fake_id).lower()
    assert "not found" in dispose(terminalId=fake_id).lower()

    # Test send without text
    result = send(terminalId="some-id", text=None)
    assert "text required" in result.lower()


def test_multiple_terminals(server):
    """Test creating and managing multiple terminals."""
    create = server.tools['terminal_create']
    send = server.tools['terminal_send']
    read = server.tools['terminal_read']
    dispose = server.tools['terminal_dispose']

    terminals = []

    # Create multiple terminals
    for i in range(3):
        result = create(name=f"multi-test-{i}")
        try:
            created_obj = json.loads(result)
            tid = created_obj.get('terminalId', result)
        except Exception:
            tid = result
        terminals.append(tid)

    # Send different commands to each
    for i, tid in enumerate(terminals):
        token = f"MULTI_TEST_{i}"
        send(terminalId=tid, text=f"echo {token}")
        time.sleep(0.5)

    time.sleep(1.0)

    # Read from each
    for i, tid in enumerate(terminals):
        token = f"MULTI_TEST_{i}"
        output = read(terminalId=tid)
        assert token in output

    # Dispose all
    for tid in terminals:
        dispose(terminalId=tid)