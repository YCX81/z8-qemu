#!/usr/bin/env python3
"""Minimal read-only extractor (v4) for Nikon Zf C3.01 FilmGrain 83x83 int16_t base maps.

Background (locked by v3 Ghidra decompilation of FUN_40e44bec & FUN_4102340c..41023430):
- FUN_41023414() returns 0x35d2 = 13,778 bytes == 83 * 83 * sizeof(int16_t) (in+0x52 = 0x53 = 83).
- FUN_4102341c() returns &DAT_a02b7237c -> Map 0 (standard, used for Intensity 1..5 full-res & 1..6 small-res).
- FUN_41023430() returns &DAT_a02b7594e -> Map 1 (high-amplitude, used for Intensity 6 full-res).
- Notice 0xa02b7594e - 0xa02b7237c == 0x35d2 (13,778 B) back-to-back in initialized .data (.init_rom).
- FUN_40e44bec always zero-fills mapB (memset 0, 0x35d2) and reads the 18-byte DSP scalar LUTs at 0xa02aec1f8:
    0xa02aec1f8 (6B): [2, 2, 3, 4, 6, 9]      (reduced-res Q4 blend_w)
    0xa02aec1fe (3B): [3, 3, 3]               (reduced-res blur_kernel_size)
    0xa02aec201 (6B): [2, 3, 7, 11, 16, 16]   (full-res Q4 blend_w)
    0xa02aec207 (3B): [3, 5, 9]               (full-res blur_kernel_size)

Strictly read-only, Python 3.8+ stdlib only, zero ELF dumps (~24 KB JSON output).

Usage:
    python -B extract_zf_filmgrain_maps_v4.py <path-to-zf-full-eg-analysis-20261025.bin> --json zf_filmgrain_maps_83x83_v4.json
"""

import argparse
import base64
import hashlib
import json
import math
import os
import struct
import sys
import zlib
from typing import Any, Dict

ARM64_BASE_VA = 0x40160000
RAM_TO_ROM_DELTA = 0x9BDF31000

MAP_DIM = 83
MAP_BYTES = MAP_DIM * MAP_DIM * 2  # 0x35d2 = 13,778 bytes

EXPECTED_LUTS_RAM_VA = 0xA02AEC1F8
EXPECTED_LUTS_BYTES = bytes.fromhex("0202030406090303030203070b1010030509")

MAP0_RAM_VA = 0xA02B7237C
MAP0_HEAD16 = bytes.fromhex("0000faffd6ffc2ffdcff340093ffa5ff")

MAP1_RAM_VA = 0xA02B7594E
MAP1_HEAD16 = bytes.fromhex("f9ff7400e7ffb2ff3b00aeff40ff8700")

GETTER_BLOCK_VA = 0x41023400
GETTER_BLOCK_BYTES = 128


def ram_to_file_off(ram_va: int) -> int:
    return (ram_va - RAM_TO_ROM_DELTA) - ARM64_BASE_VA


def locate_exact_slice(data: bytes, expected_off: int, head16: bytes, length: int) -> int:
    if 0 <= expected_off and expected_off + length <= len(data):
        if data[expected_off : expected_off + len(head16)] == head16:
            return expected_off
    found = data.find(head16)
    if found == -1 or found + length > len(data):
        raise ValueError(f"Could not locate {length}-byte slice matching head {head16.hex()}")
    return found


