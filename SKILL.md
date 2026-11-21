---
name: agent-limit-monitor
description: Monitor actual CLI coding-agent quota and prepare a user-approved handoff when Codex, Google Antigravity, OpenCode, or Claude Code approaches or reaches a provider rate limit. Use for quota alerts, rate-limit errors, or transferring unfinished project work between these CLIs.
---

# Agent Limit Monitor & Handoff

Use `agent_limit_monitor.py` from this skill directory. See [README.md](README.md) for installation, commands, and supported sources.

## Handoff destination menu

- A bare skill invocation starts destination selection. Use the active CLI as the source. Both AGY slash commands use `agy`, Codex uses `codex`, and OpenCode uses `opencode`.
- Before running any commands, ask which agent should take over. Offer the other supported agents (**AGY CLI**, **Codex**, **OpenCode**, **Claude Code**) and **Cancel**. Use the CLI's interactive question tool when available (`ask_question` in AGY or `question` in OpenCode). Otherwise show the choices in the response and wait for the user's reply.
- After a choice, show `agent-limit-monitor switch SOURCE TARGET` with the matching `agy`, `codex`, `opencode`, or `claude` arguments. For example, show `agent-limit-monitor switch agy codex` for an AGY-to-Codex handoff. Tell the user to save their work, exit the source CLI normally, and run the command in Bash from the same project. It checks the target, prompts for a summary, and waits for `yes`. If the user cancels, stop.
- If the user names a destination in the invocation, show its command directly. If the user asks to check quota, follow the quota workflow below.

## Quota and handoff commands

- Use `check AGENT --project DIR` for a one-time reading. Report unsupported or missing quota as `Unknown`. Context-window usage is an independent metric.
- For AGY, `check agy` reads the built-in `/usage` report. The nested CLI needs access to its local state and localhost. From a sandboxed AGY session, use AGY's per-command approval to run only `check agy --project DIR` outside the terminal sandbox. If that run is unavailable, ask the user to enter `/usage` in AGY.
- A quota-blocked AGY turn can also block the slash skill. With the Bash launchers installed, start `agy` or `agy --continue` from the terminal to detect the error outside AGY. The alert names installed alternatives. Exit AGY normally to select Codex, OpenCode, or Claude Code in the handoff menu.
- The Bash launchers also monitor bare `codex` and `opencode` commands. Otherwise, use `run AGENT --project DIR --notes FILE` in the user's terminal. Use `--dry-run` with `--mock-5h 2` to demonstrate the threshold. For an already-closed session, use `handoff` with reviewed notes and an error file.
- To name the destination directly after closing the source, run `agent-limit-monitor switch agy codex` in the project terminal. The command prompts for a summary unless `--notes FILE` is supplied and waits for confirmation before starting the target.
- Prepare the notes using the README schema. Include the original objective, current progress, remaining tasks, test results, known issues, and the next action. Keep secrets out of the notes. The CLI adds Git status and creates a private package after confirmation.
- Wait for the user to choose and explicitly confirm in the terminal. Stop editing in the source session before confirming. Keep the worktree and its staged, unstaged, and untracked changes intact. OpenCode's configured provider may share the current account quota.
