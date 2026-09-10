# CLI

Three commands. Both `lock` and `check` read `[tool.mpat] modules` from the
nearest `pyproject.toml`, so run them from anywhere inside the project.

```toml
# pyproject.toml
[tool.mpat]
modules = ["myapp.patches"]
```

## `mpat lock`

Imports the configured modules, fingerprints every declared target and its
`depends_on` entries, and writes `mpat.lock` next to `pyproject.toml`. Commit the
result.

```
$ mpat lock
+ somelib.client.Client._send
+ somelib.client.Client.request
+ somelib.settings.MAX_RETRIES
wrote mpat.lock with 3 entries
```

The diff is against the lockfile that was already there. `+` is a new entry, `-`
is one that is gone, `~` is one whose fingerprint changed. An existing lockfile
that cannot be parsed is reported on stderr and replaced rather than blocking the
write.

Entries with no readable source get one warning each on stderr:

```
mpat: somelib._speedups.encode: no source available, only the signature is locked
```

`mpat lock` always exits 0 unless the configuration is broken.

## `mpat check`

Compares `mpat.lock` against the installed upstream and prints one row per
target. This is the command for CI and for dependency-bump merge requests.

```
$ mpat check
# somelib
body       patch      somelib.client.Client.request myapp/patches.py 2.3.1 -> 2.5.0  Works around somelib#123. Delete when fixed.
ok         depends_on somelib.client.Client._send   myapp/patches.py 2.3.1 -> 2.5.0  Works around somelib#123. Delete when fixed.
value      watch      somelib.settings.MAX_RETRIES  myapp/patches.py 2.3.1 -> 2.5.0  see somelib#98

2 drifted: read the upstream change, then fix or delete the patch and run 'mpat lock'
```

The columns are status, role, target, the file the declaration came from, the
locked and current distribution version when they differ, and the `note`.

Rows are grouped by the distribution that installs the target, under a `#` header.
Targets that belong to no installed distribution are grouped under
`(no distribution)`.

With no declarations at all it prints `no declarations found` and exits 0.

### Statuses

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

Drift beats `review`: a target that both drifted and is overdue reports the drift.

A row whose target currently has no readable source is marked, whatever its
status:

```
ok [signature-only] watch      somelib._speedups.encode      myapp/patches.py
```

That is a much weaker guard than the rest of the table, so it is called out
rather than left to look like any other `ok`.

### Footer

The footer counts each family of problem and says what to do about it. Only the
lines with a non-zero count are printed.

```
1 unlocked: run 'mpat lock'
2 drifted: read the upstream change, then fix or delete the patch and run 'mpat lock'
1 stale: run 'mpat lock' to prune
1 due for review
```

`unlocked` and `stale` mean the lockfile is out of date with your code, so the
answer is `mpat lock`. `drifted` means upstream changed under you, so the answer
is to read the upstream change first.

### `--json`

```
$ mpat check --json
[
  {
    "target": "somelib.client.Client.request",
    "role": "patch",
    "status": "body",
    "note": "Works around somelib#123. Delete when fixed.",
    "declared_in": "myapp/patches.py",
    "locked_version": "2.3.1",
    "current_version": "2.5.0",
    "no_source": false,
    "dist": "somelib"
  }
]
```

The list is in declaration order, with `stale` entries appended. Exit codes are
the same as for the table.

## `mpat show`

Prints a target's fingerprint and its source. It takes a dotted path directly and
needs no configuration, so it works on any installed package.

```
$ mpat show somelib.client.Client.request
kind: function
resolved: somelib.client.Client.request
is_async: False
signature: (self, method, url, **kwargs)
source_hash: sha256:068463544ea8c8779fa20b6d74e5b9a63143318d074273233bc380044988e2ec
value_repr: None
source_file: somelib/client.py
dist: somelib
dist_version: 2.3.1
no_source: False

    def request(self, method, url, **kwargs):
        ...
```

When there is no readable source the body is replaced by
`(no source available)`.

Use it to see what changed after a `body` row: run it before and after the bump,
or against the version the lockfile recorded.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Clean |
| 1 | `mpat check` found at least one row that is not `ok` |
| 2 | Usage or configuration error |

`review` is not `ok`, so an overdue `review_by` fails `mpat check` too.

Exit 2 covers a missing `pyproject.toml`, an empty `[tool.mpat] modules`, an
unreadable or unsupported `mpat.lock`, and an unresolvable target passed to
`mpat show`. The message goes to stderr with an `mpat: ` prefix.

```
$ mpat check
mpat: [tool.mpat] modules is empty in pyproject.toml; nothing to collect
$ echo $?
2
```

## `MPAT_COLLECT`

`mpat lock` and `mpat check` set `MPAT_COLLECT=1` while they import your patch
modules. In that mode `patch()` and `watch()` register the declaration and return
immediately: nothing is patched, no drift is compared, no `until` is evaluated. So
the CLI can read your declarations without changing the behaviour of the process
it runs in. A `when_imported=True` patch registers no import hook in this mode
either; the CLI imports its target directly when it fingerprints it.

The variable is restored to its previous value afterwards. You do not normally set
it yourself. Set it if you need to import a patch module for inspection without
applying anything.

## `MPAT_STRICT`

Unrelated to the CLI commands above, and read at import time by `patch()` and
`watch()`. `MPAT_STRICT=1` turns every drift warning into `UpstreamDriftError`,
whatever each declaration's `on_drift` asked for. See
[`on_drift`](usage.md#on_drift).
