# v36 laboratory artifact identities

2026-11-11 inventory of the existing Ubuntu-26.04 laboratory files. This directory
preserves fingerprints and build/packaging observations, not firmware programs.

The matching source and portable reconstruction tools are in
[fenestra-z8, z8-c311](https://github.com/YCX81/fenestra-z8/tree/z8-c311/research/z8-live/build-tools/livediag/feat-release).
Start with that directory's `REPRODUCE-v36.md`. This is a cross-repository source
checkpoint; the hash verifier does not compile or operate a camera.

## Moving to another machine

The distribution name and home paths below describe the original laboratory.
They do not describe every machine and are not installed by cloning either
repository. An existing WSL distribution named Ubuntu is sufficient.

Linking and MAIN reconstruction require separately transferred private inputs.
The local migration bundle is named `v36-private-inputs.zip`; it is not a public
GitHub attachment. It contains the four MAIN identities, the historical v36
container, matching materialized manifests and a dependency-free input verifier.
After transferring and extracting it, run `python3 verify_inputs.py` and set
`INPUTS` to its absolute directory. Use the explicit input arguments in the
companion reproduction guide. Source-only checks work without this bundle;
linking and the full chain do not.

## What was found

Under `$HOME/.local/share/nikon-z8-qemu/live-audio`:

- `src-fix36`, `pb-fix36` and `tools-v36/compiler/clang` exist. The adapter applies
  `-Oz` to only the two playback UI translation units.
- `pbin/relive_pb.py`, `w5in/chain.sh`, `w5in/usb_default_on.py` and
  `w5in/gui_pool_fix.py` exist. The laboratory path used by the shell chain exists.
- `feat-rec/bg34.main` has SHA-256
  `c7c6dbeea8a94064a10d47a7b9022ab26e4aa6b12ba5fb63b1ad85abac4fe4d4`.
  Its adjacent `bg34.changed-ranges.json` identifies the same output.
- `$HOME/nfp-rc2/newbase/newbase.main` is the merged input used by the v36 build.
  Its hash matches the build receipt's `input_merged_sha256`.
- `build-v36` and `chain-v36` already contain the Live, playback, USB and pool
  outputs. The pool step does have a cumulative manifest in this laboratory copy.

The base manifest's top-level `output_sha256` is stale, as are its replacement
bytes at two hook sites. The public hash recipe preserves the historical
metadata and separately identifies the actual input image/ranges. The Fenestra
materializer checks the supplied image and rebuilds its private manifest from
local files. It does not silently treat the stale header as a matching hash.

## Final package association

`verification.json` is the existing packaging record. Its package SHA-256 matches
the file named `Z_8_0406.bin.v36` in the Windows card backup. Its `main` hash also
matches the decoded MAIN, excluding the EG component's trailing CRC16, and the
local `$HOME/fxfix/out/bg40.main`.

The recorded pipeline is:

```text
NFP v0.4.0-rc.2: framing + video-quality + photo-tools
  -> Live v36 -> playback v36 -> USB default-on -> GUI pool enlargement
  -> bootguard-v2 -> MAIN fecbe07e... -> update container a6e1d92e...
```

`log-colour` was omitted because its code cave overlaps the C64 slot layer.
v36 uses the v32 behavior plus the cached-runtime Wave5 close lock and Live
icon v3. It does not use the v9b recorder layout.

The historical record reports QEMU run `n4v36-a1` as PASS. It explicitly sets
`physical_camera_verified` to false and limits the hardware REC observation to
the v9b diagnostic build. A matching hash establishes file identity, not device
qualification. The new reproduction run is reported separately in
`reproduction-check.json`; it must not be confused with the historical run.

## Verify files without uploading them

```sh
python3 experiments/20261111/v36/verify_local.py \
  --artifact reference-main="$HOME/.local/share/nikon-z8-qemu/live-audio/feat-rec/bg34.main" \
  --artifact merged-base="$HOME/nfp-rc2/newbase/newbase.main" \
  --artifact pool-main="$HOME/.local/share/nikon-z8-qemu/live-audio/chain-v36/pool/pool.main" \
  --artifact bootguard-main="$HOME/fxfix/out/bg40.main" \
  --artifact firmware=/path/to/Z_8_0406.bin.v36
```

The byte-bearing local manifests, complete firmware/MAIN files, memory captures,
media, virtual environments and raw emulator output remain local under the
repository's existing publication boundary. The adjacent logs contain only
build-stage sizes, paths and hashes; no firmware byte arrays.
