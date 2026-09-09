from __future__ import annotations

from collections.abc import Callable
from importlib import metadata
from typing import Protocol, runtime_checkable

from packaging.requirements import Requirement


@runtime_checkable
class Until(Protocol):
    def __call__(self) -> bool: ...


class Condition:
    def __call__(self) -> bool:
        raise NotImplementedError

    def __or__(self, other: Until) -> Condition:
        return _Or(left=self, right=other)

    def __and__(self, other: Until) -> Condition:
        return _And(left=self, right=other)


class _Or(Condition):
    def __init__(self, *, left: Until, right: Until) -> None:
        self.left = left
        self.right = right

    def __call__(self) -> bool:
        return self.left() or self.right()

    def __repr__(self) -> str:
        return f"({self.left!r} | {self.right!r})"


class _And(Condition):
    def __init__(self, *, left: Until, right: Until) -> None:
        self.left = left
        self.right = right

    def __call__(self) -> bool:
        return self.left() and self.right()

    def __repr__(self) -> str:
        return f"({self.left!r} & {self.right!r})"


class Version(Condition):
    def __init__(self, requirement: str) -> None:
        parsed = Requirement(requirement)
        self.requirement = requirement
        self.name = parsed.name
        self.specifier = parsed.specifier

    def __call__(self) -> bool:
        try:
            installed = metadata.version(self.name)
        except metadata.PackageNotFoundError:
            return False
        return self.specifier.contains(installed, prereleases=True)

    def __repr__(self) -> str:
        return f"Version({self.requirement!r})"


class Probe(Condition):
    def __init__(self, fn: Callable[[], object]) -> None:
        self.fn = fn

    def __call__(self) -> bool:
        return bool(self.fn())

    def __repr__(self) -> str:
        return f"Probe({getattr(self.fn, '__qualname__', repr(self.fn))})"
