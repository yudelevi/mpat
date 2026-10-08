from mpat import _render
from mpat._lock import CheckResult, Usage
from mpat._upstream import Source

RESULT = CheckResult(
    target="somelib.client.Client.request",
    role="patch",
    status="body",
    declared_in="myapp/patches.py",
    declared_line=12,
    locked_version="2.3.1",
    current_version="2.5.0",
    used_by=(
        Usage(declared_in="myapp/retry.py", declared_line=40, role="patch", target="somelib.retry"),
    ),
)
OLD = Source(
    path="somelib/client.py",
    start=88,
    lines=(
        "    def request(self, method, url, **kwargs):",
        "        self.log(method)",
        "        return self._send(method, url, **kwargs)",
    ),
)
NEW = Source(
    path="somelib/client.py",
    start=90,
    lines=(
        "    def request(self, method, url, **kwargs):",
        "        self.log(method)",
        "        return self._send(method, url, timeout=self.timeout, **kwargs)",
    ),
)


def test_body_block():
    assert _render.diff_block(RESULT, old=OLD, new=NEW) == [
        "body[patch] somelib.client.Client.request: the upstream body changed (2.3.1 -> 2.5.0)",
        "    --> somelib/client.py:92",
        "      |",
        "88 90 |       def request(self, method, url, **kwargs):",
        "89 91 |           self.log(method)",
        "90    | -         return self._send(method, url, **kwargs)",
        "   92 | +         return self._send(method, url, timeout=self.timeout, **kwargs)",
        "      |",
        "info: declared here",
        "    --> myapp/patches.py:12",
        "info: also used by myapp/retry.py:40 (patch somelib.retry)",
    ]


def test_missing_target_shows_the_old_definition_removed():
    result = CheckResult(target="pkg.f", role="watch", status="missing", declared_in="p.py")
    old = Source(path="pkg/m.py", start=9, lines=("def f():", "    return 1"))
    assert _render.diff_block(result, old=old, new=None) == [
        "missing[watch] pkg.f: no longer exists upstream",
        "    --> pkg/m.py:9",
        "      |",
        " 9    | - def f():",
        "10    | -     return 1",
        "      |",
        "info: declared here",
        "    --> p.py",
    ]


def test_hunks_are_separated_and_far_context_is_dropped():
    old = Source(path="m.py", start=1, lines=tuple(f"x{i}" for i in range(12)))
    new = Source(path="m.py", start=1, lines=("y0", *old.lines[1:11], "y11"))
    lines = _render.diff_block(RESULT, old=old, new=new)
    assert " 1    | - x0" in lines
    assert "      ..." in lines
    assert not any(line.endswith("x5") for line in lines)


def test_rebuilt_archive_warns():
    old = Source(path=OLD.path, start=OLD.start, lines=OLD.lines, matches_lock=False)
    assert "warning: the downloaded source does not match mpat.lock" in _render.diff_block(
        RESULT, old=old, new=NEW
    )


def test_unavailable_upstream_is_one_info_line():
    lines = _render.diff_block(RESULT, old=None, new=NEW, unavailable="cannot fetch x")
    assert lines[1] == "info: upstream diff unavailable: cannot fetch x"
