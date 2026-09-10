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
until = "fakeup>=99"

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
            until="fakeup>=99",
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
        '[[tool.mpat.watch]]\ntarget = "a.b"\nuntil = 1\n',
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


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ('[tool.mpat]\nmodules = "a"\n', "modules must be a list of str"),
        ("[tool.mpat]\nmodules = [1]\n", "modules must be a list of str"),
        ('[tool.mpat]\nallow = "x"\n', "allow must be a list of str"),
        ("[tool.mpat]\nbogus = 1\n", "unknown key 'bogus'"),
        ("[tool]\nmpat = 5\n", "tool.mpat must be a table"),
    ],
)
def test_invalid_top_level_section_raises(tmp_path, body, problem):
    write_pyproject(tmp_path, body)
    with pytest.raises(MpatError, match=problem):
        _config.load_config(tmp_path)


def test_top_level_tool_must_be_a_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text('tool = 5\n[project]\nname = "x"\n')
    with pytest.raises(MpatError, match="tool must be a table"):
        _config.load_config(tmp_path)


def write_mpat_toml(root: Path, body: str = "") -> None:
    (root / "mpat.toml").write_text(body)


def test_mpat_toml_alone_is_the_config(tmp_path):
    write_mpat_toml(
        tmp_path, 'modules = ["a.b"]\nallow = ["ssl.x"]\n' + WATCH_ENTRY.replace("tool.mpat.", "")
    )
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.root == tmp_path
    assert cfg.path == tmp_path / "mpat.toml"
    assert cfg.declared
    assert cfg.modules == ("a.b",)
    assert cfg.allow == ("ssl.x",)
    assert [w.target for w in cfg.watches] == ["fakeup.core.greet", "fakeup.REGISTRY"]


def test_empty_mpat_toml_is_declared_but_declares_nothing(tmp_path):
    write_mpat_toml(tmp_path)
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.declared
    assert not cfg.declares_anything


def test_mpat_toml_wins_over_pyproject_in_the_same_directory(tmp_path):
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["from_pyproject"]\n')
    write_mpat_toml(tmp_path, 'modules = ["from_mpat_toml"]\n')
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.modules == ("from_mpat_toml",)
    assert cfg.path == tmp_path / "mpat.toml"


def test_pyproject_config_records_its_path(tmp_path):
    write_pyproject(tmp_path, "[tool.mpat]\n")
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.path == tmp_path / "pyproject.toml"


def test_nearest_config_file_wins_when_walking_up(tmp_path):
    write_mpat_toml(tmp_path, 'modules = ["outer"]\n')
    nested = tmp_path / "svc"
    nested.mkdir()
    write_pyproject(nested, '[tool.mpat]\nmodules = ["inner"]\n')
    cfg = _config.load_config(nested / "deeper_missing_dir_parent")
    assert cfg is not None
    assert cfg.root == nested
    assert cfg.modules == ("inner",)


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ('modules = "a"\n', r"mpat\.toml: modules must be a list of str"),
        ("bogus = 1\n", r"mpat\.toml: unknown key 'bogus'"),
        (
            '[[watch]]\ntarget = "a.b"\nnote = 1\n',
            r"mpat\.toml: \[\[watch\]\] #0 \(a\.b\): note must be str",
        ),
        ("[tool.mpat]\n", r"mpat\.toml: unknown key 'tool'"),
    ],
)
def test_invalid_mpat_toml_names_the_file(tmp_path, body, problem):
    write_mpat_toml(tmp_path, body)
    with pytest.raises(MpatError, match=problem):
        _config.load_config(tmp_path)
