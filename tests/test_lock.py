import dataclasses
import stat
import warnings
from datetime import date
from pathlib import Path

import pytest

from mpat import _fingerprint as fp
from mpat import _lock
from mpat._errors import LockError
from mpat._registry import ROLE_DEPENDS, ROLE_PATCH, ROLE_WATCH, Declaration

TODAY = date(2026, 9, 9)


def decl(
    target, *, role=ROLE_PATCH, depends_on=(), review_by=None, note="", declared_in="/proj/src/p.py"
):
    return Declaration(
        target=target,
        role=role,
        depends_on=tuple(depends_on),
        until=None,
        on_drift="warn",
        review_by=review_by,
        note=note,
        declared_in=declared_in,
        identity=(declared_in, target),
    )


def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    return tmp_path


def test_expand_flattens_depends_on():
    wanted = _lock.expand([decl("a.b", depends_on=["a.c", "a.d"])])
    assert [(w.target, w.role, w.parent) for w in wanted] == [
        ("a.b", ROLE_PATCH, None),
        ("a.c", ROLE_DEPENDS, "a.b"),
        ("a.d", ROLE_DEPENDS, "a.b"),
    ]


def test_build_and_roundtrip(upstream, tmp_path):
    root = project(tmp_path)
    declared = str(root / "src" / "p.py")
    lock = _lock.build_lock(
        [decl("fakeup.core.greet", depends_on=["fakeup.LIMIT"], declared_in=declared)], root=root
    )
    assert set(lock.entries) == {"fakeup.core.greet", "fakeup.LIMIT"}
    assert lock.entries["fakeup.core.greet"].declared_in == "src/p.py"
    assert lock.entries["fakeup.LIMIT"].parent == "fakeup.core.greet"
    path = _lock.lock_path(root)
    _lock.write_lock(path, lock)
    text = path.read_text()
    assert text.startswith("version = 1\n")
    assert '[entry."fakeup.LIMIT"]' in text
    assert text.index('[entry."fakeup.LIMIT"]') < text.index('[entry."fakeup.core.greet"]')
    assert _lock.read_lock(path) == lock


def test_write_is_atomic_no_temp_left(upstream, tmp_path):
    root = project(tmp_path)
    lock = _lock.build_lock([decl("fakeup.core.greet")], root=root)
    _lock.write_lock(_lock.lock_path(root), lock)
    assert [p.name for p in root.iterdir() if p.name.startswith("mpat")] == ["mpat.lock"]


def test_read_rejects_bad_version(tmp_path):
    path = tmp_path / "mpat.lock"
    path.write_text("version = 99\n")
    with pytest.raises(LockError):
        _lock.read_lock(path)


def test_read_rejects_garbage(tmp_path):
    path = tmp_path / "mpat.lock"
    path.write_text("not = [toml")
    with pytest.raises(LockError):
        _lock.read_lock(path)


def test_read_rejects_binary_corruption(tmp_path):
    path = tmp_path / "mpat.lock"
    path.write_bytes(b"\xff\xfe\x00version")
    with pytest.raises(LockError):
        _lock.read_lock(path)


def test_runtime_lock_binary_corruption_warns_once(tmp_path):
    root = project(tmp_path)
    _lock.lock_path(root).write_bytes(b"\xff\xfe\x00version")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _lock.runtime_lock() is None
        assert _lock.runtime_lock() is None
    assert len(caught) == 1
    assert "mpat.lock" in str(caught[0].message)


def test_read_rejects_bool_version(tmp_path):
    path = tmp_path / "mpat.lock"
    path.write_text("version = true\n")
    with pytest.raises(LockError):
        _lock.read_lock(path)


def test_write_lock_sets_readable_permissions(tmp_path):
    root = project(tmp_path)
    lock = _lock.build_lock([], root=root)
    path = _lock.lock_path(root)
    _lock.write_lock(path, lock)
    assert stat.S_IMODE(path.stat().st_mode) == _lock.LOCK_FILE_MODE


def statuses(results):
    return {r.target: r.status for r in results}


