"""ty-style blocks showing how a drifted target's upstream source changed."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import NamedTuple

from mpat._fingerprint import BODY, CONTENT, DOCSTRING, MOVED, SIGNATURE
from mpat._gitlab import describe
from mpat._lock import MISSING, CheckResult, Usage
from mpat._upstream import Source

DIFF_STATUSES = (BODY, CONTENT, DOCSTRING, SIGNATURE, MOVED, MISSING)
CONTEXT_LINES = 3

_KEEP = " "
_REMOVED = "-"
_ADDED = "+"
_HUNK_GAP = "..."
_REBUILT = "warning: the downloaded source does not match mpat.lock"


def _where(path: str, line: int | None) -> str:
    return path if line is None else f"{path}:{line}"


def _usage(usage: Usage) -> str:
    return f"{_where(usage.declared_in, usage.declared_line)} ({usage.role} {usage.target})"


def _header(result: CheckResult) -> str:
    versions = ""
    if (
        result.locked_version
        and result.current_version
        and result.locked_version != result.current_version
    ):
        versions = f" ({result.locked_version} -> {result.current_version})"
    return f"{result.status}[{result.role}] {result.target}: {describe(result.status)}{versions}"


class _Row(NamedTuple):
    old: int | None
    new: int | None
    marker: str
    text: str


def _hunks(old: Source, new: Source | None) -> list[list[_Row]]:
    """A gone target is one hunk of removed lines."""
    if new is None:
        return [[_Row(old.start + i, None, _REMOVED, text) for i, text in enumerate(old.lines)]]
    hunks: list[list[_Row]] = []
    matcher = SequenceMatcher(a=old.lines, b=new.lines, autojunk=False)
    for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
        hunk: list[_Row] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                hunk.extend(
                    _Row(old.start + i, new.start + j, _KEEP, old.lines[i])
                    for i, j in zip(range(i1, i2), range(j1, j2), strict=True)
                )
                continue
            hunk.extend(_Row(old.start + i, None, _REMOVED, old.lines[i]) for i in range(i1, i2))
            hunk.extend(_Row(None, new.start + j, _ADDED, new.lines[j]) for j in range(j1, j2))
        hunks.append(hunk)
    return hunks


def _location(rows: list[_Row], old: Source, new: Source | None) -> str:
    """Point at the first added line in the installed file, else the first removed one."""
    added = next((r.new for r in rows if r.marker == _ADDED), None)
    if new is not None and added is not None:
        return _where(new.path, added)
    return _where(old.path, next(r.old for r in rows if r.marker == _REMOVED))


def diff_block(
    result: CheckResult, *, old: Source | None, new: Source | None, unavailable: str | None = None
) -> list[str]:
    lines = [_header(result)]
    hunks = _hunks(old, new) if old is not None and unavailable is None else []
    rows = [row for hunk in hunks for row in hunk]
    width = max(
        (len(str(n)) for row in rows for n in (row.old, row.new) if n is not None), default=0
    )
    pad = 2 * width + 1
    arrow = f"{' ' * (pad - 1)}-->"
    gutter = f"{' ' * pad} |"
    if unavailable is not None:
        lines.append(f"info: upstream diff unavailable: {unavailable}")
    if old is not None and rows:
        lines.extend((f"{arrow} {_location(rows, old, new)}", gutter))
        for index, hunk in enumerate(hunks):
            if index:
                lines.append(f"{' ' * pad} {_HUNK_GAP}")
            for row in hunk:
                o = "" if row.old is None else str(row.old)
                n = "" if row.new is None else str(row.new)
                lines.append(f"{o:>{width}} {n:>{width}} | {row.marker} {row.text}".rstrip())
        lines.append(gutter)
        if not old.matches_lock:
            lines.append(_REBUILT)
    lines.append("info: declared here")
    lines.append(f"{arrow} {_where(result.declared_in, result.declared_line)}")
    lines.extend(f"info: also used by {_usage(u)}" for u in result.used_by)
    return lines
