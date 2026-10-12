# RAW pre-capture evidence audit

`tools/z8_precapture_audit.py` checks the consistency of a stopped experiment's
native trace, producer snapshots, actual virtual-card bytes and independently
decoded NEF files. It does not start QEMU, synthesize a photograph, patch firmware
or write to a card. A passing unit test or ordinary single-shot decode is **not**
evidence that Z8 RAW pre-capture works.

The minimum accepted experiment contains two distinct retained frames whose DMA
completed strictly before the original FULL-consumer event, plus at least one
distinct frame whose DMA completed strictly after it. Retained frames must reach
the original writer in FIFO order before current frames. Every scoped candidate
DMA, submission, writer result and NVMe completion must be accounted for.

## Usage and result

Use Python 3.11 or newer with NumPy. Native decoding additionally requires
`rawpy`/LibRaw, for example `python -m pip install 'rawpy>=0.20,<1'` in an isolated
environment. No decoder fallback is used.

```sh
python tools/z8_precapture_audit.py \
  --manifest .local/my-capture/capture-evidence.json \
  --output .local/my-capture/audit-01.json
python -m unittest discover -s tests -p test_z8_precapture_audit.py -v
```

The output must be new; the tool refuses to replace an existing receipt. Evidence
paths are relative to the manifest directory, must resolve inside it, and must
refer to regular files. Keep firmware, cards, NEFs, producer dumps, traces and
screenshots private. The repository contains none of those required artifacts.

| Result | `accepted` / `native_accepted` | CLI exit |
| --- | --- | --- |
| `passed` | both `true` | 0 |
| `failed` | both `false` | 1 |
| `incomplete` | both `false` | 2 |
| `synthetic_checks_passed` | both `false` | no CLI decoder injection |

The Python entry point is `audit_manifest(manifest, base_dir, decoder=None)`.
It returns a receipt containing `issues`, predicted pre/post frame IDs, output
order and per-file sample counts/mismatches. `accepted` remains a compatibility
alias for `native_accepted`. Passing **any** callable through `decoder=` makes the
result test-only, including passing `decode_rawpy` itself. It can never award
native acceptance. The CLI always uses the fixed `decode_rawpy` implementation.

## Manifest schema

The required schema name is `z8-precapture-audit-v1`. All IDs must be bounded
strings or nonnegative 32-bit integers; digests are lowercase SHA-256 strings.
Numeric values are integers, not booleans or floating-point substitutes.

The following is a structural example, **not a runnable or accepted experiment**:

```json
{
  "schema": "z8-precapture-audit-v1",
  "run": {
    "run_id": "capture-01",
    "model": "Nikon Z8",
    "firmware_sha256": "<actual loaded MAIN SHA-256>",
    "clock_id": "capture-01:qemu_virtual",
    "clock_kind": "qemu_virtual_ns",
    "stopped": true,
    "error": null,
    "full_consumer_pc": 0,
    "evidence_kind": "native_guest_capture"
  },
  "queue": {"capacity": 2},
  "frames": [],
  "events": [],
  "storage": {
    "kind": "guest_nvme_export",
    "card_path": "capture-card.raw",
    "sector_size": 512,
    "catalog_complete": true,
    "nef_files": []
  }
}
```

Replace the digest and consumer address with actual evidence. The original
consumer address must be nonzero and instruction-aligned, and the trigger event
must name exactly that address. `stopped` must be the Boolean `true`, and `error`
must be null. `evidence_kind=synthetic` is admitted only with an injected test
decoder and never establishes native capture.

### Frame and producer

Each `frames` item has unique `frame_id`, generation-qualified `job_id`, and a
`producer` object containing:

| Field | Meaning |
| --- | --- |
| `origin` | exactly `guest_producer_dma` |
| `path`, `sha256` | actual producer snapshot and its digest |
| `encoding` | exactly `u16le` |
| `width`, `height`, `stride_samples` | complete RAW geometry and row pitch in uint16 samples |
| `bit_depth` | sample depth, 1 through 16 |
| `address` | observed original producer buffer address |
| `memory_bytes` | optional complete observed buffer span; defaults to `stride_samples * height * 2` |
| `marker_scheme`, `marker_frame_id` | optional `cfa32-complement-v1` marker and 32-bit frame ID |

Snapshots must contain all rows, including declared row padding, with no trailing
bytes. Each visible sample must fit the bit depth. The checker hashes actual
samples excluding row padding, and rejects reused static pixels relabeled as
different saved frames. The optional marker occupies a 16×64 sample region with
32 two-column bits and their complements; it supplements full-image comparison,
never replaces it.

