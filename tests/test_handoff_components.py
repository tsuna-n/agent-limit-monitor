import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from handoff import make_package, project_lock
from terminal_ui import run, select as select_agent


def read_terminal(master, on_output=lambda _output: None):
    output = b""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and select.select([master], [], [], deadline - time.monotonic())[0]:
        try:
            part = os.read(master, 4096)
        except OSError:
            break
        if not part:
            break
        output += part
        on_output(output)
    return output


def close_terminal(pid, master):
    os.close(master)
    child, _ = os.waitpid(pid, os.WNOHANG)
    if not child:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "test project\n"
        self.project.mkdir()
        self.git("init", "-q", "-b", "work")
        self.notes = Path(self.temp.name) / "notes.json"
        self.notes.write_text(json.dumps({
            "task": "fake task", "objective": "continue", "completed": [],
            "remaining": "pending", "tests": [], "issues": [],
            "next": '"password": "fake spaced credential"',
        }), encoding="utf-8")
        self.packages = []
        self.addCleanup(lambda: [path.unlink(missing_ok=True) for path in self.packages])

    def git(self, *args):
        return subprocess.run(["git", "-C", self.project, *args], capture_output=True, check=True)

    def test_lease_uses_canonical_root_and_survives_temp_directory_changes(self):
        nested = self.project / "nested"
        nested.mkdir()
        first_tmp = Path(self.temp.name) / "first-tmp"
        second_tmp = Path(self.temp.name) / "second-tmp"
        first_tmp.mkdir()
        second_tmp.mkdir()
        with patch.dict(os.environ, {"TMPDIR": str(first_tmp)}), project_lock(nested) as (root, fd):
            self.assertEqual(root, self.project.resolve())
            self.assertFalse(os.get_inheritable(fd))
            contender = subprocess.run(
                [sys.executable, "-B", "-c", """
from handoff import ProjectLockedError, project_lock
from pathlib import Path
import sys
try:
    with project_lock(Path(sys.argv[1])):
        sys.exit(1)
except ProjectLockedError:
    pass
""", self.project],
                env={**os.environ, "TMPDIR": str(second_tmp)}, capture_output=True, check=False,
            )
            self.assertEqual(contender.returncode, 0, contender.stderr.decode())
        with project_lock(nested):
            pass

    def test_lease_rejects_symlink_lock_files(self):
        state = Path(self.temp.name) / "state"
        state.mkdir(mode=0o700)
        source = Path(self.temp.name) / "source"
        source.write_text("unrelated file", encoding="utf-8")
        key = hashlib.sha256(os.fsencode(self.project.resolve())).hexdigest()
        (state / f"{key}.lock").symlink_to(source)
        with patch("handoff.lock._state_directory", return_value=state), self.assertRaises(OSError):
            with project_lock(self.project):
                pass
        self.assertEqual(source.read_text(encoding="utf-8"), "unrelated file")

    def test_package_is_private_unique_redacted_and_preserves_git(self):
        tracked = self.project / "tracked"
        tracked.write_text("initial version\n", encoding="utf-8")
        self.git("add", "tracked")
        self.git("-c", "user.name=Handoff Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Initial")
        tracked.write_text("staged version\n", encoding="utf-8")
        self.git("add", "tracked")
        tracked.write_text("local edit\n", encoding="utf-8")
        (self.project / "notes\ncontinued").write_text("untracked contents\n", encoding="utf-8")
        (self.project / ".env").write_text("unknown secret value\n", encoding="utf-8")
        index = self.project / ".git" / "index"
        index_before = index.read_bytes(), index.stat().st_mtime_ns
        head_before = self.git("rev-parse", "HEAD").stdout.strip().decode()

        with patch.dict(os.environ, {"GIT_DIR": str(self.project / "missing-git-dir"), "GIT_OPTIONAL_LOCKS": "1"}):
            self.packages.append(make_package(self.project, self.notes))
            self.packages.append(make_package(self.project, self.notes))

        first, second = self.packages
        self.assertNotEqual(first, second)
        self.assertFalse(first.is_relative_to(self.project))
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
        content = first.read_text(encoding="utf-8")
        self.assertIn("Git branch: `work`", content)
        self.assertIn(head_before[:7], content)
        self.assertIn("MM", content)
        self.assertIn(r"notes\ncontinued", content)
        self.assertIn("[sensitive path redacted]", content)
        self.assertIn("[REDACTED]", content)
        for secret in (".env", "fake spaced credential", "unknown secret value", "untracked contents", "local edit"):
            self.assertNotIn(secret, content)
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), index_before)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip().decode(), head_before)
        self.assertEqual(tracked.read_text(encoding="utf-8"), "local edit\n")

    def test_package_does_not_run_git_clean_filters(self):
        (self.project / ".gitattributes").write_text("filtered filter=test\n", encoding="utf-8")
        tracked = self.project / "filtered"
        tracked.write_text("initial contents\n", encoding="utf-8")
        self.git("add", ".gitattributes", "filtered")
        self.git("config", "filter.test.clean", "touch side-effect && cat")
        os.utime(tracked, (0, 0))
        self.packages.append(make_package(self.project, self.notes))
        self.assertFalse((self.project / "side-effect").exists())


