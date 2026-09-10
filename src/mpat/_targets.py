from __future__ import annotations

import importlib
import inspect
import types
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mpat._errors import (
    ForbiddenTarget,
    TargetImportError,
    TargetNotFound,
    UnsupportedTarget,
)

DEFAULT_DENY: tuple[str, ...] = (
    "mpat",
    "builtins",
    "sys",
    "importlib",
    "ssl",
    "hashlib",
    "hmac",
    "secrets",
    "cryptography",
    "certifi",
)

SYNC = "sync"
ASYNC = "async"
GEN = "gen"
ASYNCGEN = "asyncgen"

_DESCRIPTORS = (staticmethod, classmethod)


@dataclass(frozen=True)
class Resolved:
    target: str
    parent: Any
    attr: str
    static: Any
    obj: Any
    descriptor: type | None


def canonical_target(target: str | object) -> str:
    if isinstance(target, str):
        return target
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None)
    if not module or not qualname:
        raise TargetNotFound(f"cannot derive a dotted target from {target!r}")
    return f"{module}.{qualname}"


def _import_prefix(parts: list[str], *, target: str) -> tuple[types.ModuleType, list[str]]:
    for i in range(len(parts), 0, -1):
        name = ".".join(parts[:i])
        try:
            return importlib.import_module(name), parts[i:]
        except ModuleNotFoundError as exc:
            if exc.name is None or not name.startswith(exc.name):
                raise _import_failed(target=target, exc=exc) from exc
        except ImportError as exc:
            raise _import_failed(target=target, exc=exc) from exc
    raise TargetNotFound(f"no importable module prefix in {'.'.join(parts)!r}")


def _import_failed(*, target: str, exc: ImportError) -> TargetImportError:
    return TargetImportError(
        f"{target!r}: importing it raised {type(exc).__name__}: {exc}. "
        f"If another module has to be imported first for this target to import, "
        f"list that module in [tool.mpat] modules."
    )


def resolve(target: str) -> Resolved:
    module, rest = _import_prefix(target.split("."), target=target)
    if not rest:
        raise TargetNotFound(f"{target!r} is a module, not an attribute of one")
    parent: Any = module
    for name in rest[:-1]:
        try:
            parent = getattr(parent, name)
        except AttributeError as exc:
            raise TargetNotFound(f"{target!r}: {name!r} not found") from exc
    attr = rest[-1]
    try:
        static = inspect.getattr_static(parent, attr)
    except AttributeError:
        try:
            static = getattr(parent, attr)
        except AttributeError as exc:
            raise TargetNotFound(f"{target!r}: {attr!r} not found") from exc
    descriptor: type | None = None
    obj = static
    if isinstance(static, _DESCRIPTORS):
        descriptor = type(static)
        obj = static.__func__
    return Resolved(
        target=target, parent=parent, attr=attr, static=static, obj=obj, descriptor=descriptor
    )


def check_supported(resolved: Resolved) -> None:
    if inspect.isclass(resolved.parent) and not any(
        resolved.attr in vars(cls) for cls in resolved.parent.__mro__
    ):
        raise UnsupportedTarget(f"{resolved.target}: attribute is provided by the metaclass")
    if not isinstance(resolved.obj, types.FunctionType):
        raise UnsupportedTarget(
            f"{resolved.target}: only functions, staticmethods and classmethods can be patched, "
            f"got {type(resolved.static).__name__}; use watch() instead"
        )


def _matches(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def check_forbidden(target: str, *, allow: Sequence[str] = ()) -> None:
    if any(_matches(target, entry) for entry in allow):
        return
    for denied in DEFAULT_DENY:
        if _matches(target, denied):
            raise ForbiddenTarget(
                f"{target!r} matches denylist entry {denied!r}; "
                "add it to [tool.mpat] allow in pyproject.toml to override"
            )


def callable_kind(fn: Any) -> str:
    if inspect.isasyncgenfunction(fn):
        return ASYNCGEN
    if inspect.iscoroutinefunction(fn):
        return ASYNC
    if inspect.isgeneratorfunction(fn):
        return GEN
    return SYNC
