# FAQ

## Is this a sandbox?

No. `mpat` is not a security boundary. Anything running in your interpreter can
`setattr` on anything without asking, and `mpat` does not try to stop it.

What it does is record what you patched and tell you when upstream moved. The
denylist covers `mpat`, `builtins`, `sys`, `importlib`, `ssl`, `hashlib`, `hmac`,
`secrets`, `cryptography` and `certifi`. It is there so nobody patches or watches
those by accident, not because a determined caller could not reach them anyway.
`[tool.mpat] allow` turns any of it off.

## Why hash the source file instead of the live object?

Because a hash of the live object would not survive a Python upgrade. Bytecode in
`__code__.co_code` changes between interpreter versions, between optimization
levels, and sometimes between patch releases, so a lockfile committed on 3.11
would report drift everywhere on 3.12 while upstream had not changed at all.

Hashing the unparsed AST of the definition gives the opposite properties. It
ignores formatting and comments, so `ruff format` upstream does not produce a red
build. It reacts to a changed default, a renamed variable, a new branch. It is the
same value on every interpreter that can parse the file.

It also generalises. Classes and constants have no code object, and `mpat` has to
fingerprint those too. Signature and async-ness come from the live object,
because that is where they are accurate; the body comes from the file.

The cost is that some targets have no readable source, which is what
[`no_source`](lockfile.md#targets-without-source) is for.

## Why does the runtime only warn by default?

Because import time is the wrong place to fail. `mpat check` in CI is the guard;
by the time your application starts, the decision has already been made or
missed, and refusing to boot production because a dependency bump changed a
docstring is worse than the drift.

The default is also the safer of the two behaviours for the patch itself. A patch
whose target drifted is usually still closer to correct than no patch at all, so
`on_drift="warn"` applies it and tells you.

When you want the strict reading, ask for it. `on_drift="raise"` per declaration,
`on_drift="skip"` when a stale patch is more dangerous than a missing one, and
`MPAT_STRICT=1` in the environment to force `raise` everywhere. Setting
`MPAT_STRICT=1` in your test run is a good idea.

## Does it work with namespace packages?

The fingerprint does. The distribution attribution may be approximate.

`resolved`, `signature`, `source_hash` and `source_file` are computed from the
module and the file, so they are correct either way. `dist` and `dist_version`
come from mapping the top-level package name back to an installed distribution.
For a namespace package that several distributions contribute to, that mapping has
more than one answer and `mpat` records the first one it is given.

The consequence is cosmetic. A row can be grouped under a sibling distribution in
the `mpat check` table, and the `2.3.1 -> 2.5.0` version hint can refer to that
sibling. Drift detection itself does not use those two fields.

## What about `.pyc`-only installs?

They are locked on signature alone. `mpat` asks `inspect` for the source file and
then checks that the file exists; for a distribution shipped without its `.py`
files it does not, so there is no AST to hash.

Those entries get `no_source = true`, one warning from `mpat lock`, and a
`[signature-only]` marker on every `mpat check` row. Signature and sync/async
changes are still caught. Body changes are not. Treat such a target as much weaker
coverage than the rest of the table, and prefer watching something in your own or
another pure-Python package that would have to change alongside it.

Builtins and compiled extension modules behave the same way, for the same reason.

## What cannot be fingerprinted?

Anything whose value is not a scalar or a tuple of scalars, and is not a function,
class or property, is recorded as existence only. Its `value_repr` is
`<unhashable>` and it stays `<unhashable>` no matter what happens to its contents.

That includes the case people hit first: a dict used as a registry.

```python
watch("somelib.plugins.REGISTRY")   # existence only
```

Add a key upstream, remove one, change a handler, and `mpat check` stays green.
There is no fix inside `mpat` for this. A dict's contents depend on import order
and on whatever ran before the fingerprint was taken, so a hash of them would be
unstable in the other direction.

Watch the code that populates the registry instead.

```python
watch("somelib.plugins.register_defaults")
```

Functions defined inside other functions are the other gap. Their `__qualname__`
contains `<locals>`, so they cannot be located in the module's AST and get no body
hash. That case is at least visible: the entry is locked with `no_source = true`
and the row is marked `[signature-only]`. Watch the enclosing function instead.
