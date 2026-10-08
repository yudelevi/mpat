# CLI

Four commands. `lock`, `check` and `diff` read the configuration from the
nearest `mpat.toml` or `pyproject.toml`, so run them from anywhere inside the
project.

```toml
# pyproject.toml
[tool.mpat]
modules = ["myapp.patches"]
```

```toml
# mpat.toml, the same keys without the [tool.mpat] prefix
modules = ["myapp.patches"]
```

Walking up from the working directory, the first directory that has either file
is the project root. In that directory `mpat.toml` wins and any `[tool.mpat]` in
`pyproject.toml` is ignored, the same rule `ruff` and `ty` use.

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
target that is not `ok`. This is the command for CI and for dependency-bump
merge requests.

```
$ mpat check
# somelib
body       patch      somelib.client.Client.request myapp/patches.py 2.3.1 -> 2.5.0  Works around somelib#123. Delete when fixed.
value      watch      somelib.settings.MAX_RETRIES  myapp/patches.py 2.3.1 -> 2.5.0  see somelib#98

2 drifted: read the upstream change, then fix or delete the patch and run 'mpat lock'
1 ok (--all lists them)
```

`ok` targets are only counted, so a clean run prints a single line. `--all`
lists them in the table too.

A target used by more than one declaration lists the others under its row, as
`used by: myapp/retry.py:40 (patch somelib.retry.backoff)`, so a shared
dependency that drifted points at every patch that needs a look.

`--diff` adds the [`mpat diff`](#mpat-diff) block under each drifted row. It
downloads the locked release, so it is opt-in; add it to the CI job to get the
upstream change in the log:

```yaml
- uv run mpat check --diff
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
| `docstring` | The source hash changed, but only in docstrings. |
| `content` | A watched file's bytes changed. |
| `value` | A watched constant's value changed. |
| `no_source` | The locked source is no longer readable, so only the signature is still comparable. |
| `missing` | The target no longer exists upstream. |
| `unlocked` | Declared in code but absent from `mpat.lock`. |
| `stale` | In `mpat.lock` but no longer declared in code. |
| `review` | Nothing drifted, but `review_by` has passed. |

Drift beats `review`: a target that both drifted and is overdue reports the drift.

A row with a changed signature, value, location or kind lists the change under
it, so the table says what moved without a separate `mpat diff`:

```
signature  patch      somelib.client.Client.request myapp/patches.py 2.3.1 -> 2.5.0
    signature: (self, method, url, **kwargs) -> (self, method, url, *, timeout, **kwargs)
```

Hash changes are not listed; `body` and `content` already say that much, and
`mpat diff TARGET` shows the hashes.

A row whose target currently has no readable source is marked, whatever its
status, including `ok` rows listed with `--all`:

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
1 docstring-only: skim the upstream docs change, then run 'mpat lock'
1 stale: run 'mpat lock' to prune
1 due for review
3 ok (--all lists them)
```

`unlocked` and `stale` mean the lockfile is out of date with your code, so the
answer is `mpat lock`. `drifted` means upstream changed under you, so the answer
is to read the upstream change first. `docstring` still fails the check, because
a docstring edit can announce a behaviour change, but it is counted apart from
the drift that needs a code review.

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
    "dist": "somelib",
    "declared_line": 12,
    "changes": [],
    "used_by": []
  }
]
```

The list is in declaration order, with `stale` entries appended. Exit codes are
the same as for the table.

### `--gitlab`

Prints a [GitLab Code Quality](https://docs.gitlab.com/ci/testing/code_quality/)
report of every target that is not `ok`, so drift shows up on the merge request
instead of only in the job log.

```
$ mpat check --gitlab
[
  {
    "description": "patch somelib.client.Client.request: the upstream body changed (Works around somelib#123.)",
    "check_name": "mpat/body",
    "fingerprint": "8f14e45fceea167a5a36dedd4bea2543...",
    "severity": "major",
    "location": {
      "path": "services/api/myapp/patches.py",
      "lines": {"begin": 12}
    }
  }
]
```

`missing` and `moved` are `blocker`, the fingerprint mismatches and `unlocked`
are `major`, `no_source`, `docstring` and `review` are `minor`, `stale` is
`info`. The description ends with the same changed fields the table lists.
Every other declaration that uses a drifted target gets its own issue at its own
line, so the merge request marks each patch that depends on it.
`lines.begin` is the line of the `patch()` or `watch()` call, or for a
configuration table and a `stale` entry the first line quoting the target. The
fingerprint is a hash of the path, role and target, so the same drift keeps the
same identity between runs and GitLab does not report it twice.

GitLab reads `location.path` relative to the repository root, but `mpat check`
runs where the configuration file is, which in a monorepo is a subdirectory. So
the paths are resolved against the nearest directory above the `mpat` root that
has a `.git`, and `--repo-root PATH` overrides that for a checkout where the two
do not line up. Getting this wrong is silent: GitLab drops annotations whose
path does not exist rather than reporting them.

`location.lines.begin` is the first line of the declaring file that mentions the
target in quotes, and line 1 when there is none, which is what happens for a
target declared as an object rather than a dotted string.

The report goes to stdout, and `mpat check` still exits 1 on drift, so the job
has to keep the artifact anyway:

```yaml
mpat:
  script:
    - mpat check --gitlab > gl-code-quality.json
  artifacts:
    when: always
    reports:
      codequality: gl-code-quality.json
