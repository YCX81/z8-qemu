<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Exact wide internal D980 control candidate

Status: isolated build and CPU-free QTest passed on the published generation-journal baseline. No Nikon program has run with this new option; no physical camera operation or RAW precapture acceptance.

The stopped HALF19 original-program observation reached `150c0064=ff00`, then `150c0008=ff00` at PC40c1fc58. Its28 submitted/latched D980 words were identical. Relative to the existing narrow internal profile, only+4,+8,+100 differ: `20580000`, `12500000`, `38a0`. These encode8280×4688 with pitch14496; height low16 is0. Remaining25 words include+118=3, bank0, four e0000000 FIFO addresses with zero high words and eight zero spare-slot words. This navigation attempt ended before the final C120 UI/HALF gate, so this configuration is not a C120 output acceptance.

`z8-d980-internal-wide-start-model` is a separate default-off, Z8 creation-time option and requires `z8-d980-internal-start-model`. Existing flags alone preserve the rejection. Admission requires the exact whole tuple, the existing original submit/commit/ff00 enable contract and all prior validity/stop/reset/frame/byte/pending/preparation/arm guards. It only waits for an unimplemented internal supplier.

No RAM/FIFO alias, input epoch, DMA, pixel tag, frame, source notification or completion IRQ is generated. The shared internal predicate excludes both tuples at the existing periodic/single receive, arm, timer and active-mode entrances. Original cancellation/reset semantics remain. A stopped old internal latch still permits later ordinary RAM preparation.

Active internal relatching now compares the entire submitted and latched arrays before any latch. Identical commits retain the existing harmless behavior. Narrow↔wide and internal↔RAM changes require the original cancellation/reset; recognizing both tuples must not silently permit a supplier change.

The existing `z8-d980-internal-state` getter adds `wide_enabled`, admission-only `wide_starts` and `last_accepted_profile` (0 none,1 original,2 wide). The last-profile identifier persists as provenance after cancellation and does not mean currently running; read it with `waiting`. None of these fields is a pixel/history count.

`tests/test_z8_d980_wide_internal.py` runs real, CPU-free QTest using a four-byte inert image and synthetic pixels. All52 cases passed in normal and optimized Python modes; all32 impacted existing internal tests passed under optimized Python. A prior actual run against immutablea3b rejected the new tuple at150c0008, providing the negative control. Cases cover exact admission, opt-in/creation constraints, all28 one-word deviations, protected-state equality on rejection, active transitions, original cancellation/reset, and no D980 output/epoch. With the generation journal enabled, internal admission generated no output record; after original cancellation/reset, a real2048-byte ordinary RAM output had a matching journal hash. Parallel preview or unrelated original control-stop IRQs are separate from D980 image completion.

The tested immutable binary SHA256 is0264b8cec6facccd05b2b6e585a4f0b23e05b885708c3f8b6e360910fc8d769d. Build/test receipts bind the actual model/test/support sources and binary before/after, and all owned QTest processes stopped. Original-program HALF20 remains a separate, pending experiment.
