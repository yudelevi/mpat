import asyncio
import importlib
import logging
import sys
import textwrap
import warnings
from typing import Any

import pytest

import mpat
from mpat import _api, _lock, _registry
from mpat._errors import (
    AlreadyPatched,
    ForbiddenTarget,
    KindMismatch,
    TargetNotFound,
    UnsupportedTarget,
    UpstreamDriftError,
    UpstreamDriftWarning,
)
from mpat._fingerprint import fingerprint
from mpat._registry import Declaration
from mpat._targets import resolve


def test_patch_module_function(upstream):
    import fakeup.core

    @mpat.patch("fakeup.core.greet")
    def loud(original, name, punct="!"):
        return original(name, punct).upper()

    assert fakeup.core.greet("bob") == "HI BOB!"
    patched: Any = fakeup.core.greet
    assert patched.__mpat_target__ == "fakeup.core.greet"
    assert patched.__wrapped__ is patched.__mpat_original__
    assert loud(lambda n, p: "x", "a") == "X"


def test_patch_by_object(upstream):
    import fakeup.core

    @mpat.patch(fakeup.core.greet)
    def quiet(original, name, punct="!"):
        return original(name, punct).lower()

    assert fakeup.core.greet("BOB") == "hi bob!"


def test_patch_method_static_and_classmethod(upstream):
    import fakeup.core as c

    @mpat.patch("fakeup.core.Store.add")
    def add_twice(original, self, item):
        original(self, item)
        return original(self, item)

    @mpat.patch("fakeup.core.Store.double")
    def triple(original, x):
        return original(x) + x

    @mpat.patch("fakeup.core.Store.build")
    def build_with_item(original, cls):
        store = original(cls)
        store.add("seed")
        return store

    s = c.Store()
    assert s.add("a") == 2
    assert c.Store.double(2) == 6
    assert c.Store.build().items == ["seed", "seed"]
    assert isinstance(vars(c.Store)["double"], staticmethod)
    assert isinstance(vars(c.Store)["build"], classmethod)


def test_async_and_generators(upstream):
    import fakeup.core as c

    @mpat.patch("fakeup.core.agreet")
    async def a(original, name):
        return (await original(name)) + "?"

    @mpat.patch("fakeup.core.count")
    def g(original, n):
        yield from original(n + 1)

    @mpat.patch("fakeup.core.acount")
    async def ag(original, n):
        async for i in original(n):
            yield i * 10

    async def run():
        return await c.agreet("x"), [i async for i in c.acount(2)]

    assert asyncio.run(run()) == ("hi x?", [0, 10])
    assert list(c.count(1)) == [0, 1]


def test_kind_mismatch(upstream):
    with pytest.raises(KindMismatch):

        @mpat.patch("fakeup.core.agreet")
        def sync_replacement(original, name):
            return "x"


def test_unsupported_and_missing_and_forbidden(upstream):
    with pytest.raises(UnsupportedTarget):
        mpat.patch("fakeup.core.Store.size")
    with pytest.raises(TargetNotFound):
        mpat.patch("fakeup.core.nope")
    with pytest.raises(ForbiddenTarget):
        mpat.patch("ssl.SSLContext.wrap_socket")


def test_allow_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.mpat]\nallow = ["hashlib.new"]\n')
    mpat.watch("hashlib.new")


def test_invalid_on_drift(upstream):
    with pytest.raises(ValueError):
        mpat.patch("fakeup.core.greet", on_drift="explode")


def test_until_skips(upstream, caplog):
    import fakeup.core

    with caplog.at_level(logging.INFO, logger="mpat"):

        @mpat.patch("fakeup.core.greet", until=mpat.Probe(lambda: True), note="upstream fixed it")
        def never(original, name, punct="!"):
            return "never"

    assert fakeup.core.greet("a") == "hi a!"
    assert "upstream fixed it" in caplog.text
    assert _registry.declarations()[0].status == _registry.STATUS_SKIPPED_UNTIL


def test_collect_mode_registers_only(upstream, monkeypatch):
    import fakeup.core

    monkeypatch.setenv(_registry.COLLECT_ENV, "1")

    @mpat.patch("fakeup.core.greet", depends_on=["fakeup.LIMIT"], note="n")
    def p(original, name, punct="!"):
        return "x"

    mpat.watch("fakeup.REGISTRY")
    assert fakeup.core.greet("a") == "hi a!"
    decls = {d.target: d for d in _registry.declarations()}
    assert decls["fakeup.core.greet"].depends_on == ("fakeup.LIMIT",)
    assert decls["fakeup.core.greet"].declared_in == __file__
    assert decls["fakeup.REGISTRY"].role == _registry.ROLE_WATCH


