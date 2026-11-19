# Nikon Zf C3.01 FilmGrain & Flexible Color Research

Complete end-to-end reverse-engineering & bit-exact reference implementation for Nikon Zf C3.01 (`EXPEED 7`) Film Grain (`FilmGrain.cpp` on CEVA-XM6 DSP + ARM64 `DspFrameworkWrapper::FilmGrain` dispatch + real `83x83` ROM base grain maps) and Flexible Color (`.NP3` 0310 / MIPU pipeline).

## Status: 100% End-to-End Closed

- **CEVA-XM6 DSP `FilmGrain()` (`0x418761e0`, `17,084` B, `1,377` VLIW packets, 0 undecoded)**:
  - `tagDSPImageParam` (`0x38` B) & `TFilmGrainInputParam` (`0x80` B: `+0x4c` bypass, `+0x50` Q4 `map_blend_weight`, `+0x51` `blur_kernel_size` `{0,3,5,7,9}` via `0x2a9` mask, `+0x52..+0x54` `M/P/S`, `+0x55/+0x56` `tone_knee_lo/hi`, `+0x58` `prng_seed`, `+0x60/+0x70` `mapA/mapB`, `+0x68/+0x78` `map_dim`).
  - Online IDM `0x2d720` staggered tiling LUT `idx_lut[k] = ((k // P) * S + (k % P)) % M` (`.LBB0_12..16`, `0x41876ab8..0x41876ffc`, 16-step radix-4 `SC0.divstep`).
  - `ApplyMaptoImageLine` (`0x41879f20`): per-32px Marsaglia `xorshift32` `+-1` polarity flip + Q6 `tone_lut[Y]` (`>> 6`) + `[0, 255]` `vst{satu}` on Luma `Y` only (`plane_addr[0]`).
  - Two-stage serial `BlurLineRing` (`0x38f40`/`0x38f70` + flush `0x38ee0`/`0x38f10`): `UpdateBlurLine_1` (1x1), `UpdateBlurLine_3` (`.LCPI2_0` 3x3 `/16`), `UpdateBlurLine_5` (`.LCPI3_0` rank-3 octagonal 5x5 `/256`, corners=0), floor truncation (`round_bias=0`) & `BORDER_REFLECT_101`.
- **ARM64 `FUN_40e44bec` (`0x40e44bec..0x40e44e7b`, `656` B) & 6 ISP Callers (`v3` + `v4` verified)**:
  - **RPC slot 39 vs 40**: 5 still/RAW/playback/resized pipelines (`FUN_40e6f440`, `40e71620`, `40e71cc0`, `40e7276c`, `40e78eec`) use `slot 39`; LiveView/video pipeline (`FUN_40e5d5b4`) uses `slot 40`.
  - **18 B EXPEED 7 DSP scalar LUTs** (`RAM 0xa02aec1f8` / `ROM 0x44bbb1f8` / file `0x4a5b1f8`):
    - Full-res (`param_1[0x11] == 0`): `w = [2, 3, 7, 11, 16, 16]` (`0xa02aec201`), `blur_kernel_size = [3, 5, 9]` (`0xa02aec207`).
    - Reduced-res (`param_1[0x11] != 0`): `w = [2, 2, 3, 4, 6, 9]` (`0xa02aec1f8`), `blur_kernel_size = [3, 3, 3]` (`0xa02aec1fe`, always Map 0).
  - **Hardcoded scalars**: `format = 0x10`, `bypass = 0`, `M = 83`, `P = 5`, `S = 23` (`415x415` aperiodic prime tiling), `tone_knee_lo = 32`, `tone_knee_hi = 223`, `prng_seed = low32(umulh(CNTPCT_EL0, 0xAAAAAAAAAAAAAAAA) >> 4)` (`CNTPCT_EL0 / 24` µs), `mapB` always zero-filled (`memset 0, 0x35d2`), 64B-aligned runtime `mapA`/`mapB` spacing (`0x35e0`), `dc CIVAC` flush (`FUN_40d30e58`) prior to `FUN_40c91ba4` submit.
  - **Dual `83x83` `int16_t` ROM base grain maps** (`0x35d2 = 13,778` B each, contiguous in `.init_rom`):
    - **Map 0 (`FUN_4102341c`)**: `RAM 0xa02b7237c` / `ROM 0x44c4137c`, SHA-256 `d36cfcbfa644599818260d74eeb02f4ff2775bc94b8c529be7c9e66add8e161e` (`std = 63.7841`, i.i.d. $\mathcal{N}(0, 64^2)$, used for full-res Intensity 1..5 & all reduced-res).
    - **Map 1 (`FUN_41023430`)**: `RAM 0xa02b7594e` / `ROM 0x44c4494e`, SHA-256 `0d50aedb83a3b4488c0fa9dabdf28b228bea0fa072122104120d0907eb1cfd41` (`std = 95.9047`, independent i.i.d. $\mathcal{N}(0, 96^2)$, used for full-res Intensity 6).
