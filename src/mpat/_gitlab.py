from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mpat._fingerprint import BODY, CONTENT, MOVED, NO_SOURCE, OK, SIGNATURE, VALUE
from mpat._lock import MISSING, REVIEW, STALE, UNLOCKED, CheckResult

SEVERITY_BLOCKER = "blocker"
SEVERITY_MAJOR = "major"
SEVERITY_MINOR = "minor"
SEVERITY_INFO = "info"

_GIT = ".git"
_CHECK_NAME_PREFIX = "mpat"
_FIRST_LINE = 1
_FINGERPRINT_SEPARATOR = "|"

_SEVERITIES = {
    MISSING: SEVERITY_BLOCKER,
    MOVED: SEVERITY_BLOCKER,
    SIGNATURE: SEVERITY_MAJOR,
    BODY: SEVERITY_MAJOR,
    CONTENT: SEVERITY_MAJOR,
    VALUE: SEVERITY_MAJOR,
    UNLOCKED: SEVERITY_MAJOR,
    NO_SOURCE: SEVERITY_MINOR,
    REVIEW: SEVERITY_MINOR,
    STALE: SEVERITY_INFO,
}
_DEFAULT_SEVERITY = SEVERITY_MAJOR

_DESCRIPTIONS = {
    MISSING: "no longer exists upstream",
    MOVED: "moved upstream",
    SIGNATURE: "the upstream signature changed",
    BODY: "the upstream body changed",
    CONTENT: "the upstream file changed",
    VALUE: "the upstream value changed",
    NO_SOURCE: "upstream source is unavailable, only the signature is locked",
    UNLOCKED: f"not in the lock; run '{_CHECK_NAME_PREFIX} lock'",
    STALE: f"in the lock but no longer declared; run '{_CHECK_NAME_PREFIX} lock' to prune",
    REVIEW: "past its review_by date",
}
_UNKNOWN_DESCRIPTION = "drifted"


def repo_root(root: Path) -> Path | None:
    """The working tree `root` sits in, found by the `.git` a checkout always has.

    A worktree and a submodule carry `.git` as a file rather than a directory, so
    existence, not `is_dir`, is the test.
    """
    for candidate in (root, *root.parents):
        if (candidate / _GIT).exists():
            return candidate
    return None


def _report_path(declared_in: str, *, root: Path, base: Path) -> str:
    absolute = Path(declared_in)
    if not absolute.is_absolute():
        absolute = root / absolute
    try:
        return absolute.relative_to(base).as_posix()
    except ValueError:
        return Path(declared_in).as_posix()


def _line_in(text: str, target: str) -> int:
    for number, line in enumerate(text.splitlines(), start=_FIRST_LINE):
        if f'"{target}"' in line or f"'{target}'" in line:
            return number
    return _FIRST_LINE


def _declaration_line(path: Path, target: str, cache: dict[Path, str]) -> int:
    if path not in cache:
        try:
            cache[path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            cache[path] = ""
    return _line_in(cache[path], target)


def _description(result: CheckResult) -> str:
    what = _DESCRIPTIONS.get(result.status, _UNKNOWN_DESCRIPTION)
    note = f" ({result.note})" if result.note else ""
    return f"{result.role} {result.target}: {what}{note}"


def _fingerprint(path: str, result: CheckResult) -> str:
    identity = _FINGERPRINT_SEPARATOR.join((path, result.role, result.target))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def code_quality_report(
    results: Sequence[CheckResult], *, root: Path, base: Path | None = None
) -> list[dict[str, Any]]:
    """Render the non-OK results as GitLab Code Quality issues.

    `location.path` is resolved against the repository root, not against `root`,
    because GitLab reads it relative to the checkout: a `mpat check` run in
    `services/api` must still report `services/api/src/patches.py`.
    """
    base = base or repo_root(root) or root
    cache: dict[Path, str] = {}
    report: list[dict[str, Any]] = []
    for result in results:
        if result.status == OK:
            continue
        path = _report_path(result.declared_in, root=root, base=base)
        report.append(
            {
                "description": _description(result),
                "check_name": f"{_CHECK_NAME_PREFIX}/{result.status}",
                "fingerprint": _fingerprint(path, result),
                "severity": _SEVERITIES.get(result.status, _DEFAULT_SEVERITY),
                "location": {
                    "path": path,
                    "lines": {"begin": _declaration_line(base / path, result.target, cache)},
                },
            }
        )
    return report