The declared address/span must describe the actual retained DMA buffer, not an
invented nonoverlapping address. The checker reserves each buffer at its DMA
completion and rejects overlapping subsequent DMA until capacity eviction or the
matching native writer completion. Leaving the history FIFO at submission does
not release it. This is a conservative acceptance condition: the current schema
cannot accept earlier reuse based on an unrecorded source-read completion, even
when the actual implementation could safely reuse the buffer. The receipt names
this release boundary. It checks recorded completion events, not unobserved DMA
start intervals or arbitrary guest writes; those require separate native lifetime
instrumentation. If native producer storage is packed14, an
adapter must explicitly preserve the original snapshot, packing/stride and
conversion provenance when producing the normalized `u16le` comparison artifact;
do not rename packed bytes or claim the normalized file was a literal RAM dump.
This audit does not itself unpack packed14 inputs.

### Event sequence

Every event needs `seq` (contiguous from zero), unique `event_id`, `kind`, `clock_id`
and `t_ns`. Use one observed QEMU virtual nanosecond clock throughout. Host elapsed
time, enqueue time and EXIF timestamps cannot substitute for original FULL
consumption. Events must be nondecreasing in time; history DMA must be strictly
earlier than FULL and current DMA strictly later. No events may follow `capture_end`.

| `kind` | Additional required data / constraint |
| --- | --- |
| `dma_complete` | `frame_id`, `job_id`; exactly one completion per scoped frame |
| `enqueue` | matching frame/job; before FULL, after DMA, no queue overflow |
| `evict` | matching oldest frame/job; `reason=capacity`; queue must have been full |
| `full_press_consumed` | exactly once; `origin=original_firmware`, integer `consumer_pc` matching `run.full_consumer_pc` |
| `submit` | matching frame/job; `role=history` or `current`; FIFO history first |
| `writer_complete` | matching submitted frame/job, unique `file_id`, zero-based writer-assigned `output_index`, positive `bytes`, `origin=original_firmware` |
| `nvme_write_complete` | actual card-byte `offset`, `length`, integer `status=0`; Boolean false is invalid |
| `capture_end` | independent `dma_count`, `submit_count`, `writer_count`, `nvme_count` for this bounded capture scope |

For a capacity-two trace without eviction, an example order is:
`DMA A → enqueue A → DMA B → enqueue B → FULL → submit A → submit B → DMA C → submit C`.
The three original writer completions and actual successful card writes must then
be covered before the final counters. DMA completion is only a timing boundary;
this tool does not infer a physical exposure-start time or measured pre-capture
duration from it.

### Actual card and complete NEF catalog

Each `storage.nef_files` item has `file_id`, relative `path`, `bytes`, `sha256`,
`output_index`, and an `extents` list. Each extent contains card-byte `offset` and
`length`. Extents are in **file logical order**, can be physically fragmented, and
include the partition offset because they refer to the full `card_path` file.

The exporter must derive this complete catalog and its extents from the stopped
guest's actual filesystem. The auditor does not infer completeness by scanning
the filesystem itself: `catalog_complete` and the scoped counter observations
remain provenance claims supplied by the collector. It independently checks that:

- every NEF has a matching original writer completion and contiguous logical
  output index, and that counts/order equal the FIFO prediction;
- declared extents cover the entire file exactly, fit the card and do not overlap
  any other NEF data extent;
- their bytes match the exported NEF, and completed sector-aligned NVMe writes
  cover each extent after that frame's submission;
- the file digest and byte count match the writer/export metadata;
- the file has a Nikon TIFF container, is not a first-IFD DNG renamed `.NEF`, and
  rawpy/LibRaw can decode its **whole** RAW mosaic;
- dimensions, uint16 type, white level and every RAW sample match the producer.

## What a pass does not establish

This is a consistency audit, not cryptographic attestation of a camera or of the
collector. A dishonest collector could fabricate mutually consistent metadata;
retain original logs, collector/source/build hashes and independent review of the
instrumented firmware addresses. Model and firmware identity are reported from
the manifest, not automatically pinned to a source checkout by this auditor.

A pass does not prove physical sensor behavior, real-time throughput, proprietary
NEF compression, final color quality, long bursts or real camera safety. This
schema does not accept JPEG header inspection, a few sampled pixels, three names
for one static Bayer image, injected-decoder output, or an ordinary one-frame NEF
as RAW pre-capture evidence. Menu restoration, filesystem integrity checks and
absence of firmware patches need separate run evidence.

The unit tests use explicitly synthetic TIFF-shaped byte fixtures and an injected
decoder; those fixtures are not usable camera images. The real-decoder smoke check
performed during development used an already stopped upstream Z9 ordinary NEF:
it confirmed the decoding/comparison path only, not Z8 or pre-capture acceptance.
