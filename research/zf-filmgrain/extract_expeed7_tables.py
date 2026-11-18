#!/usr/bin/env python3
"""Read-only extractor for Nikon EXPEED 7 Flexible Color (.NP3 0310) ROM LUTs & Zf Film Grain table.

Extracts hardware MIPU tone-curve, Color Grading, and Color Blender ROM tables from
local Z8 C3.11 main firmware (z8-c311-main.bin, base VA 0x40160000) together with
the verified Zf C3.01 18x3 Film Grain preset table (RAM 0xa02acdd2b, module offset 0x4a3cd2b).
"""

import argparse
import hashlib
import json
import os
import struct
from typing import Any, Dict, List

DEFAULT_Z8_FW = r"C:\Users\ycx\Desktop\Nikon\z8-qemu\.local\firmware\z8-c311-main.bin"
DEFAULT_OUT = r"C:\Users\ycx\Desktop\Nikon\.local-zf-film-analysis\expeed7_flexible_color_tables.json"
Z8_BASE_VA = 0x40160000

ZF_GRAIN_HEX = (
    "0f1d501a1d50371d50521d50641d59641d640c2050122050"
    "2a20504120505f20506420550822500c22501c22502d2250"
    "412250522250"
)

# Exact 32-sector cyclic permutation around atan2(Cr, Cb) in 11.25-deg steps (0..31 * 11.25 deg)
MIPU_RING_TO_SLICE_32: List[int] = [
    8, 25, 26, 27, 28, 29, 30, 31,
    24, 23, 22, 21, 20, 19, 18, 17,
    0, 1, 2, 3, 4, 5, 6, 7,
    16, 15, 14, 13, 12, 11, 10, 9,
]


def _slice_meta(fw: bytes, va: int, byte_len: int) -> Dict[str, Any]:
    off = va - Z8_BASE_VA
    raw = fw[off : off + byte_len]
    if len(raw) != byte_len:
        raise ValueError(f"Out-of-range read at VA 0x{va:08x} (len={byte_len})")
    return {
        "va": f"0x{va:08x}",
        "file_offset": f"0x{off:07x}",
        "byte_length": byte_len,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw": raw,
    }


def extract_1d(fw: bytes, va: int, count: int, fmt_char: str, desc: str) -> Dict[str, Any]:
    elem_size = struct.calcsize(f"<{fmt_char}")
    meta = _slice_meta(fw, va, count * elem_size)
    raw = meta.pop("raw")
    vals = list(struct.unpack(f"<{count}{fmt_char}", raw))
    meta.update({"shape": [count], "dtype": fmt_char, "description": desc, "values": vals})
    return meta


def extract_2d(
    fw: bytes, va: int, rows: int, cols: int, fmt_char: str, desc: str, row_labels: List[str] | None = None
) -> Dict[str, Any]:
    elem_size = struct.calcsize(f"<{fmt_char}")
    meta = _slice_meta(fw, va, rows * cols * elem_size)
    raw = meta.pop("raw")
    flat = struct.unpack(f"<{rows * cols}{fmt_char}", raw)
    mat = [list(flat[r * cols : (r + 1) * cols]) for r in range(rows)]
    meta.update({"shape": [rows, cols], "dtype": fmt_char, "description": desc})
    if row_labels is not None:
        meta["row_labels"] = row_labels
    meta["values"] = mat
    return meta


