"""Antigravity CLI quota report and account readiness."""

from datetime import datetime
import re
import subprocess
import time

from quota_monitor import ErrorObserver, Reading, Window, obj, parse_json

from .common import Verification, capture


def usage_reading(data) -> Reading:
    data = obj(data)
    windows = {}
    groups = obj(data.get("data")).get("groups")
    if data.get("name") == "usage" and isinstance(groups, list):
        for group in groups:
            buckets = obj(group).get("buckets")
            if not isinstance(buckets, list):
                continue
            for bucket in map(obj, buckets):
                if bucket.get("disabled"):
                    continue
                label = window_label(bucket.get("window")) or window_label(bucket.get("name"))
                fraction = bucket.get("remaining_fraction")
                if label is None or type(fraction) not in (int, float) or not 0 <= fraction <= 1:
                    continue
                reset = bucket.get("reset_time")
                resets_at = reset_time(reset)
                if reset is not None and (resets_at is None or resets_at <= time.time()):
                    continue
                remaining = fraction * 100
                if label not in windows or remaining < windows[label].remaining:
                    windows[label] = Window(label, remaining, resets_at)
    if windows:
        ordered = tuple(windows[label] for label in ("5-hour", "Weekly") if label in windows)
        return Reading(windows=ordered, detail="Antigravity model-group minimums")
    return Reading(detail="Antigravity quota report has no recognized limits")


def window_label(value):
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s_-]+", "", value.lower())
    if normalized in {"5h", "5hr", "5hour", "5hours", "fivehour", "fivehours", "18000s"}:
        return "5-hour"
    if normalized in {"7d", "7day", "7days", "sevenday", "sevendays", "week", "weekly", "604800s"}:
        return "Weekly"
    return None


def reset_time(value):
    if isinstance(value, str):
        try:
            date = datetime.fromisoformat(value)
            if date.tzinfo is not None:
                return int(date.timestamp())
        except (ValueError, OverflowError, OSError):
            pass
    return None


def probe(cwd, timeout) -> Reading:
    # /usage is handled locally instead of submitting an inference turn.
    # https://antigravity.google/docs/cli/headless/#unsupported-commands-in-streaming-mode
    try:
        result = capture(["agy", "-p", "/usage", "--output-format", "json"], cwd, timeout)
    except (OSError, subprocess.SubprocessError):
        return Reading(detail="Antigravity quota probe failed")
    if result.returncode != 0:
        blocked = ErrorObserver("agy").feed(result.stdout + b"\n" + result.stderr, final=True)
        return Reading(detail="Antigravity quota probe failed", blocked=blocked)
    return usage_reading(parse_json(result.stdout))


def verify(cwd, timeout) -> Verification:
    reading = probe(cwd, timeout)
    if reading.blocked or not any(window.remaining > 0 for window in reading.windows):
        return Verification(False, "Antigravity account quota could not be verified with /usage")
    return Verification(True, "Antigravity returned authenticated model quota data")
