<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/mpat-lockup-dark.svg">
    <img src="assets/mpat-lockup-light.svg" alt="mpat" width="420">
  </picture>
</p>

# mpat

Lockfile-based upstream drift tracking for Python monkeypatches.

Declare a monkeypatch once, lock the fingerprint of every upstream symbol it
depends on, and let CI tell you when a dependency bump moved the ground under
it. https://monkeypat.ch

This is a placeholder release. Design spec lives in
`docs/superpowers/specs/`.