```

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

## `mpat diff`

Prints the drift status of one locked target, every fingerprint field whose
locked value differs from the installed one, and then how its upstream source
changed.

```
$ mpat diff somelib.client.Client.request
status: body
source_hash: sha256:0684635… -> sha256:9b1c7ee…
dist_version: 2.3.1 -> 2.5.0

body[patch] somelib.client.Client.request: the upstream body changed (2.3.1 -> 2.5.0)
    --> somelib/client.py:92
      |
88 90 |       def request(self, method, url, **kwargs):
89 91 |           self.log(method)
90    | -         return self._send(method, url, **kwargs)
   92 | +         return self._send(method, url, timeout=self.timeout, **kwargs)
      |
info: declared here
    --> myapp/patches.py:12
info: also used by myapp/retry.py:40 (patch somelib.retry.backoff)
```

The two gutter columns are the line numbers in the locked and the installed
file. `mpat diff` with no target prints the block for every drifted target.

The lock records hashes, not source, so the locked side is downloaded: the
archive of the locked `dist_version` of the target's distribution, a wheel if
there is one and the sdist otherwise. The index is the first of
`UV_DEFAULT_INDEX`, `UV_INDEX_URL` and `PIP_INDEX_URL` that is set, else PyPI.
Credentials in the index URL, or in `~/.netrc` for its host, are sent to that
host only, never to a host a download redirects to. Archives are cached in
`MPAT_CACHE_DIR`, default `~/.cache/mpat`; point it at a CI cache path to keep
them across jobs. The downloaded definition is hashed again, and a line warns
when it does not match the lock, which happens when a release was rebuilt or
ships per-platform code.

Only `body`, `content`, `docstring`, `signature`, `moved` and `missing` get a
block; `missing` and `moved` show the locked definition as removed. When the
archive cannot be fetched the block says `info: upstream diff unavailable` and
why, and the exit code does not change.

`mpat diff` imports `[tool.mpat] modules` like `mpat check`, so the block knows
where the target is declared and what else uses it.

Exits 0 on `ok`, 1 on any other status, 2 when the target is not in `mpat.lock`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Clean |
| 1 | `mpat check` found at least one row that is not `ok` |
| 2 | Usage or configuration error |

`review` is not `ok`, so an overdue `review_by` fails `mpat check` too.

Exit 2 covers a missing `pyproject.toml` or `mpat.toml`, a configuration that
declares nothing, an unreadable or unsupported `mpat.lock`, an unresolvable
target passed to `mpat show`, and a target passed to `mpat diff` that is not in
the lock. The message goes to stderr with an `mpat: ` prefix.

```
$ mpat check
mpat: [tool.mpat] in pyproject.toml lists no modules and no watch entries; nothing to collect
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
