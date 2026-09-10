# Changelog

## 0.2.2

- Configuration can live in a standalone `mpat.toml` next to `pyproject.toml`.
  It carries the same keys without the `[tool.mpat]` prefix, so
  `[[tool.mpat.watch]]` becomes `[[watch]]`. When both files are in the same
  directory `mpat.toml` wins and the `[tool.mpat]` section is ignored, the rule
  `ruff` and `ty` follow. Error messages, `declared_in` and the source column of
  `mpat check` name whichever file the declaration came from.
- New `mpat diff <target>` prints the drift status of one locked target and every
  fingerprint field as `locked -> current`. It reads `mpat.lock` and resolves the
  target without importing `[tool.mpat] modules`. Exit 2 when the target is not
  in the lock.
- `on_drift` and `MPAT_STRICT=1` now cover `depends_on` at import. A dependency
  whose lock entry no longer matches warns, skips or raises together with its
  patch, and the message says which entry drifted. Before, only `mpat check` and
  the pytest plugin looked at dependencies.
- `[tool.mpat] modules` and `allow` must be lists of strings, `tool.mpat` must
  be a table, and unknown keys in the section are configuration errors. A bare
  string in `modules` used to be split into single characters.
- A source file that cannot be read or parsed no longer crashes `mpat lock` or
  `mpat check`; the target is fingerprinted by signature only and marked
  `no_source`, like a `.pyc`-only install.
- A name defined more than once in one file, in `if`/`else` or `try`/`except`
  branches, is now hashed at the definition that is actually live, matched by
  the code object's first line. The first same-named definition used to win.
- A target whose module raises `ImportError` on import now fails as a named
  configuration error (exit 2) instead of a bare traceback, and says that a
  module that has to be imported first belongs in `[tool.mpat] modules`. The new
  `TargetImportError` subclasses both `MpatError` and `ImportError`, so it is
  still caught by anything that caught the raw error, and it is not
  `TargetNotFound`, so `mpat check` never reports a broken environment as
  `missing`.

## 0.2.1

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
- New `[[tool.mpat.override]]` tables assign a scalar upstream attribute from
  `pyproject.toml`, and new `mpat.apply_overrides()` applies them once per
  process; that call is the only code an override needs. The target must be an
  existing attribute of exactly the value's type, `bool` and `int` included,
  and it goes through the same drift check as a `watch()` before assignment.
  Each override is locked as a watch with `track_value = false`, so `mpat check`
  is green whether or not the override was applied in that process.
- `watch()` accepts `until`, the same `Version`, `Probe`, `|` and `&` as
  `patch`. A watch applies nothing, so the condition has no runtime effect; it
  gives the workaround a generated `still-needed[<target>]` test, which is what
  a per-instance `setattr` wrapper or an attribute-creation shim needed instead
  of a hand-written one. `[[tool.mpat.watch]]` entries take `until` as a
  string: a requirement with a version specifier, or a dotted path to a
  zero-argument callable that is imported when first evaluated.
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
