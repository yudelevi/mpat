from __future__ import annotations

import functools
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from mpat._errors import MpatError

PYPROJECT = "pyproject.toml"
CONFIG_FILENAME = "mpat.toml"
CONFIG_FILENAMES = (CONFIG_FILENAME, PYPROJECT)
TOOL_SECTION = "mpat"
WATCH_KEY = "watch"
OVERRIDE_KEY = "override"

_MODULES = "modules"
_ALLOW = "allow"
_SECTION_KEYS = frozenset({_MODULES, _ALLOW, WATCH_KEY, OVERRIDE_KEY})
_TARGET = "target"
_DEPENDS_ON = "depends_on"
_REVIEW_BY = "review_by"
_NOTE = "note"
_VALUE = "value"
_UNTIL = "until"
_TOML_SCALARS = (int, float, str, bool)
_WATCH_FIELDS: dict[str, type | tuple[type, ...]] = {
    _TARGET: str,
    _DEPENDS_ON: list,
    _UNTIL: str,
    _REVIEW_BY: date,
    _NOTE: str,
}
_OVERRIDE_FIELDS: dict[str, type | tuple[type, ...]] = {
    _TARGET: str,
    _VALUE: _TOML_SCALARS,
    _REVIEW_BY: date,
    _NOTE: str,
}


@dataclass(frozen=True)
class WatchSpec:
    target: str
    depends_on: tuple[str, ...] = ()
    until: str | None = None
    review_by: date | None = None
    note: str = ""


@dataclass(frozen=True)
class OverrideSpec:
    target: str
    value: int | float | str | bool
    note: str = ""
    review_by: date | None = None


@dataclass(frozen=True)
class _Source:
    """Where the section came from: the file, and how its keys are spelled there."""

    path: Path
    prefix: str
    section: str

    @property
    def name(self) -> str:
        return self.path.name

    def table(self, key: str) -> str:
        return f"[[{self.prefix}{key}]]"


_MPAT_TOML_SOURCE = {"prefix": "", "section": ""}
_PYPROJECT_SOURCE = {"prefix": f"tool.{TOOL_SECTION}.", "section": f"[tool.{TOOL_SECTION}] "}


@dataclass(frozen=True)
class Config:
    root: Path
    path: Path
    modules: tuple[str, ...]
    allow: tuple[str, ...]
    declared: bool = False
    watches: tuple[WatchSpec, ...] = ()
    overrides: tuple[OverrideSpec, ...] = ()

    @property
    def declares_anything(self) -> bool:
        return bool(self.modules or self.watches or self.overrides)

    @property
    def where(self) -> str:
        if self.path.name == CONFIG_FILENAME:
            return CONFIG_FILENAME
        return f"[tool.{TOOL_SECTION}] in {PYPROJECT}"


def config_path(root: Path) -> Path | None:
    for name in CONFIG_FILENAMES:
        if (root / name).is_file():
            return root / name
    return None


