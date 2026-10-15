# Finite capture receiver reprogramming

RAW60's first historical RAW reached the actual card, then original C3.11
`40c26848` wrote `0x103` to C1 `15432004` while that receiver was active and
held. The old control model rejected it. The first NEF also had an empty
main preview; two other declared JPEGs decoded. A rearm must not conceal
that missing consumer.

The default-off, immutable `capture-stream-rearm-model` adapts
`capture_stream_can_rearm` from local Z9 shared146 commit
`c6dbfc4ef3ea2ac9d48be2ab66c7d732e241b1ff`. It requires the existing NV16
pipeline. Admission is restricted to the observed finite C1 capture header:
current control `0x102`, latched START `0x101`, complete accepted header,
active and held, and the new command exactly `0x103`. Video profiles are
not admitted.

All three outputs must have been consumed by actual JPEG encoding for the
current NV16 generation. No request or failure may remain, and source,
input and receiver pending causes must be zero. This adaptation additionally
requires the JPEG status and core acknowledgements to have completed.
Rearm only clears active/held control state and increments its own counter.
It does not write pixels, retire pending causes, emit an IRQ, free a guest
allocation, or alter a task flag. C0's separate stop contract is unchanged.

`tests/test_z8_capture_stream_rearm.py` uses real QTest RAM and MMIO, with no
Nikon program execution or host-injected ownership. It produces 151 NV16,
checks all three resized outputs against actual input pixels, encodes and
independently opens three JPEGs, acknowledges the original causes, then
reprograms C1. Fourteen normal and optimized cases cover two generations,
each missing JPEG, each pending producer/receiver cause, separate JPEG
status/core ACKs, an unheld receiver, an unsupported control, default-off
behavior and the immutable option. Twenty-two existing C0 cases pass.

The old binary fails at the same C1 write after those actual consumers.
Earlier fixture failures (scale field encoding, initial source reset, and
an incorrect assumption that input pixels were all zero) are preserved
separately. These component results do not establish native burst recovery,
allocation release, file completeness, or physical receiver behavior.
