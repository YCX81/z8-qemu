# Incremental HEIF source publication — 2026-10-15

This checkpoint follows `30c3d79450686d0664f332f6578a4dec6ae3ec8e`.
It records source progress in three batches while retaining the experiment branch.
Earlier reports keep their original dates and evidence limits; their statements
that no commit or push had occurred refer to those earlier sessions.

## Changes

- Host recovery: anchor intershot timing to actual input queue time, gate MENU
  release on observed native stop notifications, and add bounded native MENU,
  property 2620 and raw OS wait-return observations. The raw return helper is a
  separate diagnostic component; its presence does not establish native recovery.
- Codec experiment: add a default-off virtual-clock completion barrier for the
  existing x265 worker. Existing source, ownership, encode, DMA, readback and IRQ
  checks remain prerequisites. This is a functional timing model with no physical
  hardware latency claim; device test scripts are included.
- Evidence: preserve P17, P19 and P20 reports and their failed recovery cases.
  Property 2620 status and the original stop-wait failure chain are diagnostic
  findings, not grounds to inject an IRQ or clear a pending condition.

## Validation and limits

For this publication, `sh tests/run.sh` passed 756 existing regression tests,
with 88 skipped, plus the script's C checks. Changed Python source passed syntax
checks. Each staged batch was exported separately and checked against its source
inventory, manifest, Git index and reachable history before commit.

No QEMU build, device QTest, firmware execution or physical camera validation was
rerun for publication. P21's fixed off/on native comparison was still in progress
at the source snapshot; its watched host source files were left unchanged. Its
partial measurements are not a completed stability result. Historical native
and QTest reports retain their own binaries, inputs and receipts.

Stable second-shot recovery and a second accepted HIF remain unverified. Firmware,
derived programs, virtual cards, images and private runtime logs are excluded.
