import dataclasses
import importlib

import pytest

from mpat import _fingerprint as fp
from mpat._targets import resolve


def fingerprint_of(target):
    return fp.fingerprint(resolve(target))


def test_function_fingerprint(upstream):
    f = fingerprint_of("fakeup.core.greet")
    assert f.kind == fp.KIND_FUNCTION
    assert f.resolved == "fakeup.core.greet"
    assert f.signature == "(name, punct='!')"
    assert f.source_hash.startswith("sha256:")
    assert f.source_file == "fakeup/core.py"
    assert f.is_async is False
    assert f.no_source is False
    assert f.dist is None


def test_async_flag(upstream):
    assert fingerprint_of("fakeup.core.agreet").is_async is True


def test_formatting_does_not_change_hash(upstream):
    before = fingerprint_of("fakeup.core.greet")
    upstream.edit(
        "core.py", 'return f"hi {name}{punct}"', '# comment\n\n    return   f"hi {name}{punct}"'
    )
    assert fingerprint_of("fakeup.core.greet").source_hash == before.source_hash


def test_body_change_changes_hash(upstream):
    before = fingerprint_of("fakeup.core.greet")
    upstream.edit("core.py", 'f"hi {name}{punct}"', 'f"hello {name}{punct}"')
    after = fingerprint_of("fakeup.core.greet")
    assert after.source_hash != before.source_hash
    assert fp.compare(locked=before, current=after) == fp.BODY


def test_signature_change(upstream):
    before = fingerprint_of("fakeup.core.greet")
    upstream.edit("core.py", 'def greet(name, punct="!"):', 'def greet(name, punctuation="!"):')
    upstream.edit("core.py", "{punct}", "{punctuation}")
    after = fingerprint_of("fakeup.core.greet")
    assert fp.compare(locked=before, current=after) == fp.SIGNATURE


def test_sync_to_async_is_signature_drift(upstream):
    before = fingerprint_of("fakeup.core.greet")
    upstream.edit("core.py", "def greet(", "async def greet(")
    assert fp.compare(locked=before, current=fingerprint_of("fakeup.core.greet")) == fp.SIGNATURE


def test_decorator_change_changes_hash(upstream):
    before = fingerprint_of("fakeup.core.decorated")
    upstream.edit("core.py", '@tag("v1")', '@tag("v2")')
    assert fingerprint_of("fakeup.core.decorated").source_hash != before.source_hash


def test_decorated_function_hashes_definition_site(upstream):
    f = fingerprint_of("fakeup.core.decorated")
    assert f.resolved == "fakeup.core.decorated"
    assert f.source_file == "fakeup/core.py"


def test_method_and_descriptors(upstream):
    m = fingerprint_of("fakeup.core.Store.add")
    assert m.resolved == "fakeup.core.Store.add"
    assert m.signature == "(self, item)"
    s = fingerprint_of("fakeup.core.Store.double")
    assert s.kind == fp.KIND_FUNCTION
    assert s.signature == "(x)"


def test_class_kind(upstream):
    c = fingerprint_of("fakeup.core.Store")
    assert c.kind == fp.KIND_CLASS
    assert c.source_hash
    upstream.edit("core.py", "limit = 3", "limit = 4")
    assert fingerprint_of("fakeup.core.Store").source_hash != c.source_hash


def test_scalar_attribute(upstream):
    a = fingerprint_of("fakeup.LIMIT")
    assert a.kind == fp.KIND_ATTRIBUTE
    assert a.value_repr == "16"
    assert a.resolved == "fakeup.LIMIT"
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 500")
    assert fp.compare(locked=a, current=fingerprint_of("fakeup.LIMIT")) == fp.VALUE


def test_unhashable_attribute(upstream):
    a = fingerprint_of("fakeup.REGISTRY")
    assert a.value_repr == fp.UNHASHABLE
    assert a.source_hash is None


def test_class_attribute_scalar(upstream):
    assert fingerprint_of("fakeup.core.Store.limit").value_repr == "3"


def test_property_hashes_fget(upstream):
    p = fingerprint_of("fakeup.core.Store.size")
    assert p.kind == fp.KIND_ATTRIBUTE
    assert p.value_repr is None
    assert p.source_hash
    upstream.edit("core.py", "return len(self.items)", "return len(self.items) + 0")
    assert fp.compare(locked=p, current=fingerprint_of("fakeup.core.Store.size")) == fp.BODY


def test_property_with_wrapped_getter_hashes_definition_site(upstream):
    upstream.edit(
        "core.py",
        "    @property\n    def size(self):",
        '    @property\n    @tag("p")\n    def size(self):',
    )
    p = fingerprint_of("fakeup.core.Store.size")
    assert p.source_hash
    assert p.source_file == "fakeup/core.py"


def test_reexport_resolves_to_definition(upstream):
    a = fingerprint_of("fakeup.greet_alias")
    assert a.resolved == "fakeup.core.greet"
    assert a.source_file == "fakeup/core.py"


def test_redirected_reexport_is_moved(upstream):
    before = fingerprint_of("fakeup.greet_alias")
    upstream.edit(
        "__init__.py",
        "from fakeup.core import greet as greet_alias",
        "from fakeup.core import decorated as greet_alias",
    )
    assert fp.compare(locked=before, current=fingerprint_of("fakeup.greet_alias")) == fp.MOVED


def test_definition_inside_try_block(upstream):
    assert fingerprint_of("fakeup.core.guarded").source_hash


def test_pyc_only_install_is_no_source(upstream):
    import compileall

    compileall.compile_dir(str(upstream.pkg), quiet=1, legacy=True)
    for py in upstream.pkg.rglob("*.py"):
        py.unlink()
    upstream.purge()
    f = fingerprint_of("fakeup.core.greet")
    assert f.no_source is True
    assert f.source_hash is None
    assert f.signature == "(name, punct='!')"


