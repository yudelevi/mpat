# mpat — design spec

Date: 2026-09-09
Status: approved for planning (revised after Codex review)

## Problem

Application code monkeypatches third-party libraries to work around upstream
bugs. Each patch depends on upstream internals (function bodies, signatures,
call sites, constants, registries) that change without notice on every
Renovate bump. Today those dependencies are guarded by hand-written canary
tests. Observed failure modes in `discolike-platform` (7 patches, 5 non-patch
contract pins):

- Canary too loose (`hasattr`): upstream keeps the name, changes semantics,
  patch is silently wrong. Found in prod. This is the worst case.
- No canary at all for older patches, because writing one is effort.
- Canary lives far from the patch; one is updated, the other forgotten.
- Canary too strict: harmless upstream refactors turn it red; "canary needs
  updating" is indistinguishable from "upstream broke us" without reading the
  test.

## Goal

A small Python library plus CLI that:

1. Declares a monkeypatch in one place with a decorator.
2. Records a fingerprint of every upstream symbol the patch depends on in a
   lockfile, the way `uv.lock` records dependency versions.
3. Reports drift between the lockfile and the installed upstream, classified
   (symbol missing, signature changed, body changed, value changed, moved),
   in CI on Renovate MRs and optionally in pre-commit and pytest.
4. Makes re-locking after review a single command, so "canary needs updating"
   never means editing tests.

Runtime default is to warn on drift and still apply the patch. Each patch can
opt into skipping or raising instead. Judging whether a change is *breaking*
stays a human decision after reading the report. Automatic breaking-change
judgment is explicitly a future product.

## Naming

- Distribution and import name: `mpat` (free on PyPI; `monkeypatch` is a dead
  2011 package and also collides with the pytest built-in fixture name, which
  would shadow the module inside any test function taking the fixture).
- Domain: monkeypat.ch. CLI: `mpat`. Lockfile: `mpat.lock`.
- Optional later: PEP 541 claim on `monkeypatch` as an alias. Vanity, separate
  track.

## Prior art (and why this is not them)

- `wrapt`: patching primitives. No drift tracking. Not used as a dependency in
  v1; plain `setattr` covers all observed cases.
- `gorilla`: `@gorilla.patch` decorator, unmaintained. No drift tracking.
- `patchy` (adamchainz): applies a unified diff to a function's source in
  memory and swaps `__code__`. Drift detection by construction (context
  mismatch raises at apply). Function bodies only; raises at import; no
  warn mode, classification, lock, CI report, `until`, or generated tests.
  Treated as a complementary apply backend for v2 (`diff=`), and mentioned in
  the README.
- `patch-package` / `pnpm patch` (JS): file-level diffs against
  `node_modules`, applied at install, stored with the exact version. The
  semantics to steal (version-pinned, loud on bump, one command to re-lock).
  The mechanism does not port: Python has no post-install hook, wheels are
  cached and hardlinked, and it cannot self-disable at runtime.

## Public API

```python
from mpat import patch, watch, Version, Probe

@patch(
    "litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.transform_request",
    depends_on=[
        "litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.async_transform_request",
    ],
    until=Version("litellm>=1.60") | Probe(upstream_downgrades_deepseek_json_schema),
    on_drift="skip",
    review_by=date(2026, 12, 1),
    note="Mirrors BerriAI/litellm#31466. Delete when merged.",
)
def transform_request(original, self, model, messages, optional_params, *args, **kwargs):
    ...
    return original(self, model, messages, optional_params, *args, **kwargs)

watch("engineio.payload.Payload.max_decode_packets", note="see zauberzeug/nicegui#209")
Payload.max_decode_packets = 500
```

### `patch(target, *, depends_on=(), until=None, on_drift="warn", review_by=None, note="")`

- `target`: dotted string `module.qualname`, or an object. Objects are sugar
  and are resolved to the string via `__module__` and `__qualname__`; the
  string is the canonical identity used in the lockfile.
- The decorated function receives `original` as its first positional argument.
  `functools.wraps(original)` is applied to the replacement.
