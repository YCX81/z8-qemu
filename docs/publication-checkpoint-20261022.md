# Extended-slot source checkpoint — 2026-10-22

This publication retains the `codex/z8-r4-lifecycle` experiment branch and its
existing NP3 zero-key terminator fix, `a27df6b29e4d1d0f0778e182fe29e93853cc94a7`.
The new source changes cover the C32/C64 instruction-level research helpers:

- Route owned extended-slot labels through the original liveview widget text
  setter, while preserving the original path for built-in identifiers.
- Resume the original card input, validation, allocation, free and parse helpers
  whenever no synthetic NP3 import is active. Clear synthetic import parameters
  on exit and verify the original instructions at the patched call sites.
- Register a configured custom record after a successful original main-parameter
  write, and retain the optional copy-destination page extension behind
  `extend_copy_page=False` by default.
- Add eleven indicator/import/composition tests and publish the existing NP3
  padding tests in the explicit source inventory.

For publication, `sh tests/run.sh` completed 645 tests with 3 skipped, followed
by the script's C checks. `Z8_MAIN` pointed to the identity-checked original
local MAIN, so the original-instruction tests, including all eleven new cases,
ran instead of being skipped. Real external NP3 cases were not supplied.
Each published source file was checked for syntax, license and publication
inventory membership; the staged snapshot and reachable history were audited.

Clean source checkouts retain explicit skips for tests requiring a private
MAIN or real NP3 files. Neither input is published. These results cover bounded
instruction emulation and source regressions; no full QEMU/UI, real card import,
camera behavior or hardware validation was performed for this publication.
No firmware package was built or flashed, and the optional copy-page extension
was not enabled in a deployed runtime.
