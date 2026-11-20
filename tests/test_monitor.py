import time
import unittest

from agent_adapters.claude import status_reading
from agent_adapters.codex import quota_reading
from quota_monitor import ErrorObserver, Reading, Window, rate_error


class MonitorTests(unittest.TestCase):
    def test_threshold_inclusive_and_unknown_and_context_do_not_trigger(self):
        self.assertEqual(Reading((Window("5-hour", 2),)).trigger(2), True)
        self.assertEqual(Reading((Window("5-hour", 2.01),), 99).trigger(2), False)
        self.assertIn("5-hour quota remaining Unknown", Reading(context_used=99).report())

    def test_codex_uses_reported_duration_and_discards_stale_windows(self):
        now = int(time.time())
        state = quota_reading({"rateLimitsByLimitId": {"codex": {
            "primary": {"windowDurationMins": 10080, "usedPercent": 99, "resetsAt": now + 60},
            "secondary": {"windowDurationMins": 300, "usedPercent": 100, "resetsAt": now - 60},
        }}})
        self.assertEqual(state.windows, (Window("Weekly", 1, now + 60),))
        self.assertFalse(quota_reading({"rateLimits": {"primary": {"usedPercent": 99}}}).trigger(2))
        limited = quota_reading({"rateLimits": {"rateLimitReachedType": "rate_limit_reached"}})
        self.assertTrue(limited.trigger(2))
        self.assertEqual(limited.windows, ())

    def test_claude_reads_independent_quota_and_context_fields(self):
        state = status_reading({"context_window": {"used_percentage": 99}, "rate_limits": {
            "five_hour": {"used_percentage": 70}, "seven_day": {"used_percentage": 98},
        }})
        self.assertEqual(state.context_used, 99)
        self.assertEqual(state.windows, (Window("5-hour", 30), Window("Weekly", 2)))
        self.assertTrue(state.trigger(2))
        self.assertEqual(status_reading({"rate_limits": {"five_hour": {"used_percentage": True}}}).windows, ())

    def test_structured_rate_errors_ignore_context_and_success_content(self):
        self.assertTrue(rate_error("claude", '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}'))
        self.assertTrue(rate_error("claude", '{"type":"assistant","error":"rate_limit"}'))
        self.assertTrue(rate_error("claude", "API Error: Rate limit reached"))
        self.assertFalse(rate_error("claude", "You've hit your context limit."))
        self.assertTrue(rate_error("codex", '{"type":"turn.failed","error":{"codexErrorInfo":"usageLimitExceeded"}}'))
        self.assertTrue(rate_error("codex", '{"type":"error","message":"You have hit your usage limit."}'))
        self.assertTrue(rate_error("codex", "■ You’ve hit your usage limit."))
        self.assertTrue(rate_error("opencode", '{"type":"error","error":{"name":"APIError","data":{"statusCode":429}}}'))
        self.assertTrue(rate_error("opencode", '{"type":"error","error":{"type":"provider.rate-limit","status":429}}'))
        self.assertTrue(rate_error("opencode", '{"type":"session.execution.failed","data":{"error":{"type":"provider.quota"}}}'))
        self.assertFalse(rate_error("codex", '{"type":"error","error":{"codexErrorInfo":"contextWindowExceeded"}}'))
        self.assertFalse(rate_error("agy", '{"status":"SUCCESS","response":"API Error: 429"}'))
        self.assertFalse(rate_error("claude", 'Tests should expect HTTP 429 when limited.'))

    def test_terminal_chunk_split_and_ansi(self):
        observer = ErrorObserver("claude")
        self.assertFalse(observer.feed(b"\x1b[31mAPI Error: 4"))
        self.assertTrue(observer.feed(b"29 Too Many Requests\x1b[0m\r\n"))

    def test_oversized_line_cannot_turn_its_suffix_into_an_error(self):
        observer = ErrorObserver("claude")
        self.assertFalse(observer.feed(b"x" * (64 * 1024 + 1)))
        self.assertFalse(observer.feed(b"API Error: 429\n"))
        self.assertTrue(observer.feed(b"API Error: 429", final=True))
