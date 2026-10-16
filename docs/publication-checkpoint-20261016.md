# HEIF source publication — 2026-10-16

This checkpoint follows `4a06829cf52db53e1d0b51fe269373563a6b794a` and
publishes three source batches on the existing experiment branch:

- Connect the caller-bound raw OS stop-return observer to the capture probe,
  with explicit opt-in dependencies and bounded secondary-stop observation.
- Add default-off P22 secondary-source stop, format2 bank0 address admission,
  and retired finite HLG C1 receiver reconfiguration models and their tests.
- Preserve the completed P21 comparison and P22 stage reports, including the
  remaining JPEG bank1 rejection and absence of a second accepted HIF.

For this publication, the complete local source regression passed 760 tests
with 88 skipped, plus the existing C checks. The original native MMIO fixture
was present for that local regression and matched its recorded SHA-256.
Each staged batch was exported and audited separately against its manifest,
Git index and reachable history.

The binary `tests/fixtures/z8-1534-p22-native.bin` is private runtime evidence
and is not distributed. Its text provenance receipt is included. A clean public
checkout explicitly skips the native-fixture helper test when the binary is
absent; local research checkouts can still validate it when available. This
publication adds only that missing-fixture skip to the new helper wrapper; it
does not change the recorded fixture, device contracts or historical runs.
The exported public snapshot also passed all 760 discovered regression tests,
with 89 skipped, plus the existing C checks; the additional skip is this one
unavailable native-fixture test.

Historical P21/P22 builds, QTests and native runs retain their recorded binaries,
inputs, hashes and acceptance limits. They were not rerun for publication.
P22's three fixed second-version samples reached a second frame and the JPEG
bank1 guard; they did not produce a second HIF. Those three waits used mask
`0x40`, so they are not three validations of the dual-source `0x40040` path.
The separately reported first P22 run supplies that dual-source observation.

Earlier reports' statements that no commit or push had occurred refer to their
original sessions. Firmware, derived programs, virtual cards, image payloads and
private runtime logs remain outside the published source inventory.
