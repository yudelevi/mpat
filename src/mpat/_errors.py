class MpatError(Exception):
    pass


class TargetNotFound(MpatError):
    pass


class TargetImportError(MpatError, ImportError):
    pass


class UnsupportedTarget(MpatError):
    pass


class KindMismatch(MpatError):
    pass


class AlreadyPatched(MpatError):
    pass


class ForbiddenTarget(MpatError):
    pass


class UpstreamDriftError(MpatError):
    pass


class LockError(MpatError):
    pass


class UpstreamDriftWarning(UserWarning):
    pass
