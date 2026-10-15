# Z8 primary frame boundary experiment

This default-off QEMU option supplies primary subgroup0 bit3 only after the
existing combined ISP has successfully written all three Y and three UV preview
planes. It models a frame-boundary hypothesis. It does not implement retained
Bayer frames, D980 internal FIFO delivery, RAW output, a camera update, or physical
sensor timing.

Enable `z8-primary-frame-boundary-model=on` only with the Z8 machine, the existing
RAW preview pipeline, primary control/stop, and stopped-source reset models.
The option is immutable after machine realization. Read its counters through
`z8-primary-frame-boundary-state`.

## Original program evidence

The bindings below are for the original Z8 C3.11 MAIN, SHA-256
`2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853`.

RAW34 observed the original diagnostic at `40eb5aec` with code31,
mask`80000008`, returned flags0 and LR`40e9973c`. This follows the failed
`40e99720` wait on the event object at`a03b6b498`. Observed type11 jobs at
`40e99684` had disposition0. The observations do not establish a backlog or
allocation identity from task numbers alone.

The original hardware-status dispatcher tests packet word0 bit3 and enters
`410b0a78`. Its base event`04220009` minus7 produces`04220002`; the native call
at`410b0aa0` uses the registered subscriber. The callback at`40e8b81c` selects
`40e8b880`, and`40e8b894` sets mask`0xc` on the event object. The original
primary interrupt mask actually included bit3 (`150c0200=a0003ffc`) in RAW34.

There is also a software operation-report path through`422c0002` and
`410b5550/5564`. It is a separate producer and must not be substituted for the
hardware-status route. Native operation reports and frame pixels are different
evidence.

## Model contract

- Configuration/start alone generates no boundary. Missing input, inactive or
  held sources, stopped input timing, and rejected output geometry generate none.
- A boundary follows all six successful RAM writes. It does not increment the
  existing D980 frame, RAW151 job, sensor-tag, or retained-history counters.
- Primary raw status`150c0208` retains bit3 while masked. The enabled view at
  `150c0204` and the actual primary IRQ follow`150c0200`.
- The original 32-bit W1C at`150c0204` retires this cause. Other primary causes
  and the shared IRQ remain independent. No additional preview frame is written
  while this boundary remains pending.
- The existing native reset sequence cancels pending ownership separately from
  acknowledgement. Width, machine, dependency, and default-off boundaries remain
  strict.

This completion/acknowledgement pacing is an explicit functional assumption.
It is not a recovered silicon specification or proof of historical RAW ownership.

## Validation

`tests/test_z8_primary_frame_boundary.py` uses QTest without Nikon firmware or CPU
execution. An authored black Bayer input is converted into six actual RAM planes;
the test checks every Y/UV byte before checking status and IRQ. It covers pending
reuse, masking, unmasking, W1C, shared causes, absent input, inactivity, timing,
invalid final-plane geometry, hold, reset, wrong widths, dependencies and machine
creation. The old binary passes the six-plane baseline and fails the bit3 case.

The new binary passed19 cases in both normal and optimized Python, plus32 D980
and22 C0 regression cases. Receipts under`.local/primary36` bind the binary and
sources and verify every owned test process stopped. The first normal test run
also retained a fixture error: the existing statistics model rejects missing RAW
input at startup. The final test checks that explicit startup rejection.

The separate RAW36 experiment executed the original C3.11 program with the
explicit four-byte experimental RAW-format policy. It used the same185 planned
contacts as RAW35. It stopped after183 consumed contacts, during final HALF and
before FULL, at the first new unsupported access:
`40c3167c -> write32 152c0008 = 1`.

Before stopping, the new boundary had323 completions and323 native W1C
acknowledgements. Actual `40e8b894` calls had X0=`a03b6b498`, W1=`0xc` and
LR=`410b0aa4`, confirming the original hardware-status subscriber route.

A type11 job with native history disposition1 then reached`40e9978c`, requested
four slots, and successfully returned a class39 allocation. The first descriptor
was`a1192e6a0`, with backing record`a118f6430`, base`8eb8dfc00` and per-slot
capacity`420a00`. The original request contained encoded targets`ceb8dfc00`,
`ceb4bf200`, `ceb09e800`, `ceac7de00`. This establishes original allocation and
request construction during HALF; it does not establish completed pixels or
retention lifetime.

The new hardware stop exposes a different152c mode: implicit input, four output
targets,4140x2756 dimensions and14-bit input configuration. Existing152c request
admission expects a different explicit-input profile and rejects this start.
RAW36 still had zero D980 RAM frames, RAW151 jobs and accepted history NEFs.
All173 watched sources/inputs and the owned-process shutdown were verified.
The frozen binary was
`8f01c24a53f39d0f297b1223bf20f0e700dbc86d72dccf19036048f2b00a66a1`.
