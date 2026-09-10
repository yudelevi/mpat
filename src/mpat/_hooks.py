"""Post-import hooks: run a callback once a top-level module has finished executing.

The import system offers no such hook, so a finder on sys.meta_path claims the
modules we are waiting on, lets the normal machinery locate them, and wraps the
loader so the callbacks run right after exec_module. The wrapper hands the real
loader back to the module before executing it, so nothing else can observe it.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Callable, Sequence

Hook = Callable[[], None]

_pending: dict[str, list[Hook]] = {}


class HookedLoader(importlib.abc.Loader):
    def __init__(
        self, *, spec: importlib.machinery.ModuleSpec, loader: importlib.abc.Loader
    ) -> None:
        self.spec = spec
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType | None:
        return self.loader.create_module(spec)

    def exec_module(self, module: types.ModuleType) -> None:
        module.__loader__ = self.loader
        self.spec.loader = self.loader
        self.loader.exec_module(module)
        fire(self.spec.name)


class PostImportFinder(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.finding: set[str] = set()

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        _ = target
        if fullname not in _pending or fullname in self.finding:
            return None
        self.finding.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self.finding.discard(fullname)
        if spec is None or spec.loader is None:
            return None
        spec.loader = HookedLoader(spec=spec, loader=spec.loader)
        return spec


def _install() -> None:
    if not any(isinstance(finder, PostImportFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, PostImportFinder())


def when_imported(module: str, hook: Hook) -> None:
    if module in sys.modules:
        hook()
        return
    _pending.setdefault(module, []).append(hook)
    _install()


def fire(module: str) -> None:
    """Run the hooks for a module; a hook that raises stays queued, so the next
    import attempt of that module runs it again instead of silently skipping."""
    hooks = _pending.pop(module, [])
    while hooks:
        try:
            hooks[0]()
        except BaseException:
            _pending[module] = hooks
            raise
        hooks.pop(0)


def import_pending() -> None:
    for module in list(_pending):
        importlib.import_module(module)


def reset() -> None:
    _pending.clear()
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, PostImportFinder)]
