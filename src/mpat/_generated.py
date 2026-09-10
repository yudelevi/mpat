"""Shared body of the generated drift tests.

Both `mpat.testing` and the pytest plugin build their tests from these helpers,
so the two paths report identical text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mpat._config import load_config
from mpat._fingerprint import OK
from mpat._lock import CheckResult, Lock, check, lock_path, read_lock
from mpat._registry import Declaration, collect_declarations

UNCONFIGURED_TARGET = "<unconfigured>"
UNCONFIGURED_ROLE = "config"
UNCONFIGURED_STATUS = "unconfigured"
UNCONFIGURED_NOTE = "no [tool.mpat] modules found; add modules to pyproject.toml"


@dataclass(frozen=True)
class Collected:
    declarations: list[Declaration]
    lock: Lock
    root: Path


def unconfigured_result() -> CheckResult:
    return CheckResult(
        target=UNCONFIGURED_TARGET,
        role=UNCONFIGURED_ROLE,
        status=UNCONFIGURED_STATUS,
        note=UNCONFIGURED_NOTE,
    )


def collect(start: Path | None = None) -> Collected | None:
    config = load_config(start)
    if config is None or not config.declares_anything:
        return None
    path = lock_path(config.root)
    return Collected(
        declarations=collect_declarations(config, apply=True),
        lock=read_lock(path) if path.is_file() else Lock(entries={}),
        root=config.root,
    )


def results_for(collected: Collected) -> list[CheckResult]:
    return check(collected.declarations, collected.lock, root=collected.root, today=date.today())


def until_for(collected: Collected) -> list[Declaration]:
    return [decl for decl in collected.declarations if decl.until is not None]


def drift_results(start: Path | None = None) -> list[CheckResult]:
    collected = collect(start)
    return [unconfigured_result()] if collected is None else results_for(collected)


def until_declarations(start: Path | None = None) -> list[Declaration]:
    collected = collect(start)
    return [] if collected is None else until_for(collected)


def drift_ok(result: CheckResult) -> bool:
    return result.status == OK


def drift_message(result: CheckResult) -> str:
    return f"{result.target}: {result.status}. {result.note}".rstrip()


def still_needed_message(decl: Declaration) -> str:
    return f"{decl.target}: upstream fixed, delete this patch. {decl.note}".rstrip()
