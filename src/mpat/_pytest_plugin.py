"""pytest plugin: collects the generated drift tests without any boilerplate."""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mpat._config import load_config
from mpat._generated import (
    Collected,
    collect,
    drift_message,
    drift_ok,
    results_for,
    still_needed_message,
    unconfigured_result,
    until_for,
)
from mpat._lock import CheckResult, lock_path
from mpat._registry import Declaration

if TYPE_CHECKING:
    from _pytest._code.code import TerminalRepr, TracebackStyle

# An entry-point plugin that fails to import breaks pytest for everyone who has
# mpat installed, so the one private name needed at runtime is optional.
try:
    from _pytest.runner import collect_one_node
except ImportError:
    collect_one_node = None

COLLECTOR_NAME = "mpat"
DISABLE_OPTION = "--no-mpat"
DISABLE_DEST = "no_mpat"
INI_NAME = "mpat"
MARKER = "mpat"
MARKER_HELP = "mpat: generated upstream drift tests"
UNCONFIGURED_NAME = "unconfigured"
DRIFT_NAME = "drift"
STILL_NEEDED_NAME = "still-needed"
TESTING_MODULE = "mpat.testing"
NODEID_SEPARATOR = "::"
EXPLICIT_TEST = "test_upstream_drift"

_collected_key: pytest.StashKey[Collected | None] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup(COLLECTOR_NAME).addoption(
        DISABLE_OPTION,
        action="store_true",
        dest=DISABLE_DEST,
        default=False,
        help="do not collect mpat's generated upstream drift tests",
    )
    parser.addini(
        INI_NAME,
        type="bool",
        default=True,
        help="collect mpat's generated upstream drift tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    if _active(config):
        config.addinivalue_line("markers", MARKER_HELP)


def _active(config: pytest.Config) -> bool:
    if config.getoption(DISABLE_DEST) or not config.getini(INI_NAME):
        return False
    mpat_config = load_config(config.rootpath)
    return mpat_config is not None and mpat_config.declared


def _only_directories_requested(config: pytest.Config) -> bool:
    for arg in config.args:
        path = arg.split(NODEID_SEPARATOR, 1)[0]
        if not path or not (config.invocation_params.dir / path).is_dir():
            return False
    return True


def _explicit_tests_collected(items: Sequence[pytest.Item]) -> bool:
    module = sys.modules.get(TESTING_MODULE)
    if module is None:
        return False
    explicit = getattr(module, EXPLICIT_TEST, None)
    return any(isinstance(i, pytest.Function) and i.function is explicit for i in items)


def _collected(session: pytest.Session) -> Collected | None:
    if _collected_key not in session.stash:
        session.stash[_collected_key] = collect(session.config.rootpath)
    return session.stash[_collected_key]


class MpatItem(pytest.Item):
    def __init__(self, *, name: str, parent: pytest.Collector) -> None:
        super().__init__(name=name, parent=parent)
        self.add_marker(MARKER)

    def reportinfo(self) -> tuple[Path, int, str]:
        return lock_path(self.config.rootpath), 0, self.name

    def repr_failure(
        self, excinfo: pytest.ExceptionInfo[BaseException], style: TracebackStyle | None = None
    ) -> str | TerminalRepr:
        if isinstance(excinfo.value, AssertionError):
            return str(excinfo.value)
        return super().repr_failure(excinfo, style)


class DriftItem(MpatItem):
    def __init__(self, *, name: str, parent: pytest.Collector, result: CheckResult) -> None:
        super().__init__(name=name, parent=parent)
        self.result = result

    def runtest(self) -> None:
        assert drift_ok(self.result), drift_message(self.result)


class StillNeededItem(MpatItem):
    def __init__(self, *, name: str, parent: pytest.Collector, decl: Declaration) -> None:
        super().__init__(name=name, parent=parent)
        self.decl = decl

    def runtest(self) -> None:
        assert self.decl.until is not None
        assert not self.decl.until(), still_needed_message(self.decl)


class MpatCollector(pytest.Collector):
    def collect(self) -> Iterable[pytest.Item]:
        collected = _collected(self.session)
        if collected is None:
            return [
                DriftItem.from_parent(self, name=UNCONFIGURED_NAME, result=unconfigured_result())
            ]
        items: list[pytest.Item] = [
            DriftItem.from_parent(self, name=f"{DRIFT_NAME}[{r.target}]", result=r)
            for r in sorted(results_for(collected), key=lambda r: r.target)
        ]
        items.extend(
            StillNeededItem.from_parent(self, name=f"{STILL_NEEDED_NAME}[{d.target}]", decl=d)
            for d in sorted(until_for(collected), key=lambda d: d.target)
        )
        return items


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    if (
        not _active(config)
        or not _only_directories_requested(config)
        or _explicit_tests_collected(items)
    ):
        return
    collector = MpatCollector.from_parent(session, name=COLLECTOR_NAME, nodeid=COLLECTOR_NAME)
    if collect_one_node is None:
        items.extend(collector.collect())
        return
    report = collect_one_node(collector)
    session.ihook.pytest_collectreport(report=report)
    if report.passed:
        items.extend(node for node in report.result if isinstance(node, pytest.Item))
