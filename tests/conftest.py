import importlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from mpat import _config, _fingerprint, _lock, _registry

pytest_plugins = ["pytester"]

FIXTURE_ROOT = Path(__file__).parent / "fake_upstream"
PKG = "fakeup"


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

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.pkg / rel
        text = path.read_text()
        assert old in text, f"{old!r} not in {rel}"
        path.write_text(text.replace(old, new))
        self.purge()


@pytest.fixture
def upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Upstream]:
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    up = Upstream(site)
    yield up
    up.purge()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    before = set(sys.modules)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MPAT_COLLECT", raising=False)
    monkeypatch.delenv("MPAT_STRICT", raising=False)
    _registry.reset()
    _config.reset_caches()
    _fingerprint.reset_caches()
    _lock.reset_caches()
    yield
    _registry.reset()
    _config.reset_caches()
    _fingerprint.reset_caches()
    _lock.reset_caches()
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)
