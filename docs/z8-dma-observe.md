# Z8 board-write observer

`qemu/nikon-dma-observe.h` is adapted from the WonderLantern z9-qemu shared441
observer (`065ea58`). It is off unless the environment variable
`WL_DMA_WRITE_OBSERVE=low:high` (hexadecimal, half-open) is set. It changes no
write, completion, interrupt or guest state.

When enabled, every write this board model issues through `address_space_write()`
or `cpu_physical_memory_write()` that overlaps the interval prints one stderr line:

```
WL_DMA_OBSERVE {"index":0,"producer":"<C function>","line":<source line>,"virtual_ns":...,
  "address":"0x...","bytes":...,"api_result":0,"sample_address":"0x...",
  "sample_ram_backed":true,"sample_matches_RAM":true,"sample_hex":"<up to 64 bytes>"}
```

`api_result` is the `MemTxResult`, or `-1` for the void CPU-physical API.
After 4,096 records one `limit_reached` line is printed and later writes are not logged.

## Differences from upstream

- Upstream accepts only work RAM `0xa00000000..0xa20000000` and at most 16 MiB.
  Z8 accepts an interval inside either:
  - work RAM `0xa00000000` plus the machine RAM size, or
  - with `image-bank-model=on`, the image-bank span `0x840000000..0x8fff47400`.
    This span contains the C30 history 0x39 block pool `0x8_77ba7200..0x8_ebd00600`.
  There is no size cap; the record limit bounds the output.
- The interval is checked when the machine is created. An invalid interval exits
  with status 1 before any guest runs, instead of on the first observed write.
- The 64-byte sample is read back only when it lies in RAM-backed memory. The
  0x200-byte gap between the two image banks is reported as
  `"sample_ram_backed":false` rather than read as unmodeled MMIO.

## Limits

Guest CPU stores, including firmware writes to its own descriptors, are not
observed. So are writes made through RAM host pointers, such as persistent media.
Within the 0x39 pool, a busy interval can reach the record limit within the first
frames; narrow the interval to the slot under study.

## Test

From a Linux build checkout:

```sh
PYTHONPATH=tools:tests python3 tests/test_z8_dma_observe.py \
  --qemu /path/to/qemu-system-aarch64 --output /fresh/output/dir
```

The test uses the authored 151/1543 preview protocol from `test_z8_c0_stop.py`.
It needs no firmware and executes no guest CPU instructions. It checks:

- Default off: frames are produced and no records are printed.
- An interval inside the 0x39 pool records the actual preview writes. Every
  sample is RAM-backed and matches RAM, and the last sample equals a QTest read.
- A work-RAM interval ignores writes to the image banks.
- Six malformed or out-of-window intervals are rejected at creation.
- Output RAM and the frame count are identical with the observer on and off.
