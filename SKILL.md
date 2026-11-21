---
name: agent-limit-monitor
description: Monitor actual CLI coding-agent quota and prepare a user-approved handoff when Codex, Google Antigravity, OpenCode, or Claude Code approaches or reaches a provider rate limit. Use for quota alerts, rate-limit errors, or transferring unfinished project work between these CLIs.
---

# Agent Limit Monitor & Handoff

Use `agent_limit_monitor.py` from this skill directory. See [README.md](README.md) for installation, commands, and supported sources.

- Use `check AGENT --project DIR` for a one-time reading. Report unsupported or missing quota as `Unknown`. Context-window usage is an independent metric.
- For AGY, `check agy` reads the built-in `/usage` report. The nested CLI needs access to its local state and localhost. From a sandboxed AGY session, use AGY's per-command approval to run only `check agy --project DIR` outside the terminal sandbox. If that run is unavailable, ask the user to enter `/usage` in AGY.
- Use `run AGENT --project DIR --notes FILE` in the user's terminal to monitor a new or resumed interactive session. Use `--dry-run` with `--mock-5h 2` to demonstrate the threshold without opening an agent. For an existing session, gather reviewed notes and use `handoff` after the session exits.
- Prepare the notes using the README schema. Include the original objective, current progress, remaining tasks, test results, known issues, and the next action. Keep secrets out of the notes. The CLI adds Git status and creates a private package after confirmation.
- Wait for the user to choose and explicitly confirm in the terminal. Stop editing in the source session before confirming. Keep the worktree and its staged, unstaged, and untracked changes intact. OpenCode's configured provider may share the current account quota.
