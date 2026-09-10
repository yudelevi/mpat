from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from importlib import metadata
from typing import Protocol, runtime_checkable

from packaging.requirements import InvalidRequirement, Requirement

from mpat._errors import MpatError

_DOTTED_PATH = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


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


class DottedCallable:
    """A zero-argument callable named by dotted path, imported on first call."""

    def __init__(self, *, path: str) -> None:
        self.path = path

    def __call__(self) -> object:
        module, _, name = self.path.rpartition(".")
        return getattr(importlib.import_module(module), name)()

    def __repr__(self) -> str:
        return self.path


class Probe(Condition):
    def __init__(self, fn: Callable[[], object]) -> None:
        self.fn = fn

    def __call__(self) -> bool:
        return bool(self.fn())

    def __repr__(self) -> str:
        return f"Probe({getattr(self.fn, '__qualname__', repr(self.fn))})"


def _requirement_with_specifier(text: str) -> Requirement | None:
    try:
        parsed = Requirement(text)
    except InvalidRequirement:
        return None
    return parsed if parsed.specifier else None


def until_from_string(text: str) -> Until:
    """Parse the `until` a pyproject.toml watch gives as a string.

    A requirement with a version specifier becomes `Version`; a dotted path to a
    zero-argument callable becomes `Probe`, imported when first evaluated.
    """
    if _requirement_with_specifier(text) is not None:
        return Version(text)
    if _DOTTED_PATH.match(text):
        return Probe(DottedCallable(path=text))
    raise MpatError(
        f"until {text!r} is neither a requirement with a version specifier nor a dotted "
        "path to a zero-argument callable"
    )
