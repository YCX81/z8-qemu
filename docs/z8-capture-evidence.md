# Capture snapshots and card exports

These tools collect evidence for RAW capture research. They do not create a
camera image or establish Z8 NEF/pre-capture acceptance. The capture probe and its
experimental device recipe are separate; neither is added by this batch.

## Paused producer snapshots

`tools/z8_capture_pixels.py` provides APIs for an owner already controlling a
paused, identity-checked Z8 C3.11 guest:

```python
from z8_capture_pixels import capture_d980, capture_raw_output

sensor = capture_d980(guest, 'd980-00001')
raw = capture_raw_output(guest, 'raw-output-00001')
```

Each label must be new. The functions verify actual device counters, latched
geometry and bounded image-RAM addresses, then use QMP `pmemsave`. They preserve
the complete packed snapshot and produce a separate normalized `u16le` file with
an explicit packing/stride conversion receipt. The normalized file is not a
literal RAM dump. `capture_raw_output` additionally requires an actual RAW-output
job identifying the same D980 source; it cannot substitute a sensor snapshot for
that output.

When the optional sensor marker is enabled and its identity and whole-buffer hash
match, the receipt records the model's DMA completion time. This is not physical
exposure time or proof of pre-trigger retention. Without a marker,
`dma_completed_virtual_ns` is null and temporal attribution is unverified.
`raw_precapture_verified` remains false in both cases. The module has no standalone
QEMU-launching CLI.

## Stopped-card export

```sh
python tools/z8_export_nefs.py \
  --run .local/capture-run \
  --output .local/export-01 \
  --firmware .local/actual-executed-main.bin
```

`--output` must be a fresh directory. The exporter requires a stopped, fault-free
run without recorded runtime CPU/RAM replacement and rechecks the executed input
program's digest. An explicit `executed_program_sha256` can identify a derived
kernel separately from the original MAIN binding; that provenance is retained
and never converted into native acceptance.

The exporter preserves the full original directory catalog, verifies complete
cluster chains and ownership, and compares every exported byte against absolute
card extents, including the partition offset. Existing audit artifacts are
rechecked read-only. If missing, a verified sparse copy is audited inside the new
output directory; old captures and receipts are not overwritten.

Host filenames use safe ordinals. `file_id` and `output_index` identify directory
observations, **not** native jobs or writer order. The paths in `storage` are
relative to `audit_base_dir`. A later collector must bind them to actual writer
evidence before passing a complete manifest to the
[pre-capture auditor](z8-precapture-audit.md).

`status=exported` means the complete observed NEF collection was copied and its
bytes checked; it does not mean RAW decoding passed. Zero NEFs return
`status=incomplete`/CLI exit 2. An invalid file or catalog causes failure rather
than dropping that file to manufacture a successful subset. `native_accepted`
and `writer_mapping_verified` always remain false.

## Evidence obtained

- Z8 diagnostic C04 captured a complete D980 buffer: 8280×5520, 14 bit,
  14,496-byte packed row stride, 80,017,920 packed bytes and 91,411,200 normalized
  bytes. The original packed bytes were retained. The marker was disabled, so
  this result supplies no exact DMA-time or pre/post-trigger attribution. The
  diagnostic timed out without an accepted Z8 NEF or pre-capture result.
- Z8 diagnostic C05 enabled the external marker and saved the same full geometry.
  Independent checking decoded marker ID 1 and compared all 45,705,600 samples
  against C04: exactly the intended 1,024 marker samples changed, with zero
  differences elsewhere. Actual DMA completed at 89,568,372,596 virtual ns;
  the original GPIO FULL worker was observed at 89,540,621,197 virtual ns.
  This is one **post-trigger periodic-source frame**, not a retained history
  frame or completed single-shot request. The single-frame model recorded zero
  requests/completions; RAW output and JPEG jobs remained zero and no NEF writer
  was observed. Completing the bounded contact diagnostic does not change that.
  The original CCOMP GPIO-to-task notification reached its flag8-specific path;
  the later wait-policy points were not observed. A masked D980 pending bit is
  not evidence that forcing an interrupt subscription would repair the workflow.
  The complete stopped-card catalog independently contained `NIKON001.DSC`,
  `DCIM`, `NIKON` and `NIKON/Z_8`, with no NEF. The fresh copy passed the exFAT
  check; the original card remained unchanged. The exporter correctly reported
  `incomplete` with `catalog_complete=true`, not a successful capture.
- A separate exporter check used the already stopped
  [Z9 ordinary-capture reference](z8-raw-foundation.md). All seven original catalog
  entries were retained; its one 80,790,016-byte NEF was exported byte-for-byte
  with SHA-256 `1ed8f44f6a1aa8031dd4d423a5887b6b654a13c8ba35a0d87d1ed655c3ec025a`.
  The original 8-GiB card and prior receipts/files remained unchanged. This is an
  exporter/Z9 single-shot check, not Z8 capture or pre-capture acceptance.

The tests use authored packed-pixel and filesystem/transport fixtures; they do
not distribute firmware, camera images or captured RAM. Use
`requirements-capture.txt` and run the two `test_z8_capture_pixels.py` and
`test_z8_export_nefs.py` unittest modules. Private run artifacts are not included.
