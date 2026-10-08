from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from mpat._config import CONFIG_FILENAME, PYPROJECT, Config, load_config
from mpat._errors import LockError, MpatError, TargetNotFound
from mpat._fingerprint import (
    BODY,
    CONTENT,
    DOCSTRING,
    MOVED,
    NO_SOURCE,
    OK,
    SIGNATURE,
    VALUE,
    Fingerprint,
    changed_fields,
    compare,
    fingerprint,
)
from mpat._gitlab import code_quality_report
from mpat._lock import (
    LOCK_FILENAME,
    MISSING,
    REVIEW,
    STALE,
    UNLOCKED,
    CheckResult,
    Lock,
    Usage,
    build_lock,
    check,
    lock_path,
    read_lock,
    write_lock,
)
from mpat._registry import collect_declarations
from mpat._render import DIFF_STATUSES, diff_block
from mpat._targets import ResolvedFile, resolve
from mpat._upstream import UpstreamUnavailable, installed_source, locked_source

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_USAGE = 2

_ADDED = "+"
_REMOVED = "-"
_CHANGED = "~"

_STATUS_WIDTH = 10
_ROLE_WIDTH = 10
_JSON_INDENT = 2

_NO_DECLARATIONS = "no declarations found"
_NO_SOURCE_MARKER = " [signature-only]"
_NO_SOURCE_NOTICE = "no source available, only the signature is locked"
_NO_DIST = "(no distribution)"
_MISSING_CELL = "(missing)"
_OK_HIDDEN_HINT = " (--all lists them)"
_CHANGE_INDENT = "    "
_NOTHING_TO_DIFF = "no drifted targets to diff"
_DRIFT_STATUSES = (MISSING, MOVED, SIGNATURE, BODY, CONTENT, VALUE, NO_SOURCE)
_FOOTERS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((UNLOCKED,), "unlocked: run 'mpat lock'"),
    (
        _DRIFT_STATUSES,
        "drifted: read the upstream change, then fix or delete the patch and run 'mpat lock'",
    ),
    ((DOCSTRING,), "docstring-only: skim the upstream docs change, then run 'mpat lock'"),
    ((STALE,), "stale: run 'mpat lock' to prune"),
    ((REVIEW,), "due for review"),
)


def _require_config() -> Config:
    config = load_config()
    if config is None:
        raise MpatError(
            f"no {PYPROJECT} or {CONFIG_FILENAME} found in the current directory or its parents"
        )
    if not config.declares_anything:
        raise MpatError(f"{config.where} lists no modules and no watch entries; nothing to collect")
    return config


def _existing_lock(root: Path) -> Lock:
    path = lock_path(root)
    return read_lock(path) if path.is_file() else Lock(entries={})


def _print_lock_diff(old: Lock, new: Lock) -> None:
    for target in sorted(set(old.entries) | set(new.entries)):
        before, after = old.entries.get(target), new.entries.get(target)
        if before is None:
            print(f"{_ADDED} {target}")
        elif after is None:
            print(f"{_REMOVED} {target}")
        elif before.fingerprint != after.fingerprint:
            print(f"{_CHANGED} {target}")


def _lock_to_replace(root: Path) -> Lock:
    try:
        return _existing_lock(root)
    except LockError as exc:
        print(f"mpat: ignoring unreadable {LOCK_FILENAME}: {exc}", file=sys.stderr)
        return Lock(entries={})


def cmd_lock(_: argparse.Namespace) -> int:
    config = _require_config()
    declarations = collect_declarations(config, apply=False)
    new = build_lock(declarations, root=config.root)
    _print_lock_diff(_lock_to_replace(config.root), new)
    write_lock(lock_path(config.root), new)
    print(f"wrote {LOCK_FILENAME} with {len(new.entries)} entries")
    for target in sorted(new.entries):
        if new.entries[target].fingerprint.no_source:
            print(f"mpat: {target}: {_NO_SOURCE_NOTICE}", file=sys.stderr)
    return EXIT_OK


def _status_cell(result: CheckResult) -> str:
    return result.status + (_NO_SOURCE_MARKER if result.no_source else "")


def _print_footer(results: Sequence[CheckResult], *, ok_hidden: bool, after_rows: bool) -> None:
    lines = [
        f"{count} {advice}"
        for statuses, advice in _FOOTERS
        if (count := sum(1 for r in results if r.status in statuses))
    ]
    if ok_count := sum(1 for r in results if r.status == OK):
        lines.append(f"{ok_count} {OK}" + (_OK_HIDDEN_HINT if ok_hidden else ""))
    if lines:
        if after_rows:
            print()
        print("\n".join(lines))


def _usage_cell(usage: Usage) -> str:
    where = (
        usage.declared_in
        if usage.declared_line is None
        else f"{usage.declared_in}:{usage.declared_line}"
    )
    return f"{where} ({usage.role} {usage.target})"


def _source_block(result: CheckResult, lock: Lock) -> list[str]:
    entry = lock.entries.get(result.target)
    if result.status not in DIFF_STATUSES or entry is None:
        return []
    try:
        old, unavailable = locked_source(entry), None
    except UpstreamUnavailable as exc:
        old, unavailable = None, str(exc)
    return diff_block(result, old=old, new=installed_source(result.target), unavailable=unavailable)


