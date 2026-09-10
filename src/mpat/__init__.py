"""mpat: lockfile-based upstream drift tracking for Python monkeypatches."""

from mpat._api import apply_all, apply_overrides, patch, watch
from mpat._errors import (
    AlreadyPatched,
    ForbiddenTarget,
    KindMismatch,
    LockError,
    MpatError,
    TargetImportError,
    TargetNotFound,
    UnsupportedTarget,
    UpstreamDriftError,
    UpstreamDriftWarning,
)
from mpat._until import Probe, Until, Version

__version__ = "0.2.2"

__all__ = [
    "AlreadyPatched",
    "ForbiddenTarget",
    "KindMismatch",
    "LockError",
    "MpatError",
    "Probe",
    "TargetImportError",
    "TargetNotFound",
    "UnsupportedTarget",
    "Until",
    "UpstreamDriftError",
    "UpstreamDriftWarning",
    "Version",
    "apply_all",
    "apply_overrides",
    "patch",
    "watch",
]
