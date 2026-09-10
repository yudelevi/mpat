# Changelog

## Unreleased

- Fixed: the pytest plugin looked for `[tool.mpat]` only at pytest's rootdir,
  so in a monorepo with one `pyproject.toml` per service and none at the root,
  `pytest services/api/tests` from the root silently collected nothing. The
  plugin now walks up from each directory argument to its nearest
  `pyproject.toml` and collects one set of items per `[tool.mpat]` it finds,
  each reading the lockfile next to its own `pyproject.toml`. A project that is
  not the rootdir's own is collected as `mpat[services/api]::...`; the rootdir's
  own project keeps the `mpat::...` node ids it had.

## 0.2.0

- The generated drift tests are now collected by a pytest plugin. Installing
  mpat is enough: a project with `[tool.mpat]` in its `pyproject.toml` gets
  `mpat::drift[...]` and `mpat::still-needed[...]` items in its normal pytest
  run, with no test module to write. A project with no `[tool.mpat]` collects
  nothing.
- The generated items are collected by `pytest` and by `pytest <dir>`. An
  argument that names a file or a nodeid selects only what it names, since items
  that belong to no file cannot be narrowed by a path.
- Fixed: collecting the generated tests imports your patch modules normally, so
  the patches apply and later tests in the session see patched behaviour.
  Previously both the plugin and `mpat.testing` imported them in the collect-only
  mode the CLI uses, which cached them unpatched and made a suite pass or fail
  depending on file order.
- New `--no-mpat` flag, and a `mpat = false` ini option, to turn the generated
  items off.
- `from mpat.testing import test_upstream_drift, test_patch_still_needed` keeps
  working. When that module is collected, the plugin adds nothing, so the checks
  never run twice.

## 0.1.0

First release. See https://github.com/yudelevi/mpat/releases/tag/v0.1.0.
