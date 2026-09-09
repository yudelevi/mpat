# Usage

Everything public lives at the top of the package.

```python
from mpat import Probe, Until, Version, patch, watch
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

## `watch()`

```python
def watch(
    target: str | object,
    *,
    depends_on: Sequence[str] = (),
    review_by: date | None = None,
    note: str = "",
) -> None
```

`watch` fingerprints a symbol without patching it. Use it for the upstream code
your patch depends on being shaped a certain way, and for constants you read.

```python
from mpat import watch

watch("somelib.settings.MAX_RETRIES", note="see somelib#98")
```

`watch` takes no `until` and no `on_drift`. A watched target that drifts always
warns, and `MPAT_STRICT=1` turns that warning into an error like it does for a
patch.

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

A condition that says when the patch is no longer needed. If it evaluates true at
import, the patch is not applied and a message is logged at info level on the
`mpat` logger.

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

A patch with `until` also gets a generated test that fails once the condition
comes true, so the branch that deletes the patch is the one that goes green. See
[pytest](pytest.md).

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
