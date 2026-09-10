"""pytest plugin: collects the generated drift tests without any boilerplate."""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mpat._config import Config, load_config
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

_projects_key: pytest.StashKey[list[Project]] = pytest.StashKey()
_collected_key: pytest.StashKey[dict[Path, Collected | None]] = pytest.StashKey()


@dataclasses.dataclass(frozen=True)
class Project:
    name: str
    root: Path


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
    config.stash[_projects_key] = _enabled_projects(config)
    if config.stash[_projects_key]:
        config.addinivalue_line("markers", MARKER_HELP)


def _enabled_projects(config: pytest.Config) -> list[Project]:
    if config.getoption(DISABLE_DEST) or not config.getini(INI_NAME):
        return []
    return _declared_projects(config)


def _argument_paths(config: pytest.Config) -> list[Path]:
    return [config.invocation_params.dir / arg.split(NODEID_SEPARATOR, 1)[0] for arg in config.args]


def _declared_projects(config: pytest.Config) -> list[Project]:
    """The rootdir's own project keeps the bare `mpat` name so its node ids do not
    change; each other project the positional arguments walk up to gets one too."""
    rootdir_config = load_config(config.rootpath)
    projects: dict[Path, Project] = {}
    for start in (config.rootpath, *_argument_paths(config)):
        mpat_config = load_config(start)
        if mpat_config is None or not mpat_config.declared or mpat_config.root in projects:
            continue
        name = (
            COLLECTOR_NAME
            if mpat_config == rootdir_config
            else f"{COLLECTOR_NAME}[{_label(config, mpat_config)}]"
        )
        projects[mpat_config.root] = Project(name=name, root=mpat_config.root)
    return list(projects.values())


def _label(config: pytest.Config, mpat_config: Config) -> str:
    try:
        return mpat_config.root.relative_to(config.rootpath).as_posix()
    except ValueError:
        return mpat_config.root.as_posix()


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


def _owner(decl: Declaration, roots: Sequence[Path]) -> Path | None:
    declared_in = Path(decl.declared_in).resolve()
    owners = sorted(
        (root for root in roots if declared_in.is_relative_to(root.resolve())),
        key=lambda root: len(root.parts),
    )
    return owners[-1] if owners else None


def _scoped(collected: Collected, roots: Sequence[Path]) -> Collected:
    """With several configs in one process the registry is shared, so a collector
    keeps only the declarations under its own root, plus any under none of them."""
    declarations = [d for d in collected.declarations if _owner(d, roots) in (None, collected.root)]
    return dataclasses.replace(collected, declarations=declarations)


def _collected(session: pytest.Session, root: Path) -> Collected | None:
    cache = session.stash.setdefault(_collected_key, {})
    if root not in cache:
        collected = collect(root)
        roots = [p.root for p in session.config.stash[_projects_key]]
        cache[root] = None if collected is None else _scoped(collected, roots)
    return cache[root]


class MpatItem(pytest.Item):
    def __init__(self, *, name: str, parent: pytest.Collector, root: Path) -> None:
        super().__init__(name=name, parent=parent)
        self.root = root
        self.add_marker(MARKER)

    def reportinfo(self) -> tuple[Path, int, str]:
        return lock_path(self.root), 0, self.name

    def repr_failure(
        self, excinfo: pytest.ExceptionInfo[BaseException], style: TracebackStyle | None = None
    ) -> str | TerminalRepr:
        if isinstance(excinfo.value, AssertionError):
            return str(excinfo.value)
        return super().repr_failure(excinfo, style)


class DriftItem(MpatItem):
    def __init__(
        self, *, name: str, parent: pytest.Collector, root: Path, result: CheckResult
    ) -> None:
        super().__init__(name=name, parent=parent, root=root)
        self.result = result

    def runtest(self) -> None:
        assert drift_ok(self.result), drift_message(self.result)


class StillNeededItem(MpatItem):
    def __init__(
        self, *, name: str, parent: pytest.Collector, root: Path, decl: Declaration
    ) -> None:
        super().__init__(name=name, parent=parent, root=root)
        self.decl = decl

    def runtest(self) -> None:
        assert self.decl.until is not None
        assert not self.decl.until(), still_needed_message(self.decl)


class MpatCollector(pytest.Collector):
    def __init__(self, *, name: str, parent: pytest.Session, nodeid: str, root: Path) -> None:
        super().__init__(name=name, parent=parent, nodeid=nodeid)
        self.root = root

    def collect(self) -> Iterable[pytest.Item]:
        collected = _collected(self.session, self.root)
        if collected is None:
            return [
                DriftItem.from_parent(
                    self, name=UNCONFIGURED_NAME, root=self.root, result=unconfigured_result()
                )
            ]
        items: list[pytest.Item] = [
            DriftItem.from_parent(self, name=f"{DRIFT_NAME}[{r.target}]", root=self.root, result=r)
            for r in sorted(results_for(collected), key=lambda r: r.target)
        ]
        items.extend(
            StillNeededItem.from_parent(
                self, name=f"{STILL_NEEDED_NAME}[{d.target}]", root=self.root, decl=d
            )
            for d in sorted(until_for(collected), key=lambda d: d.target)
        )
        return items


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    if (
        not config.stash[_projects_key]
        or not _only_directories_requested(config)
        or _explicit_tests_collected(items)
    ):
        return
    for project in config.stash[_projects_key]:
        collector = MpatCollector.from_parent(
            session, name=project.name, nodeid=project.name, root=project.root
        )
        if collect_one_node is None:
            items.extend(collector.collect())
            continue
        report = collect_one_node(collector)
        session.ihook.pytest_collectreport(report=report)
        if report.passed:
            items.extend(node for node in report.result if isinstance(node, pytest.Item))
