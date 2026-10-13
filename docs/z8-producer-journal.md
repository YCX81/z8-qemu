# Bounded producer generation journal

The optional machine property `producer-generation-journal=on` records metadata for actual D980 RAM output,151 instance0 NV16/RAW output and1540 RAW copies. It defaults off and cannot change after machine initialization, including off→on. Read it with QMP `qom-get`, path `/machine`, property `producer-generation-journal-state`.

It adds no pixel producer and does not enable historical input selection. Original latest-source restrictions, busy state, RAM writes, results, IRQ and W1C remain. The internal D980 FIFO profile still supplies no pixels. This is not RAW pre-release capture or native retained-owner acceptance.

The fixed64-record journal stores complete physical spans,format/shape/stride,full SHA256 read back from actual RAM,completion virtual time,model operation/write/reset epochs and parent/sensor model lineage.151 NV16 and optional RAW outputs publish together only after the original full operation succeeds. Every plane must match stride×height and packing. Unknown parents retain actual input span/hash with a null parent ID and an explicit gap. No host pixel archive is kept or fed back into guest RAM.

Known monitored destination writes invalidate overlapping old records before the first write, including partial failures. Separate-RAM A and B remain separate records; same-content reuse still creates a new write epoch. Capacity exhaustion is explicit and never silently evicts records. Accepted primary and151 reset barriers conservatively close their producer's parent availability, without recursively erasing completed downstream copies.1540 and general machine-reset lifetimes are not claimed. Stop/cancel/W1C are not treated as resets.

`no_monitored_overlap_since_completion` is limited to these three instrumented producer paths. CPU/other DMA writes, same-content ABA outside those paths, native allocation generation and ownership remain unverified. Run identity must come from an external run manifest. Frame/job counters are model lineage, not native camera identities.

Returned telemetry errors do not alter original producer success. First readback failure retains both the full requested span and actual failed chunk plus `MemTxResult` when available. Pre-read range rejection does not read MMIO. Readback reason1 means unavailable context/scratch,2 means range/bounds rejection,3 means a returned bus read failure; `memtx_result_present=false` means no bus result was obtained. Existing QEMU/GLib unrecoverable OOM semantics remain. SHA uses one reusable GLib context and bounded scratch allocated only when enabled.

Run the helper tests with `sh tests/run.sh` (the unittest discovery compiles the C helper/adapter cases in normal and optimized/NDEBUG modes). Linux CI supplies cc, pkg-config and GLib; missing build dependencies are explicit test failures. They use authored RAM stubs and are not QEMU transactions.

Use Linux or WSL for these C/model tests; native Windows compiler support is not provided by this entry.

After the normal repository build, run actual firmware-free model tests:

```sh
python tests/test_z8_producer_journal.py \
  --qemu .build/build/qemu-system-aarch64 \
  --output .local/producer-journal-check
```

The output directory must be fresh. CI runs the same command after building. Tests reuse existing QTest support and use an authored zero kernel, synthetic sensor data and the QTest accelerator; no Nikon program executes. Seven cases cover full A/B D980→151→1540 transactions with the option off/on, actual destination reuse, unchanged151/1540 rejection of oldA after B, and both post-init option-change refusals. Full output hashes,functional states and IRQ/W1C are checked. Each case saves stderr and raw QTest command/reply/IRQ lines and closes its owned process.

The private candidate's seven actual transactions passed with identical off/on bytes,state and stderr; the portable public entry was separately rerun before integration. The first private test attempt failed because its IRQ-drain read targeted write-only150d9800. That failed receipt was retained; changing the test to read ordinary RAM fixed it without changing the model. Partial-write/readback-error injection,malformed metadata,overflow and reset-domain counterexamples are helper/adapter evidence, not claims of actual QEMU fault injection. No producer fault-injection property was added.

These results establish bounded emulator telemetry for ordinary RAM captures. They do not establish a historical pool/descriptor lifetime, sensor fidelity, camera hardware behavior or NEF pre-release acceptance.
