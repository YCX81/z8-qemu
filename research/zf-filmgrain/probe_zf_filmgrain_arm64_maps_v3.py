#!/usr/bin/env python3
"""Read-only extractor (v3) for Nikon Zf C3.01 FilmGrain ARM64 parameter tables & base noise maps.

Context:
- v2 completely resolved the CEVA-XM6 DSP FilmGrain kernel (0x418761e0, 17,084 B):
  * TFilmGrainInputParam scalar fields (+0x4c bypass, +0x50 map_blend_weight w in 0..16,
    +0x51 blur_kernel_size in {0,3,5,7,9}, +0x52 map_dim M<=128, +0x53 tile_period P,
    +0x54 tile_step S, +0x55 tone_knee_lo, +0x56 tone_knee_hi, +0x58 prng_seed)
  * Dual external int16_t[M][M] base grain maps at in+0x60 (mapA) and in+0x70 (mapB)
  * Zf's unique ARM64 DspFrameworkWrapper entry around 0x40e44bec..0x40e44e98
    (containing BL 0x40c91ba4 @ 0x40e44e4c, dispatching RPC slot 39 / 40).

What v3 collects (strictly read-only, Python 3.8+ stdlib only, zero ELF dumps):
1. Exact ARM64 wrapper function around 0x40e44bec..0x40e44e98 (BL 0x40c91ba4 @ 0x40e44e4c)
   plus dynamic verification of any RPC caller loading slot 39 (0x27) or 40 (0x28) across its full body.
2. Upward & downward FilmGrain ARM64 call graph (ISP node Exec/Setup/Init + ISP parameter builders):
   - Upward BL/B callers (up to 3 hops) + .rodata/.data function-pointer table xrefs + +-4 sibling TU functions
   - Downward BL/B callees (up to 3 hops, filtered to specific helpers with global BL in-degree <= 30)
3. Wide-window Picture Control (+0x7c intensity u8 & +0x80 size i32) converters in 0x407e0000..0x411c0000,
   plus neighbourhood of the 18x3 table (0xa02acdd2b +- 0x200) and PtpFilmGrain runtime slots.
4. Automatic ROM/RAM constant table & int16_t[M][M] base noise map (mapA / mapB) extraction:
   - Resolves ADRP+ADD/LDR (.rodata) and MOVZ+MOVK (RAM .data <-> ROM via auto-calibrated delta)
     across all discovered FilmGrain ARM64 functions (+ 1-level pointer array dereferencing)
   - Exports 256-byte decoded previews for scalar parameter tables, and zlib+base64 payloads
     for candidate int16_t[128][128] (32 KB) / int16_t[64][64] (8 KB) base grain maps.
5. Optional read-only Ghidra SQLite enrichment (c_text + DATA refs from shared X: index if mounted).

Usage:
    python -B probe_zf_filmgrain_arm64_maps_v3.py <path-to-zf-full-eg-analysis-20261025.bin> --json zf_filmgrain_arm64_maps_v3.json
"""

import argparse
import base64
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import zlib
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ARM64_BASE_VA = 0x40160000
DEFAULT_WRAPPER_BL_VA = 0x40E44E4C
DEFAULT_WRAPPER_HINT_VA = 0x40E44BEC
DEFAULT_SYNC_SUBMIT_VA = 0x40C91BA4
DEFAULT_ASYNC_SUBMIT_VA = 0x40C91A60
DEFAULT_RAM_TO_ROM_DELTA = 0x9BDF31000  # Verified: 0xa02981bc0 - 0x44a50bc0 == 0xa02acdd2b - 0x44b9cd2b

ZF_GRAIN_PRESET_18X3_HEAD = bytes.fromhex("0f1d501a1d50371d50521d50641d59641d64")
ZF_GRAIN_PRESET_RAM_VA = 0xA02ACDD2B

DEFAULT_GHIDRA_SQLITE = (
    "X:/Nikon-Firmware/Data/Shared/Ghidra/Zf-C3.01-EG/"
    "2026-11-01/run02/eg-search-1793558316888779800.sqlite"
)


