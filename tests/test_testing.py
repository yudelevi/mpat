import textwrap
from pathlib import Path

import pytest

from mpat import _cli
from tests.conftest import forget_patch_module


def project(tmp_path: Path, until: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
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
    (tmp_path / "test_generated.py").write_text(
        "from mpat.testing import test_upstream_drift, test_patch_still_needed\n"
    )
    return tmp_path


def run_generated(pytester: pytest.Pytester) -> pytest.RunResult:
    return pytester.runpytest("test_generated.py", "-p", "no:cacheprovider", "-v")


def test_generated_tests_pass_when_locked(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: False)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    result = run_generated(pytester)
    result.assert_outcomes(passed=2)


def test_generated_tests_fail_on_drift_and_fixed_upstream(pytester, upstream, monkeypatch):
    root = project(pytester.path, "mpat.Probe(lambda: True)")
    monkeypatch.syspath_prepend(str(root))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    upstream.edit("core.py", 'f"hi', 'f"hey')
    result = run_generated(pytester)
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["*body*drop me*", "*upstream fixed*drop me*"])


def test_unconfigured_project_fails_loudly(pytester):
    (pytester.path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (pytester.path / "test_generated.py").write_text(
        "from mpat.testing import test_upstream_drift, test_patch_still_needed\n"
    )
    result = pytester.runpytest("test_generated.py", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1, skipped=1)
    result.stdout.fnmatch_lines(["*[[]tool.mpat[]] declares nothing*"])


def test_generated_module_leaves_patches_applied(pytester, upstream, monkeypatch):
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
    result = pytester.runpytest("-p", "no:cacheprovider", "-v")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(["test_generated.py::test_upstream_drift*PASSED*"])
    result.stdout.no_fnmatch_line("*mpat::drift*")