- Applies at decoration time.
- The replacement must match the original's kind: `async def` for a coroutine
  function original, plain `def` otherwise. Mismatch raises `KindMismatch`
  at decoration. Generators and async generators are supported the same way.
- `depends_on`: extra targets to fingerprint without patching. Used for
  upstream call sites, sibling functions, and decorator implementations whose
  change would make the patch a silent no-op (for example a release that
  stops calling `close_tab` on disconnect).
- `until`: an `Until` condition (see below). When it evaluates true the patch
  is not applied and an INFO log line with `note` is emitted.
- `on_drift`: `"warn"` (default) warns and applies; `"skip"` warns and leaves
  upstream unpatched; `"raise"` raises `UpstreamDriftError`. Environment
  variable `MPAT_STRICT=1` forces `"raise"` for every patch and watch.
- `review_by`: a `datetime.date`. Has no runtime effect. After the date,
  `mpat check` and the generated pytest test report `review`. This is
  deliberately separate from `until`: a calendar date is a nag, never a
  switch that changes production behaviour at midnight.
- `note`: free text printed in every drift report and test failure for this
  target. Convention: name the upstream issue and what to delete when fixed.

### `watch(target, *, depends_on=(), review_by=None, note="")`

Fingerprint only, no replacement. Plain call at module level. Covers
constants, registry dicts, call sites, version strings, and any upstream
contract the application relies on without patching.

### `Until` conditions

```python
class Until(Protocol):
    def __call__(self) -> bool: ...  # True means upstream is fixed; skip the patch
```

Built-ins:

- `Version("litellm>=1.60")`: PEP 440 specifier evaluated against the
  installed version of the distribution that provides the target's top-level
  module (`importlib.metadata.packages_distributions()`).
- `Probe(fn)`: wraps a user callable returning bool. Must be cheap and pure;
  it runs once at apply time and once per generated test run.
- Combinators `|` and `&` on any `Until`.

Anything requiring network (ticket merged, PR state) is out of scope for the
core and is implemented by users via the protocol.

### Supported targets

Patchable: plain functions, methods, `staticmethod`, `classmethod`, including
coroutine functions and generators. Everything else (`property`, custom
descriptors, attributes provided by a metaclass, `__slots__` members, module
attributes that are not callables) raises `UnsupportedTarget` at decoration,
before anything is mutated. All of these can still be `watch`ed.

### Runtime drift behaviour

- If `mpat.lock` is found and has an entry for the target, the fingerprint is
  computed and compared at apply time. On drift, behave per `on_drift`.
  Warnings are `UpstreamDriftWarning`, emitted once per target, pointing at
  the declaration site.
- If no lock is found, or the lock has no entry for the target: silent.
- The lock is read at most once per process.
- `MPAT_COLLECT=1`: declarations register but do not apply. Used by the CLI
  and by `mpat.testing`.

### Idempotency and identity

A declaration's identity is `(declaring file, decorated function qualname)`.

- Re-executing the same declaration (module reload, a second app factory in
  the same process) finds the existing replacement via `__mpat_identity__`,
  restores `__mpat_original__`, and re-applies. No nesting, no error.
- A different declaration targeting the same dotted target, or the same
  resolved object under another alias, raises `AlreadyPatched`. The registry
  keys on both the dotted target and `id()` of the resolved original.
