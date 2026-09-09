from __future__ import annotations

import ast
import functools
import hashlib
import inspect
import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from mpat._targets import ASYNC, ASYNCGEN, Resolved, callable_kind

KIND_FUNCTION = "function"
KIND_CLASS = "class"
KIND_ATTRIBUTE = "attribute"
KIND_MODULE = "module"
UNHASHABLE = "<unhashable>"

OK = "ok"
MOVED = "moved"
SIGNATURE = "signature"
BODY = "body"
VALUE = "value"
NO_SOURCE = "no_source"

HASH_PREFIX = "sha256:"
SCALARS = (int, str, bool, float, type(None))
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_BLOCKS = (ast.If, ast.Try, ast.With, ast.For, ast.While)
_ASYNC_KINDS = (ASYNC, ASYNCGEN)


@dataclass(frozen=True)
class Fingerprint:
    kind: str
    resolved: str
    is_async: bool = False
    signature: str | None = None
    source_hash: str | None = None
    value_repr: str | None = None
    source_file: str | None = None
    dist: str | None = None
    dist_version: str | None = None
    no_source: bool = False


def _iter_defs(
    body: list[ast.stmt],
) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    for node in body:
        if isinstance(node, _DEFS):
            yield node
        elif isinstance(node, _BLOCKS):
            yield from _iter_defs(node.body)
            yield from _iter_defs(getattr(node, "orelse", []))
            yield from _iter_defs(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                yield from _iter_defs(handler.body)


def _definition_node(
    tree: ast.Module, qualname: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None:
    if "<locals>" in qualname:
        return None
    body: list[ast.stmt] = tree.body
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    for name in qualname.split("."):
        node = next((n for n in _iter_defs(body) if n.name == name), None)
        if node is None:
            return None
        body = node.body
    return node


def _hash(node: ast.AST) -> str:
    return HASH_PREFIX + hashlib.sha256(ast.unparse(node).encode()).hexdigest()


def _source_file(obj: Any) -> str | None:
    try:
        file = inspect.getsourcefile(obj)
    except TypeError:
        return None
    if file and os.path.exists(file):
        return file
    return None


def _source_hash(obj: Any, file: str) -> str | None:
    tree = ast.parse(Path(file).read_text(encoding="utf-8"))
    if inspect.ismodule(obj):
        return _hash(tree)
    qualname = getattr(obj, "__qualname__", None)
    if not qualname:
        return None
    node = _definition_node(tree, qualname)
    return None if node is None else _hash(node)


def _kind(obj: Any) -> str:
    if inspect.ismodule(obj):
        return KIND_MODULE
    if inspect.isclass(obj):
        return KIND_CLASS
    if inspect.isroutine(obj):
        return KIND_FUNCTION
    return KIND_ATTRIBUTE


def _owner_name(parent: Any) -> str:
    if inspect.ismodule(parent):
        return parent.__name__
    return f"{parent.__module__}.{parent.__qualname__}"


def _resolved_name(resolved: Resolved, kind: str) -> str:
    obj = resolved.obj
    if kind == KIND_MODULE:
        return obj.__name__
    if kind in (KIND_FUNCTION, KIND_CLASS):
        module = getattr(obj, "__module__", None)
        qualname = getattr(obj, "__qualname__", None)
        if module and qualname:
            return f"{module}.{qualname}"
    return f"{_owner_name(resolved.parent)}.{resolved.attr}"


@functools.cache
def _packages_distributions() -> Mapping[str, list[str]]:
    return metadata.packages_distributions()


def reset_caches() -> None:
    _packages_distributions.cache_clear()


def _dist(top: str) -> tuple[str | None, str | None]:
    names = _packages_distributions().get(top)
    if not names:
        return None, None
    return names[0], metadata.version(names[0])


def _relative_source(file: str, top: str) -> str:
    module = sys.modules.get(top)
    module_file = getattr(module, "__file__", None)
    search_path = [Path(entry) for entry in getattr(module, "__path__", [])]
    if module_file:
        base = Path(module_file).parent
        roots = [base.parent] if search_path else [base]
    elif search_path:
        roots = [portion.parent for portion in search_path]
    else:
        return file
    for root in roots:
        try:
            return Path(file).relative_to(root).as_posix()
        except ValueError:
            continue
    return file


def _signature(obj: Any) -> str | None:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None


def fingerprint(resolved: Resolved) -> Fingerprint:
    obj = resolved.obj
    kind = _kind(obj)
    name = _resolved_name(resolved, kind)
    top = name.split(".")[0]
    dist, dist_version = _dist(top)

    if kind == KIND_ATTRIBUTE:
        raw_target = obj.fget if isinstance(obj, property) else None
        if isinstance(obj, property):
            value_repr = None
        elif isinstance(obj, SCALARS) or (
            isinstance(obj, tuple) and all(isinstance(item, SCALARS) for item in obj)
        ):
            value_repr = repr(obj)
        else:
            value_repr = UNHASHABLE
    else:
        raw_target = obj
        value_repr = None

    hash_target = inspect.unwrap(raw_target) if raw_target is not None else None
    file = _source_file(hash_target) if hash_target is not None else None
    source_hash = _source_hash(hash_target, file) if file else None
    expects_source = hash_target is not None
    return Fingerprint(
        kind=kind,
        resolved=name,
        is_async=kind == KIND_FUNCTION and callable_kind(obj) in _ASYNC_KINDS,
        signature=_signature(obj) if kind == KIND_FUNCTION else None,
        source_hash=source_hash,
        value_repr=value_repr,
        source_file=_relative_source(file, top) if file else None,
        dist=dist,
        dist_version=dist_version,
        no_source=expects_source and source_hash is None,
    )


def compare(*, locked: Fingerprint, current: Fingerprint) -> str:
    if locked.resolved != current.resolved:
        return MOVED
    if locked.kind != current.kind:
        return MOVED
    if locked.source_file and current.source_file and locked.source_file != current.source_file:
        return MOVED
    if locked.signature != current.signature or locked.is_async != current.is_async:
        return SIGNATURE
    if locked.value_repr != current.value_repr:
        return VALUE
    if locked.source_hash is not None and current.source_hash is None:
        return NO_SOURCE
    if locked.source_hash != current.source_hash:
        return BODY
    return OK
