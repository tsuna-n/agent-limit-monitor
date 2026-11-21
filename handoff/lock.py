"""Serialize monitor and agent activity per canonical project directory."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat

from ._git import read_git


class ProjectLockedError(RuntimeError):
    """Raised when another monitor already owns the project lease."""


def _project_root(project: Path) -> Path:
    project = Path(project).resolve(strict=True)
    root = read_git(project, "rev-parse", "--show-toplevel")
    return Path(os.fsdecode(root.removesuffix(b"\n"))).resolve() if root else project


@contextmanager
def project_lock(project: Path, *, wait=False):
    """Hold an exclusive lease until the context exits."""
    root = _project_root(project)
    state = _state_directory(root)
    key = hashlib.sha256(os.fsencode(root)).hexdigest()
    path = state / f"{key}.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError(f"Unsafe project lock: {path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProjectLockedError(f"A monitor or agent already owns {root}") from error
        yield root, fd
    finally:
        os.close(fd)


def _state_directory(project: Path) -> Path:
    uid = os.getuid()
    state = Path("/tmp") / f"agent-limit-monitor-{uid}"
    if state.is_relative_to(project):
        raise PermissionError("Monitor state directory is inside the project")
    try:
        state.mkdir(mode=0o700)
        state.chmod(0o700)
    except FileExistsError:
        pass
    info = state.lstat()
    if info.st_uid != uid or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError(f"Unsafe state directory: {state}")
    return state
