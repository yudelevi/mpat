<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/mpat-lockup-dark.svg">
    <img src="assets/mpat-lockup-light.svg" alt="mpat" width="420">
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

import somelib
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
patching: constants, registries, call sites, anything you rely on. `until`
turns the patch off once upstream is fixed; `Version` takes a requirement
string, `Probe` takes a zero-argument callable, and both combine with `|` and
`&`. `review_by` is a nag date only. `on_drift="warn" | "skip" | "raise"`
decides what happens at import when the lock no longer matches; `MPAT_STRICT=1`
forces `raise`. Passing a target as an object instead of a dotted string works
too, resolved through `__module__` and `__qualname__`.

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

`mpat check` reports one row per target with one of these statuses:

| Status | Meaning |
| --- | --- |
| `ok` | Installed upstream matches the lock. |
| `moved` | The target now resolves elsewhere, or its source file changed. |
| `signature` | The signature changed, or the target switched between sync and async. |
| `body` | The source hash changed. |
| `value` | A watched constant's value changed. |
| `missing` | The target no longer exists upstream. |
| `unlocked` | Declared in code but absent from `mpat.lock`. |
| `stale` | In `mpat.lock` but no longer declared in code. |
| `review` | Nothing drifted, but `review_by` has passed. |

Drift beats `review`: a target that both drifted and is overdue reports the
drift.

Run `mpat check` on dependency-bump MRs. When it fails, read the upstream
change, fix or delete the patch, run `mpat lock`, commit.

## pytest

```python
# tests/test_upstream.py
from mpat.testing import test_upstream_drift, test_patch_still_needed
```

One test per declared or locked target, one per patch with `until`.

## pre-commit

```yaml
- repo: https://github.com/yudelevi/mpat
  rev: v0.1.0
  hooks:
    - id: mpat-check
```

## What it will not do

`mpat` is not a sandbox. Anything running in your interpreter can `setattr`
without asking. It refuses a short denylist (`mpat`, `builtins`, `sys`,
`importlib`, `ssl`, `hashlib`, `hmac`, `secrets`, `cryptography`, `certifi`)
so nobody patches those by accident; override with `[tool.mpat] allow`.

Only plain functions, staticmethods and classmethods can be patched.
Properties, custom descriptors and metaclass attributes can be watched, not
patched. A replacement must be the same kind of callable as the original: sync
for sync, async for async, generator for generator. Per-instance patching,
undo, and diff-based patching are out of scope.

## Development

```
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src tests
```