class FirmwareMapper:
    """Maps ARM64 ROM VAs and initialized RAM (.data) VAs to read-only file offsets."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.is_elf64 = data[:5] == b"\x7fELF\x02"
        self.elf_sections: List[Tuple[int, int, int, str]] = []
        if self.is_elf64:
            (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
            e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
            raw_sh = [
                struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize)
                for i in range(e_shnum)
            ]
            shstr = b""
            if 0 <= e_shstrndx < len(raw_sh):
                st = raw_sh[e_shstrndx]
                shstr = data[st[4] : st[4] + st[5]]
            for s in raw_sh:
                nm_off, sh_type, _, sh_addr, sh_offset, sh_size, *_ = s
                if sh_type != 8 and sh_size > 0 and sh_offset + sh_size <= len(data):
                    end = shstr.find(b"\x00", nm_off)
                    nm = shstr[nm_off:end].decode("latin1", "replace") if end != -1 else ""
                    self.elf_sections.append((sh_addr, sh_offset, sh_size, nm))

        # Auto-calibrate RAM (.data) -> ROM VA delta from the 18x3 grain table if present
        self.ram_to_rom_delta = DEFAULT_RAM_TO_ROM_DELTA
        self.preset_18x3_file_off = data.find(ZF_GRAIN_PRESET_18X3_HEAD)
        if self.preset_18x3_file_off != -1:
            rom_va = self.file_to_va(self.preset_18x3_file_off)
            if rom_va < 0x100000000:
                self.ram_to_rom_delta = ZF_GRAIN_PRESET_RAM_VA - rom_va

        # Determine executable code ceiling (stop before embedded XM6 ELF0 or .rodata)
        xm6_idx = data.find(b"\x7fELF\x01\x01\x01")
        self.code_limit_off = min(len(data) - 4, xm6_idx if xm6_idx != -1 else 0x1520000)

    def file_to_va(self, file_off: int) -> int:
        if self.is_elf64:
            for sh_addr, sh_off, sh_sz, _ in self.elf_sections:
                if sh_off <= file_off < sh_off + sh_sz:
                    return sh_addr + (file_off - sh_off)
        return ARM64_BASE_VA + file_off

    def va_to_file(self, va: int) -> Optional[int]:
        if self.is_elf64:
            for sh_addr, sh_off, sh_sz, _ in self.elf_sections:
                if sh_addr <= va < sh_addr + sh_sz:
                    return sh_off + (va - sh_addr)
        # Direct ROM / .rodata / .init_rom VA
        if ARM64_BASE_VA <= va < ARM64_BASE_VA + len(self.data):
            return va - ARM64_BASE_VA
        # Initialized RAM (.data) VA projected from .init_rom (e.g. 0xa02808000..0xa02f00000)
        if 0xA02000000 <= va < 0xA03500000:
            rom_va = va - self.ram_to_rom_delta
            if self.is_elf64:
                for sh_addr, sh_off, sh_sz, _ in self.elf_sections:
                    if sh_addr <= rom_va < sh_addr + sh_sz:
                        return sh_off + (rom_va - sh_addr)
            if ARM64_BASE_VA <= rom_va < ARM64_BASE_VA + len(self.data):
                return rom_va - ARM64_BASE_VA
        return None

    def read_va(self, va: int, size: int) -> Optional[bytes]:
        off = self.va_to_file(va)
        if off is None or off < 0 or off + size > len(self.data):
            return None
        return self.data[off : off + size]

    def is_code_va(self, va: int) -> bool:
        off = self.va_to_file(va)
        return off is not None and 0 <= off < self.code_limit_off and (va & 3) == 0


def decode_arm64_logical_imm32(ins: int) -> Optional[int]:
    if (ins & 0xFFC003E0) != 0x320003E0:
        return None
    imms = (ins >> 10) & 0x3F
    immr = (ins >> 16) & 0x3F
    not_imms = (~imms) & 0x3F
    if not_imms == 0:
        return None
    esize = 1 << (not_imms.bit_length() - 1)
    if esize < 2 or esize > 32:
        return None
    levels = esize - 1
    s = imms & levels
    if s == levels:
        return None
    r = immr & levels
    welem = (1 << (s + 1)) - 1
    if r != 0:
        welem = ((welem >> r) | (welem << (esize - r))) & ((1 << esize) - 1)
    val = 0
    for pos in range(0, 32, esize):
        val |= welem << pos
    return val & 0xFFFFFFFF


def build_code_index(fm: FirmwareMapper) -> Dict[str, Any]:
    """Build fast in-memory index of ARM64 function boundaries and BL/B call graph."""
    data = fm.data
    limit = fm.code_limit_off

    # 1. Collect all BL / unconditional B edges
    callers_by_target: Dict[int, List[int]] = {}
    callees_by_site: Dict[int, int] = {}

    for off in range(0, limit, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        top6 = ins >> 26
        if top6 in (0b100101, 0b000101):  # BL or B
            imm26 = ins & 0x3FFFFFF
            if imm26 & (1 << 25):
                imm26 -= 1 << 26
            src_va = fm.file_to_va(off)
            dst_va = src_va + (imm26 << 2)
            if top6 == 0b100101:  # BL definitely targets a function entry
                callers_by_target.setdefault(dst_va, []).append(src_va)
                callees_by_site[src_va] = dst_va

    # 2. Identify function starts from RET boundaries, PACIASP, and BL targets
    fn_starts_set: Set[int] = {fm.file_to_va(0)}
    for dst_va in callers_by_target:
        if fm.is_code_va(dst_va):
            fn_starts_set.add(dst_va)

    for off in range(0, limit - 4, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        if ins in (0xD65F03C0, 0xD65F0FFF):  # RET / RETAB
            nxt_off = off + 4
            while nxt_off < limit:
                (n_ins,) = struct.unpack_from("<I", data, nxt_off)
                if n_ins not in (0x00000000, 0xD503201F):  # skip zero/NOP padding
                    fn_starts_set.add(fm.file_to_va(nxt_off))
                    break
                nxt_off += 4
        elif ins == 0xD503233F:  # PACIASP
            fn_starts_set.add(fm.file_to_va(off))

    fn_starts = sorted(fn_starts_set)

    # Binary search helper
    import bisect

    def enclosing_fn(va: int) -> Tuple[int, int]:
        idx = bisect.bisect_right(fn_starts, va) - 1
        if idx < 0:
            return (va & ~3, (va & ~3) + 64)
        s_va = fn_starts[idx]
        e_va = fn_starts[idx + 1] if idx + 1 < len(fn_starts) else fm.file_to_va(limit)
        # Cap runaway spans to 16 KB
        return (s_va, min(e_va, s_va + 0x4000))

    def sibling_fns(entry_va: int, radius: int = 4) -> List[int]:
        idx = bisect.bisect_left(fn_starts, entry_va)
        lo = max(0, idx - radius)
        hi = min(len(fn_starts), idx + radius + 1)
        return fn_starts[lo:hi]

    # Also scan 64-bit function pointers in .rodata / .init_rom (.data)
    ptr_refs_by_target: Dict[int, List[int]] = {}
    for off in range(limit & ~7, len(data) - 8, 8):
        (qval,) = struct.unpack_from("<Q", data, off)
        if ARM64_BASE_VA <= qval < ARM64_BASE_VA + limit and (qval & 3) == 0:
            ptr_refs_by_target.setdefault(qval, []).append(fm.file_to_va(off))

    return {
        "fn_starts": fn_starts,
        "enclosing_fn": enclosing_fn,
        "sibling_fns": sibling_fns,
        "callers_by_target": callers_by_target,
        "callees_by_site": callees_by_site,
        "ptr_refs_by_target": ptr_refs_by_target,
    }


def analyze_arm64_function_body(fm: FirmwareMapper, start_va: int, end_va: int) -> Dict[str, Any]:
    """Decode constants, ADRP+ADD/LDR refs, MOVZ+MOVK 64-bit refs, struct mem ops, and BLs."""
    blob = fm.read_va(start_va, end_va - start_va)
    if not blob:
        return {"entry_va": f"0x{start_va:08x}", "present": False}

    n_ins = len(blob) // 4
    words = struct.unpack(f"<{n_ins}I", blob[: n_ins * 4])

    # Track register states linearly for ADRP and MOVZ+MOVK address formation
    reg_adrp: Dict[int, int] = {}
    reg_mov64: Dict[int, int] = {}
    reg_mov_valid: Set[int] = set()

    data_refs: Set[int] = set()
    small_imms: List[str] = []
    bl_targets: List[str] = []
    mem_offsets: List[str] = []

    for i, ins in enumerate(words):
        pc = start_va + 4 * i
        rd = ins & 0x1F
        rn = (ins >> 5) & 0x1F

        # 1. ADRP Xd, page
        if (ins & 0x9F000000) == 0x90000000:
            immlo = (ins >> 29) & 3
            immhi = (ins >> 5) & 0x7FFFF
            imm = (immhi << 2) | immlo
            if imm & (1 << 20):
                imm -= 1 << 21
            reg_adrp[rd] = (pc & ~0xFFF) + (imm << 12)
            reg_mov_valid.discard(rd)
            continue

        # ADD Xd, Xn, #imm12 (after ADRP)
        if (ins & 0xFFC00000) == 0x91000000 and rn in reg_adrp:
            imm12 = (ins >> 10) & 0xFFF
            sh = 12 if ((ins >> 22) & 1) else 0
            tgt = reg_adrp[rn] + (imm12 << sh)
            data_refs.add(tgt)
            if rd != rn:
                reg_adrp.pop(rd, None)
            continue

        # 2. MOVZ Wd/Xd, #imm16, lsl #(hw*16)
        if (ins & 0x7F800000) == 0x52800000:
            hw = (ins >> 21) & 3
            imm16 = (ins >> 5) & 0xFFFF
            val = imm16 << (hw * 16)
            reg_mov64[rd] = val
            reg_mov_valid.add(rd)
            reg_adrp.pop(rd, None)
            if hw == 0 and imm16 <= 0x100:
                small_imms.append(f"+0x{4*i:03x}:movz_r{rd}={imm16}(0x{imm16:x})")
            continue

        # MOVK Wd/Xd, #imm16, lsl #(hw*16)
        if (ins & 0x7F800000) == 0x72800000 and rd in reg_mov_valid:
            hw = (ins >> 21) & 3
            imm16 = (ins >> 5) & 0xFFFF
            mask = ~(0xFFFF << (hw * 16)) & 0xFFFFFFFFFFFFFFFF
            reg_mov64[rd] = (reg_mov64[rd] & mask) | (imm16 << (hw * 16))
            cand = reg_mov64[rd]
            if (0x41650000 <= cand < 0x46000000) or (0xA00000000 <= cand < 0xD00000000):
                data_refs.add(cand)
            continue

        # 3. ORR Wd, WZR, #logical_imm
        limm = decode_arm64_logical_imm32(ins)
        if limm is not None and limm <= 0x100:
            small_imms.append(f"+0x{4*i:03x}:orr_w{rd}={limm}(0x{limm:x})")

        # 4. BL / B
        top6 = ins >> 26
        if top6 in (0b100101, 0b000101):
            imm26 = ins & 0x3FFFFFF
            if imm26 & (1 << 25):
                imm26 -= 1 << 26
            tgt = pc + (imm26 << 2)
            op_nm = "bl" if top6 == 0b100101 else "b"
            if op_nm == "bl" or not (start_va <= tgt < end_va):
                bl_targets.append(f"0x{pc:08x}:{op_nm}->0x{tgt:08x}")

        # 5. Unsigned immediate LDR/STR (byte / half / word / xword) for ADRP page or struct offsets
        if (ins & 0x3B000000) == 0x39000000:
            size_code = (ins >> 30) & 3
            opc = (ins >> 22) & 3
            imm12 = (ins >> 10) & 0xFFF
            byte_off = imm12 << size_code
            if rn in reg_adrp:
                data_refs.add(reg_adrp[rn] + byte_off)
            elif rn != 31 and byte_off in (0x4C, 0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x58, 0x60, 0x70, 0x7C, 0x80):
                rw = "str" if opc == 0 else "ldr"
                mem_offsets.append(f"0x{pc:08x}:{rw}{1<<size_code}[x{rn}+0x{byte_off:x}]")

    return {
        "entry_va": f"0x{start_va:08x}",
        "end_va": f"0x{end_va:08x}",
        "size_bytes": len(blob),
        "body_sha256": hashlib.sha256(blob).hexdigest(),
        "hex": blob.hex(),
        "small_imms": small_imms[:48],
        "interesting_struct_ops": mem_offsets[:64],
        "calls": bl_targets[:64],
        "referenced_data_vas": [f"0x{v:x}" for v in sorted(data_refs)],
    }


def scan_wide_7c_80_functions(fm: FirmwareMapper, code_idx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find functions in 0x407e0000..0x411c0000 accessing BOTH +0x7c (1B) and +0x80 (4B) anywhere inside."""
    hits: List[Dict[str, Any]] = []
    fn_starts: List[int] = code_idx["fn_starts"]
    enclosing_fn = code_idx["enclosing_fn"]

    for s_va in fn_starts:
        if not (0x407E0000 <= s_va < 0x411C0000):
            continue
        _, e_va = enclosing_fn(s_va)
        blob = fm.read_va(s_va, e_va - s_va)
        if not blob:
            continue
        n_ins = len(blob) // 4
        words = struct.unpack(f"<{n_ins}I", blob[: n_ins * 4])
        va_7c: List[str] = []
        va_80: List[str] = []
        for i, ins in enumerate(words):
            rn = (ins >> 5) & 0x1F
            if rn == 31:
                continue
            imm12 = (ins >> 10) & 0xFFF
            opc = (ins >> 22) & 3
            # 1-byte load/store at #0x7c
            if (ins & 0xFF000000) == 0x39000000 and opc in (0, 1, 2, 3) and imm12 == 0x7C:
                va_7c.append(f"0x{s_va + 4*i:08x}")
            # 4-byte load/store at #0x80 (imm12 = 0x20)
            elif (ins & 0xFF000000) == 0xB9000000 and opc in (0, 1) and imm12 == 0x20:
                va_80.append(f"0x{s_va + 4*i:08x}")
        if va_7c and va_80:
            hits.append(
                {
                    "fn_entry_va": f"0x{s_va:08x}",
                    "fn_end_va": f"0x{e_va:08x}",
                    "size_bytes": e_va - s_va,
                    "sites_7c": va_7c[:8],
                    "sites_80": va_80[:8],
                }
            )
    return hits


