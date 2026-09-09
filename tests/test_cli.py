import json
import textwrap
from pathlib import Path

from mpat import _cli, _lock


def project(tmp_path: Path, upstream) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text(
        textwrap.dedent(
            """
            from datetime import date
            import mpat

            @mpat.patch("fakeup.core.greet", depends_on=["fakeup.LIMIT"], note="issue #7",
                        review_by=date(2020, 1, 1))
            def shout(original, name, punct="!"):
                return original(name, punct).upper()

            mpat.watch("fakeup.REGISTRY")
            """
        )
    )
    return tmp_path


def test_lock_writes_file(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    lock = _lock.read_lock(_lock.lock_path(root))
    assert set(lock.entries) == {"fakeup.core.greet", "fakeup.LIMIT", "fakeup.REGISTRY"}
    out = capsys.readouterr().out
    assert "+ fakeup.core.greet" in out
    import fakeup.core

    assert fakeup.core.greet("a") == "hi a!"


def test_check_reports_unlocked_then_ok(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    assert "unlocked" in capsys.readouterr().out
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "review" in out
    assert "issue #7" in out


def test_check_json_and_drift(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 2")
    assert _cli.main(["check", "--json"]) == _cli.EXIT_DRIFT
    data = json.loads(capsys.readouterr().out)
    by_target = {r["target"]: r for r in data}
    assert by_target["fakeup.LIMIT"]["status"] == "value"
    assert by_target["fakeup.LIMIT"]["role"] == "depends_on"


def test_lock_prints_changes(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    upstream.edit("core.py", 'f"hi', 'f"hey')
    _cli.main(["lock"])
    assert "~ fakeup.core.greet" in capsys.readouterr().out


def test_show(tmp_path, upstream, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _cli.main(["show", "fakeup.core.greet"]) == _cli.EXIT_OK
    out = capsys.readouterr().out
    assert "sha256:" in out
    assert "def greet(" in out


def test_no_pyproject_is_usage_error(tmp_path, capsys):
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    assert "pyproject.toml" in capsys.readouterr().err
