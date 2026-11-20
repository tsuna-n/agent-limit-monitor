"""Quota normalization and rate-limit evidence."""

from dataclasses import dataclass
import json
import re
import time


@dataclass(frozen=True)
class Window:
    label: str
    remaining: float
    resets_at: int | None = None


@dataclass(frozen=True)
class Reading:
    windows: tuple[Window, ...] = ()
    context_used: float | None = None
    detail: str = ""
    blocked: bool = False

    def trigger(self, threshold: float) -> bool:
        return self.blocked or any(window.remaining <= threshold for window in self.windows)

    def report(self) -> str:
        parts = []
        for label in ("5-hour", "Weekly"):
            amounts = [window.remaining for window in self.windows if window.label == label]
            parts.append(f"{label} quota remaining {min(amounts):g}%" if amounts else f"{label} quota remaining Unknown")
        for window in self.windows:
            if window.label not in ("5-hour", "Weekly"):
                parts.append(f"{window.label} quota remaining {window.remaining:g}%")
        parts.append("Context window usage Unknown" if self.context_used is None
                     else f"Context window usage {self.context_used:g}%")
        if self.detail:
            parts.append(self.detail)
        return " | ".join(parts)


def obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def parse_json(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        return None


def percent(value) -> float | None:
    return value if type(value) in (int, float) and 0 <= value <= 100 else None


def used_window(label: str, used, reset=None) -> Window | None:
    used = percent(used)
    if used is None or (reset is not None and (type(reset) is not int or reset <= time.time())):
        return None
    return Window(label, 100 - used, reset)


_ANSI = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]")
_RATE = re.compile(
    r"^(?:[⚠✕✖×!●▪■•│┃║ ]+)?(?:"
    r"(?:API Error|HTTP(?: Error)?|Status code|error)[: ]+429\b"
    r"|(?:Error|API Error|Provider error):\s*(?:RESOURCE_EXHAUSTED|rate_limit(?:_error|_exceeded)|usage_limit_reached)\b"
    r"|(?:Error:\s*)?(?:You['’]ve|You have) (?:hit|reached) your "
    r"(?:(?:5-hour|weekly|daily|monthly|session) )?(?:usage |rate )?limit\b"
    r"|(?:(?:API )?Error:\s*)?(?:Too many requests|Rate limit (?:exceeded|reached))\b"
    r")",
    re.IGNORECASE,
)


def rate_error(agent: str, line: str) -> bool:
    line = line.strip()
    data = parse_json(line) if line.startswith("{") else None
    if isinstance(data, dict):
        if agent == "claude":
            return ((data.get("type") == "rate_limit_event"
                     and obj(data.get("rate_limit_info")).get("status") == "rejected")
                    or (data.get("type") == "assistant" and data.get("error") == "rate_limit"))
        if agent == "codex":
            fields = obj(data.get("params")) if data.get("method") == "error" else data
            metadata = obj(fields.get("error"))
            info = metadata.get("codexErrorInfo")
            message = fields.get("message") if data.get("type") == "error" else None
            if not isinstance(message, str):
                message = metadata.get("message")
            return ((data.get("type") in ("error", "turn.failed") or data.get("method") == "error")
                    and not fields.get("willRetry")
                    and (info in ("usageLimitExceeded", "rateLimitExceeded")
                         or obj(obj(info).get("httpConnectionFailed")).get("httpStatusCode") == 429
                         or (isinstance(message, str) and bool(_RATE.match(message)))))
        if agent == "agy":
            result = obj(data.get("result")) if data.get("event") == "result" else data
            message = result.get("error")
            return (result.get("status") == "ERROR" and isinstance(message, str)
                    and bool(_RATE.match(message) or _RATE.match(f"Error: {message}")))
        if agent == "opencode":
            kind = data.get("type")
            fields = obj(data.get("data")) if kind == "session.execution.failed" else data
            error = obj(fields.get("error"))
            return (kind in ("error", "session.execution.failed")
                    and (error.get("type") in ("provider.rate-limit", "provider.go-limit", "provider.free-limit",
                                               "provider.budget-limit", "provider.chatgpt-limit", "provider.quota")
                         or (error.get("name") == "APIError" and obj(error.get("data")).get("statusCode") == 429)))
        return False
    return bool(_RATE.match(line))


class ErrorObserver:
    """Remember matching evidence without retaining a terminal transcript."""

    def __init__(self, agent: str):
        self.agent = agent
        self.pending = b""
        self.detected = False

    def feed(self, data: bytes, *, final: bool = False) -> bool:
        if self.detected:
            return True
        limit = 64 * 1024
        lines = re.split(rb"[\r\n]", data)
        lines[0] = self.pending + lines[0] if self.pending is not None else None
        self.pending = b"" if final else lines.pop()
        if self.pending is not None and len(self.pending) > limit:
            self.pending = None
        self.detected = any(rate_error(self.agent, _ANSI.sub(b"", line).decode(errors="replace"))
                            for line in lines if line is not None and len(line) <= limit)
        return self.detected