def pack_map_entry(name: str, data: bytes, file_off: int, ram_va: int, getter_va: str) -> Dict[str, Any]:
    raw = data[file_off : file_off + MAP_BYTES]
    vals = struct.unpack(f"<{MAP_DIM * MAP_DIM}h", raw)
    n = len(vals)
    mean_v = sum(vals) / float(n)
    std_v = math.sqrt(sum((v - mean_v) ** 2 for v in vals) / float(n))
    abs_mean = sum(abs(v) for v in vals) / float(n)
    zeros = sum(1 for v in vals if v == 0)
    neg = sum(1 for v in vals if v < 0)
    pos = sum(1 for v in vals if v > 0)
    comp = zlib.compress(raw, level=9)
    rom_va = ARM64_BASE_VA + file_off
    return {
        "name": name,
        "getter_fn_va": getter_va,
        "ram_va": f"0x{ram_va:09x}",
        "rom_va": f"0x{rom_va:08x}",
        "file_offset": f"0x{file_off:x}",
        "bytes": MAP_BYTES,
        "shape": [MAP_DIM, MAP_DIM],
        "dtype": "int16_le",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "stats": {
            "count": n,
            "min": min(vals),
            "max": max(vals),
            "mean": round(mean_v, 6),
            "std": round(std_v, 6),
            "mean_abs": round(abs_mean, 6),
            "zero_count": zeros,
            "neg_count": neg,
            "pos_count": pos,
        },
        "head_16_s16": list(vals[:16]),
        "tail_16_s16": list(vals[-16:]),
        "zlib_bytes": len(comp),
        "zlib_base64": base64.b64encode(comp).decode("ascii"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract exact 83x83 int16_t FilmGrain base maps (0xa02b7237c & 0xa02b7594e) from Zf C3.01 EG."
    )
    ap.add_argument("firmware", help="Path to decrypted EG module (zf-full-eg-analysis-20261025.bin)")
    ap.add_argument("--json", default="zf_filmgrain_maps_83x83_v4.json", help="Output JSON path")
    args = ap.parse_args()

    with open(args.firmware, "rb") as fh:
        data = fh.read()

    luts_off = locate_exact_slice(data, ram_to_file_off(EXPECTED_LUTS_RAM_VA), EXPECTED_LUTS_BYTES, len(EXPECTED_LUTS_BYTES))
    map0_off = locate_exact_slice(data, ram_to_file_off(MAP0_RAM_VA), MAP0_HEAD16, MAP_BYTES)
    map1_off = locate_exact_slice(data, ram_to_file_off(MAP1_RAM_VA), MAP1_HEAD16, MAP_BYTES)

    map0_rec = pack_map_entry("map0_std", data, map0_off, MAP0_RAM_VA, "0x4102341c")
    map1_rec = pack_map_entry("map1_hi", data, map1_off, MAP1_RAM_VA, "0x41023430")

    # Small guard bytes immediately before Map 0 and immediately after Map 1 + getter code block (128 B)
    pre_guard = data[max(0, map0_off - 32) : map0_off]
    post_guard = data[map1_off + MAP_BYTES : min(len(data), map1_off + MAP_BYTES + 32)]
    getter_off = GETTER_BLOCK_VA - ARM64_BASE_VA
    getter_hex = (
        data[getter_off : getter_off + GETTER_BLOCK_BYTES].hex()
        if 0 <= getter_off and getter_off + GETTER_BLOCK_BYTES <= len(data)
        else ""
    )

    receipt = {
        "schema": "zf-c301-filmgrain-maps-83x83-receipt-v4",
        "firmware_bytes": len(data),
        "firmware_sha256": hashlib.sha256(data).hexdigest(),
        "dsp_param_luts_18b": {
            "ram_va": f"0x{EXPECTED_LUTS_RAM_VA:09x}",
            "rom_va": f"0x{ARM64_BASE_VA + luts_off:08x}",
            "file_offset": f"0x{luts_off:x}",
            "hex": data[luts_off : luts_off + 18].hex(),
            "reduced_res_blend_w_6": list(data[luts_off : luts_off + 6]),
            "reduced_res_blur_size_3": list(data[luts_off + 6 : luts_off + 9]),
            "full_res_blend_w_6": list(data[luts_off + 9 : luts_off + 15]),
            "full_res_blur_size_3": list(data[luts_off + 15 : luts_off + 18]),
        },
        "maps_contiguous_check": {
            "map1_off_minus_map0_off": map1_off - map0_off,
            "equals_83x83x2": (map1_off - map0_off) == MAP_BYTES,
            "pre_map0_32b_hex": pre_guard.hex(),
            "post_map1_32b_hex": post_guard.hex(),
        },
        "getter_block_0x41023400_128b_hex": getter_hex,
        "maps_83x83_s16": {
            "map0_std": map0_rec,
            "map1_hi": map1_rec,
        },
    }

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    out_sz = os.path.getsize(args.json)
    print(f"Wrote v4 maps JSON -> {args.json} ({out_sz:,} bytes)")
    print(f"  map0_std @ ROM 0x{ARM64_BASE_VA + map0_off:08x} (file 0x{map0_off:x}): sha256={map0_rec['sha256']} std={map0_rec['stats']['std']}")
    print(f"  map1_hi  @ ROM 0x{ARM64_BASE_VA + map1_off:08x} (file 0x{map1_off:x}): sha256={map1_rec['sha256']} std={map1_rec['stats']['std']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
