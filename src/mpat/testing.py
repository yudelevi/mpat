"""Generated pytest tests.

Installing mpat is enough: the pytest plugin collects these tests as `mpat::...`
for any project with `[tool.mpat]` in its pyproject. Importing both names into a
test module is still supported for projects that want them written out:

from mpat.testing import test_upstream_drift, test_patch_still_needed
"""

from __future__ import annotations

import pytest

from mpat._generated import (
    UNCONFIGURED_NOTE,
    UNCONFIGURED_ROLE,
    UNCONFIGURED_STATUS,
    UNCONFIGURED_TARGET,
    Collected,
    drift_message,
    drift_ok,
    drift_results,
    still_needed_message,
    until_declarations,
)
from mpat._lock import CheckResult
from mpat._registry import Declaration

__all__ = [
    "UNCONFIGURED_NOTE",
    "UNCONFIGURED_ROLE",
    "UNCONFIGURED_STATUS",
    "UNCONFIGURED_TARGET",
    "Collected",
    "drift_results",
    "test_patch_still_needed",
    "test_upstream_drift",
    "until_declarations",
]


@pytest.mark.parametrize("result", [pytest.param(r, id=r.target) for r in drift_results()])
def test_upstream_drift(result: CheckResult) -> None:
    assert drift_ok(result), drift_message(result)


@pytest.mark.parametrize("decl", [pytest.param(d, id=d.target) for d in until_declarations()])
def test_patch_still_needed(decl: Declaration) -> None:
    assert decl.until is not None
    assert not decl.until(), still_needed_message(decl)
