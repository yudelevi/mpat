from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from mpat._config import PYPROJECT, Config, OverrideSpec, WatchSpec
from mpat._errors import MpatError
from mpat._targets import check_forbidden
from mpat._until import Until, until_from_string

ROLE_PATCH = "patch"
ROLE_WATCH = "watch"
ROLE_DEPENDS = "depends_on"

STATUS_REGISTERED = "registered"
STATUS_DEFERRED = "deferred"
STATUS_APPLIED = "applied"
STATUS_SKIPPED_UNTIL = "skipped_until"
STATUS_SKIPPED_DRIFT = "skipped_drift"
STATUS_WATCHED = "watched"

ON_DRIFT_WARN = "warn"

COLLECT_ENV = "MPAT_COLLECT"
STRICT_ENV = "MPAT_STRICT"
ENV_ON = "1"


@dataclass
class Declaration:
    target: str
    role: str
    depends_on: tuple[str, ...]
    until: Until | None
    on_drift: str
    review_by: date | None
    note: str
    declared_in: str
    identity: tuple[str, str]
    status: str = field(default=STATUS_REGISTERED)
    track_value: bool = True


_declarations: dict[tuple[str, str], Declaration] = {}
_applied: dict[int, Declaration] = {}
_override_originals: dict[str, Any] = {}

_OVERRIDE_IDENTITY = "override"


def register(decl: Declaration) -> None:
    _declarations[decl.identity] = decl


def declarations() -> list[Declaration]:
    return list(_declarations.values())


def mark_applied(original_id: int, decl: Declaration) -> None:
    _applied[original_id] = decl


def applied_for(original_id: int) -> Declaration | None:
    return _applied.get(original_id)


def record_override(target: str, original: Any) -> None:
    _override_originals[target] = original


def override_originals() -> dict[str, Any]:
    return dict(_override_originals)


def reset() -> None:
    _declarations.clear()
    _applied.clear()
    _override_originals.clear()


def collect_mode() -> bool:
    return os.environ.get(COLLECT_ENV) == ENV_ON


def _import_all(modules: Sequence[str]) -> None:
    for name in modules:
        importlib.import_module(name)


def _check_forbidden(target: str, depends_on: Sequence[str], *, allow: Sequence[str]) -> None:
    check_forbidden(target, allow=allow)
    for dep in depends_on:
        check_forbidden(dep, allow=allow)


def watch_declaration(spec: WatchSpec) -> Declaration:
    return Declaration(
        target=spec.target,
        role=ROLE_WATCH,
        depends_on=spec.depends_on,
        until=None if spec.until is None else until_from_string(spec.until),
        on_drift=ON_DRIFT_WARN,
        review_by=spec.review_by,
        note=spec.note,
        declared_in=PYPROJECT,
        identity=(PYPROJECT, f"{ROLE_WATCH}:{spec.target}"),
    )


def override_declaration(spec: OverrideSpec) -> Declaration:
    return Declaration(
        target=spec.target,
        role=ROLE_WATCH,
        depends_on=(),
        until=None,
        on_drift=ON_DRIFT_WARN,
        review_by=spec.review_by,
        note=spec.note,
        declared_in=PYPROJECT,
        identity=(PYPROJECT, f"{_OVERRIDE_IDENTITY}:{spec.target}"),
        track_value=False,
    )


def config_declarations(config: Config) -> list[Declaration]:
    """Build the declarations pyproject.toml makes, checked against the denylist."""
    for spec in config.watches:
        _check_forbidden(spec.target, spec.depends_on, allow=config.allow)
    for spec in config.overrides:
        _check_forbidden(spec.target, (), allow=config.allow)
    return [
        *(watch_declaration(spec) for spec in config.watches),
        *(override_declaration(spec) for spec in config.overrides),
    ]


def _register_config(config: Config) -> None:
    from_code = {d.target: d for d in declarations() if d.declared_in != PYPROJECT}
    for decl in config_declarations(config):
        if decl.target in from_code:
            raise MpatError(
                f"{decl.target}: declared twice: {PYPROJECT} and "
                f"{from_code[decl.target].declared_in}"
            )
        register(decl)


def collect_declarations(config: Config, *, apply: bool) -> list[Declaration]:
    """Import the patch modules, add what pyproject.toml declares, and return it all.

    apply=True imports the modules the way the application does, so the patches
    take effect and a module cached by this import is the patched one. apply=False
    is for the CLI, which runs in its own process and must not touch live objects.
    """
    if apply:
        _import_all(config.modules)
    else:
        _import_under_collect(config.modules)
    _register_config(config)
    return declarations()


def _import_under_collect(modules: Sequence[str]) -> None:
    previous = os.environ.get(COLLECT_ENV)
    os.environ[COLLECT_ENV] = ENV_ON
    try:
        _import_all(modules)
    finally:
        if previous is None:
            del os.environ[COLLECT_ENV]
        else:
            os.environ[COLLECT_ENV] = previous