def _print_table(
    results: Sequence[CheckResult], *, show_ok: bool, diff_from: Lock | None = None
) -> None:
    shown = results if show_ok else [r for r in results if r.status != OK]
    status_width = max([_STATUS_WIDTH, *(len(_status_cell(r)) for r in shown)])
    target_width = max((len(r.target) for r in shown), default=0)
    source_width = max((len(r.declared_in) for r in shown), default=0)
    ordered = sorted(shown, key=lambda r: (r.dist or "", r.target))
    previous: str | None = None
    for r in ordered:
        group = r.dist or _NO_DIST
        if group != previous:
            if previous is not None:
                print()
            print(f"# {group}")
            previous = group
        versions = ""
        if r.locked_version and r.current_version and r.locked_version != r.current_version:
            versions = f" {r.locked_version} -> {r.current_version}"
        note = f"  {r.note}" if r.note else ""
        row = (
            f"{_status_cell(r):<{status_width}} {r.role:<{_ROLE_WIDTH}} "
            f"{r.target:<{target_width}} {r.declared_in:<{source_width}}{versions}{note}"
        )
        print(row.rstrip())
        for change in r.changes:
            print(f"{_CHANGE_INDENT}{change}".rstrip())
        block = _source_block(r, diff_from) if diff_from is not None else []
        if not block:
            for usage in r.used_by:
                print(f"{_CHANGE_INDENT}used by: {_usage_cell(usage)}")
        for line in block:
            print(f"{_CHANGE_INDENT}{line}".rstrip())
    _print_footer(results, ok_hidden=not show_ok, after_rows=bool(ordered))


def _check(config: Config, lock: Lock) -> list[CheckResult]:
    declarations = collect_declarations(config, apply=False)
    return check(declarations, lock, root=config.root, today=date.today())


def cmd_check(args: argparse.Namespace) -> int:
    config = _require_config()
    lock = _existing_lock(config.root)
    results = _check(config, lock)
    if args.json:
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=_JSON_INDENT))
    elif args.gitlab:
        print(
            json.dumps(
                code_quality_report(results, root=config.root, base=args.repo_root),
                indent=_JSON_INDENT,
            )
        )
    elif not results:
        print(_NO_DECLARATIONS)
    else:
        _print_table(results, show_ok=args.all, diff_from=lock if args.diff else None)
    return EXIT_OK if all(r.status == OK for r in results) else EXIT_DRIFT


def cmd_show(args: argparse.Namespace) -> int:
    resolved = resolve(args.target)
    for name, value in dataclasses.asdict(fingerprint(resolved)).items():
        print(f"{name}: {value}")
    if isinstance(resolved, ResolvedFile):
        source = resolved.read_bytes().decode("utf-8", errors="replace")
    else:
        try:
            source = inspect.getsource(resolved.obj)
        except (OSError, TypeError):
            source = "(no source available)"
    print()
    print(source)
    return EXIT_OK


def _diff_lines(locked: Fingerprint, current: Fingerprint | None) -> list[str]:
    if current is None:
        return [
            f"{name}: {before} -> {_MISSING_CELL}"
            for name, before in dataclasses.asdict(locked).items()
        ]
    return [
        f"{name}: {before} -> {after}"
        for name, before, after in changed_fields(locked=locked, current=current)
    ]


def _diff_all(config: Config, lock: Lock) -> int:
    results = _check(config, lock)
    blocks = [block for r in results if (block := _source_block(r, lock))]
    print("\n\n".join("\n".join(block) for block in blocks) if blocks else _NOTHING_TO_DIFF)
    return EXIT_OK if all(r.status == OK for r in results) else EXIT_DRIFT


def cmd_diff(args: argparse.Namespace) -> int:
    config = _require_config()
    lock = _existing_lock(config.root)
    if args.target is None:
        return _diff_all(config, lock)
    entry = lock.entries.get(args.target)
    if entry is None:
        raise MpatError(f"{args.target}: not in {LOCK_FILENAME}")
    try:
        current = fingerprint(resolve(args.target), track_value=entry.track_value)
    except TargetNotFound:
        current = None
    status = MISSING if current is None else compare(locked=entry.fingerprint, current=current)
    print(f"status: {status}")
    for line in _diff_lines(entry.fingerprint, current):
        print(line)
    result = next((r for r in _check(config, lock) if r.target == args.target), None)
    block = _source_block(result, lock) if result is not None else []
    if block:
        print()
        print("\n".join(block))
    return EXIT_OK if status == OK else EXIT_DRIFT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpat")
    sub = parser.add_subparsers(dest="command", required=True)
    lock_parser = sub.add_parser("lock", help="fingerprint declared targets and write mpat.lock")
    lock_parser.set_defaults(fn=cmd_lock)
    check_parser = sub.add_parser("check", help="compare mpat.lock against installed upstream")
    output = check_parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument(
        "--gitlab",
        action="store_true",
        help="print a GitLab Code Quality report of every non-ok target",
    )
    check_parser.add_argument(
        "--all",
        action="store_true",
        help="list ok targets in the table too (default: only their count)",
    )
    check_parser.add_argument(
        "--diff",
        action="store_true",
        help="show the upstream source change under each drifted row (fetches the locked release)",
    )
    check_parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="base for report paths (default: the nearest .git above the mpat root)",
    )
    check_parser.set_defaults(fn=cmd_check)
    show_parser = sub.add_parser("show", help="print fingerprint and source of a target")
    show_parser.add_argument("target")
    show_parser.set_defaults(fn=cmd_show)
    diff_parser = sub.add_parser(
        "diff", help="show how a target, or every drifted target, changed upstream"
    )
    diff_parser.add_argument("target", nargs="?")
    diff_parser.set_defaults(fn=cmd_diff)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.fn(args)
    except MpatError as exc:
        print(f"mpat: {exc}", file=sys.stderr)
        return EXIT_USAGE
