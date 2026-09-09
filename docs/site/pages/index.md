# mpat

Lockfile-based upstream drift tracking for Python monkeypatches.

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

## Quickstart

```python
# myapp/patches.py
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
    kwargs.setdefault("timeout", 30)
    return original(self, method, url, **kwargs)


watch("somelib.settings.MAX_RETRIES", note="see somelib#98")
```

Point `mpat` at that module, then lock and check.

```toml
# pyproject.toml
[tool.mpat]
modules = ["myapp.patches"]
```

```
mpat lock     # fingerprint everything, write mpat.lock, commit it
mpat check    # exit 1 unless every target is ok
```

Run `mpat check` on dependency-bump merge requests. When it fails, read the
upstream change, fix or delete the patch, run `mpat lock`, commit.

## Where to go next

- [Usage](usage.md) covers `patch()`, `watch()`, every keyword, and the errors
  a bad target raises.
- [CLI](cli.md) covers the three commands, the status table and the exit codes.
- [pytest](pytest.md) turns the same check into tests and a pre-commit hook.
- [Lockfile](lockfile.md) documents the `mpat.lock` format.
- [FAQ](faq.md) covers the design choices and the known limits.