def find_config_file(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        path = config_path(candidate)
        if path is not None:
            return path
    return None


def find_project_root(start: Path) -> Path | None:
    path = find_config_file(start)
    return None if path is None else path.parent


def _entry_error(source: _Source, key: str, label: str, problem: str) -> MpatError:
    return MpatError(f"{source.name}: {source.table(key)} {label}: {problem}")


def _typed_fields(
    source: _Source,
    key: str,
    label: str,
    data: Mapping[str, Any],
    fields: Mapping[str, type | tuple[type, ...]],
) -> None:
    for name, value in data.items():
        if name not in fields:
            raise _entry_error(source, key, label, f"unknown key {name!r}")
        if not isinstance(value, fields[name]):
            raise _entry_error(
                source,
                key,
                label,
                f"{name} must be {_type_name(fields[name])}, got {type(value).__name__}",
            )


def _type_name(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _entries(
    source: _Source, section: Mapping[str, Any], key: str
) -> Iterator[tuple[str, dict[str, Any]]]:
    raw = section.get(key, [])
    if not isinstance(raw, list):
        raise MpatError(
            f"{source.name}: {source.prefix}{key} must be an array of tables, "
            f"got {type(raw).__name__}"
        )
    for index, data in enumerate(raw):
        if not isinstance(data, dict):
            raise _entry_error(
                source, key, f"#{index}", f"must be a table, got {type(data).__name__}"
            )
        target = data.get(_TARGET)
        label = f"#{index}" if not isinstance(target, str) else f"#{index} ({target})"
        if _TARGET not in data:
            raise _entry_error(source, key, label, f"missing {_TARGET}")
        yield label, data


def _watch_spec(source: _Source, label: str, data: dict[str, Any]) -> WatchSpec:
    _typed_fields(source, WATCH_KEY, label, data, _WATCH_FIELDS)
    depends_on = data.get(_DEPENDS_ON, [])
    if not all(isinstance(dep, str) for dep in depends_on):
        raise _entry_error(source, WATCH_KEY, label, f"{_DEPENDS_ON} must be a list of str")
    return WatchSpec(
        target=data[_TARGET],
        depends_on=tuple(depends_on),
        until=data.get(_UNTIL),
        review_by=data.get(_REVIEW_BY),
        note=data.get(_NOTE, ""),
    )


def _override_spec(source: _Source, label: str, data: dict[str, Any]) -> OverrideSpec:
    _typed_fields(source, OVERRIDE_KEY, label, data, _OVERRIDE_FIELDS)
    if _VALUE not in data:
        raise _entry_error(source, OVERRIDE_KEY, label, f"missing {_VALUE}")
    return OverrideSpec(
        target=data[_TARGET],
        value=data[_VALUE],
        note=data.get(_NOTE, ""),
        review_by=data.get(_REVIEW_BY),
    )


def _unique_targets(source: _Source, specs: Sequence[WatchSpec | OverrideSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if spec.target in seen:
            raise MpatError(f"{source.name}: {spec.target} is declared twice")
        seen.add(spec.target)


def _table(
    source: _Source, data: Mapping[str, Any], key: str, *, parent: str = ""
) -> Mapping[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        name = f"{parent}.{key}" if parent else key
        raise MpatError(f"{source.name}: {name} must be a table, got {type(value).__name__}")
    return value


def _known_keys(source: _Source, section: Mapping[str, Any]) -> None:
    for key in section:
        if key not in _SECTION_KEYS:
            raise MpatError(f"{source.name}: {source.section}unknown key {key!r}")


def _str_list(source: _Source, section: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MpatError(f"{source.name}: {source.section}{key} must be a list of str")
    return tuple(value)


def _section(path: Path) -> tuple[_Source, Mapping[str, Any], bool]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if path.name == CONFIG_FILENAME:
        return _Source(path=path, **_MPAT_TOML_SOURCE), data, True
    source = _Source(path=path, **_PYPROJECT_SOURCE)
    tool = _table(source, data, "tool")
    return source, _table(source, tool, TOOL_SECTION, parent="tool"), TOOL_SECTION in tool


def load_config(start: Path | None = None) -> Config | None:
    path = find_config_file(start or Path.cwd())
    if path is None:
        return None
    source, section, declared = _section(path)
    _known_keys(source, section)
    watches = tuple(
        _watch_spec(source, label, entry) for label, entry in _entries(source, section, WATCH_KEY)
    )
    overrides = tuple(
        _override_spec(source, label, entry)
        for label, entry in _entries(source, section, OVERRIDE_KEY)
    )
    _unique_targets(source, (*watches, *overrides))
    return Config(
        root=path.parent,
        path=path,
        modules=_str_list(source, section, _MODULES),
        allow=_str_list(source, section, _ALLOW),
        declared=declared,
        watches=watches,
        overrides=overrides,
    )


@functools.cache
def runtime_config() -> Config | None:
    return load_config()


def reset_caches() -> None:
    runtime_config.cache_clear()
