"""Installed binary smoke tests and opt-in real account probes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agent_adapters import AGENTS, adapter


class RealCLIIntegrationTests(unittest.TestCase):
    def test_installed_entrypoints_expose_documented_flags(self):
        commands = {
            "codex": (["app-server", "--help"], b"stdio://"),
            "agy": (["--help"], b"--prompt-interactive"),
            "opencode": (["mini", "--help"], b"--model"),
            "claude": (["--help"], b"--settings"),
        }
        installed = [name for name in commands if shutil.which(name)]
        if not installed:
            self.skipTest("No supported CLI is installed")
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ | {"XDG_DATA_HOME": f"{directory}/data",
                                "XDG_CACHE_HOME": f"{directory}/cache",
                                "XDG_STATE_HOME": f"{directory}/state"}
            for name in installed:
                with self.subTest(agent=name):
                    flags, documented = commands[name]
                    result = subprocess.run([name, *flags], cwd=directory, env=env,
                                            capture_output=True, timeout=15)
                    self.assertIn(documented, result.stdout + result.stderr)

    def test_requested_real_account_probes(self):
        requested = os.environ.get("ALM_LIVE_AGENTS", "").split()
        if not requested:
            self.skipTest("Set ALM_LIVE_AGENTS to agents with eligible, authenticated accounts")
        self.assertTrue(set(requested) <= set(AGENTS))
        project = Path(os.environ.get("ALM_LIVE_PROJECT", Path.cwd())).resolve()
        for name in requested:
            with self.subTest(agent=name):
                provider = adapter(name)
                if name != "claude":
                    self.assertTrue(provider.probe(project, 20).windows, "No live quota window returned")
                if name != "codex":
                    self.assertTrue(provider.verify(project, 20).ready, "CLI did not verify authentication")
