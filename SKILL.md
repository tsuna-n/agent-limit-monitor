---
name: agent-limit-monitor
description: Monitor actual CLI coding-agent quota and prepare a user-approved handoff when Codex, Google Antigravity, OpenCode, or Claude Code approaches or reaches a provider rate limit. Use for quota alerts, rate-limit errors, or transferring unfinished project work between these CLIs.
---

# Agent Limit Monitor & Handoff

Use `agent_limit_monitor.py` from this skill directory. See [README.md](README.md) for installation, commands, and supported sources.

## Handoff destination menu

- Set `SOURCE` to the CLI hosting this conversation. Inside Antigravity CLI, `SOURCE` is always `agy`. The supported agent keys are `agy`, `codex`, `opencode`, and `claude`.
- For a bare invocation, show the destination menu and wait for the user's reply. In Antigravity CLI, show **1. Codex** (`codex`), **2. OpenCode** (`opencode`), **3. Claude Code** (`claude`), **4. Cancel**. In other hosts, offer the supported agents with `SOURCE` excluded, plus Cancel. Use the host's question tool when available. Accept a name or a number from the menu you showed. An explicit destination supplied in the invocation also counts as the choice. If the reply is unclear or names `SOURCE`, ask again before preparing notes. On cancellation, stop. For a quota check, follow the quota workflow below.
- The destination choice authorizes the handoff. Stop source editing and let any editing jobs finish. Use this conversation to write the existing JSON notes schema to a mode-0600 file outside the project, with the original task, progress, test results, and next action. Keep credentials, private keys, and unrelated conversation out of the file. Keep the file available until the target starts.
- Run `python3 -B /absolute/path/to/agent_limit_monitor.py switch SOURCE TARGET --project PROJECT --notes NOTES --open` using the host's shell tool. Use the script next to this main skill document and absolute paths for the project and notes. For example, the arguments in Antigravity CLI are `switch agy codex` for Codex and `switch agy claude` for Claude Code.
- When Antigravity's terminal sandbox is active, use its normal per-command approval to create the private notes in the host's temporary directory and run the exact `switch ... --open` command on the host. Set `BypassSandbox` on those tool calls when available. The host command needs access to the same temporary files as the monitor and to the desktop socket if it opens Kitty. If host execution is unavailable, tell the user to run the manual `switch` command in the project terminal after exiting Antigravity. In other hosts, use their per-command approval if the sandbox blocks the handoff.
- For an unmanaged source, Kitty opens and starts the verified destination with the handoff package. For a monitor-managed source, tell the user to exit the source normally so the destination can take over. The monitor queues it in the owning terminal when supported. If a separate window waits and the old terminal shows a quota handoff menu after exit, choose Cancel there to release the project. Report the tool result accurately and honor any startup error shown by the monitor.

## Quota and handoff commands

- Use `check AGENT --project DIR` for a one-time reading. Report unsupported or missing quota as `Unknown`. Context-window usage is an independent metric.
- For AGY, `check agy` reads the built-in `/usage` report. The nested CLI needs access to its local state and localhost. From a sandboxed AGY session, use AGY's per-command approval to run only `check agy --project DIR` outside the terminal sandbox. If that run is unavailable, ask the user to enter `/usage` in AGY.
- A quota-blocked AGY turn can also block the slash skill. With the Bash launchers installed, start `agy` or `agy --continue` from the terminal to detect the error outside AGY. The alert names installed alternatives. Exit AGY normally to select Codex, OpenCode, or Claude Code in the handoff menu.
- The Bash launchers also monitor bare `codex` and `opencode` commands. Otherwise, use `run AGENT --project DIR --notes FILE` in the user's terminal. Use `--dry-run` with `--mock-5h 2` to demonstrate the threshold. For an already-closed session, use `handoff` with reviewed notes and an error file.
- To name the destination directly after closing the source, run `agent-limit-monitor switch agy codex` in the project terminal. The command prompts for a summary unless `--notes FILE` is supplied and waits for confirmation before starting the target.
- Prepare the notes using the README schema. Include the original objective, current progress, remaining tasks, test results, known issues, and the next action. Keep secrets out of the notes. The CLI adds Git status and creates a private package after confirmation.
- For manual terminal handoffs, wait for the terminal confirmation. Keep the worktree and its staged, unstaged, and untracked changes intact. OpenCode's configured provider may share the current account quota.
