# Nikon Z8 firmware emulation research

[简体中文](README.zh-CN.md)

Documentation updated: **2026-10-19**. This workspace ports Nikon Z8 C3.11 to the research emulator from [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu), starting from upstream commit `27498735e3ed202052dfc6306ded6b867af70a58`. Z8 firmware identities, MCU bindings, resources and acceptance baselines are defined separately from the retained Z9 reference tools.

**Assisted original-firmware menus, synthetic live view and ordinary post-trigger NEFs (one packed14 and one F0 lossless) have recorded local QEMU acceptance. RAW pre-release capture remains incomplete.** This is an experimental emulator, not camera-installable firmware. It does not connect to or flash a physical camera.

The status below was reconciled on 2026-10-19 against main `a140c42` (later commits change documentation only) and the results recorded in its documents. This documentation update did not rerun firmware, verify the current binary or test physical hardware. Historical results belong to their recorded inputs, sources and binaries. Detailed research notes are mostly in Chinese; see the [feature TODO](docs/TODO.md) and [implementation record](docs/z8-status.md).

## Current results and limits

| Area | Recorded evidence | Remaining boundary |
|---|---|---|
| Menus and synthetic live view | Three MENU → half-press return cycles; five scanout checks with an independent decoder. Historical run: 1,743 RAW frames, 2,547 LCD frames, 731 containing live view; zero unknown MMIO accesses. | External MCU peer and explicit virtual resources required. Menu hashes are regression baselines, without real-camera image comparison. Bare-model full boot is unverified. |
| C32/C64 Picture Control slots | Separate four-stage acceptance for 32 and 64 photo slots plus 32 and 64 video slots: import, native selection, guest NAND save and new-process recovery. | Explicit experiment using a derived MAIN and active firmware calls. Real Nikon Imaging Cloud files, final color output and camera hardware are unverified. 64 is the tested capacity, not a hardware limit. |
| Ordinary NEF | C11/C12 exported one 80,790,528-byte NEF. LibRaw decoded all 45,705,600 RAW samples with zero differences against actual producer RAM; packed bytes matched and three embedded JPEGs decoded. C12 used the public CLI recipe. | One post-trigger frame from the periodic stream model; single-frame sensor-model requests remained zero. This NEF is a padded packed14 copy, not compression. Not pre-release capture or sensor/ISP fidelity evidence. |
| Per-photo audit and F0 lossless14 (2026-10-14) | One original C3.11 single shot with candidate QEMU `d39e1fd5…` wrote a 35,111,661-byte NEF to the card (`d211e239…`). LibRaw decoded all 45,705,600 samples with zero differences against the 151 producer snapshot. The F0 strip is 34,339,053 bytes, which rules out the 80,017,920-byte packed fallback; exiftool independently checked the predictor metadata. Three embedded JPEGs decoded. | Opt-in only: `engine1540-lossless14-model` and `output-buffer-ownership-model` are off by default, and the run used `profiles/z8-lossless14-properties.json`. The encoder is an attributed functional F0 format taken from upstream; it does not execute Nikon compression microcode or implement HE/HE*. The audit grants `pixels_verified`, not `native_accepted`. JPEG job byte matching, multi-photo UI recovery, the complete native writer lifecycle, historical RAW and physical devices remain unverified. See [photo audit](docs/z8-photo-audit.md). |
| Native writer binding | C16 associated the native file object, ID/path, nine writes, final length, cleanup, return and result store with the exported NEF; its file ranges were covered by completed NVMe writes. | C16 used a private observer. Public observer integration has synthetic tests, without equivalent public runtime acceptance. Complete buffer lifetime, native allocation generation and physical-media durability remain unverified. |
| RAW pre-release research | HALF20 reached the native C120/PRE display, getter4 and state2/handler3 with explicit resources and the opt-in wide control model. | Zero D980/RAW output and no accepted historical frames/NEFs. The release interval stopped on a diagnostic cross-domain hit, without a firmware exception or unknown MMIO. HALF21 is preparation only. |
| Producer generation journal | Default-off metadata for actual D980→151 NV16/RAW→1540 RAM output: full SHA-256, virtual time, model write generations and parent lineage. Seven firmware-free QTests and eight helper tests were recorded as passing. | The 64-record journal is not a 64-frame pixel cache. Latest-source restrictions remain; CPU/other DMA writes, native ownership and historical retention are unverified. |

Receipts: [slot acceptance](docs/verification/slots-20261012.json) and [ordinary NEF / C12 / C16](docs/verification/raw-single-20261012.json). NVMe completion alone does not establish NEF acceptance or media durability; file export, decoding and RAM comparisons are separate checks.

