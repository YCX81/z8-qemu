# Research and rights notice

This Z8 research fork builds on WonderLantern's independent Z9 emulator and
retains its shared model and reference tools. The fork adds Z8 C 3.11 bindings,
diagnostics, and laboratory experiments. Upstream and fork contributions retain
their applicable source notices. Nikon has not sponsored, endorsed or supplied
this project. Nikon, Z8 and Z9 identify interoperability targets; their owners
retain their rights.

We respect Nikon's rights and do not publish its private code. This repository
distributes original emulator/tool source and the accompanying open-source
license notices. Users provide their own compatible local firmware. A source
license here does not grant rights to Nikon firmware or other third-party
material. Firmware-derived local files are excluded from publication. The
publication script checks inventory, hashes and selected content patterns; a
passing result is not a complete provenance review or a legal determination.

The original public Z8 profile included a copied 32-byte entry prefix. Removing
that field from the current tree does not remove it from older Git commits.
That historical residual is recorded in docs/publication.md; history rewriting
and force-pushing are not part of routine source updates.

No camera updater, flashing tool, USB memory access or physical-device payload
is included. The model is incomplete and its synthetic inputs must not be
mistaken for recovered factory data or a hardware specification.

The overlay/tools use GPL-2.0-or-later. QEMU retains its upstream licensing;
see COPYING and LICENSES/QEMU-LICENSE.txt. No proprietary firmware is relicensed
under the source license.
