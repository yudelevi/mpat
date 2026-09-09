"""Generated pytest tests. Import both names into one test module per service:

from mpat.testing import test_upstream_drift, test_patch_still_needed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from mpat._config import load_config
from mpat._fingerprint import OK
from mpat._lock import CheckResult, Lock, check, lock_path, read_lock
from mpat._registry import Declaration, collect_declarations


@dataclass(frozen=True)
class Collected:
    declarations: list[Declaration]
    lock: Lock
    root: Path


def _collected() -> Collected | None:
    config = load_config()
    if config is None or not config.modules:
        return None
    path = lock_path(config.root)
    return Collected(
        declarations=collect_declarations(config.modules),
        lock=read_lock(path) if path.is_file() else Lock(entries={}),
        root=config.root,
    )


def drift_results() -> list[CheckResult]:
    collected = _collected()
    if collected is None:
        return []
    return check(collected.declarations, collected.lock, root=collected.root, today=date.today())


def until_declarations() -> list[Declaration]:
    collected = _collected()
    if collected is None:
        return []
    return [d for d in collected.declarations if d.until is not None]


@pytest.mark.parametrize("result", [pytest.param(r, id=r.target) for r in drift_results()])
def test_upstream_drift(result: CheckResult) -> None:
    assert result.status == OK, f"{result.target}: {result.status}. {result.note}".rstrip()


@pytest.mark.parametrize("decl", [pytest.param(d, id=d.target) for d in until_declarations()])
def test_patch_still_needed(decl: Declaration) -> None:
    assert decl.until is not None
    assert not decl.until(), (
        f"{decl.target}: upstream fixed, delete this patch. {decl.note}".rstrip()
    )
