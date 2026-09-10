# Usage

Everything public lives at the top of the package.

```python
from mpat import Probe, Until, Version, apply_all, apply_overrides, patch, watch
```

## `patch()`

```python
def patch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    until: Until | None = None,
    on_drift: str = "warn",
    review_by: date | None = None,
    note: str = "",
    when_imported: bool = False,
) -> Callable[[F], F]
```

`patch` is a decorator. It replaces the upstream attribute with your function and
records the target so `mpat lock` can fingerprint it.

```python
import somelib.client
from mpat import patch


@patch("somelib.client.Client.request")
def request(original, self, method, url, **kwargs):
    kwargs.setdefault("timeout", 30)
    return original(self, method, url, **kwargs)
```

The replacement receives `original` as its first argument, then the arguments the
caller passed. `original` is the object that was installed at patch time, so
chaining through it keeps the upstream behaviour available. The decorator returns
your undecorated function, not the wrapper, so importing the module twice does
not double-wrap.

A replacement must be the same kind of callable as the original: sync for sync,
async for async, generator for generator, async generator for async generator.

By default the decorator imports the target when it runs. Pass
[`when_imported=True`](#when_imported) to apply the patch on the target's first
import instead.

## `apply_all()`

```python
def apply_all() -> None
```

Imports every module a `when_imported=True` patch is still waiting on, which
applies those patches now. Call it from a startup path that wants every patch in
place before the first request, at the cost of importing the optional
dependencies eagerly. A module that is not installed raises
`ModuleNotFoundError` from here, exactly as `import` would.

## `apply_overrides()`

```python
def apply_overrides() -> None
```

Assigns every [`[[tool.mpat.override]]`](#overrides) value from the nearest
`pyproject.toml`, once per process. With no `pyproject.toml` it does nothing.

## `watch()`

```python
def watch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    until: Until | None = None,
    review_by: date | None = None,
    note: str = "",
    track_value: bool = True,
) -> None
```

`watch` fingerprints a symbol without patching it. Use it for the upstream code
your patch depends on being shaped a certain way, and for constants you read.

```python
from mpat import watch

watch("somelib.settings.MAX_RETRIES", note="see somelib#98")
```

`track_value=False` records that a scalar exists and what kind of thing it is, but not
what it is set to. Use it for a setting your application assigns itself:

```python
import litellm
from mpat import watch

watch("litellm.drop_params", track_value=False, note="we set this in app.config")
```

The lock then carries `track_value = false` and no `value_repr`, so only a rename,
removal or a change of kind is reported. Without it the value is unstable:
`mpat lock` imports only `[tool.mpat] modules` and records upstream's default,
while the pytest plugin runs after your application has imported and sees your
override, so CI stays red no matter which one you lock.

When you do want the value pinned, put the assignment and the `watch()` in the
same module so both `mpat lock` and the test run see the same thing:

```python
import litellm
from mpat import watch

litellm.drop_params = True
watch("litellm.drop_params", note="litellm#1234")
```

Switching `track_value` on an already locked target reports `unlocked` from
`mpat check` until you run `mpat lock` again, the same as changing a target's
role or `depends_on`.

`watch` takes no `on_drift`. A watched target that drifts always warns, and
`MPAT_STRICT=1` turns that warning into an error like it does for a patch.

`watch` does take [`until`](#until). A watch applies nothing, so the condition
changes nothing at runtime; it exists to give the workaround a generated
`still-needed[<target>]` test, for the cases `patch` cannot express, such as a
per-instance `setattr` wrapper or an attribute your code creates on an upstream
object:

```python
from mpat import Probe, Version, watch


def ontospy_still_needs_shim() -> bool:
    return not hasattr(Ontospy, "namespaces")


watch(
    "ontospy.core.ontospy.Ontospy",
    until=Version("ontospy>=2.2") | Probe(ontospy_still_needs_shim),
    note="data/upstream_shims.py adds .namespaces; see ontospy#120",
)
```

## Declaring in `pyproject.toml`

A watch does not need a Python module. `[[tool.mpat.watch]]` declares one in
the same `[tool.mpat]` section that lists your modules:

```toml
[[tool.mpat.watch]]
target = "qdrant_client.async_qdrant_remote.AsyncQdrantRemote.query_points"
depends_on = ["qdrant_client.async_qdrant_remote.AsyncQdrantRemote.scroll"]
review_by = 2026-12-01
note = "protobuf timeout wrapper in data/qdrant.py relies on this shape"
```

`target` is required. `depends_on`, `until`, `review_by` and `note` are
optional and mean what they mean on [`watch()`](#watch); `review_by` is a bare
TOML date. `until` is a string in one of two forms:

```toml
until = "ontospy>=2.2"                                  # Version
until = "data.upstream_shims.ontospy_still_needs_shim"  # Probe
```

A requirement with a version specifier becomes `Version`. A dotted path
becomes a `Probe` around the zero-argument callable it names, imported when
the condition is first evaluated, so the configuration can be read without
importing your application. Anything else is a configuration error.
Any other key, or a key of the wrong type, is a configuration error that names
the entry.

`mpat lock`, `mpat check` and the pytest plugin register these after importing
`[tool.mpat] modules`, so a project can have either or both. The lock entry
carries `declared_in = "pyproject.toml"`. The target is resolved when it is
locked or checked, not when the configuration is read, and the denylist applies
to it and to its `depends_on` the same as in code.

### Overrides

A patch whose whole job is to assign a scalar is an `[[tool.mpat.override]]`
entry:

```toml
[[tool.mpat.override]]
target = "engineio.payload.Payload.max_decode_packets"
value = 500
note = "engineio default 16 500s long-polling clients (zauberzeug/nicegui#209)"
review_by = 2026-12-01
```

`target` and `value` are required; `value` is a TOML integer, float, string
or boolean. `note` and `review_by` are optional. The application applies them
with one call at startup, which is the only code an override needs:

```python
import mpat

mpat.apply_overrides()
```

For each entry `apply_overrides()` resolves the target, checks that the
attribute exists and has exactly the value's type, records the previous value,
and assigns. `bool` is a subclass of `int` in Python but not here: `value = true`
on an int attribute and `value = 1` on a bool attribute both raise
`UnsupportedTarget`, as does a value of the wrong type on a function, a
property or a dict. A second call in the same process assigns nothing. Under
`MPAT_COLLECT=1` nothing is assigned either, so `mpat lock` and `mpat check`
never touch the live object. The denylist applies.

An override is also a watch with `track_value=False`: the lock records that the
attribute exists and what kind of thing it is, never its value, so `mpat lock`
in a fresh process and the pytest plugin after `apply_overrides()` has run
agree. A rename or removal upstream still reports `missing`. A workaround with
any logic in it is still a [`patch`](#patch).

### One declaration per target

A target declared twice, in code and in `pyproject.toml` or in both a `watch`
and an `override` entry, is refused, naming both places. Delete one of them;
there is no precedence.

## Targets

A target is a dotted path to an attribute of a module or a class.

```python
watch("somelib.client.Client.request")
```

`mpat` imports the longest importable module prefix of the path, then walks the
remaining names with `getattr`. A bare module is not a valid target, because
there is no attribute to fingerprint.

You can pass the object itself instead of a string. The dotted path is then
derived from its `__module__` and `__qualname__`.

```python
import somelib.client
from mpat import watch

watch(somelib.client.Client.request)
```

### What is tracked by body

`mpat` parses the source file with `ast` and hashes the unparsed definition node.

| Target | Tracked as |
| --- | --- |
| Function, method, staticmethod, classmethod | Signature, sync/async kind, and body hash |
| Class | Body hash of the whole class statement |
| Property | Body hash of its getter |
| `int`, `str`, `bool`, `float`, `None`, and tuples of those | Value repr |
| Anything else, including dicts, lists and custom descriptors | Existence only |

Mutating a watched registry's contents will not fail `mpat check`, because a dict
is recorded as existence only. Watch the function or class that populates it
instead.

A function defined inside another function cannot be located in the AST, so it
gets no body hash. Same for anything with no readable source file. See
[the lockfile page](lockfile.md#targets-without-source) for how those are locked.

### What can be patched

Only plain functions, staticmethods and classmethods can be patched. Properties,
custom descriptors and metaclass attributes can be watched, not patched.
Per-instance patching, undo, and diff-based patching are out of scope.

### Inherited methods

If the target names a method a class inherits rather than defines, `patch`
resolves the inherited function and installs the wrapper on the class you named.
The base class is left alone, so the other subclasses keep the original.

Two dotted targets that name the same attribute of the same owner are refused as
aliases. The same inherited method on two sibling subclasses is not an alias:
each patch lands on its own class.

```python
@patch("somelib.client.JsonClient.request")   # both inherit request
def json_request(original, self, *args, **kwargs): ...


@patch("somelib.client.XmlClient.request")    # from somelib.client.Client
def xml_request(original, self, *args, **kwargs): ...
```

## Keywords

### `depends_on`

A sequence of dotted paths that get their own lockfile entries with
`role = "depends_on"` and a `parent` pointing back at your target. Use it for the
upstream internals your patch relies on but does not replace.

```python
@patch(
    "somelib.client.Client.request",
    depends_on=["somelib.client.Client._send", "somelib.client.DEFAULT_TIMEOUT"],
)
def request(original, self, method, url, **kwargs): ...
```

Drift in a dependency fails `mpat check` exactly like drift in the patch target.
It does not affect whether the patch is applied at import.

`watch` accepts `depends_on` too.

### `until`

A condition that says when the workaround is no longer needed. On a `patch`, if
it evaluates true at import, the patch is not applied and a message is logged
at info level on the `mpat` logger. On a `watch` it has no runtime effect.

`Version` takes a requirement string in the usual packaging syntax. It is false
when the distribution is not installed at all.

```python
from mpat import Version

until = Version("somelib>=2.4")
```

`Probe` takes a zero-argument callable and coerces its result with `bool()`.

```python
from mpat import Probe


def upstream_fixed() -> bool:
    return hasattr(somelib.client.Client, "retry_on_timeout")


until = Probe(upstream_fixed)
```

Both combine with `|` and `&`, and the result combines again.

```python
until = Version("somelib>=2.4") | Probe(upstream_fixed)
until = Version("somelib>=2.4") & Version("otherlib>=1.2")
```

`Until` is the protocol both satisfy: any zero-argument callable returning a bool
works, but only `Version`, `Probe` and their combinations support the operators.

A patch or watch with `until` also gets a generated test that fails once the
condition comes true, so the branch that deletes the workaround is the one that
goes green. See [pytest](pytest.md).

### `on_drift`

What happens at import when `mpat.lock` has an entry for the target and the
installed upstream no longer matches it.

| Value | Behaviour |
| --- | --- |
| `"warn"` (default) | Emit `UpstreamDriftWarning` and apply the patch anyway |
| `"skip"` | Emit `UpstreamDriftWarning` and leave upstream untouched |
| `"raise"` | Raise `UpstreamDriftError` |

Any other value raises `ValueError` when the decorator is applied.

```python
@patch("somelib.client.Client.request", on_drift="skip")
def request(original, self, method, url, **kwargs): ...
```

Setting `MPAT_STRICT=1` in the environment forces `raise` for every declaration,
whatever each one asked for. Use it in CI and in test runs where a silent warning
would go unread.

The import-time check needs a lockfile to compare against. `mpat` looks for the
nearest `pyproject.toml` from the current working directory upwards and reads
`mpat.lock` beside it. With no lockfile, or no entry for the target, nothing is
compared and the patch is applied.

`until` is evaluated before the drift check. A patch that is already unnecessary
does not warn about drift.

### `when_imported`

By default `patch` imports the target's module when the decorator runs, so a
patch on an optional dependency raises `ModuleNotFoundError` on any machine
without it. `when_imported=True` registers the declaration without importing
anything and applies the patch from a post-import hook the first time the
target's top-level module is imported.

```python
@patch("optionallib.Client.request", when_imported=True)
def request(original, self, *args, **kwargs): ...
```

If the top-level module is already imported when the decorator runs, the patch
is applied immediately. If it is never imported, nothing happens. Everything the
eager form checks at decoration, the deferred form checks when the hook fires:
`TargetNotFound`, `UnsupportedTarget`, `KindMismatch`, `until`, and the drift
check with its `on_drift` behaviour. An error from those propagates out of the
`import` statement that triggered the hook, and the hook stays queued, so the
next attempt to import the module runs it again.

The hook keys on the first dotted segment of the target, `optionallib` above,
because that is the only module name that can be known without importing.
Importing `optionallib.client` imports `optionallib` first, so the hook fires
either way; resolving the target then imports the submodule it lives in.

The hook is a finder at the front of `sys.meta_path`. It only claims module
names a deferred patch is waiting on, hands the real loader back to the module
before executing it, and removes itself from the module's entry once fired, so
`module.__loader__` and `module.__spec__` look exactly as they would without
`mpat`.

`mpat lock` and `mpat check` do not defer. They import the patch modules with
`MPAT_COLLECT=1`, which registers the declaration and nothing else, and then
resolve every target to fingerprint it. A deferred target therefore still has to
be importable when you lock.

`apply_all()` imports every pending module, for code that wants the patches
applied before it proceeds.

### `review_by`

A `datetime.date`. It changes nothing at import. Once it has passed, a target
that is otherwise clean reports `review` in `mpat check`, so a workaround cannot
sit unexamined forever.

```python
from datetime import date


@patch("somelib.client.Client.request", review_by=date(2026, 12, 1))
def request(original, self, method, url, **kwargs): ...
```

Drift beats `review`: a target that both drifted and is overdue reports the
drift.

### `note`

Free text. It is appended to drift warnings and errors, printed in the
`mpat check` row, and included in `--json` output. Put the upstream issue number
and the condition for deleting the patch here.

## Errors

All of these subclass `MpatError`.

| Error | Raised when |
| --- | --- |
| `TargetNotFound` | No importable module prefix, a missing attribute along the path, a bare module, or an object with no `__module__`/`__qualname__` |
| `UnsupportedTarget` | `patch` on something that is not a function, staticmethod or classmethod, or on an attribute provided by the metaclass |
| `KindMismatch` | The replacement's sync/async/generator kind differs from the original's |
| `AlreadyPatched` | Another `mpat` declaration already patched that attribute, or the target resolves to an object already patched under a different name |
| `ForbiddenTarget` | The target matches the denylist and is not in `[tool.mpat] allow` |
| `UpstreamDriftError` | Drift at import with `on_drift="raise"` or `MPAT_STRICT=1` |
| `LockError` | `mpat.lock` is unreadable, has an unsupported version, or has a malformed entry |

`UpstreamDriftWarning` is a `UserWarning`, not an `MpatError`.

`UnsupportedTarget` names the type it found and points you at `watch`:

```
somelib.client.Client.timeout: only functions, staticmethods and classmethods
can be patched, got property; use watch() instead
```

## Denylist

`mpat` is not a sandbox, but it refuses a short list of targets so nobody patches
or watches them by accident.

```
mpat  builtins  sys  importlib  ssl  hashlib  hmac  secrets  cryptography  certifi
```

Matching is by dotted prefix, so `ssl` covers `ssl.SSLContext.wrap_socket`. The
denylist applies to both `patch` and `watch`. A match raises `ForbiddenTarget`.

Override it per project with `[tool.mpat] allow`, which matches by prefix the
same way.

```toml
[tool.mpat]
modules = ["myapp.patches"]
allow = ["hashlib.new"]
```
