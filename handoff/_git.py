"""Read local Git metadata without updating the index or running filters."""

import os
from pathlib import Path
import subprocess


def read_git(project: Path, *args: str, config: tuple[str, ...] = ()) -> bytes | None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_NO_LAZY_FETCH"] = "1"
    command = ["git", "--no-optional-locks", "--no-pager", "-c", "core.fsmonitor=false"]
    for setting in config:
        command.extend(("-c", setting))
    try:
        return subprocess.run(
            [*command, "-C", project, *args], env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=3, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
