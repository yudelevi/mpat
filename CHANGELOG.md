# Changelog

## Unreleased

- New `patch(..., when_imported=True)`. The declaration registers without
  importing the target; the patch is applied by a post-import hook the first
  time the target's top-level module is imported, or immediately if it already
  is. This makes optional dependencies patchable: a target whose distribution is
  not installed no longer raises at declaration, it just never fires.
  `mpat lock` and `mpat check` are unchanged, so the target still has to be
  importable when you lock.
- New `mpat.apply_all()`, which imports every module a `when_imported` patch is
  still waiting on and so applies them all now.
- New `watch(..., track_value=False)` records a scalar's existence and kind but not
  its value. Use it for an upstream setting your application assigns itself,
  where `mpat lock` sees upstream's default and the pytest plugin sees your
  override. The lock entry carries `track_value = false` and no `value_repr`, and
  changing `track_value` on a locked target reports `unlocked` until the next
  `mpat lock`, like a role or `depends_on` change.
- Watches can be declared in `pyproject.toml` as `[[tool.mpat.watch]]` tables
  with `target`, `depends_on`, `review_by` and `note`, so a project with no
  patch module still gets `mpat lock`, `mpat check` and the pytest items. The
  entry is locked with `declared_in = "pyproject.toml"`. A target declared
  both in code and in `pyproject.toml` is refused as declared twice.

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