def test_second_declaration_same_target_raises(upstream):
    @mpat.patch("fakeup.core.greet")
    def one(original, name, punct="!"):
        return "1"

    with pytest.raises(AlreadyPatched):

        @mpat.patch("fakeup.core.greet")
        def two(original, name, punct="!"):
            return "2"


def test_alias_of_same_object_raises(upstream):
    @mpat.patch("fakeup.core.greet")
    def one(original, name, punct="!"):
        return "1"

    with pytest.raises(AlreadyPatched):

        @mpat.patch("fakeup.greet_alias")
        def two(original, name, punct="!"):
            return "2"


def test_reload_reapplies_without_nesting(upstream, tmp_path, monkeypatch):
    app = tmp_path / "app_patches.py"
    app.write_text(
        textwrap.dedent(
            """
            import mpat

            @mpat.patch("fakeup.core.greet")
            def shout(original, name, punct="!"):
                return original(name, punct) + "!!"
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    import fakeup.core

    module = importlib.import_module("app_patches")
    assert fakeup.core.greet("a") == "hi a!!!"
    importlib.reload(module)
    assert fakeup.core.greet("a") == "hi a!!!"
    assert len(_registry.declarations()) == 1
    sys.modules.pop("app_patches", None)


def locked_project(
    upstream, tmp_path, target="fakeup.core.greet", role=_registry.ROLE_PATCH, **decl_kwargs
):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    decl = Declaration(
        target=target,
        role=role,
        depends_on=(),
        until=None,
        on_drift="warn",
        review_by=None,
        note="see issue #1",
        declared_in=str(tmp_path / "p.py"),
        identity=(str(tmp_path / "p.py"), "x"),
        **decl_kwargs,
    )
    _lock.write_lock(_lock.lock_path(tmp_path), _lock.build_lock([decl], root=tmp_path))
    _lock.reset_caches()


def test_drift_warns_and_applies_by_default(upstream, tmp_path):
    locked_project(upstream, tmp_path)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    import fakeup.core

    with pytest.warns(UpstreamDriftWarning, match="body.*see issue #1"):

        @mpat.patch("fakeup.core.greet", note="see issue #1")
        def p(original, name, punct="!"):
            return "patched"

    assert fakeup.core.greet("a") == "patched"


def test_drift_skip(upstream, tmp_path):
    locked_project(upstream, tmp_path)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    import fakeup.core

    with pytest.warns(UpstreamDriftWarning):

        @mpat.patch("fakeup.core.greet", on_drift="skip")
        def p(original, name, punct="!"):
            return "patched"

    assert fakeup.core.greet("a") == "hey a!"
    assert _registry.declarations()[0].status == _registry.STATUS_SKIPPED_DRIFT


def test_drift_raise_and_strict_env(upstream, tmp_path, monkeypatch):
    locked_project(upstream, tmp_path)
    upstream.edit("core.py", 'f"hi', 'f"hey')
    with pytest.raises(UpstreamDriftError):

        @mpat.patch("fakeup.core.greet", on_drift="raise")
        def p(original, name, punct="!"):
            return "patched"

    monkeypatch.setenv(_registry.STRICT_ENV, "1")
    with pytest.raises(UpstreamDriftError):
        mpat.watch("fakeup.core.greet")


def test_no_drift_is_silent(upstream, tmp_path):
    locked_project(upstream, tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        @mpat.patch("fakeup.core.greet")
        def p(original, name, punct="!"):
            return "patched"

        mpat.watch("fakeup.core.greet")


def test_watch_registers_and_warns_on_drift(upstream, tmp_path):
    locked_project(upstream, tmp_path, target="fakeup.LIMIT")
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 1")
    with pytest.warns(UpstreamDriftWarning, match="value"):
        mpat.watch("fakeup.LIMIT", note="n")
    d = _registry.declarations()[0]
    assert d.role == _registry.ROLE_WATCH
    assert d.status == _registry.STATUS_WATCHED


def test_watch_track_value_false_is_silent_when_only_the_value_changed(upstream, tmp_path):
    locked_project(upstream, tmp_path, target="fakeup.LIMIT", track_value=False)
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 1")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mpat.watch("fakeup.LIMIT", track_value=False)
    d = _registry.declarations()[0]
    assert d.track_value is False
    assert d.status == _registry.STATUS_WATCHED


def test_watch_track_value_false_still_warns_on_kind_change(upstream, tmp_path):
    locked_project(upstream, tmp_path, target="fakeup.LIMIT", track_value=False)
    upstream.edit("__init__.py", "LIMIT = 16", "def LIMIT():\n    return 16")
    with pytest.warns(UpstreamDriftWarning, match="moved"):
        mpat.watch("fakeup.LIMIT", track_value=False)


def test_watch_defaults_to_tracking_value(upstream):
    mpat.watch("fakeup.LIMIT")
    assert _registry.declarations()[0].track_value is True


def test_watch_unsupported_targets_are_fine(upstream):
    mpat.watch("fakeup.core.Store.size")
    mpat.watch("fakeup.core.Store")
    mpat.watch("fakeup.REGISTRY")


def test_public_surface():
    assert {"patch", "watch", "apply_all", "apply_overrides", "Version", "Probe", "Until"} <= set(
        mpat.__all__
    )


def test_inherited_method_patchable_on_sibling_subclasses(upstream):
    import fakeup.core

    @mpat.patch("fakeup.core.Child.go")
    def child_go(original, self):
        return original(self) + "-child"

    @mpat.patch("fakeup.core.Other.go")
    def other_go(original, self):
        return original(self) + "-other"

    assert fakeup.core.Child().go() == "base-child"
    assert fakeup.core.Other().go() == "base-other"
    assert fakeup.core.Base().go() == "base"
    assert not hasattr(vars(fakeup.core.Base)["go"], _api.IDENTITY_ATTR)


TARGET = "fakeup.core.greet"


def reload_under_lock(tmp_path, monkeypatch, on_drift):
    app = tmp_path / "app_patches.py"
    app.write_text(
        textwrap.dedent(
            f"""
            import mpat

            @mpat.patch("fakeup.core.greet", on_drift="{on_drift}")
            def shout(original, name, punct="!"):
                return original(name, punct) + "!!"
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module("app_patches")


def test_reload_under_lock_does_not_drift(upstream, tmp_path, monkeypatch):
    locked_project(upstream, tmp_path)
    import fakeup.core

    original = fakeup.core.greet
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        module = reload_under_lock(tmp_path, monkeypatch, "warn")
        importlib.reload(module)
        importlib.reload(module)
    assert fakeup.core.greet("a") == "hi a!!!"
    assert getattr(fakeup.core.greet, _api.ORIGINAL_ATTR) is original
    assert len(_registry.declarations()) == 1
    lock = _lock.runtime_lock()
    assert lock is not None
    live = fingerprint(resolve(TARGET))
    assert live.no_source is False
    assert live.source_hash == lock.entries[TARGET].fingerprint.source_hash
    sys.modules.pop("app_patches", None)


def test_reload_under_lock_with_on_drift_skip_stays_applied(upstream, tmp_path, monkeypatch):
    locked_project(upstream, tmp_path)
    import fakeup.core

    original = fakeup.core.greet
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        module = reload_under_lock(tmp_path, monkeypatch, "skip")
        importlib.reload(module)
        importlib.reload(module)
    assert fakeup.core.greet("a") == "hi a!!!"
    assert getattr(fakeup.core.greet, _api.ORIGINAL_ATTR) is original
    sys.modules.pop("app_patches", None)


def test_base_then_child_raises(upstream):
    @mpat.patch("fakeup.core.Base.go")
    def base_go(original, self):
        return original(self) + "-base"

    with pytest.raises(AlreadyPatched):

        @mpat.patch("fakeup.core.Child.go")
        def child_go(original, self):
            return original(self) + "-child"


def test_child_then_base_raises(upstream):
    @mpat.patch("fakeup.core.Child.go")
    def child_go(original, self):
        return original(self) + "-child"

    with pytest.raises(AlreadyPatched):

        @mpat.patch("fakeup.core.Base.go")
        def base_go(original, self):
            return original(self) + "-base"


def test_patch_refuses_forbidden_depends_on(upstream):
    with pytest.raises(ForbiddenTarget, match="ssl"):
        mpat.patch("fakeup.core.greet", depends_on=["ssl.SSLContext"])


def test_watch_refuses_forbidden_depends_on(upstream):
    with pytest.raises(ForbiddenTarget, match="ssl"):
        mpat.watch("fakeup.REGISTRY", depends_on=["ssl.SSLContext"])


def override_project(tmp_path, *entries: str) -> None:
    body = "".join(f"[[tool.mpat.override]]\n{entry}\n" for entry in entries)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n[tool.mpat]\n' + body)


def test_apply_overrides_assigns_and_records_the_original(upstream, tmp_path):
    override_project(tmp_path, 'target = "fakeup.LIMIT"\nvalue = 500\nnote = "too low"')
    mpat.apply_overrides()
    import fakeup

    assert fakeup.LIMIT == 500
    assert _registry.override_originals() == {"fakeup.LIMIT": 16}
    d = _registry.declarations()[0]
    assert (d.target, d.role, d.track_value, d.note) == ("fakeup.LIMIT", "watch", False, "too low")


def test_apply_overrides_is_idempotent(upstream, tmp_path):
    override_project(tmp_path, 'target = "fakeup.LIMIT"\nvalue = 500')
    mpat.apply_overrides()
    mpat.apply_overrides()
    import fakeup

    assert fakeup.LIMIT == 500
    assert _registry.override_originals() == {"fakeup.LIMIT": 16}
    assert len(_registry.declarations()) == 1


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("fakeup.LIMIT", "true"),
        ("fakeup.DEBUG", "1"),
        ("fakeup.LIMIT", "1.5"),
        ("fakeup.LIMIT", '"16"'),
        ("fakeup.core.greet", '"x"'),
        ("fakeup.REGISTRY", "1"),
        ("fakeup.core.Store.size", "1"),
    ],
)
def test_apply_overrides_requires_the_exact_type(upstream, tmp_path, target, value):
    override_project(tmp_path, f'target = "{target}"\nvalue = {value}')
    with pytest.raises(UnsupportedTarget, match=target):
        mpat.apply_overrides()
    assert _registry.override_originals() == {}


