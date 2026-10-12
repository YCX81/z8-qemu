# User-supplied firmware

The Z8 entry points require the extracted Z8 C 3.11 MAIN payload. The retained
Z9 reference tools have a separate identity; the two inputs are not interchangeable.

| Property | Z8 C 3.11 | Z9 C 5.32 reference |
| --- | --- | --- |
| Length | 81,807,709 bytes | 81,823,629 bytes |
| SHA-256 | `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` | `6380ded18395ce11cb327c766721274c85127d2cf1a1e887ee60e3d156810bdd` |
| Initial load/entry | `0x40160000` | `0x40160000` |
| CPU family | AArch64 | AArch64 |

The complete length and SHA-256 are the identity lock. Public profiles do not
need a copied instruction prefix. Z8 MCU bindings and menu regression hashes
are version-specific; shared emulator code is not evidence of hardware equality.

The original upgrade container is not this input. Obtain a compatible payload
independently and keep it outside tracked source. This repository does not
provide a decryption key, extraction service or firmware download.
The hash pins a research sample; it is not a vendor signature validation.

The launcher opens the file for reading and rejects mismatches. It derives one
virtual ROM parameter image from defaults inside that local file and records
the derivation. Four-byte format trailers and a few version/selector values
are generated explicitly. These fill only selected gaps in the emulated ROM
address space; they do not reconstruct the missing sensor program, parameter
tables, calibration or physical flash layout.

The strict bring-up runner and the externally assisted menu runner exercise
different scopes. In the latter, explicit MCU/peripheral peers and documented
resource assumptions are required. A passing menu run does not establish bare
boot, a complete camera, hardware timing, or a flashable product. Slot experiments
create a separate derived MAIN for QEMU; the original input stays read-only.

Generated `.bin`, run JSON, logs and screenshots may contain proprietary
material and remain local. `.gitignore` is a convenience, not a sufficient
publication audit. Never add those artifacts with `git add -f`.

For a different version, do not change the accepted hash alone. Re-establish
entry points, object layouts, peripheral mappings and host peer bindings,
then add a separately tested profile. Shared SoC branding is insufficient.
