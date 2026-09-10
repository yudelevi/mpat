"""mpat: lockfile-based upstream drift tracking for Python monkeypatches."""

from mpat._api import apply_all, patch, watch
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
from mpat._until import Probe, Until, Version

__version__ = "0.2.0"

__all__ = [
    "AlreadyPatched",
    "ForbiddenTarget",
    "KindMismatch",
    "LockError",
    "MpatError",
    "Probe",
    "TargetNotFound",
    "UnsupportedTarget",
    "Until",
    "UpstreamDriftError",
    "UpstreamDriftWarning",
    "Version",
    "apply_all",
    "patch",
    "watch",
]
