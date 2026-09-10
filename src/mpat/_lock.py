from __future__ import annotations

import dataclasses
import functools
import os
import tempfile
import tomllib
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import tomli_w

from mpat._config import find_project_root
from mpat._errors import LockError, TargetNotFound
from mpat._fingerprint import OK, Fingerprint, compare, fingerprint
from mpat._registry import ROLE_DEPENDS, Declaration
from mpat._targets import resolve

LOCK_VERSION = 1
SUPPORTED_VERSIONS: tuple[int, ...] = (1,)
LOCK_FILENAME = "mpat.lock"

UNLOCKED = "unlocked"
STALE = "stale"
REVIEW = "review"
MISSING = "missing"

_ENTRY_KEY = "entry"
_VERSION_KEY = "version"
_FINGERPRINT_FIELDS = tuple(f.name for f in dataclasses.fields(Fingerprint))
_REQUIRED_STR_FIELDS = ("role", "declared_in", "kind", "resolved")
_OPTIONAL_STR_FIELDS = (
    "signature",
    "source_hash",
    "value_repr",
    "source_file",
    "dist",
    "dist_version",
    "parent",
)
_TRACK_VALUE_KEY = "track_value"
_BOOL_FIELDS = ("is_async", "no_source", _TRACK_VALUE_KEY)

LOCK_FILE_MODE = 0o644


@dataclass(frozen=True)
class LockEntry:
    target: str
    role: str
    declared_in: str
    fingerprint: Fingerprint
    parent: str | None = None
    track_value: bool = True


@dataclass
class Lock:
    entries: dict[str, LockEntry]
    version: int = LOCK_VERSION


@dataclass(frozen=True)
class Wanted:
    target: str
    role: str
    parent: str | None
    decl: Declaration
    track_value: bool


@dataclass(frozen=True)
class CheckResult:
    target: str
    role: str
    status: str
    note: str = ""
    declared_in: str = ""
    locked_version: str | None = None
    current_version: str | None = None
    no_source: bool = False
    dist: str | None = None


def lock_path(root: Path) -> Path:
    return root / LOCK_FILENAME


def _entry_to_dict(entry: LockEntry) -> dict[str, Any]:
    data: dict[str, Any] = {"role": entry.role, "declared_in": entry.declared_in}
    if entry.parent is not None:
        data["parent"] = entry.parent
    if not entry.track_value:
        data[_TRACK_VALUE_KEY] = False
    for name in _FINGERPRINT_FIELDS:
        value = getattr(entry.fingerprint, name)
        if value is not None:
            data[name] = value
    return data


def _typed(target: str, data: dict[str, Any], name: str, expected: type) -> None:
    value = data[name]
    if type(value) is not expected:
        raise LockError(
            f"{LOCK_FILENAME}: entry {target!r}: {name} must be "
            f"{expected.__name__}, got {type(value).__name__}"
        )


def _validate_entry(target: str, data: Any) -> None:
    if not isinstance(data, dict):
        raise LockError(
            f"{LOCK_FILENAME}: entry {target!r} must be a table, got {type(data).__name__}"
        )
    for name in _REQUIRED_STR_FIELDS:
        if name not in data:
            raise LockError(f"{LOCK_FILENAME}: entry {target!r}: missing {name}")
        _typed(target, data, name, str)
    for name in _OPTIONAL_STR_FIELDS:
        if name in data:
            _typed(target, data, name, str)
    for name in _BOOL_FIELDS:
        if name in data:
            _typed(target, data, name, bool)


def _entry_from_dict(target: str, data: Any) -> LockEntry:
    _validate_entry(target, data)
    try:
        fp_kwargs = {name: data[name] for name in _FINGERPRINT_FIELDS if name in data}
        return LockEntry(
            target=target,
            role=data["role"],
            declared_in=data["declared_in"],
            parent=data.get("parent"),
            track_value=data.get(_TRACK_VALUE_KEY, True),
            fingerprint=Fingerprint(**fp_kwargs),
        )
    except (KeyError, TypeError) as exc:
        raise LockError(f"{LOCK_FILENAME}: malformed entry {target!r}: {exc}") from exc


