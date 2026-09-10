import sys
from importlib import metadata

import pytest

from mpat._errors import MpatError
from mpat._until import Probe, Until, Version, until_from_string


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


def test_until_from_string_requirement_is_a_version():
    installed = metadata.version("packaging")
    until = until_from_string(f"packaging>={installed}")
    assert isinstance(until, Version)
    assert until()
    assert not until_from_string("packaging>9999")()


def test_until_from_string_dotted_path_is_a_lazy_probe(tmp_path, monkeypatch):
    (tmp_path / "probes.py").write_text(
        "CALLS = []\n\n\ndef fixed():\n    CALLS.append(1)\n    return False\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    until = until_from_string("probes.fixed")
    assert isinstance(until, Probe)
    assert "probes" not in sys.modules
    assert not until()
    assert sys.modules["probes"].CALLS == [1]
    assert repr(until) == "Probe(probes.fixed)"


def test_until_from_string_dotted_path_with_specifier_is_a_version():
    assert isinstance(until_from_string("some.pkg>=1"), Version)


@pytest.mark.parametrize("text", ["ontospy", "", "not a path!", "a.b c", ".fixed", "a..b"])
def test_until_from_string_rejects_everything_else(text):
    with pytest.raises(MpatError, match="until"):
        until_from_string(text)
