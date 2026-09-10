from datetime import date
from pathlib import Path

import pytest

from mpat import _config
from mpat._errors import MpatError


def write_pyproject(root: Path, body: str = "") -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n' + body)


def test_no_pyproject(tmp_path):
    assert _config.find_project_root(tmp_path) is None
    assert _config.load_config(tmp_path) is None


def test_walks_up(tmp_path):
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["a.b"]\nallow = ["ssl.x"]\n')
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    cfg = _config.load_config(nested)
    assert cfg is not None
    assert cfg.root == tmp_path
    assert cfg.modules == ("a.b",)
    assert cfg.allow == ("ssl.x",)
    assert cfg.declared


def test_missing_tool_section(tmp_path):
    write_pyproject(tmp_path)
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.modules == ()
    assert cfg.allow == ()
    assert not cfg.declared


def test_runtime_config_is_cached(tmp_path):
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["a"]\n')
    cached = _config.runtime_config()
    assert cached is not None
    assert cached.modules == ("a",)
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["b"]\n')
    still_cached = _config.runtime_config()
    assert still_cached is not None
    assert still_cached.modules == ("a",)
    _config.reset_caches()
    fresh = _config.runtime_config()
    assert fresh is not None
    assert fresh.modules == ("b",)


WATCH_ENTRY = """
[[tool.mpat.watch]]
target = "fakeup.core.greet"
depends_on = ["fakeup.LIMIT"]
review_by = 2026-12-01
note = "see fakeup#1"

[[tool.mpat.watch]]
target = "fakeup.REGISTRY"
"""


def test_parses_watch_entries(tmp_path):
    write_pyproject(tmp_path, "[tool.mpat]\n" + WATCH_ENTRY)
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.modules == ()
    assert cfg.watches == (
        _config.WatchSpec(
            target="fakeup.core.greet",
            depends_on=("fakeup.LIMIT",),
            review_by=date(2026, 12, 1),
            note="see fakeup#1",
        ),
        _config.WatchSpec(target="fakeup.REGISTRY"),
    )


def test_no_watch_entries(tmp_path):
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["a"]\n')
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.watches == ()


@pytest.mark.parametrize(
    "body",
    [
        '[tool.mpat]\nwatch = "fakeup.core.greet"\n',
        "[tool.mpat]\nwatch = [1]\n",
        "[[tool.mpat.watch]]\nnote = 'no target'\n",
        "[[tool.mpat.watch]]\ntarget = 5\n",
        '[[tool.mpat.watch]]\ntarget = "a.b"\ndepends_on = "a.c"\n',
        '[[tool.mpat.watch]]\ntarget = "a.b"\ndepends_on = [1]\n',
        '[[tool.mpat.watch]]\ntarget = "a.b"\nreview_by = "2026-12-01"\n',
        '[[tool.mpat.watch]]\ntarget = "a.b"\nnote = 1\n',
        '[[tool.mpat.watch]]\ntarget = "a.b"\ndepend_on = ["a.c"]\n',
    ],
)
def test_invalid_watch_entry_raises(tmp_path, body):
    write_pyproject(tmp_path, body)
    with pytest.raises(MpatError, match=r"tool\.mpat\.watch"):
        _config.load_config(tmp_path)


def test_invalid_watch_entry_names_its_target(tmp_path):
    write_pyproject(tmp_path, '[[tool.mpat.watch]]\ntarget = "a.b"\nnote = 1\n')
    with pytest.raises(MpatError, match=r"a\.b.*note"):
        _config.load_config(tmp_path)


def test_duplicate_watch_target_raises(tmp_path):
    write_pyproject(
        tmp_path, '[[tool.mpat.watch]]\ntarget = "a.b"\n[[tool.mpat.watch]]\ntarget = "a.b"\n'
    )
    with pytest.raises(MpatError, match=r"a\.b.*twice"):
        _config.load_config(tmp_path)


OVERRIDE_ENTRY = """
[[tool.mpat.override]]
target = "fakeup.LIMIT"
value = 500
note = "fakeup default 16 is too low"
review_by = 2026-12-01

[[tool.mpat.override]]
target = "fakeup.DEBUG"
value = true
"""


def test_parses_override_entries(tmp_path):
    write_pyproject(tmp_path, "[tool.mpat]\n" + OVERRIDE_ENTRY)
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.overrides == (
        _config.OverrideSpec(
            target="fakeup.LIMIT",
            value=500,
            note="fakeup default 16 is too low",
            review_by=date(2026, 12, 1),
        ),
        _config.OverrideSpec(target="fakeup.DEBUG", value=True),
    )
    assert cfg.overrides[1].value is True


@pytest.mark.parametrize("value", ["1.5", '"text"', "false"])
def test_override_accepts_every_toml_scalar(tmp_path, value):
    write_pyproject(tmp_path, f'[[tool.mpat.override]]\ntarget = "a.b"\nvalue = {value}\n')
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert len(cfg.overrides) == 1


@pytest.mark.parametrize(
    "body",
    [
        '[[tool.mpat.override]]\ntarget = "a.b"\n',
        '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = [1]\n',
        '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = 2026-12-01\n',
        '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = { x = 1 }\n',
        '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = 1\nnote = 1\n',
        '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = 1\ndepends_on = ["a.c"]\n',
        "[[tool.mpat.override]]\nvalue = 1\n",
    ],
)
def test_invalid_override_entry_raises(tmp_path, body):
    write_pyproject(tmp_path, body)
    with pytest.raises(MpatError, match=r"tool\.mpat\.override"):
        _config.load_config(tmp_path)


def test_target_in_both_watch_and_override_raises(tmp_path):
    write_pyproject(
        tmp_path,
        '[[tool.mpat.watch]]\ntarget = "a.b"\n[[tool.mpat.override]]\ntarget = "a.b"\nvalue = 1\n',
    )
    with pytest.raises(MpatError, match=r"a\.b.*twice"):
        _config.load_config(tmp_path)


def test_declares_anything(tmp_path):
    write_pyproject(tmp_path, "[tool.mpat]\n")
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert not cfg.declares_anything
    write_pyproject(tmp_path, '[[tool.mpat.override]]\ntarget = "a.b"\nvalue = 1\n')
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.declares_anything
