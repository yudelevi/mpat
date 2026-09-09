import mpat
from mpat import _errors


def test_all_errors_subclass_mpat_error():
    for cls in (
        _errors.TargetNotFound,
        _errors.UnsupportedTarget,
        _errors.KindMismatch,
        _errors.AlreadyPatched,
        _errors.ForbiddenTarget,
        _errors.UpstreamDriftError,
        _errors.LockError,
    ):
        assert issubclass(cls, _errors.MpatError)


def test_public_exports():
    assert mpat.MpatError is _errors.MpatError
    assert issubclass(mpat.UpstreamDriftWarning, UserWarning)
