import os
import sys
import textwrap
from pathlib import Path

import pytest

from mpat import _cli, _registry
from tests.conftest import forget_patch_module

PYPROJECT = '[project]\nname = "x"\n'
MPAT_SECTION = '[tool.mpat]\nmodules = ["app_patches"]\n'


def project(tmp_path: Path, until: str, *, section: str = MPAT_SECTION) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT + section)
    (tmp_path / "app_patches.py").write_text(
        textwrap.dedent(
            f"""
            import mpat

            @mpat.patch("fakeup.core.greet", until={until}, note="drop me")
            def shout(original, name, punct="!"):
                return original(name, punct).upper()
            """
        )
    )
    return tmp_path


def local_test(tmp_path: Path) -> None:
    (tmp_path / "test_local.py").write_text("def test_ok():\n    pass\n")


def run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest("-p", "no:cacheprovider", *args)


def test_plugin_collects_generated_tests_without_boilerplate(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "mpat::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat::still-needed[[]fakeup.core.greet[]] PASSED*",
        ]
    )


def test_drift_item_fails_with_classification_and_note(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    upstream.edit("core.py", 'f"hi', 'f"hey')
    result = run(pytester)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(
        ["*drift[[]fakeup.core.greet[]]*", "fakeup.core.greet: body. drop me"]
    )


def test_still_needed_item_fails_once_upstream_is_fixed(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: True)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(
        [
            "*still-needed[[]fakeup.core.greet[]]*",
            "fakeup.core.greet: upstream fixed, delete this patch. drop me",
        ]
    )


def test_no_tool_section_is_inert(pytester):
    (pytester.path / "pyproject.toml").write_text(PYPROJECT)
    result = run(pytester, "-W", "error")
    result.assert_outcomes()
    result.stdout.no_fnmatch_line("*mpat::*")
    result.stdout.no_fnmatch_line("*warnings summary*")


def test_empty_modules_collects_one_failing_item(pytester):
    (pytester.path / "pyproject.toml").write_text(PYPROJECT + "[tool.mpat]\n")
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*unconfigured*", "*no [[]tool.mpat[]] modules found; add modules to pyproject.toml"]
    )


def test_no_mpat_flag_disables_collection(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "--no-mpat")
    result.assert_outcomes()


def test_ini_option_disables_collection(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    (root / "pyproject.toml").write_text(
        PYPROJECT + MPAT_SECTION + "[tool.pytest.ini_options]\nmpat = false\n"
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester)
    result.assert_outcomes()


def test_explicit_testing_module_is_not_duplicated(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    (root / "test_generated.py").write_text(
        "from mpat.testing import test_upstream_drift, test_patch_still_needed\n"
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.no_fnmatch_line("*mpat::drift*")


def test_keyword_selection_applies_to_generated_items(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-k", "drift")
    result.assert_outcomes(passed=1, deselected=1)


def test_marker_selection_applies_to_generated_items(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-m", "mpat", "-W", "error")
    result.assert_outcomes(passed=2)


def test_raising_until_reports_its_traceback(pytester, upstream, monkeypatch):
    root = pytester.path
    (root / "pyproject.toml").write_text(PYPROJECT + MPAT_SECTION)
    (root / "app_patches.py").write_text(
        textwrap.dedent(
            """
            import mpat

            def boom():
                raise RuntimeError("kaput")

            @mpat.patch("fakeup.core.greet", until=mpat.Probe(boom), note="drop me")
            def shout(original, name, punct="!"):
                return original(name, punct).upper()
            """
        )
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(['*raise RuntimeError("kaput")*', "E*RuntimeError: kaput"])


def test_file_argument_does_not_add_generated_items(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    local_test(root)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "test_local.py")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*mpat::*")


def test_nodeid_argument_does_not_add_generated_items(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    local_test(root)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "test_local.py::test_ok")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*mpat::*")


def test_directory_argument_adds_generated_items(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    local_test(root)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, ".")
    result.assert_outcomes(passed=3)


def test_generated_items_are_counted_and_listed(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    local_test(root)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "--collect-only")
    result.stdout.fnmatch_lines(
        [
            "collected 3 items",
            "*<MpatCollector mpat>*",
            "*<DriftItem drift[[]fakeup.core.greet[]]>*",
            "*<StillNeededItem still-needed[[]fakeup.core.greet[]]>*",
        ]
    )


def run_with_workers(
    pytester: pytest.Pytester, upstream, monkeypatch, *args: str
) -> pytest.RunResult:
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), str(upstream.root)]))
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_xdist_workers_collect_the_same_generated_items(pytester, upstream, monkeypatch):
    result = run_with_workers(pytester, upstream, monkeypatch, "-n", "2")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*2 workers [[]2 items[]]*"])
    result.stdout.no_fnmatch_line("*Different tests were collected*")


def test_xdist_loadfile_runs_the_generated_items(pytester, upstream, monkeypatch):
    result = run_with_workers(pytester, upstream, monkeypatch, "-n", "2", "--dist", "loadfile")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*2 workers [[]2 items[]]*"])
    result.stdout.no_fnmatch_line("*Different tests were collected*")


def test_items_still_run_without_the_private_collect_helper(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    (root / "hide_collect_one_node.py").write_text(
        "import _pytest.runner\n\ndel _pytest.runner.collect_one_node\n"
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), str(upstream.root)]))
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "hide_collect_one_node", "-v"
    )
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(
        [
            "*collected 0 items",
            "mpat::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat::still-needed[[]fakeup.core.greet[]] PASSED*",
        ]
    )


