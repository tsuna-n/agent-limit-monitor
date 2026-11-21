# Agent Limit Monitor & Handoff

Monitor provider quota from a Linux terminal. At 2% remaining, or when the running agent reports a rate-limit error, the monitor alerts you. A managed session offers a handoff after the current agent exits.

## Install

Keep this directory together, and make its entry point available as a command. Python 3.11 or newer is required. Git provides the handoff's worktree status. Install and sign in to each agent you intend to use through its own CLI. For optional Linux desktop notifications, install `notify-send` and use a graphical session with a notification daemon.

```bash
mkdir -p "$HOME/.local/bin"
ln -s /absolute/path/to/switch-work/agent_limit_monitor.py "$HOME/.local/bin/agent-limit-monitor"
agent-limit-monitor --help
```

Add `$HOME/.local/bin` to your `PATH` if needed. You can also run `python3 /absolute/path/to/switch-work/agent_limit_monitor.py`.

Run `bash install-skill.sh` to install the skill and its alias for Codex and OpenCode in `~/.agents/skills`, AGY in `~/.gemini/antigravity-cli/skills`, and Claude Code in `~/.claude/skills`. [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Antigravity skills](https://antigravity.google/docs/skills), [OpenCode skills](https://opencode.ai/docs/skills/), [Claude Code skills](https://code.claude.com/docs/en/skills)

Run `bash install-skill.sh --update` to refresh all four user installations. Updates retain each installed `config.yaml` and print the backup path for each replaced copy. To install or refresh a project copy in this checkout, use `--project-opencode` or `--project-claude`, with `--update` first when refreshing. OpenCode gives the project copy precedence over the user copy. Start a new agent session to pick up the updated skill. [OpenCode skill precedence](https://opencode.ai/docs/skills/#precedence)

### Monitor normal launches in Bash

Run this once from the monitor checkout, then open a new Bash terminal.

```bash
bash install-skill.sh --auto
```

In your working project, type `codex`, `opencode`, or `agy` to open a monitored interactive session. `agy --continue` and `agy -c` resume the latest AGY session with monitoring. Other commands with arguments and non-interactive calls go straight to the original CLI.

The AGY launcher recognizes `Individual quota reached` even when the quota prevents a slash skill from running. The alert names the installed alternatives. Save your work and exit the agent normally to open the menu. To switch, select an agent, enter a short handoff summary, and type `yes` to confirm.

Setup adds an `agent-limit-monitor` PATH line to `~/.bashrc` and links the monitor command and launchers under `~/.local/share/agent-limit-monitor/bin/` to this checkout. Keep the checkout at that path. To turn off automatic monitoring, remove the tagged line from `~/.bashrc` and open a new terminal.

## Use

Invoke `agent-limit-monitor` or the alias `agent-limit-moritor` using the skill syntax of your CLI. Enter the destination by name or by its number in the displayed menu. In Antigravity, select **1. Codex**, **2. OpenCode**, **3. Claude Code**, or **4. Cancel**. The skill writes handoff notes from the current conversation and runs `switch SOURCE TARGET --notes FILE --open`. When Antigravity requests permission for the handoff command to run outside its terminal sandbox, approve that command to let it reach the monitor or Kitty. For a monitor-managed source, exit the source normally so the target can take over. The target uses the same terminal when the source wrapper supports queuing. An unmanaged source opens the target in Kitty. If the source cannot respond because its quota is exhausted, use `switch` directly from Bash after exiting it.

Run from the project directory. Every command also accepts `--project DIR`.

```bash
agent-limit-monitor check codex
agent-limit-monitor watch codex --threshold 3
agent-limit-monitor run codex --notes /path/to/reviewed-notes.json
agent-limit-monitor run claude --resume --notes /path/to/reviewed-notes.json
agent-limit-monitor run codex --dry-run --mock-5h 2 --mock-weekly 43
agent-limit-monitor switch agy codex
```

- `check` reads once. `watch` polls and alerts on entry into a low-quota state.
- `run` starts a managed interactive agent in the same directory. It polls while the agent runs and watches for rate-limit errors. If an alert occurs during the session, save work and exit normally to open the menu. A handoff after an already-closed session is also available with `handoff AGENT --notes FILE`. A previous CLI error can be supplied with `--error-file FILE` to `check` or `handoff`. Use a CLI-generated error stream with `--error-file`, such as Codex's `exec --json` output.
- `switch SOURCE TARGET` starts a manual handoff after the source exits. It checks the target, asks for a summary or reads `--notes FILE`, and waits for `yes` before launching the target. It works without quota data or a rate-limit error from the source.
- The terminal menu accepts agent names or numbers, plus Continue and Cancel. The selected CLI must pass its readiness checks. Codex also needs a verified quota with capacity remaining. OpenCode handoffs verify the model and start the interactive session in [standalone mode](https://opencode.ai/docs/cli/#background-service). Type `yes` during a manual terminal switch to confirm the prior agent and its editing jobs have stopped and start the selected destination.
- `--dry-run` runs one check and prints the action it would take. Pair it with `--mock-5h`, `--mock-weekly`, or `--mock-rate-error` to simulate values without invoking any vendor CLI.
- `config.yaml` accepts the JSON subset of YAML. Copy it and pass `--config FILE` for local settings. `threshold_percent` defaults to 2, `poll_seconds` to 120, and `probe_timeout_seconds` to 12. Set `desktop_notifications` to `true` to add a desktop alert to the terminal bell. Set `claude_statusline` to `false` to keep your existing Claude status line during managed runs.

To check a trusted Claude status-line payload directly, pipe it into `check claude --claude-status-stdin`. The standalone `claude-statusline` command renders validated metrics and can be used as a custom Claude status line.

## Handoff notes

Keep a private JSON file outside the project. Use a string or list of strings for each field, and update the notes as the work progresses.

```json
{
  "task": "Fix the parser regression",
  "objective": "Preserve empty quoted fields",
  "completed": ["Reproduced the regression"],
  "remaining": ["Fix parsing and verify other delimiters"],
  "tests": ["Parser regression test fails"],
  "issues": [],
  "next": ["Run the parser test suite"]
}
```

Review notes for credentials and sensitive details before approving a switch. The monitor includes changed filenames and Git status, plus your reviewed notes, in a private Markdown file under `/tmp/agent-limit-monitor-<uid>/`. The package contains no project file contents or patches. Keep notes available until the destination starts. Remove the package once you no longer need it. Temporary system storage may be cleared by the OS. The target receives the package path as its initial prompt and uses its usual filesystem permissions to read the file.

A lock coordinates monitor-managed sessions for the same Git worktree, including invocations from subdirectories. For projects without Git it coordinates the exact project directory. The lock covers the current and replacement processes. Close any agent or detached editing job before approving the switch. The wrapper leaves the Git index, refs, and worktree in place. The package records changes to submodule pointers in the parent worktree. Run the monitor inside a submodule to capture its own worktree changes.

## Quota coverage

| CLI | Supported source | Limits and authentication |
| --- | --- | --- |
| Codex | [App Server account protocol](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt) | Reads the default ChatGPT Codex bucket in the configured OpenAI provider. Identifies the 5-hour and weekly windows by provider-reported duration. API keys, unrelated model buckets and custom providers can yield `Unknown`. |
| AGY | [Headless CLI](https://antigravity.google/docs/cli/headless/), [usage command](https://antigravity.google/docs/cli/commands/usage/) | Reads the built-in usage report. Shows the lowest verified remaining quota in each supported window across enabled model groups. Older or unrecognized report formats yield `Unknown`. |
| OpenCode v2 | [OpenCode API](https://opencode.ai/docs/api#usage), [model selection](https://opencode.ai/docs/models/) | Uses the effective project default model and the provider connection reported by the CLI. The experimental workspace route provides the 5-hour, weekly, and monthly allowances for an associated OpenCode Go workspace. Usage for other providers and resumed sessions stays `Unknown`. OpenAI sign-in can share the Codex subscription quota. |
| Claude Code | [Status line](https://code.claude.com/docs/en/statusline#rate-limit-usage), [authentication CLI](https://code.claude.com/docs/en/cli-reference) | Reads 5-hour and 7-day percentages from the session status line as provided by eligible plans. Data can be absent before the first API response, on unsupported plans, or when a managed status-line policy applies. Target preflight uses `claude auth status`. |

Context window usage is a separate metric. Missing, malformed, expired, and unsupported quota data display as `Unknown`. Built-in quota reads use vendor CLIs and their own authentication. They send no inference prompts. The monitor recognizes supported structured rate-limit errors and common terminal error messages. Terminal text can vary by CLI version and locale. A local authentication status check does not guarantee that the next API request succeeds.

## Test

```bash
python3 -m unittest discover -s tests -v
ALM_LIVE_AGENTS="codex agy opencode claude" ALM_LIVE_PROJECT="$PWD" \
  python3 -m unittest discover -s tests -p test_real_cli.py -v
```

The default suite exercises mock quota, structured errors, the real terminal boundary, Git preservation, and the documented flags of installed binaries. For the second command, list only the agents with eligible accounts and a configured project. Live quota assertions require nonempty provider data for Codex, AGY and OpenCode. The Claude live check exercises authentication; live Claude quota requires a running session and an API response.
