import os
import sys
import types

from mpat import _registry as reg


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
    result = reg.collect_declarations(["collect_probe"], apply=False)
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
    result = reg.collect_declarations(["apply_probe"], apply=True)
    assert seen["collect"] is None
    assert [d.target for d in result] == ["p.r"]