def inspect_data_and_candidate_maps(
    fm: FirmwareMapper,
    seed_data_vas: Sequence[int],
    max_maps: int = 8,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Decode referenced ROM/RAM tables and export candidate int16[M][M] grain maps (zlib+base64)."""
    decoded_tables: Dict[str, Any] = {}
    exported_maps: Dict[str, Any] = {}

    # Expand 1 level of 64-bit pointer arrays inside .rodata / .data tables
    queue: List[Tuple[int, str]] = [(va, "direct_code_ref") for va in sorted(set(seed_data_vas))]
    visited: Set[int] = set()
    exported_map_offsets: List[int] = []

    while queue:
        va, origin = queue.pop(0)
        if va in visited:
            continue
        visited.add(va)

        off = fm.va_to_file(va)
        if off is None or off < fm.code_limit_off or off >= len(fm.data):
            continue

        # Read up to 256 B preview for table inspection
        avail = len(fm.data) - off
        prev_len = min(256, avail)
        if prev_len < 16:
            continue
        prev = fm.data[off : off + prev_len]
        n2 = (prev_len // 2) * 2
        n4 = (prev_len // 4) * 4
        n8 = (prev_len // 8) * 8

        child_ptrs: List[str] = []
        if origin == "direct_code_ref":
            seen_qw: Set[int] = set()
            for p_off in range(0, min(prev_len - 7, 192), 4):
                (qw,) = struct.unpack_from("<Q", prev, p_off)
                if qw in seen_qw:
                    continue
                seen_qw.add(qw)
                if fm.va_to_file(qw) is not None and not fm.is_code_va(qw):
                    child_ptrs.append(f"0x{qw:x}")
                    if qw not in visited and len(visited) < 128:
                        queue.append((qw, f"ptr_from_0x{va:x}+0x{p_off:x}"))

        key = f"0x{va:x}"
        decoded_tables[key] = {
            "va": key,
            "origin": origin,
            "file_offset": f"0x{off:x}",
            "preview_bytes": prev_len,
            "hex_128": prev[:128].hex(),
            "u8_64": list(prev[:64]),
            "s16_le_64": list(struct.unpack(f"<{min(64, n2 // 2)}h", prev[: min(128, n2)])),
            "u32_hex_16": [f"0x{x:08x}" for x in struct.unpack(f"<{min(16, n4 // 4)}I", prev[: min(64, n4)])],
            "deref_data_ptrs": child_ptrs,
        }

        # Check if this address starts a non-overlapping 128x128 (32,768 B) or 64x64 (8,192 B) int16 grain map
        if (
            len(exported_maps) < max_maps
            and avail >= 8192
            and (off & 1) == 0
            and all(abs(off - prev_off) >= 4096 for prev_off in exported_map_offsets)
        ):
            map_bytes_len = 32768 if avail >= 32768 else 8192
            raw_map = fm.data[off : off + map_bytes_len]
            s16_vals = struct.unpack(f"<{map_bytes_len // 2}h", raw_map)
            nz_count = sum(1 for v in s16_vals[:1024] if v != 0)
            vmin = min(s16_vals)
            vmax = max(s16_vals)
            mean_v = sum(s16_vals) / float(len(s16_vals))
            var_v = sum((v - mean_v) ** 2 for v in s16_vals[:4096]) / float(min(4096, len(s16_vals)))
            std_v = math.sqrt(var_v)

            looks_like_grain_map = (
                nz_count >= 256
                and -16384 <= vmin < vmax <= 16384
                and 0.5 <= std_v <= 4096.0
            )
            if looks_like_grain_map:
                exported_map_offsets.append(off)
                comp = zlib.compress(raw_map, level=9)
                exported_maps[key] = {
                    "va": key,
                    "origin": origin,
                    "file_offset": f"0x{off:x}",
                    "bytes": map_bytes_len,
                    "assumed_shape_s16": [128, 128] if map_bytes_len == 32768 else [64, 64],
                    "sha256": hashlib.sha256(raw_map).hexdigest(),
                    "min_s16": vmin,
                    "max_s16": vmax,
                    "mean_s16": round(mean_v, 4),
                    "std_s16_approx": round(std_v, 4),
                    "s16_head_64": list(s16_vals[:64]),
                    "zlib_base64": base64.b64encode(comp).decode("ascii"),
                }

    return decoded_tables, exported_maps


def enrich_from_ghidra_sqlite(
    db_path: str,
    seed_fn_vas: Sequence[Tuple[str, int]],
) -> Tuple[Dict[str, Any], List[int]]:
    """Query Ghidra SQLite for decompiled c_text AND outgoing DATA/CALL xrefs from FilmGrain functions."""
    if not db_path or not os.path.isfile(db_path):
        return ({"available": False, "reason": "sqlite file not present at path"}, [])

    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro&immutable=1"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return ({"available": False, "reason": str(exc)}, [])

    extra_data_vas: List[int] = []
    functions_out: Dict[str, Any] = {}
    resolved_sites: List[Dict[str, Any]] = []
    sql_refs_out: List[Dict[str, Any]] = []

    try:
        tables = [
            r["name"]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        has_refs = "refs" in tables

        # Queue allows adding newly discovered callers/callees from SQLite `refs`
        queue: List[Tuple[str, int]] = list(seed_fn_vas)
        seen_entries: Set[str] = set()

        while queue and len(functions_out) < 42:
            role, q_va = queue.pop(0)
            va_hex = f"{q_va:08x}"
            row = db.execute(
                "SELECT entry, name, body_bytes, ranges, c_text FROM functions "
                "WHERE entry <= ? ORDER BY entry DESC LIMIT 1",
                (va_hex,),
            ).fetchone()
            if not row:
                continue
            entry = row["entry"]
            resolved_sites.append({"role": role, "query_va": f"0x{q_va:08x}", "function_entry": f"0x{entry}"})
            if entry in seen_entries:
                continue
            seen_entries.add(entry)

            c_txt = row["c_text"] or ""
            functions_out[entry] = {
                "name": row["name"],
                "body_bytes": row["body_bytes"],
                "ranges": row["ranges"],
                "role": role,
                "c_text": c_txt[:20000],
                "c_text_truncated": len(c_txt) > 20000,
            }

            # If this is a core FilmGrain wrapper / caller / param-builder, query `refs` for callers & DATA refs!
            if has_refs and any(k in role for k in ("wrapper", "caller", "sibling", "callee_hop1")):
                try:
                    # Incoming refs to this function entry
                    in_rows = db.execute(
                        "SELECT source, destination, type, source_function FROM refs "
                        "WHERE destination = ? LIMIT 24",
                        (entry,),
                    ).fetchall()
                    for ir in in_rows:
                        sql_refs_out.append(dict(ir))
                        sf = ir["source_function"]
                        if sf and sf not in seen_entries and len(sf) <= 8:
                            try:
                                queue.append((f"sql_caller_of_{entry}", int(sf, 16)))
                            except ValueError:
                                pass

                    # Outgoing DATA/READ refs from this function
                    out_rows = db.execute(
                        "SELECT source, destination, type, source_function FROM refs "
                        "WHERE source_function = ? LIMIT 64",
                        (entry,),
                    ).fetchall()
                    for orow in out_rows:
                        dst_s = orow["destination"]
                        typ_s = (orow["type"] or "").upper()
                        if "CALL" not in typ_s:
                            sql_refs_out.append(dict(orow))
                            try:
                                d_va = int(dst_s, 16)
                                if d_va >= 0x41650000:
                                    extra_data_vas.append(d_va)
                            except ValueError:
                                pass
                except sqlite3.Error:
                    has_refs = False

    except sqlite3.Error as exc:
        db.close()
        return ({"available": False, "reason": f"query error: {exc}"}, extra_data_vas)

    db.close()
    return (
        {
            "available": True,
            "sqlite_tables": tables,
            "resolved_sites": resolved_sites,
            "decompiled_function_count": len(functions_out),
            "decompiled_functions": functions_out,
            "xref_records": sql_refs_out[:200],
        },
        extra_data_vas,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract Zf C3.01 ARM64 FilmGrain wrapper (0x40e44bec), ISP callers, parameter tables & base noise maps."
    )
    ap.add_argument("firmware", help="Path to decrypted EG module (zf-full-eg-analysis-20261025.bin)")
    ap.add_argument("--anchor-bl", default=f"0x{DEFAULT_WRAPPER_BL_VA:08x}", help="Known BL site inside FilmGrain wrapper (default: 0x40e44e4c)")
    ap.add_argument("--db", default=DEFAULT_GHIDRA_SQLITE, help="Optional read-only Ghidra SQLite index path")
    ap.add_argument("--no-db", action="store_true", help="Skip optional Ghidra SQLite lookup")
    ap.add_argument("--max-maps", type=int, default=8, help="Max candidate int16 grain maps to embed (default: 8)")
    ap.add_argument("--json", default="zf_filmgrain_arm64_maps_v3.json", help="Output JSON receipt path")
    args = ap.parse_args()

    with open(args.firmware, "rb") as fh:
        data = fh.read()

    fm = FirmwareMapper(data)
    code_idx = build_code_index(fm)
    enclosing_fn = code_idx["enclosing_fn"]
    sibling_fns = code_idx["sibling_fns"]
    callers_by_target = code_idx["callers_by_target"]
    callees_by_site = code_idx["callees_by_site"]
    ptr_refs_by_target = code_idx["ptr_refs_by_target"]

    anchor_bl_va = int(args.anchor_bl, 16)
    wrapper_start, wrapper_end = enclosing_fn(anchor_bl_va)

    # Also verify full-function slot 39/40 (0x27/0x28) immediates across all callers of 0x40c91ba4 / 0x40c91a60
    rpc_slot39_40_wrappers: List[Dict[str, Any]] = []
    for submit_va in (DEFAULT_SYNC_SUBMIT_VA, DEFAULT_ASYNC_SUBMIT_VA):
        for bl_site in callers_by_target.get(submit_va, []):
            fs, fe = enclosing_fn(bl_site)
            info = analyze_arm64_function_body(fm, fs, fe)
            hits_39_40 = [
                s for s in info.get("small_imms", [])
                if any(tok in s for tok in ("=39(0x27)", "=40(0x28)"))
            ]
            if hits_39_40 or fs == wrapper_start:
                rpc_slot39_40_wrappers.append(
                    {
                        "submit_api_va": f"0x{submit_va:08x}",
                        "bl_site_va": f"0x{bl_site:08x}",
                        "fn_entry_va": f"0x{fs:08x}",
                        "fn_end_va": f"0x{fe:08x}",
                        "slot_39_40_imms": hits_39_40,
                    }
                )

    # Traverse upward callers (up to 3 hops) + sibling TU functions + downward specific callees (up to 3 hops)
    target_fns: Dict[int, str] = {wrapper_start: "00_wrapper_FilmGrain"}

    # Upward traversal
    up_frontier: List[Tuple[int, int]] = [(wrapper_start, 1)]
    ptr_table_xrefs: Dict[str, List[str]] = {}
    while up_frontier:
        fn_va, hop = up_frontier.pop(0)
        p_refs = ptr_refs_by_target.get(fn_va, [])
        if p_refs:
            ptr_table_xrefs[f"0x{fn_va:08x}"] = [f"0x{x:08x}" for x in p_refs[:24]]
        if hop > 3:
            continue
        for call_site in callers_by_target.get(fn_va, []):
            cs_start, _ = enclosing_fn(call_site)
            if cs_start not in target_fns:
                target_fns[cs_start] = f"01_caller_hop{hop}_from_0x{fn_va:08x}"
                up_frontier.append((cs_start, hop + 1))
            # Capture sibling functions in the same ISP node translation unit (+-3 functions) for hop 1 callers!
            if hop == 1:
                for sib_va in sibling_fns(cs_start, radius=3):
                    if sib_va not in target_fns:
                        target_fns[sib_va] = f"02_sibling_tu_of_0x{cs_start:08x}"
                    sib_prefs = ptr_refs_by_target.get(sib_va, [])
                    if sib_prefs:
                        ptr_table_xrefs[f"0x{sib_va:08x}"] = [f"0x{x:08x}" for x in sib_prefs[:16]]

    # Downward traversal from wrapper, hop-1 callers, and their TU siblings (filtering out generic RTOS/RPC/libc)
    seed_roots_for_down = [
        va for va, role in list(target_fns.items())
        if role.startswith(("00_wrapper", "01_caller_hop1", "02_sibling"))
    ]
    down_frontier: List[Tuple[int, int]] = [(va, 1) for va in seed_roots_for_down]
    while down_frontier:
        fn_va, hop = down_frontier.pop(0)
        if hop > 3:
            continue
        _, fn_end = enclosing_fn(fn_va)
        for pc in range(fn_va, fn_end, 4):
            callee_va = callees_by_site.get(pc)
            if callee_va is None or not fm.is_code_va(callee_va):
                continue
            # Skip already-known generic DSP RPC driver / RTOS primitives (0x40c00000..0x40d40000) & libc (0x41200000+)
            if (0x40C00000 <= callee_va < 0x40D40000) or callee_va >= 0x41200000:
                continue
            in_deg = len(callers_by_target.get(callee_va, []))
            if in_deg > 20:
                continue
            if callee_va not in target_fns and len(target_fns) < 48:
                target_fns[callee_va] = f"03_callee_hop{hop}_from_0x{fn_va:08x}(indeg={in_deg})"
                down_frontier.append((callee_va, hop + 1))

    # Analyze all discovered FilmGrain ARM64 functions & collect referenced ROM/RAM data VAs
    analyzed_fns: Dict[str, Any] = {}
    all_seed_data_vas: List[int] = []

    for fn_va, role in sorted(target_fns.items(), key=lambda kv: (kv[1], kv[0])):
        _, fn_end = enclosing_fn(fn_va)
        info = analyze_arm64_function_body(fm, fn_va, fn_end)
        info["role"] = role
        info["global_bl_caller_count"] = len(callers_by_target.get(fn_va, []))
        analyzed_fns[f"0x{fn_va:08x}"] = info
        for dva_s in info.get("referenced_data_vas", []):
            all_seed_data_vas.append(int(dva_s, 16))

    # Wide-window +0x7c / +0x80 scan across 0x407e0000..0x411c0000
    wide_7c_80 = scan_wide_7c_80_functions(fm, code_idx)

    # Neighbourhood around the known 18x3 grain preset table (0xa02acdd2b +- 0x200)
    preset_neighbourhood: Dict[str, Any] = {"present": False}
    if fm.preset_18x3_file_off != -1:
        nb_start = max(0, (fm.preset_18x3_file_off - 0x200) & ~15)
        nb_end = min(len(data), fm.preset_18x3_file_off + 0x240)
        nb_blob = data[nb_start:nb_end]
        preset_neighbourhood = {
            "present": True,
            "preset_18x3_file_offset": f"0x{fm.preset_18x3_file_off:x}",
            "preset_18x3_ram_va": f"0x{ZF_GRAIN_PRESET_RAM_VA:09x}",
            "calibrated_ram_to_rom_delta": f"0x{fm.ram_to_rom_delta:x}",
            "window_start_file_offset": f"0x{nb_start:x}",
            "window_start_rom_va": f"0x{fm.file_to_va(nb_start):08x}",
            "window_bytes": len(nb_blob),
            "hex": nb_blob.hex(),
        }

    # Optional Ghidra SQLite enrichment (adds c_text AND outgoing DATA refs from Ghidra's analyzer)
    sqlite_queries: List[Tuple[str, int]] = [
        (role, fn_va) for fn_va, role in sorted(target_fns.items(), key=lambda kv: kv[1])
    ]
    for item in wide_7c_80[:12]:
        sqlite_queries.append(("wide_7c_80_fn", int(item["fn_entry_va"], 16)))
    # Bonus: FUN_410db4f4 (XMP GrainAmount helper right after FUN_410d9d8c)
    if fm.is_code_va(0x410DB4F4):
        sqlite_queries.append(("xmp_grain_amount_helper", 0x410DB4F4))

    if args.no_db:
        sqlite_info, sql_data_vas = ({"available": False, "reason": "disabled via --no-db"}, [])
    else:
        sqlite_info, sql_data_vas = enrich_from_ghidra_sqlite(args.db, sqlite_queries)
        all_seed_data_vas.extend(sql_data_vas)

    # Extract decoded constant tables & candidate int16[M][M] grain maps
    decoded_tables, exported_maps = inspect_data_and_candidate_maps(
        fm, all_seed_data_vas, max_maps=args.max_maps
    )

    receipt = {
        "schema": "zf-c301-filmgrain-arm64-maps-receipt-v3",
        "firmware_bytes": len(data),
        "firmware_sha256": hashlib.sha256(data).hexdigest(),
        "calibrated_ram_to_rom_delta": f"0x{fm.ram_to_rom_delta:x}",
        "anchor_wrapper_bl_va": f"0x{anchor_bl_va:08x}",
        "anchor_wrapper_fn_bounds": [f"0x{wrapper_start:08x}", f"0x{wrapper_end:08x}"],
        "rpc_slot_39_40_verified_wrappers": rpc_slot39_40_wrappers,
        "function_pointer_table_xrefs": ptr_table_xrefs,
        "filmgrain_arm64_call_tree": analyzed_fns,
        "wide_7c_80_candidate_functions": wide_7c_80,
        "preset_table_0xa02acdd2b_neighbourhood": preset_neighbourhood,
        "referenced_rom_ram_tables": decoded_tables,
        "candidate_int16_grain_maps": exported_maps,
        "optional_ghidra_sqlite": sqlite_info,
    }

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    out_sz = os.path.getsize(args.json)
    print(f"Wrote v3 JSON receipt -> {args.json} ({out_sz:,} bytes)")
    print(f"  Wrapper entry bounds       : 0x{wrapper_start:08x} .. 0x{wrapper_end:08x}")
    print(f"  ARM64 call-tree functions  : {len(analyzed_fns)}")
    print(f"  Wide +0x7c/+0x80 functions : {len(wide_7c_80)}")
    print(f"  Decoded ROM/RAM tables     : {len(decoded_tables)}")
    print(f"  Candidate int16 grain maps : {len(exported_maps)}")
    print(f"  Ghidra SQLite attached     : {sqlite_info.get('available')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
