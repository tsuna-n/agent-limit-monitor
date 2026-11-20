import contextlib
import errno
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "agent_limit_monitor.py"


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)
        self.bin = self.project / "bin"
        self.bin.mkdir()
        self.env = os.environ | {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                                 "PYTHONDONTWRITEBYTECODE": "1"}
        self.env.pop("AGENT_LIMIT_MONITOR_ACTIVE", None)
        self.notes = self.project / "handoff.json"
        self.notes.write_text(json.dumps({
            "task": "Fix parser", "objective": "Parse valid input", "completed": ["Found regression"],
            "remaining": ["Fix parser"], "tests": ["One failing regression test"],
            "issues": [], "next": "Run tests",
        }), encoding="utf-8")

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        path.chmod(0o700)

    def test_mock_dry_run_neither_probes_nor_launches_agent(self):
        self.fake("codex", "from pathlib import Path\nPath('unexpected').touch()\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "run", "codex", "--dry-run", "--mock-5h", "2"],
            cwd=self.project, env=self.env, capture_output=True, text=True, timeout=5, check=True,
        )
        self.assertIn("5-hour quota remaining 2%", result.stdout)
        self.assertIn("would issue a terminal alert and offer handoff choices", result.stdout)
        self.assertFalse((self.project / "unexpected").exists())

    def test_codex_stdio_probe_uses_account_and_config_before_quota(self):
        self.fake("codex", """
import json
import sys
assert sys.argv[1:] == ['app-server', '--listen', 'stdio://']
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialized':
        initialized = True
        continue
    if method != 'initialize':
        assert initialized
    replies = {'initialize': {'userAgent': 'fake'},
               'account/read': {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt', 'email': 'secret-account'}},
               'config/read': {'config': {'model_provider': 'openai'}},
               'account/rateLimits/read': {'rateLimits': {'primary': {'usedPercent': 98, 'windowDurationMins': 300}}}}
    print(json.dumps({'id': request['id'], 'result': replies[method]}), flush=True)
""")
        result = subprocess.run([sys.executable, str(SCRIPT), "check", "codex"], cwd=self.project,
                                env=self.env, capture_output=True, text=True, timeout=5, check=True)
        self.assertIn("5-hour quota remaining 2%", result.stdout)
        self.assertIn("at or below 2%", result.stderr)
        self.assertNotIn("secret-account", result.stdout + result.stderr)

    def test_live_rate_error_waits_for_source_exit_then_requires_confirmation(self):
        self.fake("codex", """
import pathlib
import sys
if len(sys.argv) > 1:
    sys.exit(1)
print("Error: Rate limit exceeded", flush=True)
input()
pathlib.Path("source-exited").touch()
""")
        self.fake("claude", r"""
import json
import pathlib
import re
import sys
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "accessToken": "probe-secret"}))
    sys.exit(0)
assert pathlib.Path("source-exited").is_file()
path = pathlib.Path(re.search(r"(/[^ ]+\.md)", sys.argv[-1]).group(1))
assert "Found regression" in path.read_text()
assert path.stat().st_mode & 0o777 == 0o600
pathlib.Path("target-launched").touch()
path.unlink()
print("target finished")
""")
        for answer in (b"no\n", b"yes\n"):
            with self.subTest(answer=answer):
                (self.project / "source-exited").unlink(missing_ok=True)
                with self.session("run", "codex", "--notes", str(self.notes)) as (process, fd):
                    seen = self.until(fd, b"Exit the current agent normally")
                    self.assertNotIn(b"Select [1-5]", seen)
                    self.assertFalse((self.project / "source-exited").exists())
                    os.write(fd, b"exit\n")
                    self.until(fd, b"Select [1-5]")
                    os.write(fd, b"3\n")
                    seen = self.until(fd, b"Type 'yes'")
                    self.assertNotIn(b"probe-secret", seen)
                    self.assertFalse((self.project / "target-launched").exists())
                    os.write(fd, answer)
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertEqual((self.project / "target-launched").is_file(), answer == b"yes\n")

    def test_managed_claude_ingests_live_status_line(self):
        self.fake("claude", """
import json
import shlex
import subprocess
import sys
settings = json.loads(sys.argv[sys.argv.index("--settings") + 1])
metrics = {"context_window": {"used_percentage": 50},
           "rate_limits": {"seven_day": {"used_percentage": 98}}}
subprocess.run(shlex.split(settings["statusLine"]["command"]),
               input=json.dumps(metrics), text=True, check=True)
input()
""")
        with self.session("run", "claude") as (process, fd):
            seen = self.until(fd, b"Exit the current agent normally")
            self.assertIn(b"Weekly quota remaining 2%", seen)
            self.assertIn(b"Context window usage 50%", seen)
            os.write(fd, b"exit\n")
            self.until(fd, b"Select [1-5]")
            os.write(fd, b"5\n")
            self.assertEqual(process.wait(timeout=5), 0)

    @contextlib.contextmanager
    def session(self, *args):
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, str(SCRIPT), *args], cwd=self.project,
                                   env=self.env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        try:
            yield process, master
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master)

    def until(self, fd, expected):
        seen = bytearray()
        deadline = time.monotonic() + 7
        while expected not in seen and time.monotonic() < deadline:
            if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            try:
                seen.extend(os.read(fd, 8192))
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
        self.assertIn(expected, seen)
        return seen
