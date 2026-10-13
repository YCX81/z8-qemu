# Nikon Z8 firmware emulation research

[简体中文](README.zh-CN.md)

Documentation updated: **2026-10-13**. This workspace ports Nikon Z8 C3.11 to the research emulator from [WonderLantern/z9-qemu](https://github.com/WonderLantern/z9-qemu), starting from upstream commit `27498735e3ed202052dfc6306ded6b867af70a58`. Z8 firmware identities, MCU bindings, resources and acceptance baselines are defined separately from the retained Z9 reference tools.

**Assisted original-firmware menus, synthetic live view and an ordinary post-trigger NEF have recorded local QEMU acceptance. RAW pre-release capture remains incomplete.** This is an experimental emulator, not camera-installable firmware. It does not connect to or flash a physical camera.

The status below summarizes checked-in documentation and receipts through source commit `a7cab1c`. This documentation update did not rerun firmware, verify the current binary or test physical hardware. Historical results belong to their recorded inputs, sources and binaries. Detailed research notes are mostly in Chinese; see the [feature TODO](docs/TODO.md) and [implementation record](docs/z8-status.md).

## Current results and limits

| Area | Recorded evidence | Remaining boundary |
|---|---|---|
| Menus and synthetic live view | Three MENU → half-press return cycles; five scanout checks with an independent decoder. Historical run: 1,743 RAW frames, 2,547 LCD frames, 731 containing live view; zero unknown MMIO accesses. | External MCU peer and explicit virtual resources required. Menu hashes are regression baselines, without real-camera image comparison. Bare-model full boot is unverified. |
| C32/C64 Picture Control slots | Separate four-stage acceptance for 32 and 64 photo slots plus 32 and 64 video slots: import, native selection, guest NAND save and new-process recovery. | Explicit experiment using a derived MAIN and active firmware calls. Real Nikon Imaging Cloud files, final color output and camera hardware are unverified. 64 is the tested capacity, not a hardware limit. |
| Ordinary NEF | C11/C12 exported one 80,790,528-byte NEF. LibRaw decoded all 45,705,600 RAW samples with zero differences against actual producer RAM; packed bytes matched and three embedded JPEGs decoded. C12 used the public CLI recipe. | One post-trigger frame from the periodic stream model; single-frame sensor-model requests remained zero. Not pre-release capture or sensor/ISP fidelity evidence. |
| Native writer binding | C16 associated the native file object, ID/path, nine writes, final length, cleanup, return and result store with the exported NEF; its file ranges were covered by completed NVMe writes. | C16 used a private observer. Public observer integration has synthetic tests, without equivalent public runtime acceptance. Complete buffer lifetime, native allocation generation and physical-media durability remain unverified. |
| RAW pre-release research | Internal-start, F04F stop and C0 stop diagnostics are separate default-off options. HALF15–17 observed preview supply, FSM actions and task consumption; HALF19 resolved the specified slot8 selector underflow. | HALF19 stopped at strict admission of a new 8280×4688 internal FIFO configuration, before final half-press/C120 acceptance. No accepted historical RAW frames or historical NEFs. Other resource gaps, including mode2→slot3, remain. |
| Producer generation journal | Default-off metadata for actual D980→151 NV16/RAW→1540 RAM output: full SHA-256, virtual time, model write generations and parent lineage. Seven firmware-free QTests and eight helper tests were recorded as passing. | The 64-record journal is not a 64-frame pixel cache. Latest-source restrictions remain; CPU/other DMA writes, native ownership and historical retention are unverified. |

Receipts: [slot acceptance](docs/verification/slots-20261012.json) and [ordinary NEF / C12 / C16](docs/verification/raw-single-20261012.json). NVMe completion alone does not establish NEF acceptance or media durability; file export, decoding and RAM comparisons are separate checks.

RAW pre-release acceptance requires at least **two distinct retained pre-trigger frames and one post-trigger frame**, ordered against all verified native FULL consumers on the same virtual clock, with complete queue, buffer-lifetime, file-set and full-pixel evidence. Increasing preview counts or exporting repeated frames is insufficient. See [runtime comparisons](docs/z8-raw-runtime.md) and the [multi-frame auditor](docs/z8-precapture-audit.md).

Open Gate video, AF enhancements, online Nikon Imaging Cloud integration, complete film simulation, photo Log, RED Log3G10/R3D NE and in-camera panorama remain unimplemented or unaccepted; see [TODO](docs/TODO.md). Native video N-Log is not a new project feature. Video recording emulation remains unverified.

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

## Build and test

Use Linux / WSL, Python 3.12+ and the dependencies in [build instructions](docs/build.md): GCC/Clang, Ninja, patch, pkg-config, GLib, Pixman, zlib and libfdt development headers. Capture work additionally needs libjpeg development headers, exfatprogs and `requirements-capture.txt`; slot experiments use `requirements-c10.txt`.

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

REA/Ghidra uses explicitly marked analysis ELF containers made from local firmware slices, not original Nikon ELF files. `REA.ps1` calls an already-installed WSL backend; historical CLI use does not imply global Codex MCP registration. See the [implementation record](docs/z8-status.md).

Inherited CPU/GIC, memory windows, virtual timing and synthetic Bayer/ISP/JPEG paths are functional research models. Real CMOS, calibration, complete peripheral protocols, Nikon image processing and physical timing remain unverified. `BoundaryPeer`'s 33 is a diagnostic interval in milliseconds, not measured camera FPS.

Upstream GPL-2.0-or-later licensing and attribution are retained. See [NOTICE](NOTICE.md) and [contribution guidelines](CONTRIBUTING.md). Contributions should identify camera/firmware, synthetic inputs, commands, structured results and uncovered scope.
