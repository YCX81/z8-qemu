# Building

Use Linux with Python 3.12+, GCC/Clang, Ninja, patch, pkg-config, GLib, Pixman, zlib
and libfdt development headers. Install the Python dependencies from
`requirements.txt` in a virtual environment. See the README for Ubuntu commands.

`python tools/build.py --work-dir /your/linux/path --jobs 2` downloads only
the upstream QEMU 10.2.4 source archive, validates its SHA-256, extracts into
that work directory, installs the source overlay and builds AArch64 TCG.
It writes `.local/build-manifest.json` with the binary and source hashes.

For an existing upstream archive use `--archive /path/qemu-10.2.4.tar.xz`.
The required archive SHA-256 is
`821b545b92f165e57dddccac5077d76d4d436a226595b8813ad59306bbfd0746`.
Every cached archive is checked again before extraction. Automatic downloads use
a 30-second socket timeout, a 120-second deadline checked between reads, and
at most three attempts with bounded backoff. A temporary file replaces the
cache only after its complete SHA-256 matches. Failed attempts remove their
temporary file; an invalid old cache is never accepted. An explicit `--archive`
is checked without downloading over or modifying that file. The deadline is
not a hard process timer: an active read can run until its socket timeout.
No Nikon file is downloaded. Configuration may need network access for
QEMU's Python build dependencies; an archive alone does not guarantee a fully
offline first build. Subsequent builds reuse the verified source/build tree.

Use an ext4 Linux path for builds and long runs. Windows-mounted paths can be
slower for the many small files produced by diagnostic peers. On Windows:

```powershell
wsl --list --quiet
wsl -d <your-distribution-name> --exec bash
```

Replace the placeholder with an installed distribution that has the dependencies
above. Enter your clone and run the Linux commands. Prefer a Linux filesystem
path for `--work-dir` even when the clone is on a Windows-mounted drive.
The tools neither create nor select a default WSL distribution and impose no
personal distribution-name restrictions. Linux installations without WSL work
normally; native Windows execution remains unsupported.

CI caches only the fixed upstream archive and ccache compiler objects, never
the QEMU build directory, completed emulator binaries, firmware or runtime
artifacts. The archive must pass the same SHA-256 check after cache restore.
Compiler-cache restoration and saving are restricted to pushes to `main`;
pull-request builds do not read or write that cache. QEMU 10.2.4's configure
accepts the `CC`/`CXX` environment and explicitly refreshes configure-test
results through `CCACHE_RECACHE`; CI selects `ccache gcc`/`ccache g++` and still
runs configure, Ninja and the firmware-free tests on each build. Cache contents
do not substitute for those checks. CI installs both base and C10/C32 synthetic
test dependencies; dependency downloads use finite retries and timeouts.

Start with two build jobs if host memory is tight. Stop only the emulator
process you own before replacing its binary. Do not kill unrelated workloads.
The machine reserves several inferred guest RAM windows; resident memory and
host commit use are different. Leave several GB available for QEMU and Python.

`qemu/prepare_source.py` requires the verified extraction marker and the exact
upstream version. It adds this board and QEMU's generic ROM loader, and applies
a narrow TCG GDB fix for global SMP breakpoints: removal visits every vCPU,
ignores `ENOENT` on an individual vCPU, and succeeds only if at least one copy
was removed. A fully absent breakpoint remains an error. This keeps the MCU
observer's breakpoints consistent across the four emulated CPUs. It does not
install a kernel/guest OS image. The separate optional
[service-core event patch](service-events.md) adds limited AArch64 WFE/SEV
behavior, enabled only by the explicit board option. Its source hash is
included in the build manifest. Repeated preparation verifies the applied
patch; an unexpected or partially patched upstream source is rejected.
