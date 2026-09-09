from pathlib import Path

from mpat import _config


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


def test_missing_tool_section(tmp_path):
    write_pyproject(tmp_path)
    cfg = _config.load_config(tmp_path)
    assert cfg is not None
    assert cfg.modules == ()
    assert cfg.allow == ()


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
