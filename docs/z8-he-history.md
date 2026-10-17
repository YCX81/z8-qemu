# Historical HE retention: isolated lifecycle validation

[简体中文](z8-he-history.zh-CN.md)

This experiment executes the three hooks from
[NikonFirmwarePatch at 85c7538](https://github.com/XinmiaoZhao/NikonFirmwarePatch/tree/85c7538724e8b9972918aeb3726c033bd54af90c)
in a bounded ARM64 interpreter. It does not modify the QEMU model, lossless
research worktrees or the external implementation. It produces no camera update.
The [numeric receipt](verification/he-history-20261017.json) is a controlled
execution result, not a physical-camera acceptance record.

## Intended feature boundary

The external implementation adds HE NEFs for selected historical C15/C30 FX
frames, with the original pre-release settings up to one second. JPEG remains
required. Post-FULL frames remain JPEG-only. There is no HE-star, lossless NEF,
RAW-only mode or immediate camera database registration. The author reports
that a normal power cycle refreshes the RAW playback indication; that observation
has not been independently reproduced here.

The external source is GPL-3.0-only and is not vendored. This repository contains
an independently authored service harness and synthetic structure generators.
The caller provides the pinned external checkout and, optionally, a local stock
C3.11 module. Hashes bind all compiled inputs. Nikon firmware is never downloaded
or included in the result.

## Ownership under test

| Boundary | Executed behavior | Required assumption |
|---|---|---|
| P1, `0x40f6fe90` | Bind R/E/M, frame sequence and selection to a CRC-protected record in the enlarged E allocation tail; preserve the original submit result. | This is a historical frame; allocated capacity includes the tail. |
| P2, `0x40ea915c` | Keep the selected E at R+0x748; otherwise call its release service and clear the pointer. | One P2 invocation per frame; caller does not reuse retained storage. |
| P3, `0x40ea367c` | Match the JPEG's M and shot identity, construct the NEF metadata, DMA the unchanged opaque payload, then release E and the JPEG header. | One callback per actual header resource; serialized primary writer service. |
| R cleanup, `0x40ea44d0` | With a local stock module, execute original instructions that release and clear R+0x748. | Other R resource pointers are zero in this focused fixture; allocator services remain modeled. |

The writer output is independently checked for 8280x5520, 14-bit tagging,
compression identifier, full payload length, and contiguous DMA from the matching
frame. The synthetic payload is deliberately not a valid HE image. Structure
and DMA checks do not establish HE decoding or image fidelity.

## Reproduced results and limits

The local receipt covers 43 scenarios and two defective-build controls. Scenarios
include C15 sets of 5/15 and C30 sets of 9/30, reverse completion order, most-recent-N
selection, absent/invalid/ambiguous configuration, excluded post-FULL frames,
stale/corrupt bindings, canceled JPEG delivery, allocation failure, partial writes,
create/close/rename errors, budget expiration, six completed bursts and two
interleaved retained frames, and reuse of the same R/E addresses after completion
and cancellation under an explicit caller allocation contract. Every successful synthetic output has a checked
RAW-tag and frame-to-DMA binding.

Five cases deliberately reproduce a limitation or a call-once precondition:

- Replaying P1 after retention replaces the retained state; P3 skips the NEF.
  The retained E can still be released by R cleanup.
- Replaying P2 for the same retained E releases it before the writer.
- Replaying P3 with the same header attempts a second header release. A later
  file with its own header does not release the already-consumed E again.
- A filesystem call that never returns cannot be interrupted by the cooperative
  budget. Its resources remain in flight; no automatic unwind was observed.
- If creation of the stop marker also fails, that failure is not persistently
  latched and a later trigger can retry.

These are adversarial schedules and injected failures. They do not show that
the stock scheduler actually delivers duplicate hooks or that a camera has
exhibited these faults. No upstream behavior was silently changed to make the
tests pass. `PASS` means the expected behavior was reproduced, including these
limitations; it does not mean every scenario is safe.

The negative builds use the external source's own `NEG_READ_PAYLOAD` and
`NEG_NO_CLEAN` switches. The harness rejects CPU reads of the opaque payload and
DMA consumption of dirty CPU-produced metadata. Unknown releases, service
paths and exhausted execution bounds fail rather than returning success.

## Reproduction

Use Python 3.12+, Clang with an AArch64 target, and the optional dependencies:

```sh
python -m pip install -r requirements-he-history.txt
git clone https://github.com/XinmiaoZhao/NikonFirmwarePatch.git .local/he-source
git -C .local/he-source checkout --detach 85c7538724e8b9972918aeb3726c033bd54af90c
python -B tools/z8_he_history_audit.py \
  --source .local/he-source --compiler clang-18 \
  --report .local/he-history.json
```

To execute the focused original cleanup path, add
`--stock-main /absolute/path/to/your/z8-c311-main.bin`. Only the exact hash in
the source profile is admitted. Without that option, the cleanup case uses an
explicit contract stand-in; the report says original cleanup was not executed.
Reports must have new paths and are never overwritten.

The local run used Ubuntu Clang 18.1.3 and Unicorn 2.1.4. Its generated machine
code differs from the upstream reviewed Apple Clang 21 recipe. That difference
is recorded as `reviewed_machine_code_match=false`. The harness verifies the
same pinned source; it does not bypass the upstream package builder's compiler
fingerprint guard, generate a package or certify its exact reviewed bytecode.

Firmware-free fixture tests run in the normal test suite. Setting `Z8_HE_SOURCE`
and `Z8_HE_CLANG` explicitly opts into external hook execution. A separate CI job
checks out the fixed upstream revision and runs the hook scenarios without
Nikon firmware. The full QEMU build is not needed for this harness.

## Remaining acceptance work

Native scheduler traces must establish call-once delivery, E allocation
generations, pending-writer cancellation and pool backpressure. The synthetic
interleaving is not a busy-card or concurrent RTOS test. Real filesystem close
semantics, blocked I/O recovery, dual cards, exhaustion and power loss are not
modeled. Close-error injection removes the modeled descriptor; it cannot prove
that a physical filesystem released its handle.

No stock QEMU boot with the three installed hooks, valid archived HE input,
independent HE decode, physical timing or camera testing is claimed. The local
original cleanup run covers R+0x748, not the complete native resource graph.
Keep this experiment separate from the lossless policy chain: both patch
families touch the text-copy loader, so matching individual hook windows does
not admit a combined image. Any subsequent integration needs its own input,
loader, allocation and lifecycle verification. Flashing remains out of scope.
