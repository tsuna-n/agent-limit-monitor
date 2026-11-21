from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agent_adapters import agy, opencode


def process(argv, data=None, *, code=0, error=b""):
    return subprocess.CompletedProcess(argv, code, json.dumps(data).encode(), error)


class AntigravityTests(unittest.TestCase):
    def test_recognized_windows_minimum_and_redaction(self):
        reset = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
        payload = {
            "name": "usage",
            "data": {"groups": [
                {"name": "sensitive group name", "buckets": [
                    {"window": "5 hours", "remaining_fraction": 0.25},
                    {"window": "weekly", "remaining_fraction": 0.75},
                    {"window": "weekly", "remaining_fraction": 0, "disabled": True},
                ]},
                {"buckets": [
                    {"window": "5h", "remaining_fraction": 0.02, "reset_time": reset.isoformat()},
                    {"window": "7 days", "remaining_fraction": True},
                    {"window": "weekly", "remaining_fraction": 0.01, "reset_time": "yesterday"},
                    {"window": "weekly", "remaining_fraction": 0.01, "reset_time": "2000-01-01T00:00:00Z"},
                ]},
            ]},
        }
        with patch.object(agy, "capture", return_value=process([], payload)) as capture:
            reading = agy.probe(Path.cwd(), 2)
            self.assertEqual(capture.call_args.args[0], ["agy", "-p", "/usage", "--output-format", "json"])
            self.assertEqual([(w.label, w.remaining) for w in reading.windows], [("5-hour", 2), ("Weekly", 75)])
            self.assertEqual(reading.windows[0].resets_at, int(reset.timestamp()))
            self.assertNotIn("sensitive group name", reading.report())
            self.assertTrue(agy.verify(Path.cwd(), 2).ready)

    def test_usage_command_envelope_reports_quota(self):
        payload = {"status": "SUCCESS", "command": {
            "name": "usage",
            "data": {"groups": [{"buckets": [
                {"window": "5h", "remaining_fraction": 0.25},
                {"window": "weekly", "remaining_fraction": 0.75},
            ]}]},
        }}
        with patch.object(agy, "capture", return_value=process([], payload)):
            reading = agy.probe(Path.cwd(), 2)
            self.assertEqual([(w.label, w.remaining) for w in reading.windows], [("5-hour", 25), ("Weekly", 75)])

    def test_generic_success_and_rate_limit_have_no_quota(self):
        with patch.object(agy, "capture", return_value=process([], {"status": "SUCCESS"})):
            self.assertFalse(agy.verify(Path.cwd(), 2).ready)
        failed = process([], {"status": "ERROR", "error": "RESOURCE_EXHAUSTED"}, code=1)
        with patch.object(agy, "capture", return_value=failed):
            reading = agy.probe(Path.cwd(), 2)
            self.assertTrue(reading.blocked)
            self.assertFalse(reading.windows)


class OpenCodeTests(unittest.TestCase):
    @contextmanager
    def replies(self, provider, *, connection=None, reset=None, limit=False, wrong_directory=False, settings=None,
                go_console_connection=None):
        with tempfile.TemporaryDirectory(prefix="switch work ") as tmp:
            project = Path(tmp).resolve()
            model = {"id": "model", "providerID": provider, "enabled": True, "settings": settings}
            expiry = reset if reset is not None else int(datetime.now(timezone.utc).timestamp() + 3600) * 1000
            meter = {"percent": 98, "resetsAt": expiry, "limited": limit}
            integration = "opencode" if provider == "opencode-go" and go_console_connection is None else provider
            data = {
                "/api/model/default": model,
                f"/api/provider/{provider}": {"id": provider, "activation": "auto", "integrationID": integration},
                f"/api/integration/{integration}": {
                    "id": integration,
                    "connections": [connection or {"type": "env", "name": "OPENCODE_API_KEY"}],
                },
                "/api/experimental/usage/limits": {"go": {"rolling": meter, "weekly": meter, "monthly": meter}},
            }
            if go_console_connection is not None:
                data["/api/integration/opencode"] = {"id": "opencode", "connections": [go_console_connection]}

            def capture(argv, cwd, timeout):
                self.assertEqual(argv[:3], ["opencode", "api", "GET"])
                self.assertEqual(cwd, project)
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 2)
                route = urlsplit(argv[3])
                self.assertEqual(parse_qs(route.query), {"location[directory]": [str(project)]})
                directory = "/different/project" if wrong_directory else str(project)
                return process(argv, {"location": {"directory": directory}, "data": data[route.path]})

            with patch.object(opencode, "capture", side_effect=capture) as invocation:
                yield project, invocation

    def test_go_allowance_and_connection(self):
        with self.replies("opencode-go") as (project, _):
            reading = opencode.probe(project, 2)
            self.assertEqual([(w.label, w.remaining) for w in reading.windows],
                             [("5-hour", 2), ("Weekly", 2), ("Monthly", 2)])
            verification = opencode.verify(project, 2)
            self.assertTrue(verification.ready)
            self.assertEqual(verification.model, "opencode-go/model")
        with self.replies("opencode-go", limit=True) as (project, _):
            reading = opencode.probe(project, 2)
            self.assertTrue(reading.blocked)
            self.assertEqual(reading.windows[0].remaining, 2)
            self.assertFalse(opencode.verify(project, 2).ready)
        with self.replies("opencode-go", reset=1000) as (project, _):
            self.assertFalse(opencode.probe(project, 2).windows)

    def test_go_alias_uses_only_the_selected_workspace_credential(self):
        with self.replies("opencode-go", go_console_connection={"type": "env", "name": "OPENCODE_API_KEY"}) as (project, _):
            self.assertTrue(opencode.probe(project, 2).windows)
        different = {"type": "credential", "id": "different-workspace"}
        with self.replies("opencode-go", go_console_connection=different) as (project, _):
            self.assertFalse(opencode.probe(project, 2).windows)
            self.assertTrue(opencode.verify(project, 2).ready)

    def test_other_providers_read_only_their_own_status(self):
        with self.replies("anthropic") as (project, invocation):
            self.assertFalse(opencode.probe(project, 2).windows)
            self.assertEqual(invocation.call_count, 1)
            self.assertTrue(opencode.verify(project, 2).ready)
        with self.replies("openai") as (project, _):
            verification = opencode.verify(project, 2)
            self.assertTrue(verification.shared_codex_possible)
            self.assertTrue(verification.ready)
        with self.replies("anthropic", settings={"baseURL": "https://custom.invalid"}) as (project, _):
            self.assertTrue(opencode.verify(project, 2).shared_codex_possible)
        with self.replies("opencode-go", settings={"baseURL": "https://custom.invalid"}) as (project, _):
            self.assertFalse(opencode.probe(project, 2).windows)

    def test_connection_and_project_must_match(self):
        needs_auth = {"type": "credential", "status": {"status": "needs_auth", "message": "sensitive"}}
        with self.replies("anthropic", connection=needs_auth) as (project, _):
            verification = opencode.verify(project, 2)
            self.assertFalse(verification.ready)
            self.assertNotIn("sensitive", verification.detail)
        with self.replies("anthropic", wrong_directory=True) as (project, _):
            self.assertFalse(opencode.verify(project, 2).ready)
        with self.replies("anthropic", settings={"apiKey": "sensitive"}) as (project, _):
            verification = opencode.verify(project, 2)
            self.assertFalse(verification.ready)
            self.assertNotIn("sensitive", verification.detail)
