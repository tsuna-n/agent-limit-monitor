#!/usr/bin/env python3
"""Monitor provider limits and make an interactive, user-approved handoff."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from agent_adapters import AGENTS, BINARIES, adapter, command
from agent_adapters import claude
from handoff import make_package, project_lock
from quota_monitor import ErrorObserver, Reading, Window, parse_json
import terminal_ui


SCRIPT = Path(__file__).resolve()
ACTIVE = "AGENT_LIMIT_MONITOR_ACTIVE"
DEFAULTS = {"threshold_percent": 2, "poll_seconds": 120, "probe_timeout_seconds": 12,
            "desktop_notifications": False, "claude_statusline": True}


def read_config(path):
    settings = DEFAULTS.copy()
    if path is not None:
        data = parse_json(path.read_bytes())
        if not isinstance(data, dict) or set(data) - settings.keys():
            raise ValueError("Configuration must use the documented JSON-compatible YAML keys.")
        settings.update(data)
    for key, value in settings.items():
        valid = (type(value) is bool if type(DEFAULTS[key]) is bool else
                 type(value) in (int, float) and 0 <= value <= 86400
                 and (value <= 100 if key == "threshold_percent" else value > 0))
        if not valid:
            raise ValueError(f"Invalid configuration value for {key}.")
    return settings


def percentage(text):
    try:
        number = float(text)
        if 0 <= number <= 100:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected a percentage from 0 through 100")


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "watch", "run", "handoff"):
        sub = actions.add_parser(action)
        sub.add_argument("agent", choices=AGENTS)
        sub.add_argument("--project", type=Path, default=Path.cwd())
        sub.add_argument("--config", type=Path, default=None)
        sub.add_argument("--threshold", type=percentage, help="Alert at or below this remaining percentage.")
        sub.add_argument("--mock-5h", type=percentage, help="Simulated 5-hour quota remaining, in percent.")
        sub.add_argument("--mock-weekly", type=percentage, help="Simulated weekly quota remaining, in percent.")
        sub.add_argument("--mock-rate-error", action="store_true")
        sub.add_argument("--dry-run", action="store_true", help="Show status without running an interactive agent.")
        if action in ("check", "handoff"):
            sub.add_argument("--error-file", type=Path, help="Inspect existing CLI error output without echoing it.")
            sub.add_argument("--claude-status-stdin", action="store_true",
                             help="Read one trusted Claude Code status-line JSON object from stdin.")
        if action in ("run", "handoff"):
            sub.add_argument("--notes", type=Path, help="Reviewed JSON notes for a handoff.")
        if action == "run":
            sub.add_argument("--resume", action="store_true", help="Start by continuing the current agent's latest session.")
    hook = actions.add_parser("claude-statusline", help="Render Claude status-line JSON received on stdin.")
    hook.add_argument("snapshot", type=Path, nargs="?")
    return parser.parse_args(argv)


def read_blob(path, size=1024 * 1024):
    with path.open("rb") as source:
        return source.read(size)


def mock_reading(args):
    windows = tuple(Window(label, value) for label, value in
                    (("5-hour", args.mock_5h), ("Weekly", args.mock_weekly)) if value is not None)
    if windows or args.mock_rate_error:
        return Reading(windows, detail="Simulated quota", blocked=args.mock_rate_error)
    return None


def poll(args, settings):
    reading = mock_reading(args)
    if reading is None:
        if getattr(args, "claude_status_stdin", False):
            reading = claude.status_reading(parse_json(sys.stdin.buffer.read(1024 * 1024 + 1)))
        elif args.agent == "opencode" and getattr(args, "resume", False):
            reading = Reading(detail="Resumed OpenCode sessions can use a different provider from the project default")
        elif shutil.which(BINARIES[args.agent]):
            reading = adapter(args.agent).probe(args.project, settings["probe_timeout_seconds"])
        else:
            reading = Reading(detail="Agent executable unavailable")
    if getattr(args, "error_file", None):
        observer = ErrorObserver(args.agent)
        with args.error_file.open("rb") as source:
            while chunk := source.read(8192):
                if observer.feed(chunk):
                    break
        if observer.feed(b"", final=True):
            reading = Reading(reading.windows, reading.context_used, reading.detail, blocked=True)
    return reading


def announce(reading, agent, threshold, desktop=False):
    if not reading.trigger(threshold):
        return False
    reason = "provider reported a rate limit" if reading.blocked else f"quota at or below {threshold:g}%"
    terminal_ui.alert(f"{agent}: {reason}.", desktop)
    return True


def launch(agent, project, lease, settings, *, resume=False, prompt=None, model=None,
           probe=None, on_reading=None):
    environment = os.environ | {ACTIVE: "1"}
    observer = ErrorObserver(agent) if on_reading is not None else None
    reported_error = False

    def observe(chunk, *, final=False):
        nonlocal reported_error
        if observer.feed(chunk, final=final) and not reported_error:
            reported_error = True
            on_reading(Reading(blocked=True))

    with tempfile.TemporaryDirectory(prefix="agent-limit-status-") as directory:
        snapshot = Path(directory) / "claude.json"
        use_hook = agent == "claude" and settings["claude_statusline"]
        claude_settings = claude.settings(SCRIPT, snapshot) if use_hook else None
        next_probe = time.monotonic() + settings["poll_seconds"]
        last_snapshot = None
        job = None
        # Workers start from the first PTY tick, after the agent has forked.
        with ThreadPoolExecutor(max_workers=1) as worker:
            def tick(*, final=False):
                nonlocal job, next_probe, last_snapshot
                if use_hook:
                    try:
                        raw = read_blob(snapshot, 8192)
                    except OSError:
                        pass
                    else:
                        if raw != last_snapshot:
                            last_snapshot = raw
                            on_reading(claude.status_reading(parse_json(raw)))
                if job is not None and (job.done() or final):
                    on_reading(job.result())
                    job = None
                now = time.monotonic()
                if probe is not None and job is None and now >= next_probe and not final:
                    job = worker.submit(probe)
                    next_probe = now + settings["poll_seconds"]

            monitored = on_reading is not None
            status = terminal_ui.run(command(agent, resume=resume, prompt=prompt, model=model,
                                             claude_settings=claude_settings),
                                     str(project), env=environment, lease_fd=lease,
                                     observe=observe if monitored else None,
                                     tick=tick if monitored else None)
            if monitored:
                observe(b"", final=True)
                tick(final=True)
            return 128 - status if status < 0 else status


def handoff(args, settings, root, lease, *, resume_current=True):
    while True:
        selected = terminal_ui.select(args.agent)
        if selected is None:
            return 0
        if selected == "continue" or selected == args.agent:
            if shutil.which(BINARIES[args.agent]):
                return launch(args.agent, args.project, lease, settings, resume=resume_current)
            print("Current agent executable is unavailable.", file=sys.stderr)
        elif args.notes is None:
            print("Supply reviewed notes with --notes before switching.", file=sys.stderr)
        elif not shutil.which(BINARIES[selected]):
            print(f"{selected} executable is unavailable.", file=sys.stderr)
        else:
            verification = adapter(selected).verify(args.project, settings["probe_timeout_seconds"])
            print(verification.detail, flush=True)
            if verification.ready:
                break
    if verification.shared_codex_possible:
        print("The selected OpenCode provider route may share Codex quota.", flush=True)
    print("Confirm that the previous agent and its editing tasks have stopped.", flush=True)
    if not terminal_ui.confirm(selected):
        return 0
    package = make_package(root, args.notes)
    print(f"Private handoff package: {package}", flush=True)
    prompt = (f"Read the reviewed handoff package at {package}. Continue the original task in this "
              "directory using the package. Preserve the existing staged, unstaged, and untracked "
              "work. Review the Git status and noted test results before editing.")
    notified = False

    def on_reading(reading):
        nonlocal notified
        if not notified:
            notified = announce(reading, selected, settings["threshold_percent"], settings["desktop_notifications"])

    target_probe = None
    if selected != "claude":
        target_probe = lambda: adapter(selected).probe(args.project, settings["probe_timeout_seconds"])
    return launch(selected, args.project, lease, settings, prompt=prompt, model=verification.model,
                  probe=target_probe, on_reading=on_reading)


def execute(args, settings):
    threshold = settings["threshold_percent"] if args.threshold is None else args.threshold
    settings["threshold_percent"] = threshold
    desktop = settings["desktop_notifications"]
    if args.dry_run or args.action in ("check", "watch"):
        previous = None
        while True:
            reading = poll(args, settings)
            if reading != previous:
                print(f"{args.agent}: {reading.report()}", flush=True)
            if reading.trigger(threshold) and (previous is None or not previous.trigger(threshold)):
                if args.dry_run:
                    print("Dry run: would issue a terminal alert"
                          + (" and offer handoff choices." if args.action in ("run", "handoff") else "."))
                else:
                    announce(reading, args.agent, threshold, desktop)
            if args.action != "watch" or args.dry_run:
                return 0
            previous = reading
            time.sleep(settings["poll_seconds"])

    if os.environ.get(ACTIVE):
        raise ValueError("An agent managed by this monitor cannot start another handoff.")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Run or handoff requires an interactive terminal.")
    if getattr(args, "claude_status_stdin", False):
        raise ValueError("Use Claude status-line input with check or a dry run.")

    with project_lock(args.project) as (root, lease):
        initial = poll(args, settings)
        print(f"{args.agent}: {initial.report()}", flush=True)
        if announce(initial, args.agent, threshold, desktop):
            return handoff(args, settings, root, lease, resume_current=getattr(args, "resume", True))
        if args.action == "handoff":
            print("No handoff trigger was detected.")
            return 0
        if not shutil.which(BINARIES[args.agent]):
            raise ValueError("Current agent executable is unavailable.")
        triggered = False

        def on_reading(reading):
            nonlocal triggered
            if not triggered and reading.trigger(threshold):
                triggered = announce(reading, args.agent, threshold, desktop)
                print("Exit the current agent normally to open the handoff menu.", file=sys.stderr, flush=True)

        live_probe = None
        if args.agent != "claude" and mock_reading(args) is None:
            live_probe = lambda: poll(args, settings)
        status = launch(args.agent, args.project, lease, settings, resume=args.resume,
                        probe=live_probe, on_reading=on_reading)
        return handoff(args, settings, root, lease) if triggered and status < 128 else status


def main(argv=None):
    args = arguments(argv)
    if args.action == "claude-statusline":
        claude.statusline(args.snapshot)
        return 0
    try:
        args.project = args.project.resolve(strict=True)
        if not args.project.is_dir():
            raise ValueError("Project must be a directory.")
        if getattr(args, "claude_status_stdin", False) and args.agent != "claude":
            raise ValueError("Claude status-line data can only be read for the claude adapter.")
        default_config = SCRIPT.with_name("config.yaml")
        settings = read_config(args.config or (default_config if default_config.exists() else None))
        return execute(args, settings)
    except KeyboardInterrupt:
        return 130
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Agent monitor could not access a required local resource ({type(error).__name__}).", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as error:
        print(f"Agent monitor: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