def test_collection_applies_the_patches_for_later_tests(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    (root / "test_patched_behaviour.py").write_text(
        textwrap.dedent(
            """
            import fakeup.core


            def test_patch_is_active():
                assert fakeup.core.greet("world") == "HI WORLD!"
            """
        )
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    forget_patch_module(upstream)
    result = run(pytester, "-v")
    result.assert_outcomes(passed=3)


def test_collection_applies_when_imported_patches_for_later_tests(pytester, upstream, monkeypatch):
    root = pytester.path
    (root / "pyproject.toml").write_text(PYPROJECT + MPAT_SECTION)
    (root / "app_patches.py").write_text(
        textwrap.dedent(
            """
            import mpat

            @mpat.patch("fakeup.core.greet", when_imported=True)
            def shout(original, name, punct="!"):
                return original(name, punct).upper()
            """
        )
    )
    (root / "test_patched_behaviour.py").write_text(
        textwrap.dedent(
            """
            import fakeup.core


            def test_patch_is_active():
                assert fakeup.core.greet("world") == "HI WORLD!"
            """
        )
    )
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    forget_patch_module(upstream)
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)


CONFIG_WATCH = '[[tool.mpat.watch]]\ntarget = "fakeup.core.greet"\nnote = "issue #9"\n'


def test_config_watches_alone_are_collected(pytester, upstream):
    (pytester.path / "pyproject.toml").write_text(PYPROJECT + "[tool.mpat]\n" + CONFIG_WATCH)
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-v")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["mpat::drift[[]fakeup.core.greet[]] PASSED*"])
    result.stdout.no_fnmatch_line("*unconfigured*")
    upstream.edit("core.py", 'f"hi', 'f"hey')
    result = run(pytester)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["fakeup.core.greet: body. issue #9"])


def test_config_watch_duplicating_code_fails_collection(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    (root / "pyproject.toml").write_text(PYPROJECT + MPAT_SECTION + CONFIG_WATCH)
    monkeypatch.syspath_prepend(str(root))
    result = run(pytester)
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*declared twice: pyproject.toml and*app_patches.py*"])


APP_ASSIGNS_LIMIT = "import fakeup\n\nfakeup.LIMIT = 500\n"
CONFTEST_IMPORTS_APP = "import myapp\n"
TEST_APP_VALUE = "import fakeup\n\n\ndef test_app_value():\n    assert fakeup.LIMIT == 500\n"


def issue_5_project(root: Path, watch_kwargs: str) -> None:
    (root / "pyproject.toml").write_text(PYPROJECT + MPAT_SECTION)
    (root / "app_patches.py").write_text(
        f'import mpat\n\nmpat.watch("fakeup.LIMIT"{watch_kwargs})\n'
    )
    (root / "myapp.py").write_text(APP_ASSIGNS_LIMIT)
    (root / "conftest.py").write_text(CONFTEST_IMPORTS_APP)
    (root / "test_app.py").write_text(TEST_APP_VALUE)


def test_watch_with_value_tracked_is_red_after_the_app_assigns_it(pytester, upstream, monkeypatch):
    issue_5_project(pytester.path, "")
    monkeypatch.syspath_prepend(str(pytester.path))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    forget_patch_module(upstream)
    result = run(pytester)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["fakeup.LIMIT: value."])


def test_watch_without_value_tracking_is_green_in_both_paths(pytester, upstream, monkeypatch):
    issue_5_project(pytester.path, ", track_value=False")
    monkeypatch.syspath_prepend(str(pytester.path))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert _cli.main(["check"]) == _cli.EXIT_OK
    forget_patch_module(upstream)
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["mpat::drift[[]fakeup.LIMIT[]] PASSED*"])


