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
    assert _cli.main(["check", "--all"]) == _cli.EXIT_OK
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
    assert _cli.main(["check", "--all"]) == _cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert "# packaging" in lines
    assert "" in lines


def test_check_hides_ok_rows_by_default(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "fakeup.core.greet" in out
    assert "fakeup.REGISTRY" not in out
    assert "1 ok (--all lists them)" in out.splitlines()


def test_check_all_lists_ok_rows(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check", "--all"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "fakeup.REGISTRY" in out
    assert "1 ok" in out.splitlines()


def test_check_all_ok_prints_only_the_count(tmp_path, upstream, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text('import mpat\n\nmpat.watch("fakeup.core.greet")\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_OK
    assert capsys.readouterr().out == "1 ok (--all lists them)\n"


CONFIG_WATCHES = """
[[tool.mpat.watch]]
target = "fakeup.core.greet"
depends_on = ["fakeup.LIMIT"]
note = "issue #9"

[[tool.mpat.watch]]
target = "fakeup.REGISTRY"
"""


def config_only_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\n' + CONFIG_WATCHES
    )
    return tmp_path


def test_config_watches_lock_check_and_drift(tmp_path, upstream, capsys):
    root = config_only_project(tmp_path)
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    lock = _lock.read_lock(_lock.lock_path(root))
    assert set(lock.entries) == {"fakeup.core.greet", "fakeup.LIMIT", "fakeup.REGISTRY"}
    assert lock.entries["fakeup.core.greet"].declared_in == "pyproject.toml"
    assert lock.entries["fakeup.core.greet"].role == "watch"
    assert lock.entries["fakeup.LIMIT"].parent == "fakeup.core.greet"
    assert _cli.main(["check"]) == _cli.EXIT_OK
    capsys.readouterr()
    upstream.edit("core.py", 'f"hi', 'f"hey')
    assert _cli.main(["check", "--json"]) == _cli.EXIT_DRIFT
    by_target = {r["target"]: r for r in json.loads(capsys.readouterr().out)}
    assert by_target["fakeup.core.greet"]["status"] == "body"
    assert by_target["fakeup.core.greet"]["declared_in"] == "pyproject.toml"
    assert by_target["fakeup.core.greet"]["note"] == "issue #9"


def test_config_watch_table_prints_pyproject_as_source(tmp_path, upstream, capsys):
    config_only_project(tmp_path)
    _cli.main(["lock"])
    capsys.readouterr()
    assert _cli.main(["check", "--all"]) == _cli.EXIT_OK
    assert "pyproject.toml" in capsys.readouterr().out


def test_config_watch_duplicating_code_is_a_usage_error(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
        '[[tool.mpat.watch]]\ntarget = "fakeup.REGISTRY"\n'
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "fakeup.REGISTRY: declared twice: pyproject.toml and" in err
    assert "app_patches.py" in err


def test_denylisted_config_watch_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\n[[tool.mpat.watch]]\ntarget = "ssl.SSLContext"\n'
    )
    assert _cli.main(["lock"]) == _cli.EXIT_USAGE
    assert "denylist" in capsys.readouterr().err


def test_malformed_config_watch_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\n[[tool.mpat.watch]]\ntarget = 5\n'
    )
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    assert "[[tool.mpat.watch]]" in capsys.readouterr().err


def test_empty_config_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n[tool.mpat]\n')
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    assert "nothing to collect" in capsys.readouterr().err


OVERRIDE_SECTION = '[tool.mpat]\n[[tool.mpat.override]]\ntarget = "fakeup.LIMIT"\nvalue = 500\n'


def test_override_locks_existence_only_and_checks_clean_either_way(tmp_path, upstream, capsys):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n' + OVERRIDE_SECTION)
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    text = _lock.lock_path(tmp_path).read_text()
    assert "track_value = false" in text
    assert "value_repr" not in text
    assert _cli.main(["check"]) == _cli.EXIT_OK
    import mpat

    mpat.apply_overrides()
    import fakeup

    assert fakeup.LIMIT == 500
    assert _cli.main(["check"]) == _cli.EXIT_OK
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert "~ fakeup.LIMIT" not in capsys.readouterr().out
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT_RENAMED = 16")
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    assert "missing" in capsys.readouterr().out


def test_mpat_toml_watch_locks_with_mpat_toml_as_source(tmp_path, upstream, capsys):
    (tmp_path / "mpat.toml").write_text('[[watch]]\ntarget = "fakeup.core.greet"\n')
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    lock = _lock.read_lock(_lock.lock_path(tmp_path))
    assert lock.entries["fakeup.core.greet"].declared_in == "mpat.toml"
    capsys.readouterr()
    assert _cli.main(["check", "--all"]) == _cli.EXIT_OK
    assert "mpat.toml" in capsys.readouterr().out


def test_no_config_file_error_names_both_files(tmp_path, capsys):
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "pyproject.toml" in err
    assert "mpat.toml" in err


def test_empty_mpat_toml_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "mpat.toml").write_text("")
    assert _cli.main(["check"]) == _cli.EXIT_USAGE
    assert "mpat.toml lists no modules" in capsys.readouterr().err


def test_diff_reports_changed_fields(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    capsys.readouterr()
    assert _cli.main(["diff", "fakeup.core.greet"]) == _cli.EXIT_OK
    assert capsys.readouterr().out == "status: ok\n"
    upstream.edit("core.py", 'def greet(name, punct="!"):', 'def greet(name, punct="?"):')
    assert _cli.main(["diff", "fakeup.core.greet"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert out.startswith("status: signature\n")
    assert "signature: (name, punct='!') -> (name, punct='?')\n" in out
    assert "source_hash: sha256:" in out
    assert "kind:" not in out


def test_diff_missing_target_prints_locked_fields(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    upstream.edit("__init__.py", "LIMIT = 16", "CAP = 16")
    capsys.readouterr()
    assert _cli.main(["diff", "fakeup.LIMIT"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert out.startswith("status: missing\n")
    assert "value_repr: 16 -> (missing)\n" in out


def test_diff_unlocked_target_is_a_usage_error(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["diff", "fakeup.core.greet"]) == _cli.EXIT_USAGE
    assert "not in mpat.lock" in capsys.readouterr().err


def test_check_lists_changed_fields_under_the_row(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 2")
    upstream.edit("core.py", 'def greet(name, punct="!")', 'def greet(name, punct="?", loud=False)')
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    lines = capsys.readouterr().out.splitlines()
    assert "    value_repr: 16 -> 2" in lines
    assert any(line.startswith("    signature: (name, punct='!') -> ") for line in lines)
    assert not any("source_hash" in line for line in lines)


def test_check_json_carries_changes(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    capsys.readouterr()
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 2")
    _cli.main(["check", "--json"])
    by_target = {r["target"]: r for r in json.loads(capsys.readouterr().out)}
    assert by_target["fakeup.LIMIT"]["changes"] == ["value_repr: 16 -> 2"]
    assert by_target["fakeup.REGISTRY"]["changes"] == []


def test_check_footer_for_docstring_only_drift(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    upstream.edit(
        "core.py",
        'def greet(name, punct="!"):\n',
        'def greet(name, punct="!"):\n    """Say hi."""\n',
    )
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert any(line.startswith("docstring  patch") for line in out.splitlines())
    assert "1 docstring-only: skim the upstream docs change, then run 'mpat lock'" in out
    assert "drifted" not in out


def test_check_reports_a_deleted_patch_target_as_missing(tmp_path, upstream, monkeypatch, capsys):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text(
        textwrap.dedent(
            """
            import mpat

            @mpat.patch("fakeup.core.Store.add")
            def add(original, self, item):
                return original(self, item)

            mpat.watch("fakeup.core.Store.double")
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _cli.main(["lock"])
    upstream.edit("core.py", "def add(", "def put(")
    upstream.edit("core.py", "def double(", "def twice(")
    _registry.reset()
    sys.modules.pop("app_patches", None)
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("missing    patch      fakeup.core.Store.add") for line in lines)
    assert any(line.startswith("missing    watch      fakeup.core.Store.double") for line in lines)
    assert _cli.main(["check", "--gitlab"]) == _cli.EXIT_DRIFT
    issues = json.loads(capsys.readouterr().out)
    assert [i["check_name"] for i in issues] == ["mpat/missing", "mpat/missing"]


def test_check_against_a_lock_written_before_code_hash(tmp_path, upstream, monkeypatch, capsys):
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    path = _lock.lock_path(root)
    path.write_text(
        "".join(
            line for line in path.read_text().splitlines(keepends=True) if "code_hash" not in line
        )
    )
    capsys.readouterr()
    assert _cli.main(["check", "--all"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "body" not in out
    assert "1 ok" in out.splitlines()


SHARED_DEPENDENCY = """
import mpat


@mpat.patch("fakeup.core.greet", depends_on=["fakeup.LIMIT"])
def shout(original, name, punct="!"):
    return original(name, punct).upper()


@mpat.patch("fakeup.core.Store.add", depends_on=["fakeup.LIMIT"])
def add(original, self, item):
    return original(self, item)


mpat.watch("fakeup.DEBUG")


@mpat.patch("fakeup.core.Store.double", depends_on=["fakeup.DEBUG"])
def double(original, x):
    return original(x)
"""


def shared_dependency_project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text(SHARED_DEPENDENCY)
    return tmp_path


def test_check_lists_every_declaration_using_a_drifted_target(
    tmp_path, upstream, monkeypatch, capsys
):
    root = shared_dependency_project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    _cli.main(["lock"])
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 2")
    upstream.edit("__init__.py", "DEBUG = False", "DEBUG = True")
    capsys.readouterr()
    assert _cli.main(["check"]) == _cli.EXIT_DRIFT
    lines = capsys.readouterr().out.splitlines()
    assert "    used by: app_patches.py:10 (patch fakeup.core.Store.add)" in lines
    assert "    used by: app_patches.py:18 (patch fakeup.core.Store.double)" in lines


def drifted_with_index(tmp_path, upstream, index, monkeypatch) -> Path:
    root = project(tmp_path, upstream)
    monkeypatch.syspath_prepend(str(root))
    upstream.install_dist("1.0")
    _cli.main(["lock"])
    upstream.build_wheel(index.files, "1.0")
    upstream.edit("core.py", 'f"hi {name}{punct}"', 'f"hey {name}{punct}"')
    upstream.install_dist("1.1")
    _registry.reset()
    sys.modules.pop("app_patches", None)
    return root


def test_check_diff_shows_the_upstream_change_under_the_row(
    tmp_path, upstream, index, monkeypatch, capsys
):
    drifted_with_index(tmp_path, upstream, index, monkeypatch)
    capsys.readouterr()
    assert _cli.main(["check", "--diff"]) == _cli.EXIT_DRIFT
    lines = capsys.readouterr().out.splitlines()
    row = next(i for i, line in enumerate(lines) if line.startswith("body       patch"))
    assert lines[row + 1] == (
        "    body[patch] fakeup.core.greet: the upstream body changed (1.0 -> 1.1)"
    )
    assert '    5   | -     return f"hi {name}{punct}"' in lines
    assert '      5 | +     return f"hey {name}{punct}"' in lines
    assert "    info: also used by" not in "\n".join(lines)


def test_check_without_diff_never_fetches(tmp_path, upstream, index, monkeypatch, capsys):
    drifted_with_index(tmp_path, upstream, index, monkeypatch)
    _cli.main(["check"])
    _cli.main(["check", "--json"])
    _cli.main(["check", "--json", "--diff"])
    _cli.main(["check", "--gitlab", "--diff"])
    assert index.requests == []


def test_check_diff_survives_an_unreachable_index(tmp_path, upstream, index, monkeypatch, capsys):
    drifted_with_index(tmp_path, upstream, index, monkeypatch)
    index.server.shutdown()
    index.server.server_close()
    capsys.readouterr()
    assert _cli.main(["check", "--diff"]) == _cli.EXIT_DRIFT
    assert "info: upstream diff unavailable: cannot fetch" in capsys.readouterr().out


def test_diff_target_adds_the_block(tmp_path, upstream, index, monkeypatch, capsys):
    drifted_with_index(tmp_path, upstream, index, monkeypatch)
    capsys.readouterr()
    assert _cli.main(["diff", "fakeup.core.greet"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert out.startswith("status: body\n")
    assert "body[patch] fakeup.core.greet: the upstream body changed (1.0 -> 1.1)" in out


def test_diff_without_target_shows_every_drifted_block(
    tmp_path, upstream, index, monkeypatch, capsys
):
    drifted_with_index(tmp_path, upstream, index, monkeypatch)
    capsys.readouterr()
    assert _cli.main(["diff"]) == _cli.EXIT_DRIFT
    out = capsys.readouterr().out
    assert out.startswith("body[patch] fakeup.core.greet:")
    assert "fakeup.LIMIT" not in out