def test_builtin_is_no_source():
    f = fp.fingerprint(resolve("os.path.join"))
    assert f.kind == fp.KIND_FUNCTION
    f2 = fp.fingerprint(resolve("math.sqrt"))
    assert f2.no_source is True


def test_installed_dist_metadata():
    f = fp.fingerprint(resolve("packaging.version.Version"))
    assert f.dist == "packaging"
    assert f.dist_version
    assert f.source_file == "packaging/version.py"


def test_compare_ok_is_identity(upstream):
    f = fingerprint_of("fakeup.core.greet")
    assert fp.compare(locked=f, current=dataclasses.replace(f)) == fp.OK


def test_namespace_package_source_is_relative(tmp_path, monkeypatch):
    site = tmp_path / "nssite"
    sub = site / "nsp" / "sub"
    sub.mkdir(parents=True)
    (sub / "__init__.py").write_text("def hello():\n    return 1\n")
    monkeypatch.syspath_prepend(str(site))
    f = fingerprint_of("nsp.sub.hello")
    assert f.source_file == "nsp/sub/__init__.py"
    assert f.source_hash


def test_function_replaced_by_class_is_moved(upstream):
    before = fingerprint_of("fakeup.core.greet")
    upstream.edit(
        "core.py", 'def greet(name, punct="!"):', "class greet:\n    x = 1\n\ndef _unused():"
    )
    after = fingerprint_of("fakeup.core.greet")
    assert after.kind == fp.KIND_CLASS
    assert fp.compare(locked=before, current=after) == fp.MOVED


def test_source_disappearing_is_no_source_status(upstream):
    before = fingerprint_of("fakeup.core.greet")
    after = dataclasses.replace(before, source_hash=None, no_source=True)
    assert fp.compare(locked=before, current=after) == fp.NO_SOURCE


def test_sync_generator_to_async_generator_is_signature_drift(upstream):
    before = fingerprint_of("fakeup.core.count")
    assert before.is_async is False
    upstream.edit("core.py", "def count(n):", "async def count(n):")
    upstream.edit("core.py", "    yield from range(n)", "    for i in range(n):\n        yield i")
    after = fingerprint_of("fakeup.core.count")
    assert after.is_async is True
    assert fp.compare(locked=before, current=after) == fp.SIGNATURE


def test_namespace_package_second_portion_is_relative(tmp_path, monkeypatch):
    first, second = tmp_path / "site_a", tmp_path / "site_b"
    (first / "nsp2" / "one").mkdir(parents=True)
    (second / "nsp2" / "two").mkdir(parents=True)
    (first / "nsp2" / "one" / "__init__.py").write_text("def hello():\n    return 1\n")
    (second / "nsp2" / "two" / "__init__.py").write_text("def hello():\n    return 2\n")
    monkeypatch.syspath_prepend(str(second))
    monkeypatch.syspath_prepend(str(first))
    nsp2 = importlib.import_module("nsp2")
    assert len(list(nsp2.__path__)) == 2
    assert fingerprint_of("nsp2.two.hello").source_file == "nsp2/two/__init__.py"
    assert fingerprint_of("nsp2.one.hello").source_file == "nsp2/one/__init__.py"


def test_track_value_false_records_existence_only(upstream):
    a = fp.fingerprint(resolve("fakeup.LIMIT"), track_value=False)
    assert a.kind == fp.KIND_ATTRIBUTE
    assert a.resolved == "fakeup.LIMIT"
    assert a.value_repr is None
    assert fingerprint_of("fakeup.LIMIT").value_repr == "16"
    upstream.edit("__init__.py", "LIMIT = 16", "LIMIT = 500")
    after = fp.fingerprint(resolve("fakeup.LIMIT"), track_value=False)
    assert fp.compare(locked=a, current=after) == fp.OK


def test_track_value_false_still_reports_kind_change(upstream):
    before = fp.fingerprint(resolve("fakeup.LIMIT"), track_value=False)
    upstream.edit("__init__.py", "LIMIT = 16", "def LIMIT():\n    return 16")
    after = fp.fingerprint(resolve("fakeup.LIMIT"), track_value=False)
    assert after.kind == fp.KIND_FUNCTION
    assert fp.compare(locked=before, current=after) == fp.MOVED


def test_unparsable_source_is_no_source(upstream):
    import fakeup.core  # noqa: F401

    (upstream.pkg / "core.py").write_text("def broken(:\n")
    f = fingerprint_of("fakeup.core.greet")
    assert f.no_source is True
    assert f.source_hash is None
    assert f.signature == "(name, punct='!')"


def test_unreadable_source_is_no_source(upstream):
    import os

    import fakeup.core  # noqa: F401

    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    (upstream.pkg / "core.py").chmod(0)
    try:
        f = fingerprint_of("fakeup.core.greet")
    finally:
        (upstream.pkg / "core.py").chmod(0o644)
    assert f.no_source is True
    assert f.source_hash is None


def test_conditional_definition_hashes_the_live_branch(upstream):
    import ast

    tree = ast.parse((upstream.pkg / "core.py").read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "twice"]
    dead, live = sorted(nodes, key=lambda n: n.lineno)
    assert "dead" in ast.unparse(dead)
    assert fingerprint_of("fakeup.core.twice").source_hash == fp._hash(live)


def test_decorated_conditional_definition_hashes_the_live_branch(upstream):
    import ast

    tree = ast.parse((upstream.pkg / "core.py").read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "thrice"]
    _, live = sorted(nodes, key=lambda n: n.lineno)
    assert fingerprint_of("fakeup.core.thrice").source_hash == fp._hash(live)
