import json
import sys
import textwrap
from pathlib import Path

from mpat import _cli, _lock, _registry


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


def test_lock_rewrites_unreadable_lock(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _lock.lock_path(root).write_text("version = 99\n")
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    captured = capsys.readouterr()
    assert "ignoring unreadable mpat.lock" in captured.err
    assert "+ fakeup.core.greet" in captured.out
    assert _lock.read_lock(_lock.lock_path(root)).version == _lock.LOCK_VERSION


def test_check_still_fails_on_unreadable_lock(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _lock.lock_path(root).write_text("version = 99\n")
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    assert "not supported" in capsys.readouterr().err


def test_check_table_shows_declared_in_without_trailing_padding(
    tmp_path, upstream, monkeypatch, capsys
):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    _cli.main(["check"])
    lines = capsys.readouterr().out.splitlines()
    assert any("app_patches.py" in line for line in lines)
    assert all(line == line.rstrip() for line in lines)


def test_check_with_no_declarations(tmp_path, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _cli.main(["check"]) == _cli.EXIT_OK
    assert "no declarations found" in capsys.readouterr().out


def no_source_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text('import mpat\n\nmpat.watch("math.sqrt")\n')
    return tmp_path


def test_lock_warns_once_per_no_source_entry(tmp_path, monkeypatch, capsys):
    root = no_source_project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    err = capsys.readouterr().err
    assert err.count("math.sqrt: no source available, only the signature is locked") == 1


def test_check_marks_no_source_rows(tmp_path, monkeypatch, capsys):
    root = no_source_project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_OK
    assert "[signature-only]" in capsys.readouterr().out


def test_show_no_source_target(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert _cli.main(["show", "math.sqrt"]) == _cli.EXIT_OK
    out = capsys.readouterr().out
    assert "no_source: True" in out
    assert "(no source available)" in out


def test_check_footer_tells_the_user_to_lock(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    assert "3 unlocked: run 'mpat lock'" in capsys.readouterr().out


def test_check_footer_separates_drift_review_and_stale(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    (root / "app_patches.py").write_text(
        textwrap.dedent(
            """
            from datetime import date
            import mpat

            @mpat.patch("fakeup.core.greet", depends_on=["fakeup.LIMIT"],
                        review_by=date(2020, 1, 1))
            def shout(original, name, punct="!"):
                return original(name, punct).upper()
            """
        )
    )
    upstream.edit("core.py", 'f"hi', 'f"hey')
    _registry.reset()
    sys.modules.pop("app_patches", None)
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "1 drifted: read the upstream change" in out
    assert "1 stale: run 'mpat lock' to prune" in out
    assert "1 due for review" in out


def test_check_groups_rows_by_distribution(tmp_path, upstream, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text(
        'import mpat\n\nmpat.watch("packaging.version.Version")\nmpat.watch("fakeup.core.greet")\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "# packaging" in lines
    assert "" in lines
