# Changelog

## 0.2.0

- The generated drift tests are now collected by a pytest plugin. Installing
  mpat is enough: a project with `[tool.mpat]` in its `pyproject.toml` gets
  `mpat::drift[...]` and `mpat::still-needed[...]` items in its normal pytest
  run, with no test module to write. A project with no `[tool.mpat]` collects
  nothing.
- The generated items are collected by `pytest` and by `pytest <dir>`. An
  argument that names a file or a nodeid selects only what it names, since items
  that belong to no file cannot be narrowed by a path.
- New `--no-mpat` flag, and a `mpat = false` ini option, to turn the generated
  items off.
- `from mpat.testing import test_upstream_drift, test_patch_still_needed` keeps
  working. When that module is collected, the plugin adds nothing, so the checks
  never run twice.

## 0.1.0

First release. See https://github.com/yudelevi/mpat/releases/tag/v0.1.0.
