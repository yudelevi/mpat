# mpat

Lockfile-based upstream drift tracking for Python monkeypatches.

Declare a monkeypatch once, lock the fingerprint of every upstream symbol it
depends on, and let CI tell you when a dependency bump moved the ground under
it. https://monkeypat.ch

This is a placeholder release. Design spec lives in
`docs/superpowers/specs/`.
