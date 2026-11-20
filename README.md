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

Run `bash install-skill.sh` to install the skill for Codex and OpenCode in `~/.agents/skills/agent-limit-monitor/` and for AGY in `~/.gemini/antigravity-cli/skills/agent-limit-monitor/`. Run `bash install-skill.sh --project-opencode` to install it for OpenCode in this checkout only. The installer leaves existing copies in place. Start a new agent session to pick up the skill. [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Antigravity skills](https://antigravity.google/docs/skills), [OpenCode skills](https://opencode.ai/docs/skills/)

## Use

Run from the project directory. Every command also accepts `--project DIR`.

```bash
agent-limit-monitor check codex
agent-limit-monitor watch codex --threshold 3
agent-limit-monitor run codex --notes /path/to/reviewed-notes.json
agent-limit-monitor run claude --resume --notes /path/to/reviewed-notes.json
agent-limit-monitor run codex --dry-run --mock-5h 2 --mock-weekly 43
```

- `check` reads once. `watch` polls and alerts on entry into a low-quota state.
- `run` starts a managed interactive agent in the same directory. It polls while the agent runs and watches for rate-limit errors. If an alert occurs during the session, save work and exit normally to open the menu. A handoff after an already-closed session is also available with `handoff AGENT --notes FILE`. A previous CLI error can be supplied with `--error-file FILE` to `check` or `handoff`. Use a CLI-generated error stream with `--error-file`, such as Codex's `exec --json` output.
- The menu offers AGY CLI, OpenCode, Claude Code, Continue current agent, and Cancel handoff. The selected CLI must pass its availability and authentication checks. Type `yes` to attest that the prior agent and its editing jobs have stopped and to start the selected agent with the handoff package. The monitor offers a single successful switch per invocation.
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

Review notes for credentials and sensitive details before approving a switch. The monitor includes changed filenames and Git status, plus your reviewed notes, in a private Markdown file under `/tmp/agent-limit-monitor-<uid>/`. The package contains no project file contents or patches. Remove the package once you no longer need it. Temporary system storage may be cleared by the OS. The target receives the package path as its initial prompt and uses its usual filesystem permissions to read the file.

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