def read_lock(path: Path) -> Lock:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise LockError(f"{path}: {exc}") from exc
    version = data.get(_VERSION_KEY)
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version not in SUPPORTED_VERSIONS
    ):
        raise LockError(
            f"{path}: lock version {version!r} not supported (supported: {SUPPORTED_VERSIONS})"
        )
    raw_entries = data.get(_ENTRY_KEY, {})
    if not isinstance(raw_entries, dict):
        raise LockError(f"{path}: 'entry' must be a table")
    return Lock(
        entries={t: _entry_from_dict(t, e) for t, e in raw_entries.items()},
        version=version,
    )


def write_lock(path: Path, lock: Lock) -> None:
    payload = {
        _VERSION_KEY: lock.version,
        _ENTRY_KEY: {t: _entry_to_dict(lock.entries[t]) for t in sorted(lock.entries)},
    }
    fd, tmp = tempfile.mkstemp(prefix=".mpat-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            tomli_w.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, LOCK_FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def expand(declarations: Sequence[Declaration]) -> list[Wanted]:
    primary = {decl.target for decl in declarations}
    wanted: dict[str, Wanted] = {}
    for decl in declarations:
        wanted.setdefault(
            decl.target,
            Wanted(
                target=decl.target,
                role=decl.role,
                parent=None,
                decl=decl,
                track_value=decl.track_value,
            ),
        )
        for dep in decl.depends_on:
            if dep in primary:
                continue
            wanted.setdefault(
                dep,
                Wanted(
                    target=dep, role=ROLE_DEPENDS, parent=decl.target, decl=decl, track_value=True
                ),
            )
    return list(wanted.values())


def _declared_in(decl: Declaration, root: Path) -> str:
    try:
        return Path(decl.declared_in).relative_to(root).as_posix()
    except ValueError:
        return decl.declared_in


def build_lock(declarations: Sequence[Declaration], *, root: Path) -> Lock:
    entries = {
        w.target: LockEntry(
            target=w.target,
            role=w.role,
            declared_in=_declared_in(w.decl, root),
            parent=w.parent,
            track_value=w.track_value,
            fingerprint=fingerprint(resolve(w.target), track_value=w.track_value),
        )
        for w in expand(declarations)
    }
    return Lock(entries=entries)


def _current(target: str, *, track_value: bool) -> Fingerprint | None:
    try:
        return fingerprint(resolve(target), track_value=track_value)
    except TargetNotFound:
        return None


def _status(w: Wanted, entry: LockEntry, current: Fingerprint | None, today: date) -> str:
    if current is None:
        return MISSING
    result = compare(locked=entry.fingerprint, current=current)
    if result == OK and w.decl.review_by is not None and today > w.decl.review_by:
        return REVIEW
    return result


def check(
    declarations: Sequence[Declaration], lock: Lock, *, root: Path, today: date
) -> list[CheckResult]:
    results: list[CheckResult] = []
    seen: set[str] = set()
    for w in expand(declarations):
        seen.add(w.target)
        declared_in = _declared_in(w.decl, root)
        entry = lock.entries.get(w.target)
        current = _current(w.target, track_value=w.track_value)
        if entry is not None and (
            entry.role != w.role or entry.parent != w.parent or entry.track_value != w.track_value
        ):
            entry = None
        if entry is None:
            results.append(
                CheckResult(
                    target=w.target,
                    role=w.role,
                    status=UNLOCKED,
                    note=w.decl.note,
                    declared_in=declared_in,
                    dist=current.dist if current else None,
                )
            )
            continue
        results.append(
            CheckResult(
                target=w.target,
                role=w.role,
                status=_status(w, entry, current, today),
                note=w.decl.note,
                declared_in=declared_in,
                locked_version=entry.fingerprint.dist_version,
                current_version=current.dist_version if current else None,
                no_source=current.no_source if current else False,
                dist=current.dist if current else entry.fingerprint.dist,
            )
        )
    results.extend(
        CheckResult(
            target=t,
            role=e.role,
            status=STALE,
            declared_in=e.declared_in,
            dist=e.fingerprint.dist,
        )
        for t, e in sorted(lock.entries.items())
        if t not in seen
    )
    return results


@functools.cache
def runtime_lock() -> Lock | None:
    root = find_project_root(Path.cwd())
    if root is None:
        return None
    path = lock_path(root)
    if not path.is_file():
        return None
    try:
        return read_lock(path)
    except LockError as exc:
        warnings.warn(f"ignoring {LOCK_FILENAME}: {exc}", stacklevel=2)
        return None


def reset_caches() -> None:
    runtime_lock.cache_clear()
