from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from mpat._until import Until

ROLE_PATCH = "patch"
ROLE_WATCH = "watch"
ROLE_DEPENDS = "depends_on"

STATUS_REGISTERED = "registered"
STATUS_APPLIED = "applied"
STATUS_SKIPPED_UNTIL = "skipped_until"
STATUS_SKIPPED_DRIFT = "skipped_drift"
STATUS_WATCHED = "watched"

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


_declarations: dict[tuple[str, str], Declaration] = {}
_applied: dict[int, Declaration] = {}


def register(decl: Declaration) -> None:
    _declarations[decl.identity] = decl


def declarations() -> list[Declaration]:
    return list(_declarations.values())


def mark_applied(original_id: int, decl: Declaration) -> None:
    _applied[original_id] = decl


def applied_for(original_id: int) -> Declaration | None:
    return _applied.get(original_id)


def reset() -> None:
    _declarations.clear()
    _applied.clear()


def collect_mode() -> bool:
    return os.environ.get(COLLECT_ENV) == ENV_ON


def _import_all(modules: Sequence[str]) -> None:
    for name in modules:
        importlib.import_module(name)


def collect_declarations(modules: Sequence[str], *, apply: bool) -> list[Declaration]:
    """Import the patch modules and return what they declared.

    apply=True imports them the way the application does, so the patches take
    effect and a module cached by this import is the patched one. apply=False is
    for the CLI, which runs in its own process and must not touch live objects.
    """
    if apply:
        _import_all(modules)
        return declarations()
    previous = os.environ.get(COLLECT_ENV)
    os.environ[COLLECT_ENV] = ENV_ON
    try:
        _import_all(modules)
    finally:
        if previous is None:
            del os.environ[COLLECT_ENV]
        else:
            os.environ[COLLECT_ENV] = previous
    return declarations()