def test_apply_overrides_same_type_bool_and_int_work(upstream, tmp_path):
    override_project(
        tmp_path, 'target = "fakeup.DEBUG"\nvalue = true', 'target = "fakeup.LIMIT"\nvalue = 0'
    )
    mpat.apply_overrides()
    import fakeup

    assert fakeup.DEBUG is True
    assert fakeup.LIMIT == 0


def test_apply_overrides_missing_target_raises(upstream, tmp_path):
    override_project(tmp_path, 'target = "fakeup.NOPE"\nvalue = 1')
    with pytest.raises(TargetNotFound):
        mpat.apply_overrides()


def test_apply_overrides_forbidden_target_raises(tmp_path):
    override_project(tmp_path, 'target = "ssl.OP_ALL"\nvalue = 1')
    with pytest.raises(ForbiddenTarget):
        mpat.apply_overrides()


def test_apply_overrides_does_nothing_in_collect_mode(upstream, tmp_path, monkeypatch):
    override_project(tmp_path, 'target = "fakeup.LIMIT"\nvalue = 500')
    monkeypatch.setenv(_registry.COLLECT_ENV, "1")
    mpat.apply_overrides()
    import fakeup

    assert fakeup.LIMIT == 16
    assert _registry.override_originals() == {}
    assert [d.target for d in _registry.declarations()] == ["fakeup.LIMIT"]


def test_apply_overrides_without_config_is_a_no_op(upstream):
    mpat.apply_overrides()
    assert _registry.declarations() == []


def test_watch_accepts_until_without_runtime_effect(upstream, tmp_path):
    locked_project(upstream, tmp_path, target="fakeup.LIMIT")
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 1")
    with pytest.warns(UpstreamDriftWarning, match="value"):
        mpat.watch("fakeup.LIMIT", until=mpat.Probe(lambda: True), note="n")
    d = _registry.declarations()[0]
    assert d.until is not None
    assert d.until()
    assert d.status == _registry.STATUS_WATCHED


def test_apply_overrides_warns_on_drift_before_assigning(upstream, tmp_path):
    locked_project(
        upstream, tmp_path, target="fakeup.LIMIT", role=_registry.ROLE_WATCH, track_value=False
    )
    override_project(tmp_path, 'target = "fakeup.LIMIT"\nvalue = 500')
    upstream.edit("__init__.py", "LIMIT = 16", "def LIMIT():\n    return 16")
    with pytest.warns(UpstreamDriftWarning, match="moved"), pytest.raises(UnsupportedTarget):
        mpat.apply_overrides()
