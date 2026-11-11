# Source helpers for the stock-to-v36 reconstruction

These are the historical C64 card-state generators and portrait-layout sources
used by the matching Fenestra workflow. They are separate from the default
emulator overlay and do not load or distribute an intermediate MAIN.

Run the [companion build entry](https://github.com/YCX81/fenestra-z8/tree/z8-c311/research/z8-live/from-stock)
with an original Z8 C 3.11 package and this repository's root as `--qemu-root`.
The helpers use the shared `tools/` assembly and original-input validation
utilities. No emulator process or physical device is started.

The slot generators come from the November card-state implementation. The
orientation helpers come from the preserved orientation branch. The final
portrait layout source was recovered from its complete source diff, checked
against original Git blob `c00e4620b7a89a7056356b7481b8e8190ddb1d1b`; it expresses
authored coordinate placement, not copied portrait assets.

The Python sources retain GPL-2.0-or-later notices. Do not substitute the default
NAND-based slot generator for these card-state helpers: their record and native
settings contracts differ. The reconstruction verifies complete output hashes.
