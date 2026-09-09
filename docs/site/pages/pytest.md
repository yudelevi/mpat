# pytest

`mpat.testing` gives you the same check as a pytest test, so drift shows up in the
same run as everything else.

```python
# tests/test_upstream.py
from mpat.testing import test_upstream_drift, test_patch_still_needed
```

Importing the names is the whole setup. Both are parametrized tests, so pytest
collects them from your module as if you had written them there.

## `test_upstream_drift`

One test per declared target, per `depends_on` entry, and per lockfile entry that
is no longer declared. The test id is the dotted target, so a failure names the
symbol directly.

```
FAILED tests/test_upstream.py::test_upstream_drift[somelib.client.Client.request]
  AssertionError: somelib.client.Client.request: body. Works around somelib#123.
```

The status is the same value `mpat check` prints, and the text after it is the
declaration's `note`. See [the status table](cli.md#statuses).

## `test_patch_still_needed`

One test per patch that has an `until`. It fails once the condition comes true,
which is the point at which the patch should be deleted.

```
FAILED tests/test_upstream.py::test_patch_still_needed[somelib.client.Client.request]
  AssertionError: somelib.client.Client.request: upstream fixed, delete this patch.
    Works around somelib#123.
```

Patches with no `until` produce no test here.

## The unconfigured case

If the project has no `pyproject.toml`, or `[tool.mpat] modules` is empty,
`test_upstream_drift` runs once and fails:

```
FAILED tests/test_upstream.py::test_upstream_drift[<unconfigured>]
  AssertionError: <unconfigured>: unconfigured.
    no [tool.mpat] modules found; add modules to pyproject.toml
```

It fails rather than skipping on purpose. A typo in the module list would
otherwise collect zero tests and leave the suite green with no coverage at all.

`test_patch_still_needed` collects nothing in that case, because there are no
declarations to read `until` from.

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

Both are called at collection time, which means your patch modules are imported
during collection with `MPAT_COLLECT=1`. Nothing is patched by the import.

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
