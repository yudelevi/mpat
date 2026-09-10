<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/yudelevi/mpat/main/assets/mpat-lockup-dark.svg">
    <img src="https://raw.githubusercontent.com/yudelevi/mpat/main/assets/mpat-lockup-light.svg" alt="mpat" width="420">
  </picture>
</p>

# mpat

Lockfile-based upstream drift tracking for Python monkeypatches.
https://monkeypat.ch

You monkeypatch a third-party library to work around a bug. Six Renovate bumps
later the function you patched has a new signature, or the call site that made
your patch effective is gone, and nothing told you. `mpat` records a fingerprint
of every upstream symbol a patch depends on in `mpat.lock`, and `mpat check`
fails CI when the installed upstream no longer matches.

## Install

```
uv add mpat
```

Python 3.11+.

## Declare

```python
from datetime import date

import somelib.client
from mpat import Probe, Version, patch, watch


def upstream_fixed() -> bool:
    return hasattr(somelib.client.Client, "retry_on_timeout")


@patch(
    "somelib.client.Client.request",
    depends_on=["somelib.client.Client._send"],
    until=Version("somelib>=2.4") | Probe(upstream_fixed),
    review_by=date(2026, 12, 1),
    note="Works around somelib#123. Delete when fixed.",
)
def request(original, self, method, url, **kwargs):
    ...
    return original(self, method, url, **kwargs)


watch("somelib.settings.MAX_RETRIES", note="see somelib#98")
```

The patched function receives `original` first. `watch` fingerprints without
patching. It tracks the body of functions and classes, the getter of a
property, and the value of scalar constants and tuples of scalars. Every other
attribute, including dicts, lists and custom descriptors, is recorded as
existence only, so mutating a watched registry's contents will not fail
`mpat check`; watch the function or class that populates it instead.
`watch(..., track_value=False)` records that a scalar exists and its kind but not
its value, for settings your application assigns itself; if you want the value
pinned, put the assignment and the `watch()` in the same module. `until`
turns the patch off once upstream is fixed; `Version` takes a requirement
string, `Probe` takes a zero-argument callable, and both combine with `|` and
`&`. `review_by` is a nag date only. `on_drift="warn" | "skip" | "raise"`
decides what happens at import when the lock no longer matches; `MPAT_STRICT=1`
forces `raise`. Passing a target as an object instead of a dotted string works
too, resolved through `__module__` and `__qualname__`. Two dotted targets that
name the same attribute of the same owner are refused as aliases, but the same
inherited method on two sibling subclasses is not an alias: each patch lands on
its own class and the base class is left alone.

`patch` imports the target when the decorator runs. For an optional dependency
that is the wrong moment, so `when_imported=True` defers it:

```python
@patch("optionallib.Client.request", when_imported=True)
def request(original, self, *args, **kwargs):
    ...
```

The declaration registers without importing anything. A post-import hook
applies the patch the first time `optionallib` is imported, or right away if it
already is, and the `until`, drift and kind checks run at that point. If
`optionallib` is never imported, nothing happens. `mpat.apply_all()` imports
every module a deferred patch is still waiting on, for code that wants the
patches in place before it starts. Locking is unchanged: `mpat lock` has to be
able to import the target.

## Lock and check

```toml
# pyproject.toml
[tool.mpat]
modules = ["myapp.patches"]
```

```
mpat lock     # fingerprint everything, write mpat.lock, commit it
mpat check    # exit 1 unless every target is ok
mpat check --json
mpat show somelib.client.Client.request
```

`mpat lock` and `mpat check` import the modules listed in `[tool.mpat] modules`
with `MPAT_COLLECT=1`, so declarations register themselves without patching
anything. `mpat show` prints a target's fingerprint and its source. Exit codes
are 0 for clean, 1 for drift, 2 for a usage or configuration error.

`mpat.lock` is TOML, one table per target, sorted:

```toml
version = 1

[entry."somelib.client.Client._send"]
role = "depends_on"
declared_in = "myapp/patches.py"
parent = "somelib.client.Client.request"
kind = "function"
resolved = "somelib.client.Client._send"
is_async = false
signature = "(self, method, url, **kwargs)"
source_hash = "sha256:ce1f279d9839197d25b69546c88bc88efde771c1d5a28cd4da55786d77b24053"
source_file = "somelib/client.py"
dist = "somelib"
dist_version = "2.3.1"
no_source = false

[entry."somelib.client.Client.request"]
role = "patch"
declared_in = "myapp/patches.py"
kind = "function"
resolved = "somelib.client.Client.request"
is_async = false
signature = "(self, method, url, **kwargs)"
source_hash = "sha256:068463544ea8c8779fa20b6d74e5b9a63143318d074273233bc380044988e2ec"
source_file = "somelib/client.py"
dist = "somelib"
dist_version = "2.3.1"
no_source = false

[entry."somelib.settings.MAX_RETRIES"]
role = "watch"
declared_in = "myapp/patches.py"
kind = "attribute"
resolved = "somelib.settings.MAX_RETRIES"
is_async = false
value_repr = "3"
dist = "somelib"
dist_version = "2.3.1"
no_source = false
```

`role` is `patch`, `watch` or `depends_on`; a `depends_on` entry also carries
`parent`. Entries for constants carry `value_repr` instead of a source hash.
An entry whose `role` or `parent` no longer matches the declaration is reported
`unlocked`: the audit trail changed meaning, so it has to be re-locked.

`mpat check` reports one row per target with one of these statuses:

