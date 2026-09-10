# pytest

Installing mpat is the whole setup. mpat ships a pytest plugin that registers
itself through the `pytest11` entry point, so any project with `[tool.mpat]` in
its `pyproject.toml` gets the drift checks in its normal pytest run.

```
mpat::drift[somelib.client.Client.request] PASSED
mpat::still-needed[somelib.client.Client.request] PASSED
```

The items are generated, not written. They live under a `mpat` collector rather
than a file of yours, and they carry the `mpat` marker, so `-m mpat` runs only
them and `-k drift` narrows further.

Disable the whole set with `--no-mpat`, or permanently:

```toml
[tool.pytest.ini_options]
mpat = false
```

## When they are collected

Because the items belong to no file, an argument that names one cannot select
them, and inventing a rule for that would mean `pytest tests/test_client.py`
quietly running work you did not ask for. So the plugin looks at the arguments:

| Invocation | Generated items |
| --- | --- |
| `pytest` | collected |
| `pytest tests/` (any directory) | collected |
| `pytest tests/test_client.py` | left out |
| `pytest tests/test_client.py::test_retry` | left out |
| `pytest --no-mpat` | left out |

Directories include the ones `testpaths` supplies, so a project with
`testpaths = ["tests"]` gets them from a bare `pytest`. CI and a bare local
`pytest` are covered; running one file while you work on it stays fast.

`pytest-xdist` works under both `--dist load` and `--dist loadfile`. The items
are ordered by target rather than by the order your patch modules happen to
import, so every worker collects the same list.

## Monorepos

The plugin finds `[tool.mpat]` by walking up from each directory you pass, not
only from pytest's rootdir. In a repository with one `pyproject.toml` per
service and none at the root, `pytest services/api/tests` run from the root
collects the api service's items, and `pytest services/api/tests
services/web/tests` collects both sets. Each service reads its own `mpat.lock`.

When the project is not the rootdir's own, its collector is named after the
directory, so the two sets never share a node id:

```
mpat[services/api]::drift[somelib.client.Client.request] PASSED
mpat[services/web]::drift[otherlib.Session.open] PASSED
```

A project whose `pyproject.toml` is the rootdir's keeps the plain `mpat::` name,
and is collected whenever the arguments are directories, as before. Nothing
below the rootdir is scanned for you: a bare `pytest` at a root with no
`[tool.mpat]` collects no items, so set `testpaths` to the service test
directories if you want a bare `pytest` to cover them.

## `drift[...]`

One item per declared target, per `depends_on` entry, and per lockfile entry that
is no longer declared. The item name is the dotted target, so a failure names the
symbol directly.

```
FAILED mpat::drift[somelib.client.Client.request]
  somelib.client.Client.request: body. Works around somelib#123.
```

The status is the same value `mpat check` prints, and the text after it is the
declaration's `note`. See [the status table](cli.md#statuses).

## `still-needed[...]`

One item per patch or watch that has an `until`. It fails once the condition
comes true, which is the point at which the workaround should be deleted.

```
FAILED mpat::still-needed[somelib.client.Client.request]
  somelib.client.Client.request: upstream fixed, delete this patch.
    Works around somelib#123.
```

The message names the role, `patch` or `watch`, so a failing item for a watch
reads `upstream fixed, delete this watch`. Declarations with no `until` produce
no item here.

## The unconfigured case

A project with `[tool.mpat]` but no `modules` and no `[[tool.mpat.watch]]`
entries collects one item, and it fails:

```
FAILED mpat::unconfigured
  <unconfigured>: unconfigured.
    [tool.mpat] declares nothing; add modules, [[tool.mpat.watch]] or [[tool.mpat.override]] to pyproject.toml, or the same keys without the tool.mpat prefix to mpat.toml
```

It fails rather than skipping on purpose. A typo in the module list would
otherwise collect zero items and leave the suite green with no coverage at all.

A project with no `[tool.mpat]` section and no `mpat.toml` is a different
case: the plugin stays inert, collects nothing, and says nothing.

## The explicit form

The pre-plugin form still works, for projects that would rather see the tests in
a file they own:

```python
# tests/test_upstream.py
from mpat.testing import test_upstream_drift, test_patch_still_needed
```

When both are present the explicit module wins and the plugin adds nothing, so
the checks never run twice.

## Programmatic access

The two functions the tests are built from are public if you want the results
yourself.

```python
from mpat.testing import drift_results, until_declarations

for result in drift_results():
    print(result.target, result.status)
```

`drift_results()` returns the same `CheckResult` objects behind `mpat check`.
`until_declarations()` returns the declarations that have an `until`.

Both read your patch modules, which means your patch modules are imported at
collection time, once per session. They are imported normally, not in the
collect-only mode `mpat check` uses, so the patches apply and stay applied for
the rest of the session. That is deliberate: a module imported with its patches
suppressed would be cached that way, and every later test expecting patched
behaviour would see pristine upstream instead. A `when_imported=True` patch is
applied during collection too, because fingerprinting its target imports the
module the patch is waiting on.

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

The hook has to run inside the project environment, because `mpat check` imports
your patch modules and the upstream packages they target. An isolated hook
environment would not have them, which is why this is `repo: local` with
`language: system` rather than a hosted hook.
