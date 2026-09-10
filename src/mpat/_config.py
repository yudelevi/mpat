from __future__ import annotations

import functools
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from mpat._errors import MpatError

PYPROJECT = "pyproject.toml"
TOOL_SECTION = "mpat"
WATCH_KEY = "watch"

_TARGET = "target"
_DEPENDS_ON = "depends_on"
_REVIEW_BY = "review_by"
_NOTE = "note"
_WATCH_FIELDS: dict[str, type | tuple[type, ...]] = {
    _TARGET: str,
    _DEPENDS_ON: list,
    _REVIEW_BY: date,
    _NOTE: str,
}


@dataclass(frozen=True)
class WatchSpec:
    target: str
    depends_on: tuple[str, ...] = ()
    review_by: date | None = None
    note: str = ""


@dataclass(frozen=True)
class Config:
    root: Path
    modules: tuple[str, ...]
    allow: tuple[str, ...]
    declared: bool = False
    watches: tuple[WatchSpec, ...] = ()

    @property
    def declares_anything(self) -> bool:
        return bool(self.modules or self.watches)


def find_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / PYPROJECT).is_file():
            return candidate
    return None


def _entry_error(key: str, label: str, problem: str) -> MpatError:
    return MpatError(f"{PYPROJECT}: [[tool.{TOOL_SECTION}.{key}]] {label}: {problem}")


def _typed_fields(
    key: str, label: str, data: Mapping[str, Any], fields: Mapping[str, type | tuple[type, ...]]
) -> None:
    for name, value in data.items():
        if name not in fields:
            raise _entry_error(key, label, f"unknown key {name!r}")
        if not isinstance(value, fields[name]):
            raise _entry_error(
                key, label, f"{name} must be {_type_name(fields[name])}, got {type(value).__name__}"
            )


def _type_name(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _entries(section: Mapping[str, Any], key: str) -> Iterator[tuple[str, dict[str, Any]]]:
    raw = section.get(key, [])
    if not isinstance(raw, list):
        raise MpatError(
            f"{PYPROJECT}: tool.{TOOL_SECTION}.{key} must be an array of tables, "
            f"got {type(raw).__name__}"
        )
    for index, data in enumerate(raw):
        if not isinstance(data, dict):
            raise _entry_error(key, f"#{index}", f"must be a table, got {type(data).__name__}")
        target = data.get(_TARGET)
        label = f"#{index}" if not isinstance(target, str) else f"#{index} ({target})"
        if _TARGET not in data:
            raise _entry_error(key, label, f"missing {_TARGET}")
        yield label, data


def _watch_spec(label: str, data: dict[str, Any]) -> WatchSpec:
    _typed_fields(WATCH_KEY, label, data, _WATCH_FIELDS)
    depends_on = data.get(_DEPENDS_ON, [])
    if not all(isinstance(dep, str) for dep in depends_on):
        raise _entry_error(WATCH_KEY, label, f"{_DEPENDS_ON} must be a list of str")
    return WatchSpec(
        target=data[_TARGET],
        depends_on=tuple(depends_on),
        review_by=data.get(_REVIEW_BY),
        note=data.get(_NOTE, ""),
    )


def _unique_targets(specs: tuple[WatchSpec, ...]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.target in seen:
            raise MpatError(
                f"{PYPROJECT}: {spec.target} is declared twice in [tool.{TOOL_SECTION}]"
            )
        seen.add(spec.target)


def load_config(start: Path | None = None) -> Config | None:
    root = find_project_root(start or Path.cwd())
    if root is None:
        return None
    data = tomllib.loads((root / PYPROJECT).read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    section = tool.get(TOOL_SECTION, {})
    watches = tuple(_watch_spec(label, entry) for label, entry in _entries(section, WATCH_KEY))
    _unique_targets(watches)
    return Config(
        root=root,
        modules=tuple(section.get("modules", ())),
        allow=tuple(section.get("allow", ())),
        declared=TOOL_SECTION in tool,
        watches=watches,
    )


@functools.cache
def runtime_config() -> Config | None:
    return load_config()


def reset_caches() -> None:
    runtime_config.cache_clear()
