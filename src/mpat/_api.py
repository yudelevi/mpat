from __future__ import annotations

import functools
import inspect
import logging
import os
import warnings
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from datetime import date
from typing import Any, Protocol, TypeVar

from mpat import _hooks
from mpat._config import runtime_config
from mpat._errors import (
    AlreadyPatched,
    KindMismatch,
    UnsupportedTarget,
    UpstreamDriftError,
    UpstreamDriftWarning,
)
from mpat._fingerprint import OK, compare, fingerprint
from mpat._lock import runtime_lock
from mpat._registry import (
    ENV_ON,
    ON_DRIFT_WARN,
    ROLE_PATCH,
    ROLE_WATCH,
    STATUS_APPLIED,
    STATUS_DEFERRED,
    STATUS_SKIPPED_DRIFT,
    STATUS_SKIPPED_UNTIL,
    STATUS_WATCHED,
    STRICT_ENV,
    Declaration,
    applied_for,
    collect_mode,
    config_declarations,
    mark_applied,
    override_originals,
    record_override,
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


def _check_declaration_forbidden(canonical: str, depends_on: Sequence[str]) -> None:
    allow = _allow()
    check_forbidden(canonical, allow=allow)
    for dep in depends_on:
        check_forbidden(canonical_target(dep), allow=allow)


def _drift_status(decl: Declaration, resolved: Resolved) -> str:
    lock = runtime_lock()
    if lock is None or decl.target not in lock.entries:
        return OK
    return compare(
        locked=lock.entries[decl.target].fingerprint,
        current=fingerprint(resolved, track_value=decl.track_value),
    )


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


def _check_kind(fn: Replacement, resolved: Resolved) -> None:
    if callable_kind(fn) != callable_kind(resolved.obj):
        raise KindMismatch(
            f"{resolved.target}: original is {callable_kind(resolved.obj)}, "
            f"replacement is {callable_kind(fn)}"
        )


def _resolve_patchable(canonical: str) -> Resolved:
    resolved = resolve(canonical)
    check_supported(resolved)
    return resolved


def _apply(*, decl: Declaration, fn: Replacement, resolved: Resolved) -> None:
    if decl.until is not None and decl.until():
        decl.status = STATUS_SKIPPED_UNTIL
        log.info("%s: not applied, until=%r is satisfied. %s", decl.target, decl.until, decl.note)
        return
    if not _handle_drift(decl, resolved):
        decl.status = STATUS_SKIPPED_DRIFT
        return
    original = _live_original(resolved, decl)
    wrapper = _wrap(fn=fn, original=original, decl=decl)
    replacement = resolved.descriptor(wrapper) if resolved.descriptor else wrapper
    setattr(resolved.parent, resolved.attr, replacement)
    mark_applied(id(original), decl)
    decl.status = STATUS_APPLIED


def _apply_deferred(*, decl: Declaration, fn: Replacement) -> None:
    resolved = _resolve_patchable(decl.target)
    _check_kind(fn, resolved)
    _apply(decl=decl, fn=fn, resolved=resolved)


def _top_level(canonical: str) -> str:
    return canonical.partition(".")[0]


def patch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    until: Until | None = None,
    on_drift: str = ON_DRIFT_WARN,
    review_by: date | None = None,
    note: str = "",
    when_imported: bool = False,
) -> Callable[[F], F]:
    if on_drift not in ON_DRIFT_VALUES:
        raise ValueError(f"on_drift must be one of {ON_DRIFT_VALUES}, got {on_drift!r}")
    canonical = canonical_target(target)
    _check_declaration_forbidden(canonical, depends_on)
    resolved = None if when_imported else _resolve_patchable(canonical)
    declared_in = _caller_file()

    def decorator(fn: F) -> F:
        if resolved is not None:
            _check_kind(fn, resolved)
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
        if resolved is not None:
            _apply(decl=decl, fn=fn, resolved=resolved)
            return fn
        decl.status = STATUS_DEFERRED
        _hooks.when_imported(
            _top_level(canonical), functools.partial(_apply_deferred, decl=decl, fn=fn)
        )
        return fn

    return decorator


def apply_all() -> None:
    """Import every module a `when_imported=True` patch is still waiting on."""
    _hooks.import_pending()


def apply_overrides() -> None:
    """Assign every `[[tool.mpat.override]]` value, once per process."""
    config = runtime_config()
    if config is None:
        return
    for decl in config_declarations(config):
        register(decl)
    if collect_mode():
        return
    done = override_originals()
    for spec in config.overrides:
        if spec.target in done:
            continue
        resolved = resolve(spec.target)
        if type(resolved.static) is not type(spec.value):
            raise UnsupportedTarget(
                f"{spec.target}: override value is {type(spec.value).__name__}, "
                f"attribute is {type(resolved.static).__name__}"
            )
        record_override(spec.target, resolved.static)
        setattr(resolved.parent, resolved.attr, spec.value)


def watch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    until: Until | None = None,
    review_by: date | None = None,
    note: str = "",
    track_value: bool = True,
) -> None:
    canonical = canonical_target(target)
    _check_declaration_forbidden(canonical, depends_on)
    resolved = resolve(canonical)
    declared_in = _caller_file()
    decl = Declaration(
        target=canonical,
        role=ROLE_WATCH,
        depends_on=tuple(depends_on),
        until=until,
        on_drift=ON_DRIFT_WARN,
        review_by=review_by,
        note=note,
        declared_in=declared_in,
        identity=(declared_in, f"watch:{canonical}"),
        track_value=track_value,
    )
    register(decl)
    if collect_mode():
        return
    _handle_drift(decl, resolved)
    decl.status = STATUS_WATCHED
