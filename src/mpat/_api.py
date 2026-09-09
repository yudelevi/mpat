from __future__ import annotations

import functools
import inspect
import logging
import os
import warnings
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import date
from typing import Any, Protocol, TypeVar

from mpat._config import runtime_config
from mpat._errors import AlreadyPatched, KindMismatch, UpstreamDriftError, UpstreamDriftWarning
from mpat._fingerprint import OK, compare, fingerprint
from mpat._lock import runtime_lock
from mpat._registry import (
    ENV_ON,
    ROLE_PATCH,
    ROLE_WATCH,
    STATUS_APPLIED,
    STATUS_SKIPPED_DRIFT,
    STATUS_SKIPPED_UNTIL,
    STATUS_WATCHED,
    STRICT_ENV,
    Declaration,
    applied_for,
    collect_mode,
    mark_applied,
    register,
)
from mpat._targets import (
    ASYNC,
    ASYNCGEN,
    GEN,
    Resolved,
    callable_kind,
    canonical_target,
    check_forbidden,
    check_supported,
    resolve,
)
from mpat._until import Until

ON_DRIFT_WARN = "warn"
ON_DRIFT_SKIP = "skip"
ON_DRIFT_RAISE = "raise"
ON_DRIFT_VALUES = (ON_DRIFT_WARN, ON_DRIFT_SKIP, ON_DRIFT_RAISE)

ORIGINAL_ATTR = "__mpat_original__"
TARGET_ATTR = "__mpat_target__"
IDENTITY_ATTR = "__mpat_identity__"

_DECLARATION_FRAME_DEPTH = 2
_WARN_STACKLEVEL = 3


class Replacement(Protocol):
    __qualname__: str

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


F = TypeVar("F", bound=Replacement)
log = logging.getLogger("mpat")


def _caller_file() -> str:
    frame = inspect.stack()[_DECLARATION_FRAME_DEPTH]
    return os.path.abspath(frame.filename)


def _allow() -> tuple[str, ...]:
    config = runtime_config()
    return config.allow if config else ()


def _drift_status(decl: Declaration, resolved: Resolved) -> str:
    lock = runtime_lock()
    if lock is None or decl.target not in lock.entries:
        return OK
    return compare(locked=lock.entries[decl.target].fingerprint, current=fingerprint(resolved))


def _handle_drift(decl: Declaration, resolved: Resolved) -> bool:
    """Return True when the patch should still be applied."""
    status = _drift_status(decl, resolved)
    if status == OK:
        return True
    action = ON_DRIFT_RAISE if os.environ.get(STRICT_ENV) == ENV_ON else decl.on_drift
    message = f"{decl.target}: upstream drift ({status}) since mpat.lock. {decl.note}".rstrip()
    if action == ON_DRIFT_RAISE:
        raise UpstreamDriftError(message)
    warnings.warn(message, UpstreamDriftWarning, stacklevel=_WARN_STACKLEVEL)
    return action != ON_DRIFT_SKIP


def _declares_directly(resolved: Resolved) -> bool:
    if not inspect.isclass(resolved.parent):
        return True
    return resolved.attr in vars(resolved.parent)


def _live_original(resolved: Resolved, decl: Declaration) -> Any:
    live = inspect.getattr_static(resolved.parent, resolved.attr)
    if isinstance(live, (staticmethod, classmethod)):
        live = live.__func__
    identity = getattr(live, IDENTITY_ATTR, None)
    if identity == decl.identity:
        return getattr(live, ORIGINAL_ATTR)
    if identity is not None:
        raise AlreadyPatched(f"{decl.target} already patched by {identity[0]}:{identity[1]}")
    if _declares_directly(resolved):
        earlier = applied_for(id(live))
        if earlier is not None:
            raise AlreadyPatched(
                f"{decl.target} resolves to the object already patched as {earlier.target}"
            )
    return live


def _wrap(fn: Callable[..., Any], original: Any, decl: Declaration) -> Callable[..., Any]:
    kind = callable_kind(fn)
    wrapper: Callable[..., Any]
    if kind == ASYNC:

        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(original, *args, **kwargs)

        wrapper = async_wrapper
    elif kind == GEN:

        def gen_wrapper(*args: Any, **kwargs: Any) -> Iterator[Any]:
            yield from fn(original, *args, **kwargs)

        wrapper = gen_wrapper
    elif kind == ASYNCGEN:

        async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
            async for item in fn(original, *args, **kwargs):
                yield item

        wrapper = asyncgen_wrapper
    else:

        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(original, *args, **kwargs)

        wrapper = sync_wrapper

    functools.update_wrapper(wrapper, original)
    setattr(wrapper, ORIGINAL_ATTR, original)
    setattr(wrapper, TARGET_ATTR, decl.target)
    setattr(wrapper, IDENTITY_ATTR, decl.identity)
    return wrapper


def patch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    until: Until | None = None,
    on_drift: str = ON_DRIFT_WARN,
    review_by: date | None = None,
    note: str = "",
) -> Callable[[F], F]:
    if on_drift not in ON_DRIFT_VALUES:
        raise ValueError(f"on_drift must be one of {ON_DRIFT_VALUES}, got {on_drift!r}")
    canonical = canonical_target(target)
    check_forbidden(canonical, allow=_allow())
    resolved = resolve(canonical)
    check_supported(resolved)
    declared_in = _caller_file()

    def decorator(fn: F) -> F:
        if callable_kind(fn) != callable_kind(resolved.obj):
            raise KindMismatch(
                f"{canonical}: original is {callable_kind(resolved.obj)}, "
                f"replacement is {callable_kind(fn)}"
            )
        decl = Declaration(
            target=canonical,
            role=ROLE_PATCH,
            depends_on=tuple(depends_on),
            until=until,
            on_drift=on_drift,
            review_by=review_by,
            note=note,
            declared_in=declared_in,
            identity=(declared_in, fn.__qualname__),
        )
        register(decl)
        if collect_mode():
            return fn
        if until is not None and until():
            decl.status = STATUS_SKIPPED_UNTIL
            log.info("%s: not applied, until=%r is satisfied. %s", canonical, until, note)
            return fn
        if not _handle_drift(decl, resolved):
            decl.status = STATUS_SKIPPED_DRIFT
            return fn
        original = _live_original(resolved, decl)
        wrapper = _wrap(fn=fn, original=original, decl=decl)
        replacement = resolved.descriptor(wrapper) if resolved.descriptor else wrapper
        setattr(resolved.parent, resolved.attr, replacement)
        mark_applied(id(original), decl)
        decl.status = STATUS_APPLIED
        return fn

    return decorator


def watch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    review_by: date | None = None,
    note: str = "",
) -> None:
    canonical = canonical_target(target)
    check_forbidden(canonical, allow=_allow())
    resolved = resolve(canonical)
    declared_in = _caller_file()
    decl = Declaration(
        target=canonical,
        role=ROLE_WATCH,
        depends_on=tuple(depends_on),
        until=None,
        on_drift=ON_DRIFT_WARN,
        review_by=review_by,
        note=note,
        declared_in=declared_in,
        identity=(declared_in, f"watch:{canonical}"),
    )
    register(decl)
    if collect_mode():
        return
    _handle_drift(decl, resolved)
    decl.status = STATUS_WATCHED
