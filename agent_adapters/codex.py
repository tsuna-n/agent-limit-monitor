"""Codex's documented stdio app-server account interface."""

import json
import os
import selectors
import subprocess
import time

from quota_monitor import Reading, obj, parse_json, used_window

from .common import Verification


def quota_reading(result) -> Reading:
    result = obj(result)
    legacy = obj(result.get("rateLimits"))
    limits = obj(obj(result.get("rateLimitsByLimitId")).get("codex"))
    if not limits and legacy.get("limitId") in (None, "codex"):
        limits = legacy
    windows = []
    for key in ("primary", "secondary"):
        bucket = obj(limits.get(key))
        duration = bucket.get("windowDurationMins")
        if type(duration) is not int or duration <= 0:
            continue
        label = {300: "5-hour", 10080: "Weekly"}.get(duration)
        if label is None:
            label = f"Codex {duration}-minute"
        window = used_window(label, bucket.get("usedPercent"), bucket.get("resetsAt"))
        if window is not None:
            windows.append(window)
    blocked = limits.get("rateLimitReachedType") in (
        "rate_limit_reached", "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached",
    )
    return Reading(tuple(windows), detail="Codex account rate limits (codex bucket)", blocked=blocked)


def probe(cwd, timeout) -> Reading:
    unavailable = Reading(detail="Codex ChatGPT account quota unavailable")
    try:
        with subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ) as server, selectors.DefaultSelector() as selector:
            selector.register(server.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            pending = b""

            def send(message):
                server.stdin.write(json.dumps(message).encode() + b"\n")
                server.stdin.flush()

            def request(identifier, method, **params):
                nonlocal pending
                message = {"id": identifier, "method": method}
                if params:
                    message["params"] = params
                send(message)
                while time.monotonic() < deadline:
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        item = obj(parse_json(line))
                        if item.get("id") == identifier:
                            return obj(item.get("result"))
                    if not selector.select(max(0, deadline - time.monotonic())):
                        break
                    chunk = os.read(server.stdout.fileno(), 8192)
                    if not chunk or len(pending) >= 4 * 1024 * 1024:
                        break
                    pending += chunk
                return {}

            try:
                if not request(1, "initialize", clientInfo={"name": "agent_limit_monitor", "version": "1.0"}):
                    return unavailable
                send({"method": "initialized"})
                account = request(2, "account/read", refreshToken=False)
                if (obj(account.get("account")).get("type") != "chatgpt"
                        or account.get("requiresOpenaiAuth") is not True):
                    return unavailable
                config = request(3, "config/read", includeLayers=False, cwd=str(cwd)).get("config")
                if not isinstance(config, dict) or config.get("model_provider") not in (None, "openai"):
                    return unavailable
                result = request(4, "account/rateLimits/read")
                return quota_reading(result) if result else unavailable
            finally:
                if server.poll() is None:
                    server.kill()
    except (OSError, subprocess.SubprocessError):
        return unavailable


def verify(cwd, timeout) -> Verification:
    reading = probe(cwd, timeout)
    if reading.trigger(0):
        return Verification(False, "Codex account quota is exhausted")
    if not reading.windows:
        return Verification(False, "Codex account quota could not be verified")
    return Verification(True, "Codex returned authenticated account quota data")
