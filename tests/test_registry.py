import os
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from mpat import _registry as reg
from mpat._config import Config, OverrideSpec, WatchSpec
from mpat._errors import ForbiddenTarget, MpatError


def config(
    *modules: str,
    watches: tuple[WatchSpec, ...] = (),
    overrides: tuple[OverrideSpec, ...] = (),
    allow: tuple[str, ...] = (),
) -> Config:
    return Config(
        root=Path("/proj"),
        modules=modules,
        allow=allow,
        declared=True,
        watches=watches,
        overrides=overrides,
    )


def decl(target="fakeup.core.greet", identity=("/x.py", "f")):
    return reg.Declaration(
        target=target,
        role=reg.ROLE_PATCH,
        depends_on=(),
        until=None,
        on_drift="warn",
        review_by=None,
        note="",
        declared_in=identity[0],
        identity=identity,
    )


def test_register_and_list():
    reg.register(decl())
    assert [d.target for d in reg.declarations()] == ["fakeup.core.greet"]


def test_register_same_identity_replaces():
    reg.register(decl(target="a.b"))
    reg.register(decl(target="a.b"))
    assert len(reg.declarations()) == 1


def test_reset_clears():
    reg.register(decl())
    reg.mark_applied(1, decl())
    reg.reset()
    assert reg.declarations() == []
    assert reg.applied_for(1) is None


def test_collect_mode_env(monkeypatch):
    assert reg.collect_mode() is False
    monkeypatch.setenv(reg.COLLECT_ENV, "1")
    assert reg.collect_mode() is True


def test_collect_declarations_imports_under_collect(monkeypatch):
    seen = {}
    mod = types.ModuleType("collect_probe")
    mod.__file__ = "/collect_probe.py"

    def body():
        seen["collect"] = os.environ.get(reg.COLLECT_ENV)
        reg.register(decl(target="p.q", identity=("/collect_probe.py", "g")))

    monkeypatch.setitem(sys.modules, "collect_probe", mod)
    monkeypatch.setattr("importlib.import_module", lambda name: (body(), mod)[1])
    result = reg.collect_declarations(config("collect_probe"), apply=False)
    assert seen["collect"] == "1"
    assert os.environ.get(reg.COLLECT_ENV) is None
    assert [d.target for d in result] == ["p.q"]


def test_collect_declarations_applies_without_collect_env(monkeypatch):
    seen = {}
    mod = types.ModuleType("apply_probe")
    mod.__file__ = "/apply_probe.py"

    def body():
        seen["collect"] = os.environ.get(reg.COLLECT_ENV)
        reg.register(decl(target="p.r", identity=("/apply_probe.py", "h")))

    monkeypatch.setitem(sys.modules, "apply_probe", mod)
    monkeypatch.setattr("importlib.import_module", lambda name: (body(), mod)[1])
    result = reg.collect_declarations(config("apply_probe"), apply=True)
    assert seen["collect"] is None
    assert [d.target for d in result] == ["p.r"]


def test_config_watches_register_after_the_modules():
    spec = WatchSpec(
        target="fakeup.LIMIT",
        depends_on=("fakeup.REGISTRY",),
        review_by=date(2026, 12, 1),
        note="see fakeup#1",
    )
    result = reg.collect_declarations(config(watches=(spec,)), apply=False)
    assert len(result) == 1
    d = result[0]
    assert d.target == "fakeup.LIMIT"
    assert d.role == reg.ROLE_WATCH
    assert d.depends_on == ("fakeup.REGISTRY",)
    assert d.review_by == date(2026, 12, 1)
    assert d.note == "see fakeup#1"
    assert d.declared_in == "pyproject.toml"
    assert d.identity == ("pyproject.toml", "watch:fakeup.LIMIT")
    assert d.track_value is True
    assert d.until is None


def test_config_watch_is_idempotent_across_collects():
    cfg = config(watches=(WatchSpec(target="fakeup.LIMIT"),))
    reg.collect_declarations(cfg, apply=False)
    result = reg.collect_declarations(cfg, apply=True)
    assert [d.target for d in result] == ["fakeup.LIMIT"]


def test_config_watch_duplicating_a_code_declaration_raises():
    reg.register(decl(target="fakeup.LIMIT", identity=("/proj/p.py", "watch:fakeup.LIMIT")))
    cfg = config(watches=(WatchSpec(target="fakeup.LIMIT"),))
    with pytest.raises(MpatError, match=r"declared twice: pyproject\.toml and /proj/p\.py"):
        reg.collect_declarations(cfg, apply=False)


@pytest.mark.parametrize(
    "spec",
    [
        WatchSpec(target="ssl.SSLContext"),
        WatchSpec(target="fakeup.LIMIT", depends_on=("ssl.SSLContext",)),
    ],
)
def test_config_watch_honours_the_denylist(spec):
    with pytest.raises(ForbiddenTarget, match="ssl"):
        reg.collect_declarations(config(watches=(spec,)), apply=False)
    assert reg.declarations() == []


def test_config_watch_allow_list_applies():
    cfg = config(watches=(WatchSpec(target="ssl.SSLContext"),), allow=("ssl.SSLContext",))
    assert [d.target for d in reg.collect_declarations(cfg, apply=False)] == ["ssl.SSLContext"]


def test_config_override_registers_an_existence_only_watch():
    spec = OverrideSpec(target="fakeup.LIMIT", value=500, note="n", review_by=date(2026, 12, 1))
    result = reg.collect_declarations(config(overrides=(spec,)), apply=False)
    assert len(result) == 1
    d = result[0]
    assert d.role == reg.ROLE_WATCH
    assert d.track_value is False
    assert d.depends_on == ()
    assert d.note == "n"
    assert d.review_by == date(2026, 12, 1)
    assert d.declared_in == "pyproject.toml"
    assert d.identity == ("pyproject.toml", "override:fakeup.LIMIT")


def test_config_override_honours_the_denylist():
    cfg = config(overrides=(OverrideSpec(target="ssl.OP_ALL", value=1),))
    with pytest.raises(ForbiddenTarget, match="ssl"):
        reg.collect_declarations(cfg, apply=False)


def test_config_override_duplicating_a_code_declaration_raises():
    reg.register(decl(target="fakeup.LIMIT", identity=("/proj/p.py", "watch:fakeup.LIMIT")))
    cfg = config(overrides=(OverrideSpec(target="fakeup.LIMIT", value=1),))
    with pytest.raises(MpatError, match="declared twice"):
        reg.collect_declarations(cfg, apply=False)


def test_override_originals_are_recorded_and_reset():
    reg.record_override("a.b", 1)
    assert reg.override_originals() == {"a.b": 1}
    reg.reset()
    assert reg.override_originals() == {}


def test_config_watch_until_string_becomes_a_condition():
    spec = WatchSpec(target="fakeup.LIMIT", until="packaging>9999")
    (d,) = reg.collect_declarations(config(watches=(spec,)), apply=False)
    assert d.until is not None
    assert not d.until()


def test_config_watch_invalid_until_raises_at_collect():
    spec = WatchSpec(target="fakeup.LIMIT", until="ontospy")
    with pytest.raises(MpatError, match="until"):
        reg.collect_declarations(config(watches=(spec,)), apply=False)
