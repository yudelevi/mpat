import importlib
import sys
import textwrap
from importlib.machinery import SourceFileLoader

import pytest

import mpat
from mpat import _cli, _hooks, _lock, _registry
from mpat._errors import KindMismatch, TargetNotFound, UpstreamDriftWarning
from tests.test_api import locked_project


def declare(target="fakeup.core.greet", **kwargs):
    @mpat.patch(target, when_imported=True, **kwargs)
    def loud(original, name, punct="!"):
        return original(name, punct).upper()

    return loud


def test_decoration_does_not_import_the_target(upstream):
    declare()
    assert "fakeup" not in sys.modules
    assert _registry.declarations()[0].status == _registry.STATUS_DEFERRED


def test_uninstalled_target_can_be_declared(upstream):
    declare("not_installed_xyz.thing")
    assert [d.target for d in _registry.declarations()] == ["not_installed_xyz.thing"]


def test_applies_on_first_import(upstream):
    declare()
    import fakeup.core

    assert fakeup.core.greet("bob") == "HI BOB!"
    assert _registry.declarations()[0].status == _registry.STATUS_APPLIED


def test_applies_when_only_the_top_level_package_is_imported(upstream):
    declare()
    importlib.import_module("fakeup")
    import fakeup.core

    assert fakeup.core.greet("bob") == "HI BOB!"


def test_already_imported_target_applies_immediately(upstream):
    import fakeup.core

    declare()
    assert fakeup.core.greet("bob") == "HI BOB!"
    assert _registry.declarations()[0].status == _registry.STATUS_APPLIED


def test_module_loader_is_left_untouched(upstream):
    declare()
    import fakeup

    spec = fakeup.__spec__
    assert spec is not None
    assert type(fakeup.__loader__) is SourceFileLoader
    assert spec.loader is fakeup.__loader__


def test_missing_attribute_raises_on_import(upstream):
    declare("fakeup.core.nope")
    with pytest.raises(TargetNotFound, match="nope"):
        import fakeup  # noqa: F401


def test_kind_mismatch_raises_on_import(upstream):
    @mpat.patch("fakeup.core.agreet", when_imported=True)
    def sync_replacement(original, name):
        return "x"

    with pytest.raises(KindMismatch):
        import fakeup  # noqa: F401


def test_until_skips_at_import_time(upstream):
    declare(until=mpat.Probe(lambda: True))
    import fakeup.core

    assert fakeup.core.greet("a") == "hi a!"
    assert _registry.declarations()[0].status == _registry.STATUS_SKIPPED_UNTIL


def test_drift_is_checked_at_import_time(upstream, tmp_path):
    locked_project(upstream, tmp_path)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    declare(note="see issue #1")
    with pytest.warns(UpstreamDriftWarning, match="body.*see issue #1"):
        import fakeup.core
    assert fakeup.core.greet("a") == "HEY A!"


def test_collect_mode_registers_without_hooking(upstream, monkeypatch):
    monkeypatch.setenv(_registry.COLLECT_ENV, "1")
    declare(depends_on=["fakeup.LIMIT"])
    import fakeup.core

    assert fakeup.core.greet("a") == "hi a!"
    decl = _registry.declarations()[0]
    assert decl.depends_on == ("fakeup.LIMIT",)
    assert decl.status == _registry.STATUS_REGISTERED


def test_lock_fingerprints_a_deferred_patch(tmp_path, upstream, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.mpat]\nmodules = ["app_patches"]\n'
    )
    (tmp_path / "app_patches.py").write_text(
        textwrap.dedent(
            """
            import mpat

            @mpat.patch("fakeup.core.greet", when_imported=True, depends_on=["fakeup.LIMIT"])
            def shout(original, name, punct="!"):
                return original(name, punct).upper()
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _cli.main(["lock"]) == _cli.EXIT_OK
    lock = _lock.read_lock(_lock.lock_path(tmp_path))
    assert set(lock.entries) == {"fakeup.core.greet", "fakeup.LIMIT"}
    assert lock.entries["fakeup.core.greet"].fingerprint.source_hash
    import fakeup.core

    assert fakeup.core.greet("a") == "hi a!"


def test_apply_all_imports_and_applies(upstream):
    declare()
    mpat.apply_all()
    assert "fakeup" in sys.modules
    assert sys.modules["fakeup.core"].greet("a") == "HI A!"
    assert _registry.declarations()[0].status == _registry.STATUS_APPLIED


def test_apply_all_with_nothing_pending_is_a_no_op(upstream):
    mpat.apply_all()
    assert "fakeup" not in sys.modules


def test_apply_all_raises_for_an_uninstalled_target(upstream):
    declare("not_installed_xyz.thing")
    with pytest.raises(ModuleNotFoundError):
        mpat.apply_all()


def test_reset_removes_the_finder(upstream):
    declare()
    assert any(isinstance(f, _hooks.PostImportFinder) for f in sys.meta_path)
    _hooks.reset()
    assert not any(isinstance(f, _hooks.PostImportFinder) for f in sys.meta_path)
    import fakeup.core

    assert fakeup.core.greet("a") == "hi a!"


def test_public_surface():
    assert "apply_all" in mpat.__all__
