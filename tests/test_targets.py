import pytest

from mpat import _targets
from mpat._errors import (
    ForbiddenTarget,
    TargetImportError,
    TargetNotFound,
    UnsupportedTarget,
)


def test_canonical_from_object(upstream):
    import fakeup.core

    assert _targets.canonical_target(fakeup.core.greet) == "fakeup.core.greet"
    assert _targets.canonical_target(fakeup.core.Store.add) == "fakeup.core.Store.add"
    assert _targets.canonical_target("x.y") == "x.y"


def test_resolve_module_function(upstream):
    r = _targets.resolve("fakeup.core.greet")
    import fakeup.core

    assert r.parent is fakeup.core
    assert r.attr == "greet"
    assert r.obj is fakeup.core.greet
    assert r.descriptor is None


def test_resolve_static_and_class_methods(upstream):
    s = _targets.resolve("fakeup.core.Store.double")
    assert s.descriptor is staticmethod
    assert s.obj(2) == 4
    c = _targets.resolve("fakeup.core.Store.build")
    assert c.descriptor is classmethod


def test_resolve_lazy_module_getattr(upstream):
    r = _targets.resolve("fakeup.lazy_greet")
    assert r.obj.__name__ == "greet"


def test_resolve_missing_attr(upstream):
    with pytest.raises(TargetNotFound):
        _targets.resolve("fakeup.core.nope")


def test_resolve_missing_module():
    with pytest.raises(TargetNotFound):
        _targets.resolve("definitely_not_a_module.thing")


def test_resolve_module_only_is_error(upstream):
    with pytest.raises(TargetNotFound):
        _targets.resolve("fakeup.core")


def test_resolve_propagates_real_import_errors(upstream):
    upstream.edit(
        "core.py",
        "from fakeup.deco import tag",
        "import not_installed_xyz\nfrom fakeup.deco import tag",
    )
    with pytest.raises(TargetImportError, match="not_installed_xyz"):
        _targets.resolve("fakeup.core.greet")


def test_resolve_reports_import_error_raised_inside_the_target_module(upstream):
    upstream.edit(
        "core.py",
        "from fakeup.deco import tag",
        "from fakeup.deco import tag, gone",
    )
    with pytest.raises(TargetImportError, match=r"\[tool.mpat\] modules"):
        _targets.resolve("fakeup.core.greet")


@pytest.mark.parametrize(
    "target",
    ["fakeup.core.Store.size", "fakeup.LIMIT", "fakeup.core.Store"],
)
def test_unsupported_targets(upstream, target):
    with pytest.raises(UnsupportedTarget):
        _targets.check_supported(_targets.resolve(target))


def test_metaclass_attribute_unsupported(upstream):
    upstream.edit(
        "core.py",
        "class Store:",
        "class Meta(type):\n    def meta_only(cls):\n        return 1\n\n\n"
        "class Store(metaclass=Meta):",
    )
    with pytest.raises(UnsupportedTarget):
        _targets.check_supported(_targets.resolve("fakeup.core.Store.meta_only"))


def test_supported_targets(upstream):
    for t in (
        "fakeup.core.greet",
        "fakeup.core.Store.add",
        "fakeup.core.Store.double",
        "fakeup.core.Store.build",
    ):
        _targets.check_supported(_targets.resolve(t))


@pytest.mark.parametrize("target", ["ssl.SSLContext", "mpat._api.patch", "sys.path", "hashlib"])
def test_forbidden(target):
    with pytest.raises(ForbiddenTarget):
        _targets.check_forbidden(target)


def test_forbidden_prefix_is_whole_segment():
    _targets.check_forbidden("sysconfig.get_paths")
    _targets.check_forbidden("mpatx.thing")


def test_allow_overrides():
    _targets.check_forbidden("ssl.SSLContext.load_verify_locations", allow=["ssl.SSLContext"])


def test_callable_kind(upstream):
    import fakeup.core as c

    assert _targets.callable_kind(c.greet) == _targets.SYNC
    assert _targets.callable_kind(c.agreet) == _targets.ASYNC
    assert _targets.callable_kind(c.count) == _targets.GEN
    assert _targets.callable_kind(c.acount) == _targets.ASYNCGEN
