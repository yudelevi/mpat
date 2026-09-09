from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from mpat._config import Config, load_config
from mpat._errors import LockError, MpatError
from mpat._fingerprint import (
    BODY,
    MOVED,
    NO_SOURCE,
    OK,
    SIGNATURE,
    VALUE,
    fingerprint,
)
from mpat._lock import (
    LOCK_FILENAME,
    MISSING,
    REVIEW,
    STALE,
    UNLOCKED,
    CheckResult,
    Lock,
    build_lock,
    check,
    lock_path,
    read_lock,
    write_lock,
)
from mpat._registry import collect_declarations
from mpat._targets import resolve

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
_DRIFT_STATUSES = (MISSING, MOVED, SIGNATURE, BODY, VALUE, NO_SOURCE)
_FOOTERS: tuple[tuple[tuple[str, ...], str], ...] = (
    ((UNLOCKED,), "unlocked: run 'mpat lock'"),
    (
        _DRIFT_STATUSES,
        "drifted: read the upstream change, then fix or delete the patch and run 'mpat lock'",
    ),
    ((STALE,), "stale: run 'mpat lock' to prune"),
    ((REVIEW,), "due for review"),
)


def _require_config() -> Config:
    config = load_config()
    if config is None:
        raise MpatError("no pyproject.toml found in the current directory or its parents")
    if not config.modules:
        raise MpatError("[tool.mpat] modules is empty in pyproject.toml; nothing to collect")
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
    declarations = collect_declarations(config.modules)
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


def _print_footer(results: Sequence[CheckResult]) -> None:
    lines = [
        f"{count} {advice}"
        for statuses, advice in _FOOTERS
        if (count := sum(1 for r in results if r.status in statuses))
    ]
    if lines:
        print()
        print("\n".join(lines))


def _print_table(results: Sequence[CheckResult]) -> None:
    status_width = max([_STATUS_WIDTH, *(len(_status_cell(r)) for r in results)])
    target_width = max((len(r.target) for r in results), default=0)
    source_width = max((len(r.declared_in) for r in results), default=0)
    ordered = sorted(results, key=lambda r: (r.dist or "", r.target))
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
    _print_footer(ordered)


def cmd_check(args: argparse.Namespace) -> int:
    config = _require_config()
    declarations = collect_declarations(config.modules)
    results = check(
        declarations,
        _existing_lock(config.root),
        root=config.root,
        today=date.today(),
    )
    if args.json:
        print(json.dumps([dataclasses.asdict(r) for r in results], indent=_JSON_INDENT))
    elif not results:
        print(_NO_DECLARATIONS)
    else:
        _print_table(results)
    return EXIT_OK if all(r.status == OK for r in results) else EXIT_DRIFT


def cmd_show(args: argparse.Namespace) -> int:
    resolved = resolve(args.target)
    for name, value in dataclasses.asdict(fingerprint(resolved)).items():
        print(f"{name}: {value}")
    try:
        source = inspect.getsource(resolved.obj)
    except (OSError, TypeError):
        source = "(no source available)"
    print()
    print(source)
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpat")
    sub = parser.add_subparsers(dest="command", required=True)
    lock_parser = sub.add_parser("lock", help="fingerprint declared targets and write mpat.lock")
    lock_parser.set_defaults(fn=cmd_lock)
    check_parser = sub.add_parser("check", help="compare mpat.lock against installed upstream")
    check_parser.add_argument("--json", action="store_true")
    check_parser.set_defaults(fn=cmd_check)
    show_parser = sub.add_parser("show", help="print fingerprint and source of a target")
    show_parser.add_argument("target")
    show_parser.set_defaults(fn=cmd_show)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.fn(args)
    except MpatError as exc:
        print(f"mpat: {exc}", file=sys.stderr)
        return EXIT_USAGE