def test_config_override_is_green_in_both_paths(pytester, upstream, monkeypatch):
    root = pytester.path
    (root / "pyproject.toml").write_text(
        PYPROJECT + '[tool.mpat]\n[[tool.mpat.override]]\ntarget = "fakeup.LIMIT"\nvalue = 500\n'
    )
    (root / "conftest.py").write_text("import mpat\n\nmpat.apply_overrides()\n")
    (root / "test_app.py").write_text(TEST_APP_VALUE)
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert _cli.main(["check"]) == _cli.EXIT_OK
    upstream.purge()
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["mpat::drift[[]fakeup.LIMIT[]] PASSED*"])


def watch_project(root: Path, until: str) -> None:
    (root / "pyproject.toml").write_text(PYPROJECT + MPAT_SECTION)
    (root / "app_patches.py").write_text(
        f'import mpat\n\nmpat.watch("fakeup.LIMIT", until={until}, note="shim in app.py")\n'
    )


def test_watch_with_until_gets_a_still_needed_item(pytester, upstream, monkeypatch):
    watch_project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(pytester.path))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["mpat::still-needed[[]fakeup.LIMIT[]] PASSED*"])


def test_watch_still_needed_fails_once_upstream_is_fixed(pytester, upstream, monkeypatch):
    watch_project(pytester.path, "mpat.Probe(lambda: True)")
    monkeypatch.syspath_prepend(str(pytester.path))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert _cli.main(["check"]) == _cli.EXIT_OK
    result = run(pytester)
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(
        [
            "*still-needed[[]fakeup.LIMIT[]]*",
            "fakeup.LIMIT: upstream fixed, delete this watch. shim in app.py",
        ]
    )


CONFIG_UNTIL_WATCHES = """
[[tool.mpat.watch]]
target = "fakeup.LIMIT"
until = "packaging>9999"

[[tool.mpat.watch]]
target = "fakeup.core.greet"
until = "probes.fixed"
note = "shim in app.py"
"""


def test_config_watch_until_strings(pytester, upstream, monkeypatch):
    root = pytester.path
    (root / "pyproject.toml").write_text(PYPROJECT + "[tool.mpat]\n" + CONFIG_UNTIL_WATCHES)
    (root / "probes.py").write_text("def fixed():\n    return True\n")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    assert _cli.main(["check"]) == _cli.EXIT_OK
    result = run(pytester, "-v")
    result.assert_outcomes(passed=3, failed=1)
    result.stdout.fnmatch_lines(
        [
            "mpat::still-needed[[]fakeup.LIMIT[]] PASSED*",
            "mpat::still-needed[[]fakeup.core.greet[]] FAILED*",
            "fakeup.core.greet: upstream fixed, delete this watch. shim in app.py",
        ]
    )


ROOT_PYTEST_SECTION = "[tool.pytest.ini_options]\n"
SERVICE_TARGETS = {"api": "fakeup.core.greet", "web": "fakeup.core.Store.add"}
ROOT_TARGET = "fakeup.core.decorated"


def patch_module(path: Path, target: str) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            import mpat

            @mpat.patch("{target}", until=mpat.Probe(lambda: False), note="drop me")
            def repl(original, *args, **kwargs):
                return original(*args, **kwargs)
            """
        )
    )


def service(root: Path, name: str) -> Path:
    path = root / "services" / name
    (path / "tests").mkdir(parents=True)
    (path / "pyproject.toml").write_text(PYPROJECT + f'[tool.mpat]\nmodules = ["{name}_patches"]\n')
    patch_module(path / f"{name}_patches.py", SERVICE_TARGETS[name])
    (path / "tests" / f"test_{name}.py").write_text("def test_ok():\n    pass\n")
    return path


def lock_in(path: Path, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run `mpat lock` for one project and undo its in-process side effects, as a
    subprocess run would have: the next project must not see this one's registry."""
    monkeypatch.syspath_prepend(str(path))
    with monkeypatch.context() as m:
        m.chdir(path)
        assert _cli.main(["lock"]) == _cli.EXIT_OK
    sys.modules.pop(module)
    _registry.reset()


def monorepo(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *names: str) -> Path:
    root = pytester.path
    (root / "pyproject.toml").write_text(PYPROJECT + ROOT_PYTEST_SECTION)
    for name in names:
        lock_in(service(root, name), f"{name}_patches", monkeypatch)
    return root


def test_service_below_rootdir_is_collected_from_the_root(pytester, upstream, monkeypatch):
    monorepo(pytester, monkeypatch, "api")
    result = run(pytester, "-v", "services/api/tests")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(
        [
            f"rootdir: {pytester.path}*",
            "mpat[[]services/api[]]::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat[[]services/api[]]::still-needed[[]fakeup.core.greet[]] PASSED*",
        ]
    )


