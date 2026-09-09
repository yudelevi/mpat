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
    assert cfg.root == tmp_path
    assert cfg.modules == ("a.b",)
    assert cfg.allow == ("ssl.x",)


def test_missing_tool_section(tmp_path):
    write_pyproject(tmp_path)
    cfg = _config.load_config(tmp_path)
    assert cfg.modules == ()
    assert cfg.allow == ()


def test_runtime_config_is_cached(tmp_path):
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["a"]\n')
    assert _config.runtime_config().modules == ("a",)
    write_pyproject(tmp_path, '[tool.mpat]\nmodules = ["b"]\n')
    assert _config.runtime_config().modules == ("a",)
    _config.reset_caches()
    assert _config.runtime_config().modules == ("b",)
