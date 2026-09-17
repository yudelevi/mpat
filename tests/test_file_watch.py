from datetime import date

import pytest

import mpat
from mpat import _fingerprint as fp
from mpat import _lock
from mpat._cli import main
from mpat._errors import TargetNotFound, UnsupportedTarget
from mpat._registry import ROLE_WATCH, Declaration
from mpat._targets import ResolvedFile, resolve, resolve_attribute

FILE = "fakeup/static/widget.js"
TODAY = date(2026, 9, 17)


def decl(target: str) -> Declaration:
    return Declaration(
        target=target,
        role=ROLE_WATCH,
        depends_on=(),
        until=None,
        on_drift="warn",
        review_by=None,
        note="",
        declared_in="/proj/pyproject.toml",
        identity=("/proj/pyproject.toml", target),
    )


def test_resolve_file_target(upstream):
    resolved = resolve(FILE)
    assert isinstance(resolved, ResolvedFile)
    assert resolved.target == FILE
    assert resolved.read_bytes().startswith(b"export function greet")


def test_resolve_missing_file_raises(upstream):
    with pytest.raises(TargetNotFound, match="widget.ts"):
        resolve("fakeup/static/widget.ts")


def test_resolve_directory_is_not_a_file(upstream):
    with pytest.raises(TargetNotFound, match="not a file"):
        resolve("fakeup/static")


def test_fingerprint_file(upstream):
    before = fp.fingerprint(resolve(FILE))
    assert before.kind == fp.KIND_FILE
    assert before.resolved == FILE
    assert before.source_file == FILE
    assert before.source_hash is not None and before.source_hash.startswith(fp.HASH_PREFIX)
    assert before.signature is None
    assert before.value_repr is None
    assert before.no_source is False


def test_file_content_change_reports_content(upstream):
    before = fp.fingerprint(resolve(FILE))
    upstream.edit("static/widget.js", "hello", "hi")
    after = fp.fingerprint(resolve(FILE))
    assert fp.compare(locked=before, current=after) == fp.CONTENT


def test_file_whitespace_change_reports_content(upstream):
    before = fp.fingerprint(resolve(FILE))
    upstream.edit("static/widget.js", "  return", "    return")
    after = fp.fingerprint(resolve(FILE))
    assert fp.compare(locked=before, current=after) == fp.CONTENT


def test_file_unchanged_is_ok(upstream):
    before = fp.fingerprint(resolve(FILE))
    after = fp.fingerprint(resolve(FILE))
    assert fp.compare(locked=before, current=after) == fp.OK


def test_file_target_cannot_be_patched(upstream):
    with pytest.raises(UnsupportedTarget, match="cannot be patched"):
        resolve_attribute(FILE)


def test_patch_refuses_file_target(upstream):
    with pytest.raises(UnsupportedTarget):

        @mpat.patch(FILE)
        def replacement(original):
            return original()


def test_lock_and_check_file(upstream, tmp_path):
    lock = _lock.build_lock([decl(FILE)], root=tmp_path)
    entry = lock.entries[FILE]
    assert entry.fingerprint.kind == fp.KIND_FILE
    path = tmp_path / "mpat.lock"
    _lock.write_lock(path, lock)
    reread = _lock.read_lock(path)
    assert reread.entries[FILE] == entry
    ok = _lock.check([decl(FILE)], reread, root=tmp_path, today=TODAY)
    assert [r.status for r in ok] == [fp.OK]
    upstream.edit("static/widget.js", "hello", "hi")
    drifted = _lock.check([decl(FILE)], reread, root=tmp_path, today=TODAY)
    assert [r.status for r in drifted] == [fp.CONTENT]
    (upstream.pkg / "static" / "widget.js").unlink()
    gone = _lock.check([decl(FILE)], reread, root=tmp_path, today=TODAY)
    assert [r.status for r in gone] == [_lock.MISSING]


def test_watch_api_accepts_file(upstream):
    mpat.watch(FILE, note="forked into app/select.js")
    targets = [d.target for d in mpat._registry.declarations()]
    assert FILE in targets


def test_config_file_key(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.mpat.watch]]\nfile = "nicegui/elements/select.js"\nnote = "forked"\n'
    )
    cfg = mpat._config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.watches[0].target == "nicegui/elements/select.js"
    assert cfg.watches[0].note == "forked"


@pytest.mark.parametrize(
    "body",
    [
        '[[tool.mpat.watch]]\nfile = "a/b.js"\ntarget = "a.b"\n',
        "[[tool.mpat.watch]]\nfile = 5\n",
        '[[tool.mpat.watch]]\nfile = "a.b"\n',
        '[[tool.mpat.override]]\nfile = "a/b.js"\nvalue = 1\n',
    ],
)
def test_config_file_key_rejects(tmp_path, body):
    (tmp_path / "pyproject.toml").write_text(body)
    with pytest.raises(mpat._errors.MpatError, match=r"tool\.mpat"):
        mpat._config.load_config(tmp_path)


def test_cli_lock_check_diff_show_file(upstream, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(f'[[tool.mpat.watch]]\nfile = "{FILE}"\n')
    assert main(["lock"]) == 0
    assert f'"{FILE}"' in (tmp_path / "mpat.lock").read_text()
    assert main(["check"]) == 0
    assert main(["show", FILE]) == 0
    out = capsys.readouterr().out
    assert "kind: file" in out and "export function greet" in out
    upstream.edit("static/widget.js", "hello", "hi")
    assert main(["check"]) == 1
    assert main(["diff", FILE]) == 1
    out = capsys.readouterr().out
    assert "status: content" in out and "source_hash: sha256:" in out


def test_denylist_covers_file_targets():
    with pytest.raises(mpat._errors.ForbiddenTarget):
        mpat._targets.check_forbidden("ssl/cert.pem")


def test_override_rejects_file_target(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.mpat.override]]\ntarget = "a/b.js"\nvalue = 1\n'
    )
    with pytest.raises(mpat._errors.MpatError, match="cannot be overridden"):
        mpat._config.load_config(tmp_path)


def test_pytest_plugin_reports_file_drift(pytester, upstream, monkeypatch):
    root = pytester.path
    (root / "pyproject.toml").write_text(f'[[tool.mpat.watch]]\nfile = "{FILE}"\nnote = "forked"\n')
    monkeypatch.syspath_prepend(str(root))
    assert main(["lock"]) == 0
    upstream.edit("static/widget.js", "hello", "hi")
    result = pytester.runpytest("-p", "no:cacheprovider", "-v")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"mpat::drift[[]{FILE}[]] FAILED*", f"*{FILE}: content. forked*"])
