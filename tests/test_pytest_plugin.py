import textwrap
from pathlib import Path

import pytest

from mpat import _cli

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
