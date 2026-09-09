from importlib import metadata

from mpat._until import Probe, Until, Version


def test_version_true_when_installed_satisfies():
    installed = metadata.version("packaging")
    assert Version(f"packaging>={installed}")()
    assert not Version("packaging>9999")()


def test_version_false_when_not_installed():
    assert not Version("not_installed_xyz>=1")()


def test_probe():
    assert Probe(lambda: True)()
    assert not Probe(lambda: 0)()


def test_combinators():
    t, f = Probe(lambda: True), Probe(lambda: False)
    assert (t | f)()
    assert not (f | f)()
    assert (t & t)()
    assert not (t & f)()
    assert isinstance(t | f, Until)


def test_repr():
    assert repr(Version("packaging>=1")) == "Version('packaging>=1')"
