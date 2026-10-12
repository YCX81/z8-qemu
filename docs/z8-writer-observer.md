# Optional native writer observation

`tools/z8_capture_probe.py --native-writer-observer` enables a bounded, passive
observer for the original C3.11 mode0/format1 file-writer route. It is **off by
default**. Without the flag, the existing contact, one-shot trace and cleanup
behavior remains; no writer session, code reads or extra breakpoints are created.

The observer records original writer objects, native file IDs, bounded path
bytes, every data/padding write and actual returned count, final reset, reference
cleanup, writer return and original result store. It does not create file IDs,
frame numbers, image data or files. Directory order is not writer order.

## Evidence and runtime admission

The private capture16 predecessor completed one ordinary RAW/Lossless lifecycle:
92 events, nine writes totaling80,790,528 bytes, observed native reset, cleanup,
writer result0 and actual result store0. A separate stopped-card export and
LibRaw/transport comparison checked45,705,600 samples with zero differences.
That is retained private-run evidence, **not a run of this public integration**.
The public entry point has synthetic tests; its independent runtime acceptance
is still pending. Neither result establishes RAW pre-capture history, a capture
window, physical sensor fidelity or general frame/job identity.

Current admission deliberately retains both exact identities:

| Input | Required SHA256 |
|---|---|
| Original Z8 C3.11 MAIN | `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` |
| Previously validated QEMU artifact | `0ff25311649a43d8691d60e5da02160cf71ea68d0d2725df0ad7f06f49ddce27` |

The QEMU binary is not distributed. Its hash identifies that particular build
artifact; it is not a compiler-independent or reproducible source identity.
**A current-main build is not admitted by this writer option merely because it
built successfully or shares source ancestry.** There is no fallback, allow-any-
binary option, or automatic relaxation. Source/build-manifest based admission
would require separate review and is outside this change.

The admitted artifact was bound to model source commit2974e140 and C source
digest8d0696e506b93f2bf398cbe0f1cb0146293c8b4b6f79a3f59125ee3c1a2f4abe.
These source identities explain the region contract; they do not promise that
recompiling produces the required artifact hash. Supply only user-owned original
firmware and an already independently identified runtime.

## Guards and lifecycle

Prelaunch opt-in verifies the original MAIN hash and30 instruction guards. The
runtime guard separately checks the launch and actual QMP/QOM memory geometry:
four CPUs, supported512M/1024M work RAM with no plugged memory, and the two enabled
image-bank ranges with their512-byte gap. Activation happens only at the paused
`shutter_start` boundary and checks all30 points on each of the four CPUs.

Every observed hit uses its unique actual CPU, fresh exact-VA and page-VA
`gva2gpa` replies, and a fresh physical `AS "memory", root: system` flatview.
The complete four-byte span must be recognized work/image RAM or the pinned
main-image RAM/ROM/ROMD region. Unmapped, ambiguous, unknown, IO and RAMD regions
are not read. The opcode must equal the original MAIN word, and translation,
region/type and paused CPU state must remain stable across the read. No identity
mapping, C/E alias subtraction, cached mapping or previous-run delta is assumed.

All four CPU snapshots reach the observer before parent de-duplication. Every
reserved PC is excluded from parent tracing from construction; contacts cannot
rearm them. Original call, callee-entry and return traps rearm naturally, without
single-stepping or editing PC/registers/DAIF/instructions. Each plan is validated
before RSP operations, and cleanup removes only labels the observer still owns.
CPU migration, SP changes, overlapping lifetimes and transport/ownership failures
stop the interval. Fixed CPU/SP is not proof of OS thread/allocation generation.

Object lookup binds the complete translated physical span. Successful open
binds original file ID, object address, inline path pointer, length and all32
bounded path bytes. Data and padding counts come from the saved original output
pointer, not return X3 or status04320080. Status is never treated as a byte count;
each successful count is compared with its request and logical-position delta.

The original finalizer4085b6d4 calls402db308 and then4085b430; the latter ends in
40863838, which resets mode/handle/position/length/propagated length. Only a
paired successful final return from BEFORE mode301/state3/original handle may
accept AFTER mode0/handle-1/position0/length=requested/propagated length0/state1.
The opened handle is retained in the receipt. Outer status reports the earlier
402db308 result; it does not prove success of every internal4085b430 operation.
The same identity and reset tuple must remain before reference cleanup; the
released object is not read after cleanup. Writer return and the actual result
store remain separately paired. Successful write totals never end the run early.

## Receipts, bounds and interpretation

`native-code-guards.json` retains CPU, VA, PA, raw exact/page replies, virtual
time, opcode and before/after mapping/type/register evidence, including activation
failure. `native-writer-observations.json` retains bounded file-object bytes and
decoded fields before validation, previous valid bytes/fields, translations,
the first bad snapshot, events, lifetimes, pending gaps and cleanup results.
`result.json` references the same evidence and records the opt-in/static guard;
`probe_sources` includes all four observer modules only when enabled.

Bounds remain4096 events,512 writes,64 lifetimes,1GiB requested bytes,120 virtual
seconds,262144 translated data pages and1MiB metadata reads. Code evidence has
its separate bounded read/view/storage limits. The existing public CLI host
limit remains600 seconds; the private predecessor allowed1200 seconds. A timeout
is incomplete evidence, never permission to retry or weaken a guard. Breakpoint
observation is not claimed to be timing-neutral.

`interval_closed` describes this bounded observation protocol. It does not mean
NEF acceptance, fsync, durable media, historical completeness or frame binding.
The corresponding acceptance flags stay false. Independent stopped-card export,
LibRaw comparison and source-to-writer/NVMe analysis remain separate operations.

## Public acceptance recipe, not yet executed

This recipe requires an already available artifact with the exact admitted
runtime digest. It is not a `build main, then enable the flag` recipe. Use fresh
output and preserve the run's recorded hashes. The menu sequence is the retained
C12/capture16 soundOFF/RAW/Lossless input sequence; it is not firmware patching.

```sh
Z8_MENU_ACTIONS='menu,left,down,down,down,down,right,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,down,right,j,left,right,left,left,up,up,up,up,right,down,down,down,down,down,down,down,down,right,right,up,up,up,up,j,left,down,down,right,up,j,right'
python tools/z8_capture_probe.py \
  --firmware "$Z8_ORIGINAL_MAIN" --qemu "$Z8_VALIDATED_QEMU" \
  --raw "$Z8_RAW_INPUT" --output "$Z8_FRESH_OUTPUT" \
  --capture-properties profiles/z8-capture-properties.json \
  --seconds 600 --settle 10 --half-seconds 2 \
  --menu-actions "$Z8_MENU_ACTIONS" \
  --card --gpio --rtc-status --tagged-input --capture-selector \
  --capture-timing-input --capture-pixels --nvme-trace \
  --skip-cseq-repeat --native-writer-observer
```

The capture profile and ROM/resource inputs retain their existing hypothesis
labels; observer success does not validate those hardware assumptions. Do not
add HSFC/serial5 inputs or change limits and call it the same acceptance run.
Firmware-free unit tests are included in normal discovery; for example:

```sh
python -B -m unittest discover -s tests -p 'test_z8_writer*.py'
python -B -O -m unittest discover -s tests -p 'test_z8_writer*.py'
```
