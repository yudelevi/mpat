import importlib
import json
import shutil
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mpat import _config, _fingerprint, _hooks, _lock, _registry

pytest_plugins = ["pytester"]

FIXTURE_ROOT = Path(__file__).parent / "fake_upstream"
PKG = "fakeup"
PATCH_MODULE = "app_patches"
POLL_SECONDS = 0.01


class Upstream:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pkg = root / PKG
        shutil.copytree(FIXTURE_ROOT / PKG, self.pkg)
        self.purge()

    def purge(self) -> None:
        for name in list(sys.modules):
            if name == PKG or name.startswith(PKG + "."):
                del sys.modules[name]
        importlib.invalidate_caches()

    def install_dist(self, version: str) -> None:
        for old in self.root.glob(f"{PKG}-*.dist-info"):
            shutil.rmtree(old)
        info = self.root / f"{PKG}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {PKG}\nVersion: {version}\n")
        (info / "top_level.txt").write_text(f"{PKG}\n")
        importlib.invalidate_caches()

    def sources(self) -> Iterator[Path]:
        return (
            p for p in sorted(self.pkg.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
        )

    def build_wheel(self, dest: Path, version: str) -> Path:
        wheel = dest / f"{PKG}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            for path in self.sources():
                zf.write(path, path.relative_to(self.root).as_posix())
        return wheel

    def build_sdist(self, dest: Path, version: str) -> Path:
        sdist = dest / f"{PKG}-{version}.tar.gz"
        with tarfile.open(sdist, "w:gz") as tf:
            for path in self.sources():
                tf.add(path, f"{PKG}-{version}/src/{path.relative_to(self.root).as_posix()}")
        return sdist

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.pkg / rel
        text = path.read_text()
        assert old in text, f"{old!r} not in {rel}"
        path.write_text(text.replace(old, new))
        self.purge()


class Index:
    """A package index on localhost serving whatever archives sit in `files`."""

    JSON = "application/vnd.pypi.simple.v1+json"
    HTML = "text/html"

    def __init__(self, root: Path) -> None:
        self.files = root / "files"
        self.files.mkdir(parents=True)
        self.content_type = self.JSON
        self.requests: list[str] = []
        self.headers: list[dict[str, str]] = []
        self.redirect_files_to: str | None = None
        index = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                index.requests.append(self.path)
                index.headers.append(dict(self.headers))
                path, _, query = self.path.partition("?")
                if index.redirect_files_to and path.startswith("/files/") and not query:
                    self.send_response(302)
                    self.send_header("Location", f"{index.redirect_files_to}{path}?signed=1")
                    self.end_headers()
                    return
                body, content_type = index.respond(path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                _ = format, args

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/simple"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": POLL_SECONDS}, daemon=True
        ).start()

    def respond(self, path: str) -> tuple[bytes | None, str]:
        if path == f"/simple/{PKG}/":
            names = sorted(p.name for p in self.files.iterdir())
            if self.content_type == self.JSON:
                files = [{"filename": n, "url": f"../../files/{n}", "hashes": {}} for n in names]
                return json.dumps({"name": PKG, "files": files}).encode(), self.JSON
            links = "".join(f'<a href="/files/{n}#sha256=0">{n}</a>' for n in names)
            return f"<html><body>{links}</body></html>".encode(), self.HTML
        if path.startswith("/files/"):
            file = self.files / path.removeprefix("/files/")
            if file.is_file():
                return file.read_bytes(), "application/octet-stream"
        return None, ""


def forget_patch_module(upstream: Upstream) -> None:
    """Undo an in-process `mpat lock`, which the real CLI does in its own process."""
    sys.modules.pop(PATCH_MODULE, None)
    upstream.purge()


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Upstream]:
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    up = Upstream(site)
    yield up
    up.purge()


@pytest.fixture
def index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Index]:
    idx = Index(tmp_path / "index")
    for name in ("UV_DEFAULT_INDEX", "UV_INDEX_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIP_INDEX_URL", idx.url)
    monkeypatch.setenv("MPAT_CACHE_DIR", str(tmp_path / "cache"))
    yield idx
    idx.server.shutdown()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    before = set(sys.modules)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MPAT_COLLECT", raising=False)
    monkeypatch.delenv("MPAT_STRICT", raising=False)
    _registry.reset()
    _hooks.reset()
    _config.reset_caches()
    _fingerprint.reset_caches()
    _lock.reset_caches()
    yield
    _registry.reset()
    _hooks.reset()
    _config.reset_caches()
    _fingerprint.reset_caches()
    _lock.reset_caches()
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)
