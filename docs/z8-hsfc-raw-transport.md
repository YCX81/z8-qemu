# Experimental Z8 history transport

This default-off model connects an internal Bayer source to original HSFC
buffers and a paired RAM decoder. It is a functional simulation with an authored
input image. It does not implement Nikon's internal compression algorithm or
establish physical camera support. No HSFC NEF has yet been accepted.

The original C3.11 MAIN remains unchanged. Private firmware experiments execute
a separately identified four-byte format-policy variant. That variant is not a
camera firmware update and is not the final mode-scoped feature patch.

## Completed data boundaries

`z8-hsfc152c-control-model` admits the observed four-buffer request only when its
configuration, source and complete RAM extents match. Control admission supplies
no pixels or completion event.

`z8-hsfc152c-pixels-model` writes actual NZ8BAYR1 packets into those native output
buffers. Each packet contains a full 4140 by 2756 packed14 Bayer image, padded
rows, an authored frame identifier and virtual source time. Compression within
this private packet is lossless. The model does not retain historical image
payloads outside guest RAM. Native buffer replenishment, W1C and stop requests
control reuse. A completed write precedes the data interrupt.

RAW38 independently recovered two complete packets before both native FULL
input consumers and another captured after both consumers. This proves those
simulation RAM frames and their ordering, not native file saving or sensor timing.

RAW39 reached the original decoder request. It selected the RAM address of
frame23 while frame61 was the latest source frame. Its requested output had a
6240-byte stride, width4140 and height2756. These numbers alone do not establish
the physical pixel format.

## Paired decoder convention

`z8-hsfc1530-decode-model` reads the requested packet from actual RAM and checks
the complete packet against its producer metadata. It rejects missing, changed,
corrupt, out-of-range and overlapping data before output. It never substitutes
the latest input image or restores a host archive.

For the observed output profile, the explicitly chosen simulation convention is
packed12 little endian. Each 14-bit sample is converted by
`(value * 4095 + 8191) / 16383` with integer division. **This precision conversion
is lossy.** The paired decoder is not a recovered Nikon codec, and the stride is
not presented as proof of the hardware packing.

After the complete output write, status bits1 and2 reach IRQ10c. The original
ISR and callback convert these into separate frame-data and whole-job flags.
The observed single-buffer request waits for the whole-job flag. A later stop
deasserts start without manufacturing another completion. W1C acknowledges
individual causes; reset cancellation is counted separately. Completed RAM is
retained. Continuous decoder requests remain unsupported.

## Validation and remaining work

Fifteen QTest cases pass in normal and optimized Python. They compare every
output sample and row padding against an independent calculation from an authored
source, verify canaries, select an older frame while a newer frame exists, and
exercise corruption, missing input, RAM gaps, overlap, command widths, default-off
behavior, IRQ masking, W1C, stop, reset and explicit target reuse. Existing packet
and reset suites also pass. QTest does not execute Nikon firmware.

The separate RAW40 original-program experiment adds a passive snapshot after
the native decoder start returns. Its first attempt stopped at the pre-input
gate: the original UI displayed an error and remained in single-frame mode.
Decoder configuration writes, reset applications and decode jobs were all zero;
the new data path had not executed. An unchanged repeat passed the mode gate and
decoded retained frame23 while the producer had advanced beyond it. A snapshot
immediately after the original hardware-start return verified all17,197,440
output bytes against the actual retained input packet. Its source time was before
both original FULL consumers. This is actual RAM evidence, not a saved NEF.

That repeat exposed a completion-protocol error in the first model: frame-data
bit1 alone did not satisfy the original single-job wait. The original worker
returned code4 and entered its diagnostic result read after about0.984 seconds.
The corrected model publishes both completed-data and completed-job causes
after the actual DMA. It passes the updated15-case normal/optimized suites and
the packet/reset regressions. RAW41 is testing its original wait return and
backend callback. RAW41 subsequently observed the actual one-shot byte1, wait
mask4, return status0, returned flags5 and report result0 at both original
worker and backend callbacks. It then reached the next unsupported start at
`15380004` with the decoded RAM address as input.

`z8-hsfc1538-identity-model` extends the existing neutral NR model to a uniquely
identified decoder output. It verifies the full current RAM hash, geometry and
descriptor, preserves every byte in place, and tracks the decoder job separately
from D980 generations. Eleven QTest cases pass in normal and optimized Python,
including actual ordinary D980 regressions with the option off and on. This is
an explicit no-op image-processing convention, not Nikon denoising. The original
NR parameter block and actual post-NR output were checked in RAW42: the native
descriptor contained mode2, stride6240 and4140x2756 geometry. Its complete output
equaled the decoder snapshot. The original program then requested NR bank0 for
frame24 in a different actual decoded buffer.

`z8-hsfc1534-identity-model` adds that second bank. Consumption is recorded per
decoder generation, preventing both A-B-A repetition and reuse through another
bank. IRQs, frame identities and hashes remain separate for the two banks.
The first two-bank probe also exposed a host-observer assumption: either bank
may start first. The observer now selects the actual MMIO base from the original
start instruction's register. RAW44 verified frame23 through bank1 and frame24
through bank0, including native acknowledgements and complete independent RAM
readbacks for both.

RAW44 then submitted a new frame to bank1 without first writing zero to its
start register. Admission now uses pending completion and per-frame consumption
for this observed historical path, preserving the last-command readback. Eighteen
normal and optimized QTest cases pass, plus decoder regressions. RAW45 then
processed frames23 through26 across both native NR engines, including full
independent output checks for frames25 and26.

The next boundary was normal receiver shutdown: the original program wrote
zero to start, waited for end, timed out and entered forced termination. The
supplier now responds to an owned, quiescent stream stop with a distinct end
cause. It preserves pending image causes and completed RAM, creates no frame,
and separately counts end acknowledgement versus reset cancellation. Eight
normal/optimized end tests and the15 pixel/32 control regressions pass. RAW46
is checking the original stop wait and report before proceeding downstream.

Required work
includes downstream processing, native writer/card output, independently readable
NEFs with verified pre-trigger pixels, and a narrowly scoped final format policy.
Physical camera behavior and Nikon's real codec remain unverified.
