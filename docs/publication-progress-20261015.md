# Z8 progress publication — 2026-10-15

This index records source checkpoints, not a firmware release. Feature reports
retain their dated evidence limits. No firmware execution, QEMU rebuild, physical
camera test or iPhone Photos import was performed for this publication.

| Topic branch | Source regression |
| --- | --- |
| [codex/z8-af-lockon](https://github.com/YCX81/z8-qemu/tree/codex/z8-af-lockon) | Ran 379 tests in 3.676s; OK (skipped=87) |
| [codex/z8-heif-1602](https://github.com/YCX81/z8-qemu/tree/codex/z8-heif-1602) | Ran 732 tests in 19.101s; OK (skipped=88) |
| [codex/z8-integration](https://github.com/YCX81/z8-qemu/tree/codex/z8-integration) | Ran 700 tests in 15.692s; OK (skipped=118) |
| [archive/z8-internal-stream-20261012](https://github.com/YCX81/z8-qemu/tree/archive/z8-internal-stream-20261012) | Ran 494 tests in 3.855s; OK (skipped=88) |
| [codex/z8-live-photo](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo) | Ran 625 tests in 12.944s; OK (skipped=88) |
| [codex/z8-live-photo-video](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo-video) | Ran 430 tests in 3.943s; OK (skipped=118) |
| [archive/z8-merge-rehearsal-20261014](https://github.com/YCX81/z8-qemu/tree/archive/z8-merge-rehearsal-20261014) | ARCHIVE: unresolved conflicts; not buildable |
| [codex/z8-orientation](https://github.com/YCX81/z8-qemu/tree/codex/z8-orientation) | Ran 393 tests in 3.599s; OK (skipped=117) |
| [main](https://github.com/YCX81/z8-qemu/tree/main) | Ran 626 tests in 13.920s; OK (skipped=88) |
| [archive/z8-raw-precapture-20261013](https://github.com/YCX81/z8-qemu/tree/archive/z8-raw-precapture-20261013) | Ran 614 tests in 13.586s; OK (skipped=88) |
| [codex/raw-precapture-shared146](https://github.com/YCX81/z8-qemu/tree/codex/raw-precapture-shared146) | Ran 622 tests in 14.413s; OK (skipped=88) |
| [archive/z8-capture-evidence-20261012](https://github.com/YCX81/z8-qemu/tree/archive/z8-capture-evidence-20261012) | Ran 417 tests in 3.736s; OK (skipped=87) |
| [codex/z8-video-c1](https://github.com/YCX81/z8-qemu/tree/codex/z8-video-c1) | Ran 402 tests in 3.863s; OK (skipped=117) |

Each branch contains `docs/publication-checkpoint-20261015.md`. The archive
branches preserve earlier experiments and drafts independently of main. The
merge-rehearsal archive intentionally retains unresolved markers; its CI and
publication manifest are not expected to pass. Other rows show existing source
regressions only; skipped device tests were not run.

Three old publishing copies (`z8-evidence-publish-20261012`,
`z8-internal20-model-20261013`, `z8-serial5-publish-20261012`) contain source
blobs already present in main history; duplicate commits are unnecessary.

Update 2026-10-19: those three copies were removed from the local workspace,
along with the `z8-internal-stream-publish-20261012` and
`z8-todo-publish-20261012` copies. The HEADs of the last two match the
`archive/z8-internal-stream-20261012` and `archive/z8-capture-evidence-20261012`
branches above. Main already contained newer versions of every uncommitted
change in the removed copies. Before removal, their ignored local evidence,
uncommitted patches, the stash and a bundle of every local ref were saved to a
private local archive outside this repository. This publication does not
include that archive.

RAW precapture, completed video files, HEIF repeat-shot recovery, AF behavior
and Live Photo import each retain their own incomplete acceptance boundaries.

## HEIF incremental publication, 2026-10-15

Following the initial source checkpoints above, `codex/z8-heif-1602` records
three additional batches: native rearm/stop-wait diagnostics, the default-off
virtual-clock codec completion experiment, and P17/P19/P20 evidence reports.
The current source regression passed 756 tests with 88 skipped, together with
the existing C checks. Individual staged snapshots were audited before commit.

See the [incremental HEIF checkpoint](https://github.com/YCX81/z8-qemu/blob/codex/z8-heif-1602/docs/publication-checkpoint-20261015-round2.md).
Stable second-shot recovery and a second accepted HIF remain unverified. P21's
fixed off/on native comparison was ongoing at the source snapshot; publication
did not rerun QEMU or alter its watched host source files. The table above
continues to describe the earlier checkpoints and their original validation.

## HEIF P21/P22 publication, 2026-10-16

The HEIF branch adds three source batches: caller-bound raw stop observations,
P22 secondary-stop/address/C1 receiver contracts, and P21/P22 stage evidence.
The local publication regression passed 760 tests with 88 skipped and the
existing C checks. Native fixture bytes remain private; the public helper test
explicitly skips when they are absent, while retaining its text provenance.
The exported public snapshot also passed the full 760-test regression with 89
skipped; its one additional skip is the missing private native-fixture test.

See the [2026-10-16 checkpoint](https://github.com/YCX81/z8-qemu/blob/codex/z8-heif-1602/docs/publication-checkpoint-20261016.md)
and the [stage report](https://github.com/YCX81/z8-qemu/blob/codex/z8-heif-1602/docs/z8-heif-1602.md).
Historical P22 results show a second frame in three fixed samples but no second
HIF: the remaining strict stop is JPEG bank1. Those samples used single-source
mask 0x40; the separately recorded first P22 run covers the dual-source stop.
No firmware execution, device QTest or camera acceptance was rerun to publish
these sources. Earlier index sections retain their original snapshot scope.