- If the target was already replaced by something that is not mpat (another
  library, or the application's own earlier code), the file-based fingerprint
  is unaffected and `original` is whatever is live. Documented, not fought.

### Errors

All subclass `mpat.MpatError`.

- `TargetNotFound`: target does not resolve. Raised at decoration, always. A
  patch that cannot be applied is a bug, not a warning.
- `UnsupportedTarget`: target resolves to something in the unsupported list.
- `KindMismatch`: async/sync or generator kind differs between original and
  replacement.
- `AlreadyPatched`: a different declaration already patched this target.
- `ForbiddenTarget`: target matches the denylist (see Security posture).
- `UpstreamDriftError`: drift with `on_drift="raise"` or `MPAT_STRICT=1`.
- `LockError`: malformed lock or unsupported lock `version`. Raised by the
  CLI. At runtime it is downgraded to a single warning and the lock is
  ignored, because a broken lock must not break the application.

## Fingerprint

Computed from the installed **source file** via `inspect.getsourcefile` and
`ast`, never from the live object. Other code (including test suites) applies
patches in-process, so hashing the live attribute would hash our own
replacement depending on import order.

Fields per target:

| Field | Content |
|---|---|
| `kind` | `function`, `class`, `attribute`, `module` |
| `is_async` | callable is a coroutine function (`inspect.iscoroutinefunction` on the resolved object) |
| `signature` | `str(inspect.signature(obj))` for callables, else absent |
| `source_hash` | `sha256` of `ast.unparse(node)` where `node` is the `FunctionDef`/`AsyncFunctionDef`/`ClassDef` located by name in the source file. The node includes `decorator_list`, so decorators applied at the definition site are part of the hash. Formatting, comments and blank lines do not flip it. Docstrings do. |
| `value_repr` | For `attribute` kind: `repr(value)` when the value is `int`, `str`, `bool`, `float`, `None`, or a tuple of those. Otherwise `"<unhashable>"` and only existence is checked. |
| `resolved` | The `module.qualname` where the object is actually defined (after following re-exports and lazy `__getattr__`), plus the source file path relative to the distribution root. |
| `dist`, `dist_version` | Informational, printed in reports. Never compared for drift: a version bump with unchanged source is not drift. |
| `no_source` | `true` whenever `getsourcefile` or `getsource` fails or the definition node cannot be located: builtins, compiled extensions, `.pyc`-only installs, zipapps, frozen apps, dynamically created functions. Only `signature` and `is_async` are compared. Warned once at `lock` time. |

- Decorator bodies living elsewhere than the definition site are not covered
  by the hash. Declare them in `depends_on` when the patch relies on them.
  `inspect.unwrap` is not used.
- `class` kind hashes the whole class body. Coarse and honest; pin
  `Class.method` explicitly for granularity.
- Properties are `attribute` kind and hash the `fget` source.
- Re-exports and lazy module `__getattr__` (litellm) are resolved with
  `getattr`; the fingerprint records where the object actually lives in
  `resolved`, so a re-export redirected to another implementation is
  reported as `moved` even when the new implementation has identical source.

## Lockfile

`mpat.lock`, TOML, located beside the nearest `pyproject.toml` walking up from
the current working directory (same rule as `uv.lock`). A monorepo with one
`pyproject.toml` per service gets one lock per service. One table per target,
keyed by the dotted target and sorted, so merge conflicts are per target and
cannot be misresolved by dropping a neighbouring entry. `declared_in` stores
the file only, not the line, so edits above a declaration do not churn the
lock. Written atomically (temp file in the same directory, then
`os.replace`).

```toml
version = 1

[entry."litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.transform_request"]
role = "patch"           # "patch" | "watch" | "depends_on"
declared_in = "src/endpoints/common/litellm_patches.py"
kind = "function"
is_async = false
dist = "litellm"
dist_version = "1.52.3"
resolved = "litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.transform_request"
source_file = "litellm/llms/deepseek/chat/transformation.py"
signature = "(self, model, messages, optional_params, litellm_params, headers)"
source_hash = "sha256:ab12..."

[entry."litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.async_transform_request"]
role = "depends_on"
parent = "litellm.llms.deepseek.chat.transformation.DeepSeekChatConfig.transform_request"
...
```

`depends_on` targets are flat entries with `role = "depends_on"` and a
`parent`. No nesting. `until`, `on_drift`, `review_by` and `note` are not in
the lock; they live in code and are read from the registry.

Supported lock versions are declared by the library. An unsupported version is
`LockError` in the CLI and a single warning plus ignored lock at runtime. A
`mpat migrate` command is added when version 2 exists.

## Drift classification

Per entry, computed by `mpat check` and by the generated pytest tests:

| Result | Meaning | Severity |
|---|---|---|
| `missing` | target no longer resolves | loud |
| `moved` | target resolves to a different definition site | loud |
| `signature` | signature or `is_async` differs | loud |
| `body` | signature same, `source_hash` differs | medium |
| `value` | attribute value differs | loud |
| `unlocked` | declared in code, absent from lock (developer forgot `mpat lock`) | pre-commit case |
| `stale` | in lock, no longer declared | cleanup nag |
| `review` | `review_by` has passed | nag |
| `ok` | no change | |

Exit code is nonzero for anything other than `ok`. Severity affects report
formatting only. Flags to tolerate `body` are deferred until noise is
observed in practice. The CI environment is the authoritative one for
locking; the lock is expected to be produced and checked against the same
resolved dependency set.

## Configuration

```toml
[tool.mpat]
modules = ["endpoints.common.litellm_patches", "app"]
allow = []   # denylist overrides, e.g. ["ssl.SSLContext.load_verify_locations"]
```

`mpat lock`, `mpat check` and `mpat.testing` import the listed modules under
`MPAT_COLLECT=1`. Explicit listing over import discovery: discovery by
importing arbitrary application code is where such tools become flaky.

## Security posture

mpat is not a sandbox and does not claim to be one. Any code executing in the
interpreter can `setattr` anything without going through mpat; there is no
permission boundary inside a Python process. mpat provides two weaker
guarantees and documents them as such:

1. **Guardrail against accidental patches of security-critical code.**
   `patch()` and `watch()` refuse targets matching a denylist with
   `ForbiddenTarget`. Default denylist: `mpat.*`, `builtins.*`, `sys.*`,
   `importlib.*`, `ssl.*`, `hashlib.*`, `hmac.*`, `secrets.*`,
   `cryptography.*`, `certifi.*`. The list is overridable only through
   `[tool.mpat] allow` in `pyproject.toml`, never through a decorator
   argument, so every override appears in a configuration diff that a
   reviewer reads.
2. **Audit trail.** Every declared patch and watch is in `mpat.lock`, the lock
   is committed, and `mpat check` fails CI on `unlocked`. A patch that appears
   in code without a lock entry does not pass the pipeline. Reviewers get one
   file listing everything the application overrides in upstream, with notes.

## CLI

`mpat`, implemented with `argparse`.

- `mpat lock`: collect, fingerprint every declaration and dependency, write
  `mpat.lock` atomically, print what changed versus the previous lock.
- `mpat check [--json]`: collect, compare, print a table grouped by
  distribution with target, role, classification, old and new `dist_version`,
  and `note`. Exit code per classification above.
- `mpat show <target>`: print the current fingerprint and source of a target,
  for eyeballing after drift.
- No `mpat diff`: the lock does not store source snapshots. The report prints
  old and new distribution versions; the upstream changelog or a
  `site-packages` diff is the reader's next step. Snapshots are a v2 option.

A `.pre-commit-hooks.yaml` runs `mpat check`. Cost is the application import
time on every commit; users who find it slow drop the hook and keep CI. The
two failure families (`unlocked` versus upstream drift) are distinguished in
the message so the developer knows whether to run `mpat lock` or to read.

## pytest integration

`mpat.testing` exposes parametrized tests to be imported into one test file
per service:

```python
from mpat.testing import test_upstream_drift, test_patch_still_needed
```

At collection time the module imports the configured `[tool.mpat] modules`
under `MPAT_COLLECT=1`, which registers declarations without applying
anything, then reads the lock. Parametrization comes from the registry (which
carries `until`, `review_by`, `note`) joined with lock entries.

- `test_upstream_drift`: one test per lock entry, fails with the
  classification and `note`.
- `test_patch_still_needed`: one test per patch with `until`, asserts the
  condition is still false, fails with "upstream fixed, delete this patch"
  and `note` when it flips.

Explicit import, no auto-collecting plugin: greppable, opt-in per service, no
magic. Test isolation for the application's own tests is unchanged from
today: importing application modules applies patches, as it always has.

## Runtime flow of `patch`

1. Check the denylist; raise `ForbiddenTarget`.
2. Resolve target to `(parent, attr)`: import the module, walk the qualname
   with `getattr`. Failure raises `TargetNotFound`.
3. Inspect the static attribute with `inspect.getattr_static`. Anything
   outside the supported list raises `UnsupportedTarget`. Compare kind of
   original and replacement; raise `KindMismatch`.
4. Under `MPAT_COLLECT=1`: append a `Declaration` to the registry, return the
   function untouched.
5. Evaluate `until`. If true: log INFO with `note`, register as skipped,
   return the function untouched.
6. If a lock entry exists: fingerprint, compare, act per `on_drift`. On
   `"skip"`, register as skipped and return the function untouched.
7. Idempotency: if the live attribute carries `__mpat_identity__` equal to
   this declaration, restore `__mpat_original__` first. If it carries a
   different identity, or the resolved original's `id()` is already
   registered, raise `AlreadyPatched`.
8. Unwrap `staticmethod` and `classmethod`. Build the replacement closure
   binding `original` as the first argument, apply
   `functools.wraps(original)`, set `__mpat_original__`, `__mpat_target__`,
   `__mpat_identity__`, re-wrap in the same descriptor type,
   `setattr(parent, attr, replacement)`.

`watch` performs steps 1, 2, 4 and 6 (warn or raise only).

## Package layout

```
src/mpat/
  __init__.py       patch, watch, Version, Probe, UpstreamDriftWarning, errors
  _targets.py       resolve(), supported-kind checks, descriptor handling, denylist
  _fingerprint.py   AST hash, signature, attribute repr, resolved location, dist lookup
  _registry.py      Declaration, process-wide registry, identity, collect mode
  _lock.py          TOML read/atomic write, comparison, classification
  _until.py         Until protocol, Version, Probe, combinators
  _cli.py           argparse entry point
  testing.py        test_upstream_drift, test_patch_still_needed
```

Dependencies: `packaging` (PEP 440), `tomli-w` (TOML write; `tomllib` for
read). Python 3.11+.

## Testing strategy for mpat itself

A fixture package `tests/fake_upstream/` containing sync and async functions,
generators, classes, static and class methods, decorated functions,
properties, constants, a registry dict, and a lazy `__getattr__` re-export.
Tests copy it into `tmp_path`, mutate the copy (rename a kwarg, change a
body, delete a symbol, change a constant, redirect the re-export, make a
function async), put the copy on `sys.path`, and assert the classification.
Separate tests cover reload idempotency, alias detection, unsupported targets,
and `.pyc`-only installs (delete the `.py` after compiling). No real
third-party libraries in the suite, so it is deterministic and offline. TDD
throughout.

## Non-goals for v1

- Undo/unpatch, context managers, temporary patches (`patchy`, `mock.patch`).
- Patching properties, custom descriptors, metaclass attributes.
- Per-instance patching (`watch` the class method, patch the instance by
  hand).
- Lazy application (`lazy=True`) and `apply_all()`.
- Inline fingerprints in the decorator (`fingerprint=`) for library authors
  shipping patches without a lock. The kwarg name is reserved.
- Diff-based apply backend via `patchy` (`diff=`).
- Source snapshots in the lock and `mpat diff`.
- Tolerance flags for `body` drift.
- Composition ordering guarantees between patches of related targets beyond
  alias detection.
- Auto-collecting pytest plugin; test isolation for application tests.
- `mpat migrate` (until a lock version 2 exists).
- Automatic breaking-change judgment (future service).
- PEP 541 claim on `monkeypatch`.

## Success criteria

- All seven `discolike-platform` patches and five contract pins port to
  `mpat` with fewer lines than today.
- The four hand-written canary/drift test files are deleted.
- A Renovate MR bumping `litellm` shows an `mpat check` failure naming the
  exact target and classification.
