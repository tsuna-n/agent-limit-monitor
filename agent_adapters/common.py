"""Subprocess and readiness contracts shared by CLI adapters."""

from dataclasses import dataclass
import subprocess


@dataclass(frozen=True)
class Verification:
    ready: bool
    detail: str
    model: str | None = None
    shared_codex_possible: bool = False


def capture(argv, cwd, timeout):
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=timeout, check=False)