def build_tables(z8_fw_path: str) -> Dict[str, Any]:
    with open(z8_fw_path, "rb") as f:
        fw = f.read()

    fw_sha256 = hashlib.sha256(fw).hexdigest()
    channels = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "magenta"]
    neg_pos_labels = [f"{c}_neg" for c in channels] + [f"{c}_pos" for c in channels]
    bright_labels = (
        [f"{c}_neg_cb" for c in channels]
        + [f"{c}_pos_cb" for c in channels]
        + [f"{c}_neg_cr" for c in channels]
        + [f"{c}_pos_cr" for c in channels]
    )

    grain_bytes = bytes.fromhex(ZF_GRAIN_HEX)
    grain_rows = []
    size_names = {1: "small", 2: "medium", 3: "large"}
    for idx in range(18):
        size_level = idx // 6 + 1
        intensity_level = idx % 6 + 1
        amt, sz, freq = grain_bytes[3 * idx : 3 * idx + 3]
        grain_rows.append(
            {
                "index": idx,
                "size_level": size_level,
                "size_name": size_names[size_level],
                "intensity_level": intensity_level,
                "video_allowed": intensity_level <= 3,
                "crd_grain_amount": amt,
                "crd_grain_size": sz,
                "crd_grain_frequency": freq,
            }
        )

    return {
        "schema": "nikon-expeed7-flexible-color-and-zf-grain-v1",
        "sources": {
            "expeed7_mipu_firmware": {
                "file": os.path.basename(z8_fw_path),
                "size_bytes": len(fw),
                "load_base_va": f"0x{Z8_BASE_VA:08x}",
                "sha256": fw_sha256,
                "disassembly_functions": {
                    "np3_loader_magic_check": "0x410abfa0",
                    "active_pc_photo_ram": "0xa047027d0",
                    "active_pc_movie_ram": "0xa04702928",
                    "crd_xmp_writer": "0x411115c4",
                    "base_tone_curve_selector": "0x4111a540",
                    "color_blender_8to32_mipu": "0x4111dd28",
                    "color_grading_65pt_mipu": "0x4111eff4",
                    "tone_curve_257pt_mipu": "0x411205b0",
                    "matrix_3x3_solver": "0x4112cf8c",
                },
            },
            "zf_c301_film_grain_handover": {
                "target_eg_module": "zf-full-eg-analysis-20261025.bin",
                "target_eg_size_bytes": 79144343,
                "target_eg_sha256": "0b53631396314e17e5b5a46a6758a35ae521c1e8bddc2fb59b61bcf72e4889f8",
                "preset_table_ram_va": "0xa02acdd2b",
                "preset_table_module_offset": "0x04a3cd2b",
                "preset_table_sha256": hashlib.sha256(grain_bytes).hexdigest(),
                "xmp_writer_va": "0x410d9d8c",
                "ptp_film_grain_sites": [
                    "0x408434c8",
                    "0x408436f8",
                    "0x40843920",
                    "0x40843b80",
                    "0x40843db0",
                ],
                "ceva_xm6_dsp_verified": {
                    "locations_receipt_sha256": "2832b4b05117422a85fc589ed152c47c1ce8fbe0d72aa624a2216d61c52eb036",
                    "embedded_elf_offsets": ["0x15210b0", "0x1600b20", "0x1670070"],
                    "elf0_exported_size_bytes": 916072,
                    "target_elf_index": 0,
                    "rpc_function_table_slots": [39, 40],
                    "z8_c311_same_slots_state": "_ZL11nopFunctionPvS_ (reserved empty slots 0x27/0x28)",
                    "functions": {
                        "FilmGrain": {
                            "mangled": "_Z9FilmGrainP20TFilmGrainInputParamP21TFilmGrainOutputParam",
                            "dsp_va": "0x418761e0",
                            "module_offset": "0x1555e90",
                            "arm64_rodata_va": "0x416b5e90",
                            "size_bytes": 5928,
                            "basic_blocks_min": 63,
                            "body_sha256": "97606fa9dd414c051047f662c64506f329ee3778168295f063a256d22041d8be",
                        },
                        "UpdateBlurLine_1": {
                            "mangled": "_ZL16UpdateBlurLine_1PhiiP12BlurLineRingPb",
                            "dsp_va": "0x41877920",
                            "module_offset": "0x1556c50",
                            "arm64_rodata_va": "0x416b6c50",
                            "size_bytes": 14,
                            "body_sha256": "f6e555f2a587132d803b3936c92fa38cd2287ab0eb6c5f440d58333540e0e079",
                            "note": "14-byte single-BB passthrough stub -> Size 1 (Small) applies zero spatial blur",
                        },
                        "UpdateBlurLine_3": {
                            "mangled": "_ZL16UpdateBlurLine_3PhiiP12BlurLineRingPb",
                            "dsp_va": "0x41877940",
                            "module_offset": "0x1556c70",
                            "arm64_rodata_va": "0x416b6c70",
                            "size_bytes": 2218,
                            "const_pool_dsp_va": "0x00038e80",
                            "const_pool_module_offset": "0x152c150",
                            "const_pool_bytes": 32,
                            "body_sha256": "258799542d54277cbdcb68d7aed05e5519262624b51c86499efa6896550c1c63",
                            "note": "SIMD 3-line BlurLineRing spatial filter -> Size 2 (Medium)",
                        },
                        "UpdateBlurLine_5": {
                            "mangled": "_ZL16UpdateBlurLine_5PhiiP12BlurLineRingPb",
                            "dsp_va": "0x41878200",
                            "module_offset": "0x1557530",
                            "arm64_rodata_va": "0x416b7530",
                            "size_bytes": 7434,
                            "const_pool_dsp_va": "0x00038ea0",
                            "const_pool_module_offset": "0x152c170",
                            "const_pool_bytes": 32,
                            "body_sha256": "e1cd9681dc59d4e42e5f8363d9446ee35a9f617851151193ee434e2deef5b945",
                            "note": "SIMD 5-line BlurLineRing spatial filter -> Size 3 (Large)",
                        },
                        "ApplyMaptoImageLine": {
                            "mangled": "_ZL19ApplyMaptoImageLinePhPKhiiPKsi",
                            "dsp_va": "0x41879f20",
                            "module_offset": "0x1559250",
                            "arm64_rodata_va": "0x416b9250",
                            "size_bytes": 1490,
                            "const_pool_dsp_va": "0x00038ec0",
                            "const_pool_module_offset": "0x152c190",
                            "const_pool_bytes": 32,
                            "body_sha256": "ae96839a41cb9e0612d5ec2dce6ce1547faa1dac385d6adc873833481938e643",
                        },
                    },
                    "switch_jump_tables": {
                        "dsp_vas": ["0x00038ee0", "0x00038f10", "0x00038f40", "0x00038f70"],
                        "module_offsets": ["0x152c1b0", "0x152c1e0", "0x152c210", "0x152c240"],
                        "table_bytes_each": 40,
                        "entries_each": 10,
                    },
                },
            },
        },
        "mipu_geometry": {
            "ring_32_step_deg": 11.25,
            "ring_to_mipu_slice_index": MIPU_RING_TO_SLICE_32,
            "chroma_axes": ["Cb_bt601", "Cr_bt601"],
            "note": (
                "theta_m = m * 11.25 deg = atan2(Cr, Cb) mod 360 deg maps ring index m in 0..31 "
                "to MIPU hardware sector index ring_to_mipu_slice_index[m]."
            ),
        },
        "tables": {
            "base_curve_standard_229": extract_1d(
                fw, 0x441A0AB2, 229, "H", "Standard Picture Control 229-pt 15-bit scene-to-display base curve"
            ),
            "base_curve_flexible_color_229": extract_1d(
                fw, 0x441A0E46, 229, "H", "Flexible Color (0310) 229-pt 15-bit base tone curve (max=30734)"
            ),
            "tone_curve_identity_257_12bit": extract_1d(
                fw, 0x441EBD92, 257, "H", "Identity 257-pt 12-bit tone curve (0..4095)"
            ),
            "highlights_basis_4x257_s16": extract_2d(
                fw,
                0x441EBF94,
                4,
                257,
                "h",
                "Highlights 257-pt basis deltas (divisor 120): rows [std_neg, std_pos, flex_neg, flex_pos]",
                ["std_neg", "std_pos", "flex_neg", "flex_pos"],
            ),
            "shadows_basis_4x257_s16": extract_2d(
                fw,
                0x441EC79C,
                4,
                257,
                "h",
                "Shadows 257-pt basis deltas (divisor 120): rows [std_neg, std_pos, flex_neg, flex_pos]",
                ["std_neg", "std_pos", "flex_neg", "flex_pos"],
            ),
            "color_grading_breakpoints_4_u16": extract_1d(
                fw, 0x441A56A0, 4, "H", "Color Grading luma zone breakpoints [220, 352, 528, 672] on 0..1024"
            ),
            "color_grading_luma_envelope_65_s16": extract_1d(
                fw, 0x441A561E, 65, "h", "Color Grading 65-pt Q10 luma chroma-protection envelope"
            ),
            "color_grading_hue_rgb_360x3_s16": extract_2d(
                fw, 0x441A4DAE, 360, 3, "h", "Color Grading 360-deg Hue (dR, dG, dB) vector basis"
            ),
            "color_blender_hue_basis_16x32_s16": extract_2d(
                fw,
                0x441B55B2,
                16,
                32,
                "h",
                "Color Blender 8-channel -> 32-sector Hue rotation basis (8192 = 360 deg)",
                neg_pos_labels,
            ),
            "color_blender_chroma_basis_16x32_s16": extract_2d(
                fw,
                0x441B59B2,
                16,
                32,
                "h",
                "Color Blender 8-channel -> 32-sector Q10 Chroma gain delta basis (1024 = 1.0x)",
                neg_pos_labels,
            ),
            "color_blender_chroma_luma_cross_32x2_s16": extract_2d(
                fw,
                0x441B5DB2,
                32,
                2,
                "h",
                "Color Blender positive-Chroma -> Luma (Cb, Cr) Helmholtz-Kohlrausch damping vector",
            ),
            "color_blender_brightness_basis_32x32_s16": extract_2d(
                fw,
                0x441B5E32,
                32,
                32,
                "h",
                "Color Blender 8-channel -> 32-sector Brightness (dY_Cb, dY_Cr) basis",
                bright_labels,
            ),
            "color_blender_cos_lut_513_s16": extract_1d(
                fw, 0x441B6632, 513, "h", "Quarter-wave 513-entry Q10 cosine LUT (0..90 deg)"
            ),
            "mipu_sector_matrix_a_32x6_s16": extract_2d(
                fw, 0x441A60AC, 32, 6, "h", "MIPU 32-sector base color matrix A (Q8)"
            ),
            "mipu_sector_matrix_b_32x6_s16": extract_2d(
                fw, 0x441A622C, 32, 6, "h", "MIPU 32-sector saturation reference matrix B (Q10)"
            ),
            "mipu_sector_default_32x6_s16": extract_2d(
                fw, 0x441A9CAC, 32, 6, "h", "MIPU 32-sector default Picture Control matrix (Q8)"
            ),
        },
        "zf_film_grain_presets": grain_rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract EXPEED 7 Flexible Color ROM LUTs to JSON.")
    ap.add_argument("--fw", default=DEFAULT_Z8_FW, help="Path to decrypted Z8 main firmware binary")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output JSON path")
    args = ap.parse_args()

    data = build_tables(args.fw)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"Extracted {len(data['tables'])} EXPEED 7 ROM tables + 18 Zf grain presets -> {args.out}")
    print(f"Source FW SHA-256: {data['sources']['expeed7_mipu_firmware']['sha256']}")
    for k, v in data["tables"].items():
        print(f"  - {k:42s} @ {v['va']} ({v['byte_length']:4d} B) sha256={v['sha256'][:16]}...")


if __name__ == "__main__":
    main()
