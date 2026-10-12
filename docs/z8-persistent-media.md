# Explicit persistent NAND for Z8 experiments

This optional backend persists the existing logical NAND fixture. It does not
recover camera flash contents, calibration, ECC or a filesystem. Passing these
tests does not establish C10 import/selection or the original settings-save path.
NOR and the default Z9/Z8 in-memory NAND behavior remain unchanged.

## Machine options

```text
-M nikon-z8-bringup,bootstrap-fixtures=on,nand-fixture=on,z8-nand-image=/absolute/private/nand.media,z8-nand-image-create=on
```

`z8-nand-image` is absent by default. It requires the Z8 machine and both fixture
options. `z8-nand-image-create=on` permits creating a missing image; without it a
missing file is rejected. Existing valid images are loaded even when create is
on, never reset. Both options are immutable after realization.

Use a separate private media path, never the MAIN path. Source MAIN identity is
stored in the media header and checked on every new process. Symlinks, hardlinks,
non-regular files, malformed snapshots and mismatched MAIN identities are rejected.
A companion `.lock` file holds an exclusive process lock; it remains after exit,
but an existing lock file alone does not mean that an emulator is running.

Read-only QOM `z8-nand-image-state` reports JSON fields:

- `enabled`, `loaded`: explicit backend state;
- `pages`: currently materialized logical pages;
- `commits`: successful snapshot commits in this process, including initial
  empty-image creation when applicable;
- `failed_commits`: attempted media commits that failed in this process.

The original modeled program/erase operation reports success only after the
snapshot commit returns success. The backend uses a sibling temporary file,
file sync, atomic rename and directory sync through GLib's consistent/durable
write API. A failed commit restores the affected in-memory pages and produces
the existing NAND failure status/interrupt cause, rather than a completion success.
As with any host storage error after a rename, a durability error can require
restarting and validating the file to determine the final durable state.

Each NAND command is one transaction. An erase followed by a program is **two**
transactions; the backend does not make that pair atomic. A settings experiment
requiring old-or-new recovery must implement its own A/B block or journal protocol.
No host power-loss, physical NAND lifetime or hardware timing claim is made.

## File format, version 1

All integer fields are little-endian. This is a laboratory snapshot format,
not a Nikon update container, NAND dump format or cryptographic authenticity proof.

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 8 | ASCII `NZ8NAND1` |
| 8 | 4 | Format version, 1 |
| 12 | 4 | Reserved, zero |
| 16 | 8 | Source MAIN byte length |
| 24 | 32 | Binary SHA-256 of source MAIN |
| 56 | 4 | Page bytes, 2112 = 2048 main + 64 spare |
| 60 | 4 | Page count, 131072 |
| 64 | 4 | Pages per erase block, 64 |
| 68 | 4 | Number of materialized page records |
| 72 | variable | Sorted records: u32 zero-based page index + 2112 page bytes |
| final 32 | 32 | SHA-256 of every preceding byte |

Length must be exactly `72 + record_count * 2116 + 32`. Page indices must be
in range and strictly increasing; duplicates, trailing bytes, truncated records,
unsupported geometry and checksum errors reject startup without rewriting media.
Missing pages read as 0xff. Programming retains NAND bitwise-AND semantics;
erasing removes the selected block's materialized pages.

## Minimal MMIO example

The following is a QTest example for an explicitly empty laboratory image.
The CPU remains paused. It uses page 128, aligned to a 64-page block, and an
authored data buffer in work RAM. Do not infer that this is a stock settings block.

```text
writel 0x17a0032c 1
writel 0x17a00300 7

# Erase block beginning at page 128.
writel 0x17800004 128
writel 0x17800000 0x40101000
readl  0x17800014                 # 0x8000 success; 0xc000 failure

# First write exactly 0x6000 authored bytes into RAM 0xa00010000 using QTest
# "write 0xa00010000 0x6000 0x<49152 hexadecimal digits>".

writel 0x17800138 0xffffffff     # W1C old success cause
writel 0x17800128 0xffffffff     # W1C old failure cause
writel 0x17800004 128            # first page
writel 0x17800008 0x40010000     # NAND-local DMA address
writel 0x1780000c 0
writel 0x17800400 1              # column 0, one sector per page
writel 0x17800404 0x08000800     # sector and last-sector bytes: 2048
writel 0x17800000 0x4030210b     # program 12 pages (count minus one = 11)
readl  0x17800014

# In a fresh process using the same MAIN and media, repeat initialization and
# address/geometry setup, then issue read rather than program.
writel 0x17800000 0x4030220b
readl  0x17800014
read   0xa00010000 0x6000
```

The DMA-local aperture is `[0x40000000, 0x60000000)`, translated to physical
`0xa00000000 + local - 0x40000000`; the entire span must be mapped work RAM.
Requests cannot cross a 64-page erase-block boundary. Column offset is encoded
in the upper 16 bits of `0x17800400`, and main+spare accesses remain bounded to
2112 bytes per page. IRQ cause bit 0 appears at `+0x138` on success or `+0x128`
on failure when command bit 20 is set; existing mask/acknowledgement rules apply.

## Verification

```sh
python tests/test_z8_persistent_media.py --qemu /path/to/qemu-system-aarch64 \
  --output .local/analysis/c10-20261011/media/green
```

The script uses only an authored four-byte zero kernel and its own paused QEMU
processes. It checks default Z8/Z9 behavior, explicit creation, program/read/erase,
spare-byte bounds, new-process restoration, invalid metadata and checksums,
duplicate pages, source aliases, exclusive locking, immutable properties,
failed writes and in-memory rollback. It also induces a real short file write
using a temporary resource limit on a test-owned process, then restores that
limit before cleanup. It neither loads original firmware nor demonstrates C10.