class TerminalTests(unittest.TestCase):
    def test_active_agent_menu_option_continues(self):
        with patch("builtins.input", return_value="2"), patch("sys.stdout"):
            self.assertEqual(select_agent("opencode"), "continue")

    def test_agy_can_select_codex_for_handoff(self):
        for answer in ("4", " Codex ", "CODEX"):
            with self.subTest(answer=answer), patch("builtins.input", return_value=answer), patch("sys.stdout"):
                self.assertEqual(select_agent("agy"), "codex")

    def test_sigterm_restores_terminal_and_reaps_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            lease = os.open(Path(tmp) / "lease", os.O_RDWR | os.O_CREAT, 0o600)
            extra_fd = os.open(Path(tmp) / "extra", os.O_RDWR | os.O_CREAT, 0o600)
            self.addCleanup(os.close, extra_fd)
            self.addCleanup(os.close, lease)
            os.set_inheritable(extra_fd, True)
            pid, master = pty.fork()
            if pid == 0:
                import termios

                before = termios.tcgetattr(0)
                command = """
import os, sys, time
os.fstat(int(sys.argv[1]))
try:
    os.fstat(int(sys.argv[2]))
except OSError:
    print('READY', flush=True)
else:
    print('EXTRA FD INHERITED', flush=True)
time.sleep(15)
"""
                result = run([sys.executable, "-c", command, str(lease), str(extra_fd)], tmp, lease_fd=lease)
                print(f"RESTORED {termios.tcgetattr(0) == before} STATUS {result}", flush=True)
                os._exit(0)

            sent_signal = False

            def terminate_on_ready(output):
                nonlocal sent_signal
                if b"READY" in output and not sent_signal:
                    os.kill(pid, signal.SIGTERM)
                    sent_signal = True

            try:
                output = read_terminal(master, terminate_on_ready)
                self.assertIn(b"RESTORED True STATUS -15", output)
            finally:
                close_terminal(pid, master)

    def test_pty_waits_for_connected_background_process(self):
        pid, master = pty.fork()
        if pid == 0:
            command = """
import os, signal, time
signal.signal(signal.SIGHUP, signal.SIG_IGN)
if os.fork() == 0:
    time.sleep(1.25)
    print('BACKGROUND DONE', flush=True)
    os._exit(0)
os._exit(19)
"""
            result = run([sys.executable, "-c", command], os.getcwd())
            print(f"DIRECT STATUS {result}", flush=True)
            os._exit(0)

        try:
            output = read_terminal(master)
            self.assertIn(b"BACKGROUND DONE", output)
            self.assertIn(b"DIRECT STATUS 19", output)
        finally:
            close_terminal(pid, master)


if __name__ == "__main__":
    unittest.main()