def test_each_service_in_one_invocation_gets_its_own_collector(pytester, upstream, monkeypatch):
    monorepo(pytester, monkeypatch, "api", "web")
    result = run(pytester, "-v", "services/api/tests", "services/web/tests")
    result.assert_outcomes(passed=6)
    result.stdout.fnmatch_lines(
        [
            "mpat[[]services/api[]]::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat[[]services/api[]]::still-needed[[]fakeup.core.greet[]] PASSED*",
            "mpat[[]services/web[]]::drift[[]fakeup.core.Store.add[]] PASSED*",
            "mpat[[]services/web[]]::still-needed[[]fakeup.core.Store.add[]] PASSED*",
        ]
    )
    result.stdout.no_fnmatch_line("mpat[[]services/api[]]::*[[]fakeup.core.Store.add[]]*")
    result.stdout.no_fnmatch_line("mpat[[]services/web[]]::*[[]fakeup.core.greet[]]*")


def test_service_lockfile_is_read_from_the_service_directory(pytester, upstream, monkeypatch):
    root = monorepo(pytester, monkeypatch, "api")
    (root / "services" / "api" / "mpat.lock").unlink()
    result = run(pytester, "services/api/tests")
    result.assert_outcomes(failed=1, passed=2)
    result.stdout.fnmatch_lines(["*drift[[]fakeup.core.greet[]]*", "fakeup.core.greet: unlocked*"])


def test_same_service_named_twice_is_collected_once(pytester, upstream, monkeypatch):
    monorepo(pytester, monkeypatch, "api")
    result = run(pytester, "services/api/tests", "services/api")
    result.assert_outcomes(passed=3)


def test_rootdir_section_and_service_section_are_both_collected(pytester, upstream, monkeypatch):
    root = monorepo(pytester, monkeypatch, "api")
    (root / "pyproject.toml").write_text(
        PYPROJECT + ROOT_PYTEST_SECTION + '[tool.mpat]\nmodules = ["root_patches"]\n'
    )
    patch_module(root / "root_patches.py", ROOT_TARGET)
    lock_in(root, "root_patches", monkeypatch)
    result = run(pytester, "-v", "services/api/tests")
    result.assert_outcomes(passed=5)
    result.stdout.fnmatch_lines(
        [
            "mpat::drift[[]fakeup.core.decorated[]] PASSED*",
            "mpat::still-needed[[]fakeup.core.decorated[]] PASSED*",
            "mpat[[]services/api[]]::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat[[]services/api[]]::still-needed[[]fakeup.core.greet[]] PASSED*",
        ]
    )
    result.stdout.no_fnmatch_line("mpat::*[[]fakeup.core.greet[]]*")


def test_rootdir_without_section_and_no_arguments_is_inert(pytester, upstream, monkeypatch):
    monorepo(pytester, monkeypatch, "api")
    result = run(pytester, "-W", "error")
    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line("*mpat*::*")


def test_subdirectory_argument_keeps_the_single_project_node_ids(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    (root / "tests").mkdir()
    local_test(root / "tests")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run(pytester, "-v", "tests")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(
        [
            "mpat::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat::still-needed[[]fakeup.core.greet[]] PASSED*",
        ]
    )


def config_watch_service(
    root: Path, name: str, target: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    path = root / "services" / name
    (path / "tests").mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        PYPROJECT + f'[tool.mpat]\n[[tool.mpat.watch]]\ntarget = "{target}"\n'
    )
    (path / "tests" / f"test_{name}.py").write_text("def test_ok():\n    pass\n")
    with monkeypatch.context() as m:
        m.chdir(path)
        assert _cli.main(["lock"]) == _cli.EXIT_OK
    _registry.reset()
    return path


def test_config_watches_in_two_services_are_scoped_to_their_own_service(
    pytester, upstream, monkeypatch
):
    root = pytester.path
    (root / "pyproject.toml").write_text(PYPROJECT + ROOT_PYTEST_SECTION)
    config_watch_service(root, "api", "fakeup.core.greet", monkeypatch)
    config_watch_service(root, "web", "fakeup.core.Store.add", monkeypatch)
    monkeypatch.chdir(root)
    result = run(pytester, "-v", "services/api/tests", "services/web/tests")
    result.assert_outcomes(passed=4)
    result.stdout.fnmatch_lines(
        [
            "mpat[[]services/api[]]::drift[[]fakeup.core.greet[]] PASSED*",
            "mpat[[]services/web[]]::drift[[]fakeup.core.Store.add[]] PASSED*",
        ]
    )
    result.stdout.no_fnmatch_line("mpat[[]services/web[]]::*[[]fakeup.core.greet[]]*")
    result.stdout.no_fnmatch_line("mpat[[]services/api[]]::*[[]fakeup.core.Store.add[]]*")
