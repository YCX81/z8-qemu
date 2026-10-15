# Two native RAW encoder channels

RAW62 completed the original C1 reprogramming, then stopped at
`40c24cbc: write32 153c01b4 = 0`. Actual X0 identified object `a02c973e8`.
The C3.11 initializer `40c2459c` registers this object with base153c and
INTID130, then object `a02c97418` with base1540 and INTID131. Both use
callback `40c24658` and the same original leaf setters.

Default-off `z8-hsfc153c-lossless-model` requires the existing historical
F0 models and can only be selected for Z8 before realization. Its descriptors,
parameters, interface, subscription, result length, pending cause, counters
and IRQ130 are separate from1540. Mode44 shares the existing153c boot shadow;
boot/reset words0/4 retain their existing path. Register masks follow the
shared native setters. Unsupported widths, offsets and values remain strict.

The shared encoding transaction keeps the actual U16 source, unique valid
acknowledged ISP generation, exact RAW58 compression profile/Huffman tables,
capacity, overlap, full encoded readback and no-partial-success guards.
Each record identifies its encoder base. Either bank rejects a source already
encoded by the other. A pending output holds its full reservation against
the other bank; only its own W1C releases that protection. Ring metadata for
a still-pending output cannot be overwritten. Completion becomes visible on
the corresponding IRQ only after full output/readback.

RAW63 subsequently observed the153c full configuration and its current
allocation before START. Frame14 completed8,587,390 encoded bytes through
IRQ130, with one native W1C and pending zero. Independent decoding matched
all11,409,840 samples to actual U16 RAM, also exported independently before
START. Frame13 used1540/IRQ131 separately. The second file did not complete:
the next boundary was the second JPEG bank at15e21004. The card contained
one independently verified NEF; this is not multi-photo acceptance.

`tests/test_z8_hsfc153c_lossless.py` verifies actual RAM and independent full
F0 decoding without Nikon CPU execution. Fifteen normal/optimized cases
cover two different RAW generations, simultaneous independent IRQs and
results, masking/W1C isolation, duplicate-source rejection, both directions
of pending-target overlap, the unused part of the full reservation, ACKed
cross-bank reuse, bad data/profile, default-off and immutable configuration.
Nineteen existing1540 historical cases also pass. The first candidate's
cross-bank overwrite failure is retained; it was never used by native firmware.

## Ownership integration points

The pure transaction has named `HSFC_OWNER_RESERVE`, `HSFC_OWNER_COMPLETE`
and `HSFC_OWNER_CANCEL` locations. Preflight, allocation and codec failures
all converge on one cleanup exit. Reserve belongs after all checks/encoding
fit and before retiring receipts or writing RAM; complete belongs after the
full readback; cancel applies only to a token acquired by that transaction.
No `nikon-buffer-ownership.h` implementation is connected in this tree.
The state explicitly reports `output_ownership_connected=false`.

Each wrapper passes its own monotonically increasing `encoder_job`; the
combined historical `job` is only a receipt identifier. Integration must map
the encoder base to distinct ownership slots and must not put153c in1540's
slot. Both native W1C paths have an `HSFC_OWNER_ACK` location after validating
the exact bank/job record and before marking it acknowledged or clearing the
pending cause. The existing main1540 `nb_ack` must be used exactly once there;
its failure must leave both the record and pending status unchanged.

Ordinary producer-journal F0 records now follow main's ABI: `stride` remains
the input pitch, `stride*height` bounds encoded bytes, and the actual span
length remains the compressed length. The standalone lineage test is
`test_z8_lossless14_lineage.py`; the153c test retains its independent name.
HSFC still rejects `producer_journal.enabled`: support by `source_kind`
remains pending integration. The shared152c/1530 frontend hooks are reserved
for the video/integration work; this checkpoint does not replace them.
