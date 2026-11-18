# Nikon Zf C3.01 FilmGrain & Flexible Color Research

Isolated research checkpoint for Nikon Zf C3.01 (`EXPEED 7`) Film Grain (`FilmGrain.cpp` on CEVA-XM6 DSP + ARM64 RPC bridge) and Flexible Color table extraction.

## Current Progress

- **CEVA-XM6 DSP `FilmGrain()` (`0x418761e0`, `17,084` B)**: 100% instruction-level resolved (`1,377` VLIW packets, 0 undecoded):
  - `tagDSPImageParam` (`0x38` B) & `TFilmGrainInputParam` (`+0x4c` bypass, `+0x50` Q4 `map_blend_weight`, `+0x51` `blur_kernel_size` `{0,3,5,7,9}` via `0x2a9` mask, `+0x52..+0x54` `M/P/S`, `+0x55/+0x56` `tone_knee_lo/hi`, `+0x58` `prng_seed`, `+0x60/+0x70` `mapA/mapB`)
  - `ApplyMaptoImageLine` (`xorshift32` per-32px sign flip + Q6 `tone_lut[Y]` + `[0, 255]` saturation) -> two-stage `BlurLineRing` (`0x38f40`/`0x38f70` + flush `0x38ee0`/`0x38f10`, `.LCPI2_0` 3x3 `/16` and `.LCPI3_0` 5x5 `/256` with `BORDER_REFLECT_101`).
- **Local Reference Engine (`nikon_zf_film_engine.py`)**: Updated with `nikon_zf_filmgrain_dsp_bitexact`, `BlurLineRing`, and EXPEED 7 Flexible Color tables; `--self-test` passes `28/28` checks.
- **Next Step (ARM64 Parameter Tables & Base Noise Maps)**: Ready-to-run read-only `v3` extractor (`probe_zf_filmgrain_arm64_maps_v3.py`) targets Zf ARM64 wrapper `0x40e44bec..0x40e44e98` (`BL 0x40c91ba4` @ `0x40e44e4c`), ISP parameter builders, and `int16_t[M][M]` `mapA`/`mapB` textures.

## Files

- `nikon_zf_film_engine.py` — Reference Flexible Color + bit-exact FilmGrain DSP engine & `--self-test`
- `expeed7_flexible_color_tables.json` — Extracted Flexible Color & FilmGrain tables
- `extract_expeed7_tables.py` — Firmware table extractor
- `probe_zf_filmgrain_details.py` — v2 read-only ARM64/XM6 extractor
- `probe_zf_filmgrain_arm64_maps_v3.py` (+ `.py.txt`) — v3 read-only ARM64 wrapper, parameter-table & `mapA`/`mapB` extractor
- `zf_filmgrain_details_v2.json` — v2 extraction receipt from Zf C3.01 EG
- `docs/zf_c301_filmgrain_complete_analysis.md` — Full instruction-level analysis report

## Quick Verification

```bash
python -B research/zf-filmgrain/nikon_zf_film_engine.py --self-test
```

*Firmware binaries, raw images, ELFs, and third-party SDKs are excluded.*