- **Local Reference Engine (`nikon_zf_film_engine.py`)**: Automatically loads & SHA-256-verifies both real `83x83` ROM maps from `zf_filmgrain_maps_83x83_v4.json`; `--self-test` passes **`29/29`** checks.

## Deterministic Bit-Exact Golden Vectors

Input: `32x32` flat `uint8` luma `Y = 128`, `mapB = 0`, `M = 83, P = 5, S = 23, knee = [32, 223], prng_seed = 0x12345678`, real ROM `83x83` maps:

| Preset | Params | Output SHA-256 (first 16 hex) | Row 0 `[0..7]` |
| :--- | :--- | :---: | :--- |
| Size 1, Intensity 3 | `w=7, blur=3, Map 0` | `57c0b4eca3c0a8ea` | `[121, 111, 105, 118, 134, 141, 130, 116]` |
| Size 2, Intensity 3 | `w=7, blur=5, Map 0` | `814f506d78f16c22` | `[117, 112, 111, 121, 130, 135, 131, 119]` |
| Size 3, Intensity 6 | `w=16, blur=9, Map 1` | `312a039268596153` | `[77, 89, 116, 143, 157, 149, 122, 96]` |

## Files

- `nikon_zf_film_engine.py` — Portable Flexible Color (`.NP3` 0310) + 100% bit-exact Zf C3.01 FilmGrain engine & `--self-test` (`29/29` PASS)
- `expeed7_flexible_color_tables.json` — Extracted EXPEED 7 Flexible Color, CEVA-XM6 kernel/switch & ARM64 dispatch metadata
- `zf_filmgrain_maps_83x83_v4.json` — Exact `83x83` `int16_t` ROM base grain maps (`map0_std` & `map1_hi`, zlib+base64) + 18 B DSP LUT receipt
- `zf_filmgrain_maps_83x83_v4_evidence.json` — Independent ARM64 Ghidra instruction-level verification receipt
- `zf_filmgrain_arm64_maps_v3.json` — v3 ARM64 `FUN_40e44bec` call-tree & Ghidra decompilation receipt
- `zf_filmgrain_details_v2.json` — v2 CEVA-XM6 DSP & ARM64 RPC bridge receipt
- `extract_expeed7_tables.py` — EXPEED 7 Flexible Color table extractor
- `probe_zf_filmgrain_details.py` — v2 read-only ARM64/XM6 extractor
- `probe_zf_filmgrain_arm64_maps_v3.py` (+ `.py.txt`) — v3 read-only ARM64 wrapper & call-tree extractor
- `extract_zf_filmgrain_maps_v4.py` (+ `.py.txt`) — v4 minimal read-only `83x83` base map extractor
- `docs/zf_c301_filmgrain_complete_analysis.md` — Full ARM64 + CEVA-XM6 instruction-level analysis report

## Quick Verification

```bash
python -B research/zf-filmgrain/nikon_zf_film_engine.py --self-test --out-dir .local/zf-filmgrain-selftest
```

*Firmware binaries, raw images, ELFs, and third-party SDKs are excluded.*