def test_check_ok(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.greet", depends_on=["fakeup.LIMIT"])]
    lock = _lock.build_lock(decls, root=root)
    assert statuses(_lock.check(decls, lock, root=root, today=TODAY)) == {
        "fakeup.core.greet": fp.OK,
        "fakeup.LIMIT": fp.OK,
    }


def test_check_body_and_value(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.greet", depends_on=["fakeup.LIMIT"])]
    lock = _lock.build_lock(decls, root=root)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 17")
    assert statuses(_lock.check(decls, lock, root=root, today=TODAY)) == {
        "fakeup.core.greet": fp.BODY,
        "fakeup.LIMIT": fp.VALUE,
    }


def test_check_missing(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.agreet")]
    lock = _lock.build_lock(decls, root=root)
    upstream.edit("core.py", "async def agreet(", "async def agreet_renamed(")
    assert statuses(_lock.check(decls, lock, root=root, today=TODAY)) == {
        "fakeup.core.agreet": _lock.MISSING
    }


def test_check_propagates_upstream_import_errors(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.greet")]
    lock = _lock.build_lock(decls, root=root)
    upstream.edit("core.py", "def greet(", "def greet_renamed(")
    with pytest.raises(ImportError):
        _lock.check(decls, lock, root=root, today=TODAY)


def test_check_unlocked_and_stale(upstream, tmp_path):
    root = project(tmp_path)
    lock = _lock.build_lock([decl("fakeup.core.greet")], root=root)
    results = _lock.check([decl("fakeup.core.agreet")], lock, root=root, today=TODAY)
    assert statuses(results) == {
        "fakeup.core.agreet": _lock.UNLOCKED,
        "fakeup.core.greet": _lock.STALE,
    }


def test_check_review(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.greet", review_by=date(2026, 1, 1), note="look again")]
    lock = _lock.build_lock(decls, root=root)
    results = _lock.check(decls, lock, root=root, today=TODAY)
    assert statuses(results) == {"fakeup.core.greet": _lock.REVIEW}
    assert results[0].note == "look again"


def test_drift_beats_review(upstream, tmp_path):
    root = project(tmp_path)
    decls = [decl("fakeup.core.greet", review_by=date(2026, 1, 1))]
    lock = _lock.build_lock(decls, root=root)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    assert statuses(_lock.check(decls, lock, root=root, today=TODAY)) == {
        "fakeup.core.greet": fp.BODY
    }


def test_check_watch_role_and_versions(tmp_path):
    root = project(tmp_path)
    decls = [decl("packaging.version.Version", role=ROLE_WATCH)]
    lock = _lock.build_lock(decls, root=root)
    result = _lock.check(decls, lock, root=root, today=TODAY)[0]
    assert result.role == ROLE_WATCH
    assert result.locked_version == result.current_version
    assert result.locked_version


def test_runtime_lock_reads_from_cwd(upstream, tmp_path):
    root = project(tmp_path)
    lock = _lock.build_lock([decl("fakeup.core.greet")], root=root)
    _lock.write_lock(_lock.lock_path(root), lock)
    assert _lock.runtime_lock() == lock


def test_runtime_lock_none_without_file(tmp_path):
    project(tmp_path)
    assert _lock.runtime_lock() is None


def test_runtime_lock_broken_warns_once(tmp_path):
    root = project(tmp_path)
    _lock.lock_path(root).write_text("version = 99\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _lock.runtime_lock() is None
        assert _lock.runtime_lock() is None
    assert len(caught) == 1
    assert "mpat.lock" in str(caught[0].message)


def test_entry_roundtrip_drops_none_fields(tmp_path):
    entry = _lock.LockEntry(
        target="a.b",
        role=ROLE_PATCH,
        declared_in="p.py",
        fingerprint=fp.Fingerprint(kind=fp.KIND_ATTRIBUTE, resolved="a.b", value_repr="1"),
    )
    lock = _lock.Lock(entries={"a.b": entry})
    path = tmp_path / "mpat.lock"
    _lock.write_lock(path, lock)
    assert "signature" not in path.read_text()
    assert _lock.read_lock(path).entries["a.b"] == dataclasses.replace(entry)
