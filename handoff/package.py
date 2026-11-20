"""Build minimal handoff documents without copying project contents or secrets."""

import json
import os
from pathlib import Path
import re
import tempfile

from ._git import read_git
from .lock import _project_root, _state_directory


class HandoffError(ValueError):
    """Raised when reviewed handoff notes are missing or malformed."""

_FIELDS = ("task", "objective", "completed", "remaining", "tests", "issues", "next")
_SECRET = re.compile(
    r"(\b(?:api[ _-]?key|(?:access[ _-]?|refresh[ _-]?)?token|pass(?:word|phrase)|"
    r"(?:client[ _-]?)?secret|authorization)[\"']?\s*[:=]\s*(?:(?:Bearer|Basic)\s+)?)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)"
    r"|(\bBearer\s+)[A-Za-z0-9._~+/=-]+"
    r"|([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@]+@"
    r"|(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})(?![A-Za-z0-9_-])"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----(?s:.*?)(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.IGNORECASE,
)
_SENSITIVE_PATH = re.compile(
    r"(?:^|[/\\])(?:\.env(?:\.[^/\\]+)?|credentials?\.json|auth\.json|"
    r"id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?|[^/\\]*\.(?:pem|key|p12|pfx)|"
    r"\.git-credentials|\.netrc|\.npmrc|\.pypirc|\.ssh|\.aws|\.kube)(?=$|[/\\])",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    return _SECRET.sub(lambda match: f"{next((part for part in match.groups() if part), '')}[REDACTED]", value)


def _literal(value: str) -> str:
    value = json.dumps(_clean(value), ensure_ascii=False)[1:-1]
    delimiter = "`" * (1 + max(map(len, re.findall(r"`+", value)), default=0))
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"


def _display_path(path: str) -> str:
    if _SENSITIVE_PATH.search(path):
        return "[sensitive path redacted]"
    return _literal(path)


def _read_notes(path: Path) -> dict[str, str | list[str]]:
    try:
        notes = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise HandoffError("Notes must be readable JSON") from error
    if not isinstance(notes, dict) or set(notes) != set(_FIELDS):
        raise HandoffError(f"Notes must contain exactly these fields: {', '.join(_FIELDS)}")
    for field, value in notes.items():
        if isinstance(value, str):
            notes[field] = _clean(value.strip()) or "None provided"
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            notes[field] = [_clean(item.strip()) for item in value if item.strip()]
        else:
            raise HandoffError(f"Field {field!r} must be a string or list of strings")
    return notes


def _git_status(project: Path) -> list[str]:
    # Git can run clean filters when checking whether tracked content changed.
    filters = read_git(project, "config", "-z", "--name-only", "--get-regexp", r"^filter\..*\.(clean|process|required)$") or b""
    names = {os.fsdecode(key).rsplit(".", 1)[0] for key in filters.split(b"\0") if key}
    overrides = tuple(f"{name}.{setting}" for name in sorted(names) for setting in ("clean=", "process=", "required=false"))
    result = read_git(
        project, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=dirty",
        config=overrides,
    )
    if result is None:
        return ["Git status unavailable"]
    entries = iter(result.split(b"\0"))
    status = []
    for entry in entries:
        if len(entry) < 4:
            continue
        code, raw_path = entry[:2].decode("ascii", "replace"), os.fsdecode(entry[3:])
        status.append(f"{code} {_display_path(raw_path)}")
        if "R" in code or "C" in code:
            # Porcelain -z follows a rename/copy entry with the original path.
            next(entries, None)
    return status or ["Clean"]


def make_package(project: Path, notes_path: Path) -> Path:
    """Create a mode-0600 Markdown handoff file outside the project tree."""
    project = _project_root(project)
    notes = _read_notes(notes_path)
    state = _state_directory(project)

    branch = os.fsdecode((read_git(project, "branch", "--show-current") or b"").removesuffix(b"\n")) or "Unknown"
    head = os.fsdecode((read_git(project, "rev-parse", "--short", "HEAD") or b"").removesuffix(b"\n")) or "Unknown"
    sections = [
        "# Agent handoff",
        f"\nProject: {_display_path(str(project))}",
        f"\nGit branch: {_literal(branch)}",
        f"\nGit HEAD: {_literal(head)}",
        f"\nGit status\n\n" + "\n".join(f"- {item}" for item in _git_status(project)),
    ]
    labels = {"task": "Original task", "objective": "Objective", "completed": "Completed work", "remaining": "Remaining tasks", "tests": "Test results", "issues": "Known issues", "next": "Next recommended actions"}
    for field in _FIELDS:
        value = notes[field]
        body = "\n".join(f"- {item}" for item in value) if isinstance(value, list) else value
        sections.append(f"\n## {labels[field]}\n\n{body}")
    content = "\n".join(sections) + "\n"

    output = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", errors="backslashreplace", dir=state, prefix=".handoff-", delete=False,
    )
    temporary = Path(output.name)
    path = temporary.with_name(f"{temporary.name[1:]}.md")
    try:
        with output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
        os.link(temporary, path)
        return path
    finally:
        temporary.unlink()
