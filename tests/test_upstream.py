import base64

import pytest

from mpat import _upstream
from mpat._fingerprint import fingerprint
from mpat._lock import LockEntry
from mpat._registry import ROLE_PATCH
from mpat._targets import resolve

GREET = "fakeup.core.greet"
OLD_RETURN = '    return f"hi {name}{punct}"'
NEW_RETURN = '    return f"hey {name}{punct}"'


def lock_entry(target: str) -> LockEntry:
    return LockEntry(
        target=target,
        role=ROLE_PATCH,
        declared_in="app_patches.py",
        fingerprint=fingerprint(resolve(target)),
    )


def bump(upstream) -> None:
    upstream.edit("core.py", OLD_RETURN, NEW_RETURN)
    upstream.install_dist("1.1")


@pytest.fixture
def locked(upstream, index) -> LockEntry:
    upstream.install_dist("1.0")
    entry = lock_entry(GREET)
    assert entry.fingerprint.dist_version == "1.0"
    return entry


@pytest.mark.parametrize("content_type", ["json", "html"])
def test_locked_source_from_a_wheel(upstream, index, locked, content_type):
    index.content_type = index.JSON if content_type == "json" else index.HTML
    upstream.build_wheel(index.files, "1.0")
    bump(upstream)
    source = _upstream.locked_source(locked)
    assert source.path == "fakeup/core.py"
    assert source.start == 4
    assert source.lines == ('def greet(name, punct="!"):', OLD_RETURN)
    assert source.matches_lock


def test_locked_source_falls_back_to_the_sdist(upstream, index, locked):
    upstream.build_sdist(index.files, "1.0")
    bump(upstream)
    assert _upstream.locked_source(locked).lines[-1] == OLD_RETURN


def test_rebuilt_archive_is_flagged(upstream, index, locked):
    bump(upstream)
    upstream.build_wheel(index.files, "1.0")
    source = _upstream.locked_source(locked)
    assert source.lines[-1] == NEW_RETURN
    assert not source.matches_lock


def test_archive_is_cached(upstream, index, locked):
    upstream.build_wheel(index.files, "1.0")
    bump(upstream)
    _upstream.locked_source(locked)
    _upstream.locked_source(locked)
    assert sum(r.startswith("/files/") for r in index.requests) == 1


def test_missing_version_is_unavailable(upstream, index, locked):
    upstream.build_wheel(index.files, "0.9")
    with pytest.raises(_upstream.UpstreamUnavailable, match="no archive for fakeup 1.0"):
        _upstream.locked_source(locked)


def test_unreachable_index_is_unavailable(upstream, index, locked, monkeypatch):
    index.server.shutdown()
    index.server.server_close()
    with pytest.raises(_upstream.UpstreamUnavailable):
        _upstream.locked_source(locked)


def test_target_without_distribution_is_unavailable(upstream, index):
    entry = lock_entry(GREET)
    with pytest.raises(_upstream.UpstreamUnavailable, match="no distribution"):
        _upstream.locked_source(entry)


def test_index_credentials_are_sent_as_basic_auth(upstream, index, locked, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", index.url.replace("http://", "http://me:s3cret@"))
    upstream.build_wheel(index.files, "1.0")
    _upstream.locked_source(locked)
    expected = "Basic " + base64.b64encode(b"me:s3cret").decode()
    assert index.headers[0]["Authorization"] == expected


def test_file_watch_diffs_the_whole_file(upstream, index):
    (upstream.pkg / "static" / "widget.js").write_text("a\nb\n")
    upstream.install_dist("1.0")
    entry = lock_entry("fakeup/static/widget.js")
    upstream.build_wheel(index.files, "1.0")
    (upstream.pkg / "static" / "widget.js").write_text("a\nc\n")
    source = _upstream.locked_source(entry)
    assert source.lines == ("a", "b")
    assert source.matches_lock


def test_installed_source(upstream):
    bump(upstream)
    source = _upstream.installed_source(GREET)
    assert source is not None
    assert (source.path, source.start, source.lines[-1]) == ("fakeup/core.py", 4, NEW_RETURN)


def test_installed_source_of_a_deleted_target(upstream):
    upstream.edit("core.py", "def add(", "def put(")
    assert _upstream.installed_source("fakeup.core.Store.add") is None


def test_credentials_do_not_follow_a_redirect_to_another_host(upstream, index, locked, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", index.url.replace("http://", "http://me:s3cret@"))
    index.redirect_files_to = f"http://localhost:{index.server.server_port}"
    upstream.build_wheel(index.files, "1.0")
    bump(upstream)
    assert _upstream.locked_source(locked).lines[-1] == OLD_RETURN
    by_path = dict(zip(index.requests, index.headers, strict=True))
    assert "Authorization" in by_path["/files/fakeup-1.0-py3-none-any.whl"]
    assert "Authorization" not in by_path["/files/fakeup-1.0-py3-none-any.whl?signed=1"]


def test_unwritable_cache_is_unavailable(upstream, index, locked, monkeypatch, tmp_path):
    (tmp_path / "blocker").write_text("")
    monkeypatch.setenv("MPAT_CACHE_DIR", str(tmp_path / "blocker" / "cache"))
    upstream.build_wheel(index.files, "1.0")
    with pytest.raises(_upstream.UpstreamUnavailable, match="cannot cache"):
        _upstream.locked_source(locked)


def test_archive_of_another_project_is_ignored(upstream, index, locked):
    upstream.build_wheel(index.files, "1.0")
    (index.files / "fakeup-1.0-py3-none-any.whl").rename(index.files / "other-1.0.tar.gz")
    with pytest.raises(_upstream.UpstreamUnavailable, match="no archive for fakeup 1.0"):
        _upstream.locked_source(locked)


def test_index_filenames_cannot_escape_the_cache(upstream, index, locked, monkeypatch):
    links = [
        _upstream._Link(filename="../../fakeup-1.0.tar.gz", url=f"{index.url}/x"),
        _upstream._Link(filename="fakeup/../../fakeup-1.0.tar.gz", url=f"{index.url}/x"),
    ]
    monkeypatch.setattr(_upstream, "_links", lambda *a, **k: links)
    with pytest.raises(_upstream.UpstreamUnavailable, match="no archive for fakeup 1.0"):
        _upstream.locked_source(locked)


def test_non_http_download_links_are_refused(upstream, index, locked, monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("x")
    links = [_upstream._Link(filename="fakeup-1.0.tar.gz", url=secret.as_uri())]
    monkeypatch.setattr(_upstream, "_links", lambda *a, **k: links)
    with pytest.raises(_upstream.UpstreamUnavailable, match="refusing to download"):
        _upstream.locked_source(locked)


def test_errors_do_not_print_signed_query_strings(upstream, index, locked, monkeypatch):
    links = [
        _upstream._Link(
            filename="fakeup-1.0.tar.gz",
            url=f"{index.url.removesuffix('/simple')}/missing?X-Amz-Signature=s3cret",
        )
    ]
    monkeypatch.setattr(_upstream, "_links", lambda *a, **k: links)
    with pytest.raises(_upstream.UpstreamUnavailable) as raised:
        _upstream.locked_source(locked)
    assert "s3cret" not in str(raised.value)
    assert "/missing" in str(raised.value)
