# Lockfile

`mpat.lock` is TOML. It lives next to `pyproject.toml`, at the root `mpat` finds
by walking up from the working directory. Commit it.

`mpat lock` writes it. Nothing else does.

## Format

One table per target under `[entry.…]`, keyed by the dotted path and sorted by
key. A top-level `version` gates the format.

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

## Fields

| Field | Meaning |
| --- | --- |
| `role` | `patch`, `watch` or `depends_on` |
| `declared_in` | The file the declaration came from, relative to the project root |
| `parent` | Present on `depends_on` entries only, naming the target that declared it |
| `track_value` | `false` when the target was declared with `watch(..., track_value=False)`; omitted otherwise |
| `kind` | `function`, `class`, `attribute` or `module` |
| `resolved` | Where the target actually resolved to, which is not always where you named it |
| `is_async` | True for coroutine functions |
| `signature` | `inspect.signature` of the object, for functions |
| `source_hash` | `sha256:` of the unparsed AST node of the definition |
| `value_repr` | `repr()` of a scalar or tuple-of-scalars attribute, or `<unhashable>` |
| `source_file` | The source file, relative to the installed package's root |
| `dist` | The distribution that installs the target's top-level package |
| `dist_version` | The installed version of that distribution |
| `no_source` | True when the target should have had a source hash and did not |

Fields with a `None` value are omitted from the table. Entries for constants
carry `value_repr` instead of `source_hash` and `source_file`; entries for
functions and classes carry the hash and no `value_repr`. A constant declared
with `track_value=False` carries `track_value = false` and no `value_repr`, so only its
existence and kind are compared.

`resolved` is the interesting one. It is derived from the object's `__module__`
and `__qualname__`, so if upstream moves a function and re-exports it under the
old name, the dotted path you wrote stays the same while `resolved` changes, and
`mpat check` reports `moved`.

`source_hash` is the hash of the source, not of the compiled object. A reformat
that changes only whitespace or comments does not change it, because the AST is
unparsed before hashing. A change to a default value, a renamed local, or a new
line of logic does.

## Targets without source

Builtins, compiled extensions and `.pyc`-only installs have no readable source
file. Those entries carry `no_source = true`, no `source_hash`, and are compared
on signature and async-ness alone, never on a body hash. `mpat lock` warns once
per such entry, and `mpat check` marks the row `[signature-only]`.

An entry that had a hash and now has none reports `no_source` rather than `body`,
because the body did not necessarily change: it just stopped being visible.

## Atomic write

`mpat lock` writes to a temporary file in the same directory, sets mode `0644`,
and then `os.replace`s it over `mpat.lock`. An interrupted or failing write leaves
the previous lockfile intact and removes the temporary file. There is no window
where `mpat.lock` is half-written, so a `mpat lock` killed mid-run does not break
the next `mpat check`.

## Version gate

`version` must be an integer that this release supports. Version 1 is the only
supported value today.

Anything else is a `LockError`, which is exit 2 from `mpat check` rather than a
silently wrong comparison:

```
mpat: mpat.lock: lock version 2 not supported (supported: (1,))
```

At import time the rules are softer. `patch()` and `watch()` read the same file to
decide about drift, and an unreadable lockfile there produces a warning and no
drift checking, rather than breaking your application's startup.

## What to commit

Commit `mpat.lock`. It is the record `mpat check` compares against, so without it
in the repository every target reports `unlocked` in CI.

Re-run `mpat lock` and commit the result when you add or remove a declaration,
and when you have read an upstream change and decided the patch is still correct.
Never run `mpat lock` to clear a red build you have not read: that overwrites the
evidence with whatever upstream now happens to be.

The lockfile records versions, so a dependency bump plus a `mpat lock` in the
same commit shows exactly which upstream change was reviewed.
