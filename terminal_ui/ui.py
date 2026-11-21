"""Small Linux terminal UI helpers for agent handoffs."""

import errno
import os
import pty
import select as select_io
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty


_AGENTS = (("agy", "AGY CLI"), ("opencode", "OpenCode"), ("claude", "Claude Code"), ("codex", "Codex"))


def handoff_targets(current: str) -> list[str]:
    return [label for agent, label in _AGENTS if agent != current and shutil.which(agent)]


def alert(message: str, desktop: bool = False) -> None:
    """Ring the terminal bell and optionally send a bounded desktop alert."""
    print(f"\a\n{message}", file=sys.stderr, flush=True)
    if desktop and shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "--expire-time=5000", "Agent limit monitor", message],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def select(current: str) -> str | None:
    """Show the agent menu. Return an agent key, 'continue', or None to cancel."""
    print("\nAgent handoff")
    for number, (agent, label) in enumerate(_AGENTS, 1):
        status = " (current)" if agent == current else " (not installed)" if not shutil.which(agent) else ""
        print(f"  {number}. {label}{status}")
    print("  5. Continue current agent")
    print("  6. Cancel handoff")
    print(f"Current agent: {current}")
    try:
        choice = input("Select [1-6] or enter an agent name: ").strip().casefold()
    except (EOFError, KeyboardInterrupt):
        return None
    for number, (agent, label) in enumerate(_AGENTS, 1):
        if choice in (str(number), agent, label.casefold()):
            return "continue" if agent == current else agent
    if choice in ("5", "continue"):
        return "continue"
    return None


def confirm(agent: str) -> bool:
    """Require an explicit yes before switching to an agent."""
    try:
        return input(f"Switch to {agent}? Type 'yes' to confirm: ").strip().lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


def _write_all(fd: int, data: bytes) -> None:
    while data:
        data = data[os.write(fd, data):]


def _signal_child(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        # The child may still be setting up its PTY session.
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


def _stop_child(pid: int, signum: int) -> None:
    _signal_child(pid, signum)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT):
            break
        time.sleep(0.05)
    # Keep the child PID reserved until its process group has been signaled.
    _signal_child(pid, signal.SIGKILL)
    os.waitpid(pid, 0)


def _terminal_size(fd: int) -> tuple[int, int]:
    rows, columns = termios.tcgetwinsize(fd)
    return rows or 24, columns or 80


def run(
    argv: list[str],
    cwd: str,
    *,
    env: dict[str, str] | None = None,
    observe=None,
    tick=None,
    lease_fd: int | None = None,
) -> int:
    """Run an interactive command in a PTY and return its exit code.

    The observer receives output bytes as they arrive. The session stays open
    until connected background processes close the PTY.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("Interactive agent handoff requires a terminal")
    if not argv:
        raise ValueError("argv must contain an executable")

    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    old_settings = termios.tcgetattr(stdin_fd)
    size = _terminal_size(stdout_fd)
    handlers = {}
    pid, master_fd, waited, interrupted = 0, None, False, None

    def interrupt(signum, _frame):
        nonlocal interrupted
        if interrupted is None:
            interrupted = signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, interrupt)
        tty.setraw(stdin_fd)
        pid, master_fd = pty.fork()
        if pid == 0:
            try:
                for signum in handlers:
                    signal.signal(signum, signal.SIG_DFL)
                limit = os.sysconf("SC_OPEN_MAX")
                if lease_fd is not None:
                    os.set_inheritable(lease_fd, True)
                    os.closerange(3, lease_fd)
                    os.closerange(lease_fd + 1, limit)
                else:
                    os.closerange(3, limit)
                termios.tcsetattr(0, termios.TCSANOW, old_settings)
                termios.tcsetwinsize(0, size)
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env if env is not None else os.environ)
            except BaseException:
                os._exit(127)

        stdin_open = master_open = True
        next_tick = time.monotonic() + 1
        while True:
            if interrupted is not None:
                return -interrupted
            if not master_open:
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                if waited_pid:
                    waited = True
                    return os.waitstatus_to_exitcode(status)

            now = time.monotonic()
            timeout = 0.5 if tick is None else min(0.5, max(0, next_tick - now))
            readable_fds = [master_fd] if master_open else []
            if master_open and stdin_open:
                readable_fds.append(stdin_fd)
            readable, _, _ = select_io.select(readable_fds, [], [], timeout)
            if interrupted is not None:
                return -interrupted
            if master_fd in readable:
                try:
                    data = os.read(master_fd, 4096)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if data:
                    _write_all(stdout_fd, data)
                    if observe is not None:
                        observe(data)
                else:
                    master_open = False

            if stdin_fd in readable and master_open:
                data = os.read(stdin_fd, 4096)
                if data:
                    try:
                        _write_all(master_fd, data)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                else:
                    stdin_open = False

            if master_open:
                new_size = _terminal_size(stdout_fd)
                if new_size != size:
                    termios.tcsetwinsize(master_fd, new_size)
                    size = new_size
            if tick is not None and time.monotonic() >= next_tick:
                tick()
                next_tick = time.monotonic() + 1
    finally:
        try:
            if pid and not waited:
                _stop_child(pid, interrupted or signal.SIGTERM)
        finally:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_settings)
            except termios.error as error:
                if error.args[0] not in (errno.EIO, errno.ENXIO, errno.ENOTTY):
                    raise
            finally:
                if master_fd is not None:
                    os.close(master_fd)
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
