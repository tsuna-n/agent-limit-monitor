"""Claude Code's status-line JSON and authenticated CLI status."""

import json
import os
import shlex
import subprocess
import sys
import tempfile

from .common import Verification, capture
from quota_monitor import Reading, obj, parse_json, percent, used_window


def status_reading(data) -> Reading:
    data = obj(data)
    limits = obj(data.get("rate_limits"))
    windows = []
    for key, label in (("five_hour", "5-hour"), ("seven_day", "Weekly")):
        bucket = obj(limits.get(key))
        window = used_window(label, bucket.get("used_percentage"), bucket.get("resets_at"))
        if window is not None:
            windows.append(window)
    context = percent(obj(data.get("context_window")).get("used_percentage"))
    return Reading(tuple(windows), context, "Claude Code status line")


def probe(cwd, timeout) -> Reading:
    return Reading(detail="Claude quota requires session status-line data")


def verify(cwd, timeout) -> Verification:
    try:
        result = capture(["claude", "auth", "status"], cwd, timeout)
        data = obj(parse_json(result.stdout))
        if (result.returncode == 0 and data.get("loggedIn") is True
                and data.get("authMethod") not in (None, "none")):
            return Verification(True, "Claude Code reports an active login")
    except (OSError, subprocess.SubprocessError):
        pass
    return Verification(False, "Claude Code login could not be verified; run claude auth status")


def settings(script, snapshot) -> str:
    command = shlex.join([sys.executable, str(script), "claude-statusline", str(snapshot)])
    return json.dumps({"statusLine": {"type": "command", "command": command}})


def statusline(snapshot) -> None:
    reading = status_reading(parse_json(sys.stdin.buffer.read(1024 * 1024 + 1)))
    # Store only validated metric values, never the vendor payload.
    data = {"context_window": {"used_percentage": reading.context_used}, "rate_limits": {}}
    for window in reading.windows:
        key = "five_hour" if window.label == "5-hour" else "seven_day"
        data["rate_limits"][key] = {"used_percentage": 100 - window.remaining, "resets_at": window.resets_at}
    if snapshot is not None:
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=snapshot.parent, delete=False) as file:
                path = file.name
                json.dump(data, file)
            os.replace(path, snapshot)
        except OSError:
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    print(reading.report())
