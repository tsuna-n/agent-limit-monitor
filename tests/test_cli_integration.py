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

from handoff import project_lock


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
        self.fake("notify-send", "import sys\nfrom pathlib import Path\nPath('desktop-alert.txt').write_text(sys.argv[-1])\n")
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

    def test_install_puts_compatibility_alias_next_to_agy_skill(self):
        env = self.env | {"HOME": str(self.project)}
        install = ["bash", SCRIPT.with_name("install-skill.sh")]
        agy_skills = self.project / ".gemini" / "antigravity-cli" / "skills"
        shared_skills = self.project / ".agents" / "skills"
        claude_skills = self.project / ".claude" / "skills"
        alias_name = "agent-limit-moritor"
        alias_skill = agy_skills / alias_name / "SKILL.md"

        subprocess.run(install, env=env, capture_output=True, timeout=5, check=True)
        alias_text = alias_skill.read_text(encoding="utf-8")
        self.assertTrue(alias_text.startswith(f"---\nname: {alias_name}\n"))
        self.assertIn("../agent-limit-monitor/SKILL.md", alias_text)
        self.assertTrue((agy_skills / "agent-limit-monitor" / "SKILL.md").is_file())
        self.assertEqual((shared_skills / alias_name / "SKILL.md").read_text(encoding="utf-8"), alias_text)
        claude_alias = claude_skills / alias_name / "SKILL.md"
        self.assertTrue(claude_alias.is_file(), "Claude Code compatibility alias was not installed")
        self.assertEqual(claude_alias.read_text(encoding="utf-8"), alias_text)
        self.assertEqual(
            (claude_skills / "agent-limit-monitor" / "SKILL.md").read_bytes(),
            SCRIPT.with_name("SKILL.md").read_bytes(),
        )
        self.assertEqual(
            (shared_skills / "agent-limit-monitor" / "agy" / alias_name / "SKILL.md").read_text(encoding="utf-8"),
            alias_text,
        )

        subprocess.run(install, env=env, capture_output=True, timeout=5, check=True)
        alias_skill.write_text("keep local edits\n", encoding="utf-8")
        result = subprocess.run(install, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(alias_skill.parent), result.stderr)
        self.assertEqual(alias_skill.read_text(encoding="utf-8"), "keep local edits\n")

    def test_install_update_backs_up_changed_skills_and_preserves_configs(self):
        env = self.env | {
            "HOME": str(self.project),
            "XDG_DATA_HOME": str(self.project / "data"),
            "XDG_STATE_HOME": str(self.project / "state"),
        }
        install = ["bash", SCRIPT.with_name("install-skill.sh")]
        skill_roots = [self.project / path for path in (
            ".agents/skills", ".gemini/antigravity-cli/skills", ".claude/skills",
        )]
        installed = [root / "agent-limit-monitor" for root in skill_roots]
        configs = [json.dumps({"threshold_percent": index + 7}) + "\n" for index in range(len(installed))]
        alias = skill_roots[0] / "agent-limit-moritor" / "SKILL.md"
        subprocess.run(install, env=env, capture_output=True, timeout=5, check=True)
        previous_skills = {}
        for index, (skill, config) in enumerate(zip(installed, configs, strict=True)):
            skill.mkdir(parents=True, exist_ok=True)
            old_skill = f"previous skill {index}\n"
            previous_skills[old_skill] = config
            (skill / "SKILL.md").write_text(old_skill, encoding="utf-8")
            (skill / "config.yaml").write_text(config, encoding="utf-8")
        config_link = installed[0] / "config.yaml"
        config_link.rename(installed[0] / "local-config.yaml")
        config_link.symlink_to("local-config.yaml")
        alias.write_text("previous alias\n", encoding="utf-8")

        result = subprocess.run([*install, "--update"], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        backups = [Path(line.removeprefix("Backup: ")) for line in result.stdout.splitlines()
                   if line.startswith("Backup: ")]
        self.assertEqual(len(backups), len(installed) + 1)
        backed_up_skills = set()
        for backup in backups:
            self.assertTrue(backup.is_absolute())
            self.assertFalse(any(backup.is_relative_to(root) for root in skill_roots))
            self.assertEqual(backup.stat().st_mode & 0o077, 0)
            old_skill = (backup / "SKILL.md").read_text(encoding="utf-8")
            backed_up_skills.add(old_skill)
            if old_skill in previous_skills:
                self.assertEqual((backup / "config.yaml").read_text(encoding="utf-8"), previous_skills[old_skill])
        self.assertEqual(backed_up_skills, {*previous_skills, "previous alias\n"})
        source_skill = SCRIPT.with_name("SKILL.md").read_bytes()
        for skill, config in zip(installed, configs, strict=True):
            self.assertEqual((skill / "SKILL.md").read_bytes(), source_skill)
            self.assertEqual((skill / "config.yaml").read_text(encoding="utf-8"), config)
        self.assertEqual(alias.read_bytes(), (SCRIPT.parent / "agy" / alias.parent.name / "SKILL.md").read_bytes())

        result = subprocess.run([*install, "--update"], env=env, capture_output=True, text=True, timeout=5, check=True)
        self.assertNotIn("Backup: ", result.stdout)

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
                argv = (sys.executable, SCRIPT, "run", "codex", "--notes", self.notes)
                with self.session(*argv) as (process, fd):
                    seen = self.until(fd, b"Exit the current agent normally")
                    self.assertIn(b"Claude Code", seen)
                    self.assertNotIn(b"Select [1-6]", seen)
                    self.assertFalse((self.project / "source-exited").exists())
                    os.write(fd, b"exit\n")
                    menu = self.until(fd, b"Select [1-6]")
                    self.assertIn(b"4. Codex", menu)
                    self.assertIn("Claude Code", (self.project / "desktop-alert.txt").read_text())
                    os.write(fd, b"3\n")
                    seen = self.until(fd, b"Type 'yes'")
                    self.assertNotIn(b"probe-secret", seen)
                    self.assertFalse((self.project / "target-launched").exists())
                    os.write(fd, answer)
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertEqual((self.project / "target-launched").is_file(), answer == b"yes\n")

    def test_switch_uses_requested_target_without_probing_source(self):
        self.fake("agy", "from pathlib import Path\nPath('source-called').touch()\n")
        self.fake("claude", r"""
import pathlib
import re
import sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{"loggedIn": true, "authMethod": "claude.ai"}')
    sys.exit(0)
package = pathlib.Path(re.search(r"(/[^ ]+\.md)", sys.argv[-1]).group(1))
try:
    assert "Fix parser; finish the regression test" in package.read_text()
    assert package.stat().st_mode & 0o777 == 0o600
finally:
    package.unlink()
pathlib.Path("target-launched").touch()
""")
        argv = (sys.executable, SCRIPT, "switch", "agy", "claude")
        for answer in (b"no\n", b"yes\n"):
            with self.subTest(answer=answer), self.session(*argv) as (process, fd):
                seen = self.until(fd, b"Handoff summary")
                self.assertIn(b"Claude Code reports an active login", seen)
                self.assertNotIn(b"Select [1-6]", seen)
                os.write(fd, b"Fix parser; finish the regression test\n")
                self.until(fd, b"Switch to claude? Type 'yes'")
                self.assertFalse((self.project / "target-launched").exists())
                os.write(fd, answer)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual((self.project / "target-launched").is_file(), answer == b"yes\n")
                self.assertFalse((self.project / "source-called").exists())

    def test_switch_exits_when_target_is_not_ready(self):
        self.fake("claude", "print('{\"loggedIn\": false}')\n")
        with self.session(sys.executable, SCRIPT, "switch", "agy", "claude") as (process, fd):
            seen = self.until(fd, b"Claude Code login could not be verified")
            self.assertEqual(process.wait(timeout=5), 1)
            self.assertNotIn(b"Handoff summary", seen)

    def test_skill_switch_to_source_reports_valid_destinations(self):
        result = subprocess.run(
            [sys.executable, SCRIPT, "switch", "agy", "agy", "--notes", self.notes, "--open"],
            cwd=self.project, env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("agy is the source. Choose one of: codex, opencode, claude.", result.stderr)

    def handoff_target(self):
        target = r"""
import json
from pathlib import Path
import re
import sys
if sys.argv[1:] == ["app-server", "--listen", "stdio://"]:
    responses = {
        "initialize": {"userAgent": "fake"},
        "account/read": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True},
        "config/read": {"config": {"model_provider": "openai"}},
        "account/rateLimits/read": {"rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300}}},
    }
    for line in sys.stdin:
        request = json.loads(line)
        if "id" in request:
            print(json.dumps({"id": request["id"], "result": responses[request["method"]]}), flush=True)
    sys.exit(0)
if sys.argv[1:3] == ["auth", "status"]:
    print('{"loggedIn": true, "authMethod": "claude.ai"}')
    sys.exit(0)
package = Path(re.search(r"(/[^ ]+\.md)", sys.argv[-1]).group(1))
try:
    assert "Found regression" in package.read_text()
finally:
    package.unlink()
Path("target-launched").write_text(Path(sys.argv[0]).name)
"""
        for name in ("codex", "claude"):
            self.fake(name, target)

    def test_skill_switch_opens_a_terminal_and_continues_with_notes(self):
        self.handoff_target()
        self.env["DISPLAY"] = ":0"
        self.fake("kitty", """
import os
import pty
import sys
assert sys.argv[1:4] == ["--detach", "--hold", "--session=none"]
assert sys.argv[4:6] == ["--directory", os.getcwd()]
sys.exit(os.waitstatus_to_exitcode(pty.spawn(sys.argv[6:])))
""")
        for target in ("codex", "claude"):
            with self.subTest(target=target):
                result = subprocess.run(
                    [sys.executable, SCRIPT, "switch", "agy", target, "--notes", self.notes, "--open"],
                    cwd=self.project, env=self.env, capture_output=True, text=True, timeout=7, check=True,
                )
                self.assertNotIn("Type 'yes'", result.stdout)
                self.assertEqual((self.project / "target-launched").read_text(), target)

    def test_skill_switch_waits_for_a_legacy_managed_source(self):
        self.handoff_target()
        self.fake("kitty", "import os, pty, sys\nsys.exit(os.waitstatus_to_exitcode(pty.spawn(sys.argv[6:])))\n")
        env = self.env | {"DISPLAY": ":0", "AGENT_LIMIT_MONITOR_ACTIVE": "1"}
        with contextlib.ExitStack() as cleanup:
            with project_lock(self.project):
                process = cleanup.enter_context(subprocess.Popen(
                    [sys.executable, SCRIPT, "switch", "agy", "claude", "--notes", self.notes, "--open"],
                    cwd=self.project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                ))
                self.until(process.stdout.fileno(), b"Waiting for the source session")
                self.assertFalse((self.project / "target-launched").is_file())
            self.assertEqual(process.wait(timeout=5), 0)
        self.assertTrue((self.project / "target-launched").is_file())

    def test_skill_switch_in_a_managed_session_replaces_the_quota_menu(self):
        self.handoff_target()
        self.env.pop("DISPLAY", None)
        self.env.pop("WAYLAND_DISPLAY", None)
        self.fake("agy", f"""
import os
from pathlib import Path
import subprocess
import sys
if len(sys.argv) > 1:
    sys.exit(1)
print("Error: Individual quota reached", flush=True)
subprocess.run([sys.executable, {str(SCRIPT)!r}, "switch", "agy", os.environ["TEST_TARGET"], "--notes",
                "handoff.json", "--open"], check=True)
input()
assert not Path("target-launched").exists()
""")
        marker = self.project / "target-launched"
        for target in ("codex", "claude"):
            self.env["TEST_TARGET"] = target
            marker.unlink(missing_ok=True)
            with self.subTest(target=target), self.session(sys.executable, SCRIPT, "run", "agy") as (process, fd):
                self.until(fd, f"Handoff queued for {target}".encode())
                self.assertFalse(marker.exists())
                os.write(fd, b"exit\n")
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(marker.read_text(), target)

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
        with self.session(sys.executable, SCRIPT, "run", "claude") as (process, fd):
            seen = self.until(fd, b"Exit the current agent normally")
            self.assertIn(b"Weekly quota remaining 2%", seen)
            self.assertIn(b"Context window usage 50%", seen)
            os.write(fd, b"exit\n")
            self.until(fd, b"Select [1-6]")
            os.write(fd, b"6\n")
            self.assertEqual(process.wait(timeout=5), 0)

    def use_shims(self):
        shims = self.project / "shims"
        shims.mkdir()
        for agent in ("codex", "opencode", "agy"):
            (shims / agent).symlink_to(SCRIPT)
        self.env["PATH"] = f"{shims}{os.pathsep}{self.env['PATH']}"

    def test_bare_agent_monitors_and_prompts_for_handoff_after_exit(self):
        self.use_shims()
        subprocess.run(["git", "init", "-q"], cwd=self.project, env=self.env, check=True)
        (self.project / "parser.py").touch()
        for agent in ("codex", "opencode", "agy"):
            self.fake(agent, """
import os
from pathlib import Path
import sys
assert str(Path.cwd() / "shims") not in os.environ["PATH"].split(os.pathsep)
agent = Path(sys.argv[0]).name
if sys.argv[1:] == ["--continue"]:
    assert agent == "agy"
    Path("source-resumed").touch()
elif sys.argv[1:]:
    expected = {
        "agy": ["-p", "/usage", "--output-format", "json"],
        "codex": ["app-server", "--listen", "stdio://"],
        "opencode": ["api", "GET"],
    }[agent]
    assert sys.argv[1:1 + len(expected)] == expected
    Path("source-probed").touch()
    sys.exit(1)
assert os.environ["AGENT_LIMIT_MONITOR_ACTIVE"] == "1"
assert Path("parser.py").is_file()
print("⚠ Individual quota reached. Please upgrade your subscription to increase your limits."
      if agent == "agy" else "Error: Rate limit exceeded", flush=True)
input()
Path("source-exited").touch()
""")
        self.fake("claude", r"""
import pathlib
import re
import sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{"loggedIn": true, "authMethod": "claude.ai"}')
    sys.exit(0)
assert pathlib.Path("source-exited").is_file()
package = pathlib.Path(re.search(r"(/[^ ]+\.md)", sys.argv[-1]).group(1))
try:
    contents = package.read_text()
    assert package.stat().st_mode & 0o777 == 0o600
    assert "Fix parser after rate limit" in contents
    assert "Git status" in contents and "?? `parser.py`" in contents
finally:
    package.unlink()
pathlib.Path("target-launched").touch()
""")
        for argv in (("codex",), ("opencode",), ("agy",), ("agy", "-c"), ("agy", "--continue")):
            with self.subTest(argv=argv):
                for marker in ("source-probed", "source-exited", "source-resumed", "target-launched"):
                    (self.project / marker).unlink(missing_ok=True)
                with self.session(*argv) as (process, fd):
                    seen = self.until(fd, b"Exit the current agent normally")
                    self.assertNotIn(b"Select [1-6]", seen)
                    self.assertTrue((self.project / "source-probed").is_file())
                    self.assertEqual((self.project / "source-resumed").is_file(), len(argv) > 1)
                    os.write(fd, b"exit\n")
                    self.until(fd, b"Select [1-6]")
                    os.write(fd, b"3\n")
                    seen = self.until(fd, b"Handoff summary")
                    self.assertIn(b"Claude Code reports an active login", seen)
                    os.write(fd, b"Fix parser after rate limit\n")
                    self.until(fd, b"Type 'yes'")
                    self.assertFalse((self.project / "target-launched").exists())
                    os.write(fd, b"yes\n")
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertTrue((self.project / "target-launched").is_file())

    def test_shims_pass_through_arguments_noninteractive_and_nested_calls(self):
        self.use_shims()
        for agent in ("codex", "opencode", "agy"):
            self.fake(agent, """
import json
import sys
print(json.dumps(sys.argv[1:]), flush=True)
sys.exit(17)
""")
            for prefix, arguments in (((), ("--version",)), ((), ("resume", "session id")),
                                      (("env", "AGENT_LIMIT_MONITOR_ACTIVE=1"), ())):
                with self.subTest(agent=agent, prefix=prefix, arguments=arguments):
                    with self.session(*prefix, agent, *arguments) as (process, fd):
                        self.until(fd, json.dumps(arguments).encode())
                        self.assertEqual(process.wait(timeout=5), 17)
            with self.subTest(agent=agent, noninteractive=True):
                result = subprocess.run([agent], cwd=self.project, env=self.env,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertEqual(json.loads(result.stdout), [])

    def test_shim_preserves_missing_and_nonexecutable_command_statuses(self):
        self.use_shims()
        (self.bin / "python3").symlink_to(sys.executable)
        self.env["PATH"] = os.pathsep.join(map(str, (self.project / "shims", self.bin)))

        with self.session("codex") as (process, _):
            self.assertEqual(process.wait(timeout=5), 127)

        (self.bin / "codex").write_text("not executable", encoding="utf-8")
        with self.session("codex", "--version") as (process, _):
            self.assertEqual(process.wait(timeout=5), 126)

    def test_auto_install_puts_shims_on_bash_path(self):
        env = self.env | {"HOME": str(self.project)}
        bashrc = self.project / ".bashrc"
        original = "# existing shell settings\n"
        bashrc.write_text(original, encoding="utf-8")
        install = ["bash", SCRIPT.with_name("install-skill.sh"), "--auto"]
        subprocess.run(install, env=env, capture_output=True, timeout=5, check=True)
        startup = bashrc.read_text(encoding="utf-8")
        self.assertTrue(startup.startswith(original))
        subprocess.run(install, env=env, capture_output=True, timeout=5, check=True)
        self.assertEqual(bashrc.read_text(encoding="utf-8"), startup)
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c",
             'source "$HOME/.bashrc"\ncommand -v codex\ncommand -v opencode\ncommand -v agy\ncommand -v agent-limit-monitor'],
            env=env, capture_output=True, text=True, timeout=5, check=True,
        )
        paths = [self.project / ".local/share/agent-limit-monitor/bin" / agent
                 for agent in ("codex", "opencode", "agy", "agent-limit-monitor")]
        self.assertEqual(result.stdout.splitlines(), [str(path) for path in paths])
        for path in paths:
            self.assertEqual(path.resolve(), SCRIPT)

    @contextlib.contextmanager
    def session(self, *args):
        master, slave = pty.openpty()
        process = subprocess.Popen(args, cwd=self.project,
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
