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

One item per patch that has an `until`. It fails once the condition comes true,
which is the point at which the patch should be deleted.

```
FAILED mpat::still-needed[somelib.client.Client.request]
  somelib.client.Client.request: upstream fixed, delete this patch.
    Works around somelib#123.
```

Patches with no `until` produce no item here.

## The unconfigured case

A project with `[tool.mpat]` and an empty `modules` collects one item, and it
fails:

```
FAILED mpat::unconfigured
  <unconfigured>: unconfigured.
    no [tool.mpat] modules found; add modules to pyproject.toml
```

It fails rather than skipping on purpose. A typo in the module list would
otherwise collect zero items and leave the suite green with no coverage at all.

A project with no `[tool.mpat]` section is a different case: the plugin stays
inert, collects nothing, and says nothing.

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

Both read your patch modules, which means they are imported with
`MPAT_COLLECT=1` at collection time. Nothing is patched by the import, and the
plugin imports them once per session.

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
