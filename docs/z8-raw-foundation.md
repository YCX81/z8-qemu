# RAW capture foundations

This batch adds source and test infrastructure for tracing ordinary RAW capture
and, later, RAW pre-capture on the pinned Z8 C3.11 image. **It does not establish a
saved Z8 NEF or RAW pre-capture.** The Z8 shutter/menu probe and its evolving
peripheral recipe are deliberately outside this batch.

## Included components

- Shared functional QEMU models for the DesignWare PCIe/NVMe path, capture GPIO,
  D980 pixel DMA, packed14 output, neutral capture/thumbnail processing, and
  baseline JPEG encoding through libjpeg. These reuse the upstream ordinary
  capture work while retaining the Z8 board and previously published C10/C32
  infrastructure. Hardware timing and proprietary algorithms remain hypotheses.
- A bounded opt-in sensor frame marker, with C tests. Different markers can help
  distinguish delivered frames; a marker is not a history queue or proof of
  pre-release retention.
- Full-size 14-bit synthetic Bayer generation; complete CCOMP request/reply
  validation; bounded QMP transport; explicit RTC input; and a readiness helper
  requiring completed image work and fresh RAW/LCD output.
- Fresh regular-file virtual cards and read-only exFAT catalog/extraction helpers.
  The card creator refuses existing files. It uses sparse files and exfatprogs,
  never mounts or formats a physical device.
- Z8 C3.11 capture observation bindings and passive readiness reads. These check
  Z8 firmware identity and current native GPIO descriptors instead of assuming
  Z9 firmware addresses. A true readiness bit alone does not prove a ready camera.
- A [pre-capture evidence auditor](z8-precapture-audit.md) checking full RAW samples,
  actual card extents, original FULL consumption, FIFO ordering and complete
  native event counts. Injected test decoders can never produce native acceptance.

No original firmware instruction blobs, ROM data, camera images, memory dumps,
card images or screenshots are included. Numeric observation addresses and source
algorithms are not substitutes for the private firmware input.

## Evidence already obtained

An independent local run of upstream `WonderLantern/z9-qemu` at
`deb3a75df1795924e9be8f814d9a24ce6b439a67`, with the original Z9 C5.32 MAIN, completed
the upstream ordinary single-shot workflow and independent audit:

- A newly created 8-GiB sparse virtual card started without camera files.
- The original workflow produced one 80,790,016-byte NEF, extracted from the
  stopped guest's actual filesystem; its SHA-256 was
  `1ed8f44f6a1aa8031dd4d423a5887b6b654a13c8ba35a0d87d1ed655c3ec025a`.
- rawpy 0.27.1 / LibRaw 0.22.1 decoded all 45,705,600 samples of the 8280×5520,
  14-bit RAW mosaic. Every sample matched the completed guest producer output.
- Embedded 8256×5504, 1620×1080 and 160×120 JPEG streams matched completed guest
  codec output and decoded successfully. The card filesystem check passed.
- Two actual post-shot PHOTO menu round trips returned to fresh liveview. The
  owned QEMU process stopped and the source firmware remained unchanged.

This is **Z9 ordinary capture evidence**, not Z8 evidence and not pre-capture.
The firmware supplied the native workflow and file/container; functional Bayer,
neutral ISP/packed14 and baseline JPEG models supplied peripheral behavior.
Compression tag 34713 does not establish Nikon's proprietary compression.
Private source logs and artifacts are retained locally and are not distributed.

The Z8 code in this batch has source-only contract tests and a matching QEMU build
source receipt. Z8 experiments have observed original input consumption and
display activity, but have not yet supplied an accepted Z8 NEF or the complete
pre-capture event/file set. Merely consuming a shutter/menu input does not prove
that its application action occurred.

## Source-only reproduction

On Linux or an explicitly selected WSL distribution, install the build
prerequisites from [the build guide](build.md), including `libjpeg-dev` and
`exfatprogs` for these capture models/helpers. In an isolated Python environment:

```sh
python -m pip install -r requirements-capture.txt
sh tests/run.sh
python tools/build.py --jobs 2
python tools/smoke_qemu.py .build/build/qemu-system-aarch64
```

These commands do not require or validate camera firmware. Firmware-dependent
tests may be skipped when the private input is absent; report those skips rather
than counting them as passes. Building and realizing the machine is not an
end-to-end capture test. The separately published upstream Z9 capture workflow
and private Z9 MAIN are needed to reproduce the Z9 result described above;
this batch does not add a Z8 capture command.

CI installs the same capture dependencies while retaining the existing C10/C32
source checks. The publication auditor keeps an explicit per-file inventory and
raises only `qemu/nikon-z9-lab.c` to an 896-KiB source limit to accommodate the
reviewed shared capture/peripheral implementation. Every other file retains the
512-KiB limit; binary, credential and long embedded-hex checks remain active.
The limit change is not permission to include firmware-derived data.

## Remaining Z8 chain

1. Establish original menu transitions from a rendered LCD and verify the actual
   ordinary RAW quality and compression selections. A remembered key sequence or
   a 1×1 startup screenshot is insufficient.
2. Follow original FULL consumption through application permission, capture
   scheduling, producer completion, the original NEF writer and completed NVMe
   writes. Verify a fully decoded ordinary Z8 NEF before claiming pre-capture.
3. For pre-capture, retain at least two distinct frames from before the original
   FULL event and at least one after it; prove capacity/eviction, ownership, FIFO,
   output count and every decoded sample against actual producer snapshots.
4. Verify recovery and fresh liveview after saving. Physical camera validation,
   sustained throughput, image quality and hardware compatibility remain separate
   work; this batch creates no upgrade package and does not authorize flashing.