| Status | Meaning |
| --- | --- |
| `ok` | Installed upstream matches the lock. |
| `moved` | The target now resolves elsewhere, or its source file changed. |
| `signature` | The signature changed, or the target switched between sync and async. |
| `body` | The source hash changed. |
| `value` | A watched constant's value changed. |
| `no_source` | The locked source is no longer readable, so only the signature is still comparable. |
| `missing` | The target no longer exists upstream. |
| `unlocked` | Declared in code but absent from `mpat.lock`. |
| `stale` | In `mpat.lock` but no longer declared in code. |
| `review` | Nothing drifted, but `review_by` has passed. |

Drift beats `review`: a target that both drifted and is overdue reports the
drift.

Some targets have no readable source: builtins, compiled extensions, and
`.pyc`-only installs. Those are locked with `no_source = true` and compared on
signature and async-ness alone, never on a body hash. `mpat lock` warns once per
such entry and `mpat check` marks its row `[signature-only]`, because that is a
much weaker guard than the rest of the table.

Rows are grouped by installing distribution, and a footer counts each family so
it is clear whether the answer is to run `mpat lock` or to read the upstream
change.

Run `mpat check` on dependency-bump MRs. When it fails, read the upstream
change, fix or delete the patch, run `mpat lock`, commit.

## Declare without code

A watch needs no Python at all. Declare it in `pyproject.toml` and `mpat lock`,
`mpat check` and the pytest plugin pick it up alongside the modules:

```toml
[[tool.mpat.watch]]
target = "qdrant_client.async_qdrant_remote.AsyncQdrantRemote.query_points"
depends_on = ["qdrant_client.async_qdrant_remote.AsyncQdrantRemote.scroll"]
review_by = 2026-12-01
note = "protobuf timeout wrapper in data/qdrant.py relies on this shape"
```

`target` is required; `depends_on`, `review_by` and `note` mean what they mean
on `watch()`. The entry is locked with `declared_in = "pyproject.toml"`, the
denylist applies to it and its `depends_on`, and a malformed entry is a
configuration error (exit 2) that names the entry.

A patch that only assigns a scalar needs no code either:

```toml
[[tool.mpat.override]]
target = "engineio.payload.Payload.max_decode_packets"
value = 500
note = "engineio default 16 500s long-polling clients (zauberzeug/nicegui#209)"
review_by = 2026-12-01
```

```python
# the only line the application needs, at startup
import mpat

mpat.apply_overrides()
```

`apply_overrides()` assigns every override once per process, after checking
that the target is an existing attribute of exactly the value's type: `bool`
is not `int` here, so `value = true` on an int attribute or `value = 1` on a
bool one raises `UnsupportedTarget`. Each override is also a watch with
`track_value = false`, so `mpat lock` records that the attribute exists and
is a scalar, never its value, and `mpat check` is green whether or not the
override has been applied in that process. A workaround with any logic in it
is still `@patch`.

A project can have `modules`, `[[tool.mpat.watch]]` and `[[tool.mpat.override]]`
entries in any combination. A target declared twice, in code and in
`pyproject.toml` or in both TOML forms, is refused rather than silently
merged, so there is no precedence to remember.

## pytest

Install mpat, add `[tool.mpat]`, run pytest. The tests register themselves:

```
mpat::drift[somelib.client.Client.request] PASSED
mpat::still-needed[somelib.client.Client.request] PASSED
```

One `drift` item per declared or locked target, one `still-needed` item per
patch with `until`. They belong to no file of yours, so they are collected by
`pytest` and by `pytest <dir>`, and left out when you name a file or a nodeid:
`pytest tests/test_client.py` runs what you asked for and nothing else. Disable
them everywhere with `--no-mpat`, or `mpat = false` under
`[tool.pytest.ini_options]`. `pytest-xdist` works: the items are ordered by
target, so every worker collects the same list.

Collecting them imports your patch modules the way your application does, so the
patches are active for the rest of the test session exactly as in production.

A project with `[tool.mpat]` but no `modules` and no `[[tool.mpat.watch]]`
entries gets a single failing `mpat::unconfigured` item rather than a green run,
so a typo in the module list cannot turn the safety net green. A project with no `[tool.mpat]` at all collects
nothing.

The explicit form still works and wins when both are present:

```python
# tests/test_upstream.py
from mpat.testing import test_upstream_drift, test_patch_still_needed
```

## pre-commit

```yaml
- repo: local
  hooks:
    - id: mpat-check
      name: mpat check
      entry: uv run mpat check
      language: system
      pass_filenames: false
      always_run: true
```

The hook has to run inside the project environment, because `mpat check`
imports your patch modules and the upstream packages they target. An isolated
hook environment would not have them.

## What it will not do

`mpat` is not a sandbox. Anything running in your interpreter can `setattr`
without asking. It refuses a short denylist (`mpat`, `builtins`, `sys`,
`importlib`, `ssl`, `hashlib`, `hmac`, `secrets`, `cryptography`, `certifi`)
so nobody patches or watches those by accident; the denylist applies to both
`patch` and `watch`, and to every `depends_on` target they declare. Override it
with `[tool.mpat] allow`.

Only plain functions, staticmethods and classmethods can be patched.
Properties, custom descriptors and metaclass attributes can be watched, not
patched, and of those only a property is tracked by body, through its getter. A
replacement must be the same kind of callable as the original: sync for sync,
async for async, generator for generator. Per-instance patching, undo, and
diff-based patching are out of scope.

## Development

```
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src tests
uv build
```
