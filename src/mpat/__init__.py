"""mpat: lockfile-based upstream drift tracking for Python monkeypatches."""

from mpat._errors import (
    AlreadyPatched,
    ForbiddenTarget,
    KindMismatch,
    LockError,
    MpatError,
    TargetNotFound,
    UnsupportedTarget,
    UpstreamDriftError,
    UpstreamDriftWarning,
)

__version__ = "0.0.1"

__all__ = [
    "AlreadyPatched",
    "ForbiddenTarget",
    "KindMismatch",
    "LockError",
    "MpatError",
    "TargetNotFound",
    "UnsupportedTarget",
    "UpstreamDriftError",
    "UpstreamDriftWarning",
]