RAW pre-release acceptance requires at least **two distinct retained pre-trigger frames and one post-trigger frame**, with pre-trigger DMA and enqueue before the earliest FULL request across every enabled route, and post-trigger DMA after the latest verified native FULL consumer on the same virtual clock. Complete queue, buffer-lifetime, file-set and full-pixel evidence is required. Increasing preview counts or exporting repeated frames is insufficient. See [runtime comparisons](docs/z8-raw-runtime.md) and the [multi-frame auditor](docs/z8-precapture-audit.md).

Open Gate video, AF enhancements, online Nikon Imaging Cloud integration, complete film simulation, photo Log, RED Log3G10/R3D NE and in-camera panorama remain unimplemented or unaccepted; see [TODO](docs/TODO.md). Native video N-Log is not a new project feature. Video recording emulation remains unverified on main.

## Public branches

The table above describes `main`. Topic branches hold unmerged experiments and checkpoints. Their results belong to those branches, are not part of main, and each keeps its own incomplete acceptance boundaries. Each branch's source regression result is listed in the [publication progress](docs/publication-progress-20261015.md).

| Branch | Purpose |
|---|---|
| [`main`](https://github.com/YCX81/z8-qemu/tree/main) | Reviewed base: Z8 bring-up, menu and live-view runs, C32/C64 slots, ordinary NEF capture, per-photo audit and F0, plus the retained Z9 reference tools |
| [`codex/z8-integration`](https://github.com/YCX81/z8-qemu/tree/codex/z8-integration) | Combined RAW, video and orientation integration experiment |
| [`codex/raw-precapture-shared146`](https://github.com/YCX81/z8-qemu/tree/codex/raw-precapture-shared146) | RAW history transport for pre-release capture, with the dual 1540/153c encoders |
| [`codex/z8-he-history-20261017`](https://github.com/YCX81/z8-qemu/tree/codex/z8-he-history-20261017) | Lifecycle tests for a third-party HE precapture hook, replay of a real HE sample, and the native model gaps found |
| [`codex/z8-r4-lifecycle`](https://github.com/YCX81/z8-qemu/tree/codex/z8-r4-lifecycle) | Extended-slot NP3 preflight, liveview labels and native card-input fallback experiments; copy-page expansion default off |
| [`codex/e7-neural-research`](https://github.com/YCX81/z8-qemu/tree/codex/e7-neural-research) | Authored E7 parsers, candidate CPU replay, quantization and research contracts; external E7-bird arithmetic kept separate |
| [`codex/z8-heif-1602`](https://github.com/YCX81/z8-qemu/tree/codex/z8-heif-1602) | HLG/HEIF still pipeline through the 1602 still encoder, plus bounded recovery probes |
| [`codex/z8-video-c1`](https://github.com/YCX81/z8-qemu/tree/codex/z8-video-c1) | C1 video transport, codec and MCU experiments |
| [`codex/z8-orientation`](https://github.com/YCX81/z8-qemu/tree/codex/z8-orientation) | Video orientation and video data path experiments |
| [`codex/z8-live-photo`](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo) | Host-only Live Photo frame retention model |
| [`codex/z8-live-photo-video`](https://github.com/YCX81/z8-qemu/tree/codex/z8-live-photo-video) | Live Photo video inputs and pair audit research |
| [`codex/z8-af-lockon`](https://github.com/YCX81/z8-qemu/tree/codex/z8-af-lockon) | AF resource tracing and occlusion response probes |
| [`archive/z8-raw-precapture-20261013`](https://github.com/YCX81/z8-qemu/tree/archive/z8-raw-precapture-20261013) | Archive: earlier RAW precapture transport experiment |
| [`archive/z8-internal-stream-20261012`](https://github.com/YCX81/z8-qemu/tree/archive/z8-internal-stream-20261012) | Archive: earlier internal stream notes, kept for provenance |
| [`archive/z8-capture-evidence-20261012`](https://github.com/YCX81/z8-qemu/tree/archive/z8-capture-evidence-20261012) | Archive: earlier capture evidence notes, kept for provenance |
| [`archive/z8-merge-rehearsal-20261014`](https://github.com/YCX81/z8-qemu/tree/archive/z8-merge-rehearsal-20261014) | Archive: unresolved merge rehearsal; deliberately not buildable |

## Firmware input

The Z8 launcher is `tools/z8_run.py`; its machine is `nikon-z8-bringup`. Legacy `tools/run.py`, `tools/boot.py` and `nikon-z9-lab` remain Z9 references.

| Input | Required identity |
|---|---|
| Original update container | `Z_8_0311.bin`, 99,051,117 bytes |
| Container SHA-256 | `d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96` |
| EG module | `eg2070_0311b0.bi`, body offset 581844; 81,807,711 bytes including CRC |
| MAIN | 81,807,709 bytes after removing the final two CRC bytes |
| MAIN SHA-256 | `2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853` |
| Load / entry | `0x40160000`, inherited mapping assumption used in bounded execution |

Supply a compatible local MAIN at `.local/firmware/z8-c311-main.bin` for the Windows wrappers. An update container cannot substitute for MAIN; other versions require separate bindings and validation. See [identity profile](profiles/z8-c311.json) and [firmware preparation](docs/firmware.md).

Original input is read-only. Slot-expansion and format-policy experiments use separately identified derived programs; their results do not describe the unmodified default launch. Firmware, keys, derived images, dumps, screenshots and runtime artifacts stay local. See the [publication boundary and known history residual](docs/publication.md).

## Derived firmware experiments

The slot generators publish reusable A64 patch recipes, not just emulator settings. They create a separately identified local MAIN; this is different from the original-program launch. Generated MAIN, owned-region images and patch receipts may contain material copied from the user's firmware. They are local experiment artifacts, **not camera update files, and must not be written to a camera**. No repacking or physical-device validation is provided.

The current tree also contains small instruction guards and test literals derived from the original program; the earlier entry-prefix removal did not remove all such material. See the [publication categories and remaining review](docs/publication.md), [technical source/reproduction review](docs/z8-public-review-20261013.md) and [experiment assumptions with counterfactuals](docs/z8-experiment-assumptions.md). Passing the inventory audit is not provenance or legal clearance.

## Build and test

Use Linux / WSL, Python 3.12+ and the dependencies in [build instructions](docs/build.md): GCC/Clang, Ninja, patch, pkg-config, GLib, Pixman, zlib, libfdt and libjpeg development headers. Capture work additionally needs exfatprogs and `requirements-capture.txt`; slot experiments use `requirements-c10.txt`.

From the repository directory in Linux / WSL:

```bash
python3 -m venv "$HOME/.local/share/nikon-z8-qemu/venv"
. "$HOME/.local/share/nikon-z8-qemu/venv/bin/activate"
python -m pip install -r requirements.txt
python -m pip install -r requirements-capture.txt
python tools/build.py --work-dir "$HOME/.local/share/nikon-z8-qemu/work" --jobs 2
python tools/make_cmos.py
sh tests/run.sh
python tests/check_z8_machine.py --qemu "$HOME/.local/share/nikon-z8-qemu/work/build/qemu-system-aarch64"
```

The builder pins QEMU 10.2.4 and verifies the archive SHA-256; use `--archive` for an existing archive. Prefer a Linux filesystem for builds and long runs. Synthetic tests and firmware-free QTests validate model boundaries, not original-firmware or physical-camera acceptance. Explicit model tests are documented alongside each feature.

## Local menu and live-view run

PowerShell wrappers currently select WSL **Ubuntu**, resolve that user's home automatically and use the environment above. From the repository root:

```powershell
.\Test-Z8.ps1
.\Test-Z8.ps1 -BootstrapFixture -BusWindowFixture -SerialFixture -NandFixture
.\Start-Z8.ps1
.\Start-Z8.ps1 -Headless -Seconds 360 -MenuTest -RequireLiveView
```

`Test-Z8.ps1` is bounded strict bring-up: fixtures default off and unknown hardware accesses stop execution. Optional bootstrap/bus/serial/NAND fixtures are explicit experimental assumptions, not proof of bare-model full boot.

`Start-Z8.ps1` displays the original QEMU framebuffer at `http://127.0.0.1:8788/` and accepts MENU / half-press through the MCU path. Allow time for initial live view. MENU opens the menu; half-press returns to live view. Stop with the page's stop button or Ctrl+C in the launch terminal. Runs use fresh output directories on the WSL Linux disk. The headless command requires three menu-return cycles and independent pixel checks; diagnostic exit code zero alone does not mean camera acceptance. Inspect `result.json`.

This wrapper does not enable slot expansion or provide complete RAW pre-release acceptance. For ordinary NEF use the separate [capture probe and public C12 recipe](docs/z8-capture-probe.md), including its input, card, menu and audit prerequisites.

## Research documentation

- [Feature TODO](docs/TODO.md), [implementation history](docs/z8-status.md), [engineering progress](docs/research-progress.md), [parameter provenance](docs/z8-parameters.md).
- [C10](docs/z8-c10.md), [C32/C64 slots](docs/z8-c32.md), [persistent media](docs/z8-persistent-media.md).
- [RAW runtime](docs/z8-raw-runtime.md), [explicit capture resources](docs/z8-capture-resources.md), [capture evidence](docs/z8-capture-evidence.md), [writer observer](docs/z8-writer-observer.md), [NVMe evidence](docs/z8-nvme-evidence.md).
- [Internal FIFO start](docs/z8-d980-internal.md), [F04F stop](docs/z8-primary-f04f-stop.md), [C0 stop](docs/z8-1543-c0-stop.md), [producer journal](docs/z8-producer-journal.md), [multi-frame audit](docs/z8-precapture-audit.md).
- [Per-photo audit, output ownership and F0](docs/z8-photo-audit.md), [topic-branch publication progress](docs/publication-progress-20261015.md).

REA/Ghidra uses explicitly marked analysis ELF containers made from local firmware slices, not original Nikon ELF files. `REA.ps1` calls an already-installed WSL backend; historical CLI use does not imply global Codex MCP registration. See the [implementation record](docs/z8-status.md).

Inherited CPU/GIC, memory windows, virtual timing and synthetic Bayer/ISP/JPEG paths are functional research models. Real CMOS, calibration, complete peripheral protocols, Nikon image processing and physical timing remain unverified. `BoundaryPeer`'s 33 is a diagnostic interval in milliseconds, not measured camera FPS.

## Referenced repositories and sources

Used by `main`:

- [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu): upstream fork base (`27498735…`), providing the shared model and the Z9 reference tools. The F0 lossless14 encoder `qemu/nikon-expeed-lossless.h` comes from this upstream and keeps its RdWing copyright and GPL notice.
- [QEMU](https://www.qemu.org/) 10.2.4: pinned and SHA-256-checked by the builder; upstream licensing retained.
- [LibRaw/LibRaw](https://github.com/LibRaw/LibRaw): the format reference for the F0 encoder (`nikon_load_raw`), and the independent NEF decoder used by the audits through rawpy.
- [WonderLantern/nikon-fw-key-tool](https://github.com/WonderLantern/nikon-fw-key-tool): decrypts and checks the local update container (pinned commit `33426e8…`).

Also referenced by topic branches:

- [WonderLantern/zr-qemu](https://github.com/WonderLantern/zr-qemu) `f9cc5d1`: the input-pair and video-processing models are adapted from it (video C1, orientation, integration).
- [WonderLantern/wonder-lantern-z30](https://github.com/WonderLantern/wonder-lantern-z30) `video-rot`: design reference for locking video orientation at record start (orientation, integration, video C1).
- [XinmiaoZhao/NikonFirmwarePatch](https://github.com/XinmiaoZhao/NikonFirmwarePatch) v0.1.0 toolkit and [Ivan-mato/nikon-z8-halfpress-raw-precapture](https://github.com/Ivan-mato/nikon-z8-halfpress-raw-precapture) `205fd05`: third-party precapture projects whose code and samples were tested or reviewed. [raw.pixls.us](https://raw.pixls.us/) record 6616 supplies the real Z8 HE sample (he-history).
- [WonderLantern/e7-bird-network](https://github.com/WonderLantern/e7-bird-network/tree/8ced43b19edb845baef39a80c45d40be0b49efba) `8ced43b`: separately supplied resource744 arithmetic and procedural reference fixtures (e7-neural-research).
- [videolan/x265](https://github.com/videolan/x265) for Main10 still encoding, [strukturag/libheif](https://github.com/strukturag/libheif) for container rules, and [exiftool/exiftool](https://github.com/exiftool/exiftool) for tag definitions (HEIF).
- [LimitPoint/LivePhoto](https://github.com/LimitPoint/LivePhoto), [minutechreview/livephoto-maker](https://github.com/minutechreview/livephoto-maker) and ExifTool: references for Live Photo pairing (Live Photo video).

Where a branch pins a commit or version, its documents record it. A reference link does not grant rights to that project's material or to Nikon firmware.

Upstream GPL-2.0-or-later licensing and attribution are retained. See [NOTICE](NOTICE.md) and [contribution guidelines](CONTRIBUTING.md). Contributions should identify camera/firmware, synthetic inputs, commands, structured results and uncovered scope.
