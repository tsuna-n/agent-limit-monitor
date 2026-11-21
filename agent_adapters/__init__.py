"""Adapters for supported local coding agents."""

from importlib import import_module

AGENTS = ("codex", "agy", "opencode", "claude")
BINARIES = {"codex": "codex", "agy": "agy", "opencode": "opencode", "claude": "claude"}
RESUME = {"codex": ["resume", "--last"], "agy": ["--continue"],
          "opencode": ["--continue"], "claude": ["--continue"]}


def adapter(agent):
    return import_module(f".{agent}", __name__)


def command(agent, *, prompt=None, resume=False, model=None, claude_settings=None):
    args = [BINARIES[agent]]
    if agent == "opencode" and model:
        args.extend(["--standalone", "mini"])
    if resume:
        args.extend(RESUME[agent])
    if model:
        args.extend(["--model", model])
    if claude_settings:
        args.extend(["--settings", claude_settings])
    if prompt:
        if agent == "agy":
            args.extend(["--prompt-interactive", prompt])
        elif agent == "opencode":
            args.extend(["--prompt", prompt])
        else:
            args.append(prompt)
    return args
