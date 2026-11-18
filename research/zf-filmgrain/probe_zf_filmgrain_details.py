#!/usr/bin/env python3
"""Read-only deep extractor (v2) for Zf C3.01 FilmGrain (no 916 KB ELF binary export).

Extracts a self-contained JSON receipt (~65-90 KB) sufficient to resolve:
1) TFilmGrainInputParam / TFilmGrainOutputParam field offset tables & BlurLineRing layout;
2) Exact semantics of all 4 switch jump tables & the ARM64->DSP path of size/intensity/freq;
3) PRNG vs LUT noise generation, luma vs per-channel application, and output clamping/saturation.

What it collects (strictly read-only, Python 3.8+ stdlib only):
- DSP side (FilmGrain.cpp ONLY, 5 functions = 17,084 B total):
  * .LCPI2_0 / .LCPI3_0 / .LCPI4_0 constant pools (+ 64 B neighbours) in u8/s8/s16/u16/s32/u32
  * All 4 switch jump tables (4 x 10 entries) mapped to .LBB0_* labels
  * Basic-block (.LBB*) instruction slices of FilmGrain.cpp's 5 functions ONLY (zero other DSP modules)
  * Resolved PCU.call targets + tiny external helper stubs (<= 256 B) + fgsf.cpp symbol list
- ARM64 <-> XM6 RPC bridge (from raw .bin alone):
  * Locates DSPRMFL / XM6 ELF0 loader, extracts RPC pointer-marshaller jump tables (JT1/JT2) at
    slots 39 (0x27) & 40 (0x28) to list every pointer offset in TFilmGrainInputParam/OutputParam
  * Scans all external BL callers of the ARM64 DSP RPC driver for slot 39/40 (0x27/0x28) and
    captures enclosing ARM64 function bytes for matched callers
- ARM64 Picture Control consumers:
  * All [xBase, #0x7c] + [xBase, #0x80] instruction pairs (without requiring "Grain" strings)
  * True entry[0] base of the 0x44a515c0 PtpFilmGrain factory array & its ARM64 callers
- Optional read-only Ghidra SQLite enrichment (auto-skipped if X: index is not mounted):
  * Attaches decompiled c_text for matched ARM64 RPC callers, marshallers, and +0x7c/+0x80 functions

Usage:
    python -B probe_zf_filmgrain_details.py firmware/research/zf-full-eg-analysis-20261025.bin --json zf_filmgrain_details.json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

ARM64_BASE_VA = 0x40160000
EM_CEVA_XM6 = 0xF5
DEFAULT_GHIDRA_SQLITE = (
    "X:/Nikon-Firmware/Data/Shared/Ghidra/Zf-C3.01-EG/"
    "2026-11-01/run02/eg-search-1793558316888779800.sqlite"
)


def outer_file_to_va(data: bytes, file_off: int) -> int:
    if data[:5] == b"\x7fELF\x02":
        (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
        e_shentsize, e_shnum, _ = struct.unpack_from("<HHH", data, 0x3A)
        for i in range(e_shnum):
            _, typ, _, addr, offset, size, *_ = struct.unpack_from(
                "<IIQQQQIIQQ", data, e_shoff + i * e_shentsize
            )
            if typ != 8 and offset <= file_off < offset + size:
                return addr + (file_off - offset)
    return ARM64_BASE_VA + file_off


def va_to_outer_file(data: bytes, target_va: int) -> Optional[int]:
    if data[:5] == b"\x7fELF\x02":
        (e_shoff,) = struct.unpack_from("<Q", data, 0x28)
        e_shentsize, e_shnum, _ = struct.unpack_from("<HHH", data, 0x3A)
        for i in range(e_shnum):
            _, typ, _, addr, offset, size, *_ = struct.unpack_from(
                "<IIQQQQIIQQ", data, e_shoff + i * e_shentsize
            )
            if typ != 8 and addr <= target_va < addr + size:
                return offset + (target_va - addr)
        return None
    off = target_va - ARM64_BASE_VA
    return off if 0 <= off < len(data) else None


def find_first_xm6_elf(data: bytes) -> Tuple[int, List[Dict[str, Any]], List[Dict[str, Any]]]:
    pos = 0
    while True:
        idx = data.find(b"\x7fELF\x01\x01\x01", pos)
        if idx == -1:
            raise RuntimeError("No embedded CEVA-XM6 ELF32 found")
        pos = idx + 4
        if idx + 52 > len(data):
            continue
        (
            _,
            e_machine,
            _,
            _,
            _,
            e_shoff,
            _,
            _,
            _,
            _,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        ) = struct.unpack_from("<HHIIIIIHHHHHH", data, idx + 16)
        if e_machine == EM_CEVA_XM6 and e_shnum > 0 and e_shentsize == 40:
            raw_secs = [
                struct.unpack_from("<10I", data, idx + e_shoff + i * e_shentsize)
                for i in range(e_shnum)
            ]
            shstr_t = raw_secs[e_shstrndx]
            shstr = data[idx + shstr_t[4] : idx + shstr_t[4] + shstr_t[5]]
            secs = []
            sec_map = {}
            for i, t in enumerate(raw_secs):
                end_n = shstr.find(b"\x00", t[0])
                nm = shstr[t[0] : end_n].decode("latin1") if end_n != -1 else f"sec_{i}"
                info = {
                    "index": i,
                    "name": nm,
                    "type": t[1],
                    "dsp_va": t[3],
                    "elf_offset": t[4],
                    "module_offset": idx + t[4],
                    "size": t[5],
                }
                secs.append(info)
                sec_map[nm] = info

            syms = []
            sym_s = sec_map[".symtab"]
            str_s = sec_map[".strtab"]
            strtab = data[str_s["module_offset"] : str_s["module_offset"] + str_s["size"]]
            cur_tu = ""
            for k in range(sym_s["size"] // 16):
                st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from(
                    "<IIIBBH", data, sym_s["module_offset"] + k * 16
                )
                end_s = strtab.find(b"\x00", st_name)
                nm = strtab[st_name:end_s].decode("utf-8", "replace") if end_s != -1 else ""
                stt = st_info & 0xF
                stb = st_info >> 4
                if stt == 4:
                    cur_tu = nm
                elif stb == 1 and stt in (1, 2):
                    # Global symbols follow all local STT_FILE groups in ELF symtab
                    cur_tu = ""
                mod_off = None
                if 0 < st_shndx < len(secs) and secs[st_shndx]["type"] == 1:
                    sc = secs[st_shndx]
                    if sc["dsp_va"] <= st_value < sc["dsp_va"] + sc["size"]:
                        mod_off = sc["module_offset"] + (st_value - sc["dsp_va"])
                syms.append(
                    {
                        "idx": k,
                        "name": nm,
                        "tu": cur_tu,
                        "stt": stt,
                        "stb": stb,
                        "dsp_va": st_value,
                        "size": st_size,
                        "shndx": st_shndx,
                        "module_offset": mod_off,
                    }
                )
            return idx, secs, syms


def decode_const_block(blob: bytes, dsp_va: int) -> Dict[str, Any]:
    n2 = (len(blob) // 2) * 2
    n4 = (len(blob) // 4) * 4
    return {
        "dsp_va": f"0x{dsp_va:08x}",
        "bytes": len(blob),
        "hex": blob.hex(),
        "as_u8": list(blob),
        "as_s8": list(struct.unpack(f"<{len(blob)}b", blob)),
        "as_s16_le": list(struct.unpack(f"<{n2 // 2}h", blob[:n2])),
        "as_u16_le": list(struct.unpack(f"<{n2 // 2}H", blob[:n2])),
        "as_s32_le": list(struct.unpack(f"<{n4 // 4}i", blob[:n4])),
        "as_u32_hex": [f"0x{v:08x}" for v in struct.unpack(f"<{n4 // 4}I", blob[:n4])],
    }


def scan_xm6_cw_and_calls(
    body: bytes, fn_dsp_va: int, sym_lookup: Dict[int, str], data_ranges: List[Tuple[int, int, str]]
) -> Dict[str, Any]:
    """Decode CEVA-XM6 PCU.call targets and 32-bit immediate Control Words."""
    cw32_count = 0
    call_hits = []
    data_refs = []
    seen_calls = set()

    for off in range(4, len(body) - 3, 2):
        (cw,) = struct.unpack_from("<I", body, off)
        if (cw & 0xF8000000) != 0xF8000000:
            continue
        cw32_count += 1
        (prev_ins,) = struct.unpack_from("<I", body, off - 4)
        (next_ins,) = struct.unpack_from("<I", body, off + 4) if off + 8 <= len(body) else (0,)

        if ((prev_ins >> 24) & 0xBF) == 0x95:
            s18 = (cw & 0x3FFFF) - (0x40000 if (cw & 0x20000) else 0)
            raw_delta = (s18 << 15) | ((prev_ins & 0x3FFF8) >> 3)
            pc_base = fn_dsp_va + (off - 4)
            for pkt_adj in range(-16, 12, 2):
                cand = (pc_base + pkt_adj + raw_delta) & 0xFFFFFFFF
                if cand in sym_lookup:
                    key = (off - 4, cand)
                    if key not in seen_calls:
                        seen_calls.add(key)
                        call_hits.append(
                            {
                                "call_off": f"0x{off - 4:04x}",
                                "target_va": f"0x{cand:08x}",
                                "symbol": sym_lookup[cand],
                            }
                        )
                    break

        imm26 = cw & 0x03FFFFFF
        for cand_d in (imm26, (imm26 << 6) | (next_ins & 0x3F)):
            for d_lo, d_hi, d_nm in data_ranges:
                if d_lo <= cand_d < d_hi:
                    data_refs.append({"off": f"0x{off:04x}", "target_va": f"0x{cand_d:08x}", "section": d_nm})

    return {
        "control_word_count": cw32_count,
        "resolved_calls": call_hits,
        "resolved_data_refs": data_refs[:64],
    }


def partition_basic_blocks(
    body: bytes, fn_sym: Dict[str, Any], all_syms: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Split a FilmGrain.cpp function body along its .LBB* labels with 100% byte coverage."""
    fn_va = fn_sym["dsp_va"]
    fn_end = fn_va + fn_sym["size"]
    shndx = fn_sym["shndx"]

    cuts: Dict[int, str] = {fn_va: "entry"}
    for s in all_syms:
        if s["shndx"] == shndx and s["name"].startswith(".LBB") and fn_va <= s["dsp_va"] < fn_end:
            cuts[s["dsp_va"]] = s["name"]

    sorted_vas = sorted(cuts.keys())
    blocks = []
    for i, start_va in enumerate(sorted_vas):
        end_va = sorted_vas[i + 1] if i + 1 < len(sorted_vas) else fn_end
        rel = start_va - fn_va
        sz = end_va - start_va
        blocks.append(
            {
                "label": cuts[start_va],
                "dsp_va": f"0x{start_va:08x}",
                "rel_off": f"0x{rel:04x}",
                "size": sz,
                "hex": body[rel : rel + sz].hex(),
            }
        )
    return blocks


def decode_arm64_logical_imm32(ins: int) -> Optional[int]:
    """Decode 32-bit ORR Wd, WZR, #imm (0x320003e0 mask) logical bitmask immediate."""
    # ORR Wd, Wn, #imm: [31]=0, [30:23]=01100100 -> top 10 bits 0x320..0x323 with N=0
    if (ins & 0xFFC003E0) != 0x320003E0:
        return None
    imms = (ins >> 10) & 0x3F
    immr = (ins >> 16) & 0x3F
    # Determine element size (2, 4, 8, 16, 32) from highest 0 bit in (~imms & 0x3f)
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


def extract_enclosing_arm64_fn(data: bytes, hit_va: int, max_back: int = 2560, max_fwd: int = 2560) -> Dict[str, Any]:
    """Find approximate enclosing ARM64 function bounds around hit_va from raw .text."""
    hit_off = va_to_outer_file(data, hit_va)
    if hit_off is None:
        return {"present": False}

    start_off = hit_off
    min_off = max(0, hit_off - max_back)
    for off in range(hit_off, min_off - 4, -4):
        (ins,) = struct.unpack_from("<I", data, off)
        # PACIASP (0xd503233f) or previous function's RET/AUTIASP+RET/B tail
        if ins == 0xD503233F:
            start_off = off
            break
        if off < hit_off and ins in (0xD65F03C0, 0xD65F0FFF):
            start_off = off + 4
            break
        # STP X29, X30, [SP, #-imm]! (pre-index) or SUB SP, SP, #imm followed by STP
        if (ins & 0xFFC07FFF) == 0xA9807BFD:
            start_off = off
            break
    else:
        start_off = min_off

    end_limit = min(len(data), hit_off + max_fwd)
    end_off = end_limit
    max_br_off = hit_off
    for off in range(start_off, end_limit, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        # Track forward intra-function branches (B.cond / CBZ / CBNZ / TBZ / TBNZ)
        if (ins & 0xFF000010) == 0x54000000 or (ins & 0x7E000000) == 0x34000000:
            s19 = (ins >> 5) & 0x7FFFF
            if s19 & (1 << 18):
                s19 -= 1 << 19
            tgt_off = off + (s19 << 2)
            if off < tgt_off < end_limit:
                max_br_off = max(max_br_off, tgt_off)
        elif (ins & 0x7E000000) == 0x36000000:
            s14 = (ins >> 5) & 0x3FFF
            if s14 & (1 << 13):
                s14 -= 1 << 14
            tgt_off = off + (s14 << 2)
            if off < tgt_off < end_limit:
                max_br_off = max(max_br_off, tgt_off)
        elif off >= hit_off and off >= max_br_off and ins in (0xD65F03C0, 0xD65F0FFF):
            end_off = off + 4
            break

    blob = data[start_off:end_off]
    return {
        "approx_start_va": f"0x{outer_file_to_va(data, start_off):08x}",
        "approx_end_va": f"0x{outer_file_to_va(data, end_off):08x}",
        "size_bytes": len(blob),
        "hex": blob.hex(),
    }


def decode_marshaller_case_slice(data: bytes, case_va: int) -> Dict[str, Any]:
    """Extract ARM64 instructions at an RPC slot pointer-translation case and decode struct offsets."""
    off = va_to_outer_file(data, case_va)
    if off is None or off + 4 > len(data):
        return {"case_va": f"0x{case_va:08x}", "present": False}

    ins_list = []
    mem_ops = []
    for k in range(36):
        cur = off + 4 * k
        if cur + 4 > len(data):
            break
        (ins,) = struct.unpack_from("<I", data, cur)
        pc = case_va + 4 * k
        ins_list.append(f"{ins:08x}")
        # 64-bit LDR/STR Xt, [Xn, #imm]
        if (ins & 0xFFC00000) in (0xF9400000, 0xF9000000):
            op = "ldr64" if (ins & 0xFFC00000) == 0xF9400000 else "str64"
            rn = (ins >> 5) & 0x1F
            rt = ins & 0x1F
            imm = ((ins >> 10) & 0xFFF) * 8
            mem_ops.append(f"0x{pc:08x}: {op} x{rt}, [x{rn}, #0x{imm:x}]")
        # 32-bit LDR/STR Wt, [Xn, #imm]
        elif (ins & 0xFFC00000) in (0xB9400000, 0xB9000000):
            op = "ldr32" if (ins & 0xFFC00000) == 0xB9400000 else "str32"
            rn = (ins >> 5) & 0x1F
            rt = ins & 0x1F
            imm = ((ins >> 10) & 0xFFF) * 4
            mem_ops.append(f"0x{pc:08x}: {op} w{rt}, [x{rn}, #0x{imm:x}]")
        elif (ins >> 26) == 0b100101:
            imm26 = ins & 0x3FFFFFF
            if imm26 & (1 << 25):
                imm26 -= 1 << 26
            mem_ops.append(f"0x{pc:08x}: bl 0x{pc + (imm26 << 2):08x}")
        # Stop at unconditional B or RET
        if (ins >> 26) == 0b000101 or ins in (0xD65F03C0, 0xD65F0FFF):
            if (ins >> 26) == 0b000101:
                imm26 = ins & 0x3FFFFFF
                if imm26 & (1 << 25):
                    imm26 -= 1 << 26
                mem_ops.append(f"0x{pc:08x}: b 0x{pc + (imm26 << 2):08x}")
            break

    return {
        "case_va": f"0x{case_va:08x}",
        "ins_count": len(ins_list),
        "hex": "".join(ins_list),
        "decoded_ops": mem_ops,
    }


def scan_arm64_xm6_rpc_bridge(
    data: bytes, xm6_elf0_va: int, target_slots: Tuple[int, ...] = (39, 40)
) -> Dict[str, Any]:
    """Locate ARM64 DSPRMFL host driver, slot 39/40 marshaller cases, and external RPC callers."""
    limit = min(len(data) - 16, 0x1520000)
    lo16 = xm6_elf0_va & 0xFFFF
    hi16 = (xm6_elf0_va >> 16) & 0xFFFF
    loader_hits = []

    for off in range(0, limit, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        if (ins & 0xFFE00000) == 0xD2800000 and ((ins >> 5) & 0xFFFF) == lo16:
            reg = ins & 0x1F
            for k in range(1, 5):
                (i2,) = struct.unpack_from("<I", data, off + 4 * k)
                if i2 == (0xF2A00000 | (hi16 << 5) | reg):
                    loader_hits.append(outer_file_to_va(data, off))
                    break

    if not loader_hits:
        return {"present": False, "xm6_elf0_va": f"0x{xm6_elf0_va:08x}"}

    loader_pc = loader_hits[0]
    drv_lo = loader_pc - 0x1E00
    drv_hi = loader_pc + 0x400

    # 1) Find RPC slot marshaller jump tables inside [loader_pc - 0x2000, loader_pc + 0x200]
    marshaller_tables = []
    w_start = va_to_outer_file(data, max(ARM64_BASE_VA, loader_pc - 0x2000)) or 0
    w_end = va_to_outer_file(data, loader_pc + 0x200) or limit
    for off in range(w_start, min(w_end, limit), 4):
        (ins,) = struct.unpack_from("<I", data, off)
        if (ins & 0xFFE00000) == 0xD2800000:
            reg = ins & 0x1F
            j_lo = (ins >> 5) & 0xFFFF
            jt_va = None
            has_br = False
            max_slot_cmp = None
            for back in range(1, 6):
                if off - 4 * back >= 0:
                    (pb,) = struct.unpack_from("<I", data, off - 4 * back)
                    if (pb & 0xFFC0001F) == 0x7100001F:  # CMP Wn, #imm12
                        max_slot_cmp = (pb >> 10) & 0xFFF
                        break
            for k in range(1, 8):
                (ik,) = struct.unpack_from("<I", data, off + 4 * k)
                if (ik & 0xFFE0001F) == (0xF2A00000 | reg):
                    jt_va = (((ik >> 5) & 0xFFFF) << 16) | j_lo
                if (ik & 0xFFFFFC1F) == 0xD61F0000 and jt_va is not None:
                    has_br = True
                    break
            if jt_va is not None and has_br:
                jt_off = va_to_outer_file(data, jt_va)
                if jt_off is not None and jt_off + 8 * 42 <= len(data):
                    n_entries = (max_slot_cmp + 1) if (max_slot_cmp is not None and 8 <= max_slot_cmp <= 128) else 44
                    n_entries = min(n_entries, (len(data) - jt_off) // 8)
                    ptrs = struct.unpack_from(f"<{n_entries}Q", data, jt_off)
                    slot_cases = {}
                    for sl in set(target_slots) | {12, 13, 33}:
                        if sl < len(ptrs) and 0x40160000 <= ptrs[sl] < 0x41680000:
                            slot_cases[f"slot_{sl}"] = decode_marshaller_case_slice(data, ptrs[sl])
                    marshaller_tables.append(
                        {
                            "dispatch_site_va": f"0x{outer_file_to_va(data, off):08x}",
                            "jump_table_va": f"0x{jt_va:08x}",
                            "max_slot_cmp": max_slot_cmp,
                            "selected_slot_cases": slot_cases,
                        }
                    )

    # 2) Find ALL external BL callers into [drv_lo, drv_hi] and check for slot immediates
    ext_callers_by_api: Dict[int, List[Dict[str, Any]]] = {}
    matched_target_slot_callers: List[Dict[str, Any]] = []
    seen_rpc_fn_starts: Set[str] = set()
    target_set = set(target_slots)

    for off in range(0, limit, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        if (ins >> 26) != 0b100101:
            continue
        pc = outer_file_to_va(data, off)
        if drv_lo <= pc < drv_hi:
            continue
        imm26 = ins & 0x3FFFFFF
        if imm26 & (1 << 25):
            imm26 -= 1 << 26
        tgt = pc + (imm26 << 2)
        if not (drv_lo <= tgt < drv_hi):
            continue

        # Scan [-52..+8] instructions around BL for small slot immediates (0..63)
        nearby_imms = []
        hit_target_slot = False
        for k in range(-52, 9):
            p2 = off + 4 * k
            if p2 < 0 or p2 + 4 > limit or k == 0:
                continue
            (pi,) = struct.unpack_from("<I", data, p2)
            # MOVZ Wd/Xd, #imm16 (shift=0)
            if (pi & 0x7FE00000) == 0x52800000:
                imm = (pi >> 5) & 0xFFFF
                rd = pi & 0x1F
                if imm < 64:
                    nearby_imms.append(f"{k:+d}:movz_w{rd}={imm}(0x{imm:x})")
                    if imm in target_set:
                        hit_target_slot = True
            # ORR Wd, WZR, #bitmask
            else:
                bm = decode_arm64_logical_imm32(pi)
                if bm is not None and bm < 64:
                    rd = pi & 0x1F
                    nearby_imms.append(f"{k:+d}:orr_w{rd}={bm}(0x{bm:x})")
                    if bm in target_set:
                        hit_target_slot = True
                # ADD Wd, Wn, #imm12 (e.g. base_slot + core_id)
                elif (pi & 0xFFC00000) == 0x11000000:
                    imm12 = (pi >> 10) & 0xFFF
                    rd = pi & 0x1F
                    rn = (pi >> 5) & 0x1F
                    if rn != 31 and rd != 31 and imm12 in target_set:
                        nearby_imms.append(f"{k:+d}:add_w{rd}_#{imm12}")
                        hit_target_slot = True

        entry_rec: Dict[str, Any] = {
            "caller_bl_va": f"0x{pc:08x}",
            "callee_api_va": f"0x{tgt:08x}",
            "nearby_small_imms": nearby_imms[:12],
            "matches_target_slot": hit_target_slot,
        }
        ext_callers_by_api.setdefault(tgt, []).append(entry_rec)
        if hit_target_slot:
            fn_blob = extract_enclosing_arm64_fn(data, pc)
            st_va = fn_blob.get("approx_start_va", "")
            rec_full = dict(entry_rec)
            if st_va and st_va not in seen_rpc_fn_starts:
                seen_rpc_fn_starts.add(st_va)
                rec_full["enclosing_arm64_fn"] = fn_blob
            matched_target_slot_callers.append(rec_full)

    api_summary = {
        f"0x{api:08x}": {"external_caller_count": len(lst), "callers": lst}
        for api, lst in sorted(ext_callers_by_api.items())
    }

    return {
        "present": True,
        "xm6_elf0_va": f"0x{xm6_elf0_va:08x}",
        "elf_loader_pc": f"0x{loader_pc:08x}",
        "driver_window": [f"0x{drv_lo:08x}", f"0x{drv_hi:08x}"],
        "marshaller_jump_tables": marshaller_tables,
        "matched_slot_39_40_callers": matched_target_slot_callers,
        "dsp_driver_external_apis": api_summary,
    }


def scan_arm64_7c_80_pairs(data: bytes) -> List[Dict[str, Any]]:
    """Find ARM64 code sites accessing BOTH [xBase, #0x7c] and [xBase, #0x80] within 32 instructions."""
    limit = min(len(data) - 4, 0x1520000)
    hits_7c: List[Tuple[int, int, str]] = []
    for off in range(0, limit, 4):
        (ins,) = struct.unpack_from("<I", data, off)
        rn = (ins >> 5) & 0x1F
        if rn == 31:
            continue
        if (ins & 0xFFFFFC00) in (0x3941F000, 0x39C1F000, 0x3981F000):
            hits_7c.append((off, rn, "ldrb/ldrsb +0x7c"))
        elif (ins & 0xFFFFFC00) == 0x3901F000:
            hits_7c.append((off, rn, "strb +0x7c"))

    paired = []
    seen_fn_starts: Set[str] = set()
    for off, rn, kind7c in hits_7c:
        win_start = max(0, off - 32 * 4)
        win_end = min(limit, off + 32 * 4)
        for o2 in range(win_start, win_end, 4):
            if o2 == off:
                continue
            (i2,) = struct.unpack_from("<I", data, o2)
            if ((i2 >> 5) & 0x1F) != rn:
                continue
            if (i2 & 0xFFFFFC00) in (0xB9408000, 0xB9008000, 0xB9808000):
                # Reject if a RET separates off and o2 (adjacent tiny getter stubs)
                lo_o, hi_o = min(off, o2), max(off, o2)
                if any(
                    struct.unpack_from("<I", data, mid)[0] in (0xD65F03C0, 0xD65F0FFF)
                    for mid in range(lo_o + 4, hi_o, 4)
                ):
                    continue
                kind80 = "str +0x80" if (i2 & 0xFFFFFC00) == 0xB9008000 else "ldr +0x80"
                va7c = outer_file_to_va(data, off)
                rec: Dict[str, Any] = {
                    "va_7c": f"0x{va7c:08x}",
                    "va_80": f"0x{outer_file_to_va(data, o2):08x}",
                    "base_reg": f"x{rn}",
                    "ops": f"{kind7c} & {kind80}",
                }
                # Skip dumping raw bytes for already-documented XMP writer 0x410d9d8c..0x410db48c
                if not (0x410D9D8C <= va7c < 0x410DB48C) and len(seen_fn_starts) < 6:
                    fn_blob = extract_enclosing_arm64_fn(data, va7c)
                    st_key = fn_blob.get("approx_start_va", "")
                    if st_key and st_key not in seen_fn_starts:
                        seen_fn_starts.add(st_key)
                        rec["enclosing_arm64_fn"] = fn_blob
                paired.append(rec)
                break
    return paired


def scan_factory_table_base(data: bytes, mid_rom_va: int = 0x44A515C0, mid_ram_va: int = 0xA029825C0) -> Dict[str, Any]:
    """Walk backwards from 0x44a515c0 to find entry[0] of the Ptp*.c factory array and its ARM64 callers."""
    off = va_to_outer_file(data, mid_rom_va)
    if off is None or off + 8 > len(data):
        return {"present": False}

    def is_code_ptr(val: int) -> bool:
        return 0x40168000 <= val < 0x41650000 and (val & 3) == 0

    (sample_ptr,) = struct.unpack_from("<Q", data, off)
    if not is_code_ptr(sample_ptr):
        return {"present": False, "value_at_mid_va": f"0x{sample_ptr:016x}"}

    cur_off = off
    steps_back = 0
    while cur_off >= 8:
        (prev_ptr,) = struct.unpack_from("<Q", data, cur_off - 8)
        if not is_code_ptr(prev_ptr):
            break
        cur_off -= 8
        steps_back += 1

    cur_end = off
    steps_fwd = 0
    while cur_end + 8 <= len(data):
        (nxt_ptr,) = struct.unpack_from("<Q", data, cur_end)
        if not is_code_ptr(nxt_ptr):
            break
        cur_end += 8
        steps_fwd += 1

    rom_base = mid_rom_va - steps_back * 8
    ram_base = mid_ram_va - steps_back * 8
    total_entries = steps_back + steps_fwd

    limit = min(len(data) - 16, 0x1520000)
    refs = []
    for target_label, target_addr in (("rom_base", rom_base), ("ram_base", ram_base)):
        lo16 = target_addr & 0xFFFF
        mid16 = (target_addr >> 16) & 0xFFFF
        movz_w = 0xD2800000 | (lo16 << 5)
        movk_w = 0xF2A00000 | (mid16 << 5)
        for pc_off in range(0, limit, 4):
            (ins,) = struct.unpack_from("<I", data, pc_off)
            if (ins & 0xFFFFFFE0) == movz_w:
                reg = ins & 0x1F
                for k in range(1, 6):
                    (i2,) = struct.unpack_from("<I", data, pc_off + 4 * k)
                    if i2 == (movk_w | reg):
                        refs.append(
                            {
                                "kind": f"MOVZ+MOVK -> {target_label}",
                                "caller_va": f"0x{outer_file_to_va(data, pc_off):08x}",
                                "target": f"0x{target_addr:x}",
                            }
                        )
            elif target_addr < 0x100000000 and (ins & 0x9F000000) == 0x90000000:
                pc = outer_file_to_va(data, pc_off)
                immlo = (ins >> 29) & 3
                immhi = (ins >> 5) & 0x7FFFF
                imm = (immhi << 2) | immlo
                if imm & (1 << 20):
                    imm -= 1 << 21
                page = (pc & ~0xFFF) + (imm << 12)
                if page == (target_addr & ~0xFFF):
                    reg = ins & 0x1F
                    (i2,) = struct.unpack_from("<I", data, pc_off + 4)
                    if (i2 & 0xFFC003E0) == (0x91000000 | (reg << 5)):
                        if page + ((i2 >> 10) & 0xFFF) == target_addr:
                            refs.append(
                                {
                                    "kind": f"ADRP+ADD -> {target_label}",
                                    "caller_va": f"0x{pc:08x}",
                                    "target": f"0x{target_addr:x}",
                                }
                            )

    return {
        "present": True,
        "mid_rom_va_index": steps_back,
        "true_rom_base": f"0x{rom_base:08x}",
        "true_ram_base": f"0x{ram_base:09x}",
        "total_pointer_entries": total_entries,
        "arm64_code_refs_to_true_base": refs,
    }


def enrich_from_ghidra_sqlite(
    db_path: str, query_vas: List[Tuple[str, int]]
) -> Dict[str, Any]:
    """Optional read-only lookup in shared Ghidra SQLite index (silently skipped if unavailable)."""
    if not db_path or not os.path.isfile(db_path):
        return {"available": False, "reason": "sqlite file not present at path"}

    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro&immutable=1"
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return {"available": False, "reason": str(exc)}

    functions_out: Dict[str, Any] = {}
    va_to_entry: List[Dict[str, Any]] = []
    try:
        for role, va in query_vas:
            va_hex = f"{va:08x}"
            row = db.execute(
                "SELECT entry, name, body_bytes, ranges, c_text FROM functions "
                "WHERE entry <= ? ORDER BY entry DESC LIMIT 1",
                (va_hex,),
            ).fetchone()
            if not row:
                continue
            entry = row["entry"]
            va_to_entry.append({"role": role, "query_va": f"0x{va:08x}", "function_entry": f"0x{entry}"})
            if entry in functions_out or len(functions_out) >= 18:
                continue
            if entry == "410d9d8c":
                functions_out[entry] = {
                    "name": row["name"],
                    "body_bytes": row["body_bytes"],
                    "ranges": row["ranges"],
                    "note": "XMP writer already documented in docs/04; c_text omitted for brevity",
                }
            else:
                c_txt = row["c_text"] or ""
                functions_out[entry] = {
                    "name": row["name"],
                    "body_bytes": row["body_bytes"],
                    "ranges": row["ranges"],
                    "c_text": c_txt[:16000],
                    "c_text_truncated": len(c_txt) > 16000,
                }
    except sqlite3.Error as exc:
        db.close()
        return {"available": False, "reason": f"query error: {exc}"}

    db.close()
    return {
        "available": True,
        "resolved_sites": va_to_entry,
        "decompiled_functions": functions_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract FilmGrain DSP basic blocks, constants & ARM64 RPC bridge.")
    ap.add_argument("firmware", help="Path to decrypted EG module (e.g. zf-full-eg-analysis-20261025.bin)")
    ap.add_argument("--tu", default="FilmGrain", help="Target TU/symbol substring (default: FilmGrain)")
    ap.add_argument("--db", default=DEFAULT_GHIDRA_SQLITE, help="Optional read-only Ghidra SQLite index path")
    ap.add_argument("--no-db", action="store_true", help="Skip optional Ghidra SQLite lookup")
    ap.add_argument("--json", default="zf_filmgrain_details.json", help="Output JSON receipt path")
    args = ap.parse_args()

    with open(args.firmware, "rb") as fh:
        data = fh.read()

    elf_off, secs, syms = find_first_xm6_elf(data)
    xm6_elf0_va = outer_file_to_va(data, elf_off)

    sym_by_va = {
        s["dsp_va"]: s
        for s in syms
        if s["dsp_va"] != 0 and s["stt"] in (1, 2) and not s["name"].startswith(".L")
    }
    sym_lookup = {va: s["name"] for va, s in sym_by_va.items()}
    data_ranges = [
        (s["dsp_va"], s["dsp_va"] + s["size"], s["name"])
        for s in secs
        if s["name"] in (".data.internal", ".bss", ".data.external") and s["size"] > 0
    ]

    kw = args.tu.lower()
    fg_syms = [s for s in syms if kw in s["tu"].lower() or kw in s["name"].lower()]
    bb_lookup = {s["dsp_va"]: s["name"] for s in syms if s["name"].startswith(".LBB")}

    # 1. Constant pools & switch tables for target TU
    data_sec = next((s for s in secs if s["name"] == ".data.internal"), None)
    const_pools: Dict[str, Any] = {}
    switch_tables: Dict[str, Any] = {}

    if data_sec is not None:
        def read_dsp_data(dsp_va: int, length: int) -> bytes:
            off = data_sec["module_offset"] + (dsp_va - data_sec["dsp_va"])
            return data[off : off + length]

        lcpi_syms = sorted(
            [s for s in fg_syms if s["name"].startswith(".LCPI")], key=lambda x: x["dsp_va"]
        )
        if lcpi_syms:
            first_va = lcpi_syms[0]["dsp_va"]
            if first_va - 64 >= data_sec["dsp_va"]:
                const_pools["pre_first_LCPI_64B"] = decode_const_block(
                    read_dsp_data(first_va - 64, 64), first_va - 64
                )
            for i, s in enumerate(lcpi_syms):
                next_va = lcpi_syms[i + 1]["dsp_va"] if i + 1 < len(lcpi_syms) else (s["dsp_va"] + 32)
                span = max(16, min(64, next_va - s["dsp_va"]))
                const_pools[s["name"]] = decode_const_block(read_dsp_data(s["dsp_va"], span), s["dsp_va"])
            last_end = lcpi_syms[-1]["dsp_va"] + 32
            if last_end + 64 <= data_sec["dsp_va"] + data_sec["size"]:
                const_pools["post_last_LCPI_64B"] = decode_const_block(
                    read_dsp_data(last_end, 64), last_end
                )

        main_fn_va = next(
            (s["dsp_va"] for s in fg_syms if s["stt"] == 2 and s["stb"] == 1),
            0x418761E0,
        )
        for s in fg_syms:
            if ".Lswitch.table" in s["name"] and s["size"] > 0:
                raw_jt = read_dsp_data(s["dsp_va"], s["size"])
                targets = struct.unpack(f"<{len(raw_jt) // 4}I", raw_jt)
                switch_tables[s["name"]] = {
                    "dsp_va": f"0x{s['dsp_va']:08x}",
                    "entries": [
                        {
                            "case_index": idx,
                            "target_dsp_va": f"0x{t:08x}",
                            "rel_off_in_fn": f"+0x{t - main_fn_va:04x}",
                            "label": bb_lookup.get(t, f"+0x{t - main_fn_va:04x}"),
                        }
                        for idx, t in enumerate(targets)
                    ],
                }

    # 2. Target TU functions ONLY (basic-block partitioned) + tiny external callee stubs
    fg_fn_vas = {s["dsp_va"] for s in fg_syms if s["stt"] == 2 and s["size"] > 0}
    fn_details: Dict[str, Any] = {}
    ext_callee_vas: List[int] = []

    for s in sorted(
        [x for x in fg_syms if x["stt"] == 2 and x["module_offset"] is not None and x["size"] > 0],
        key=lambda x: x["dsp_va"],
    ):
        body = data[s["module_offset"] : s["module_offset"] + s["size"]]
        scan_res = scan_xm6_cw_and_calls(body, s["dsp_va"], sym_lookup, data_ranges)
        blocks = partition_basic_blocks(body, s, syms)
        fn_details[s["name"]] = {
            "dsp_va": f"0x{s['dsp_va']:08x}",
            "size_bytes": s["size"],
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "basic_block_count": len(blocks),
            "scan": scan_res,
            "basic_blocks": blocks,
        }
        for c in scan_res["resolved_calls"]:
            cva = int(c["target_va"], 16)
            if cva not in fg_fn_vas and cva not in ext_callee_vas:
                ext_callee_vas.append(cva)

    tiny_external_callees: Dict[str, Any] = {}
    for cva in ext_callee_vas[:8]:
        cs = sym_by_va.get(cva)
        if cs and cs["module_offset"] is not None and 0 < cs["size"] <= 256:
            cbody = data[cs["module_offset"] : cs["module_offset"] + cs["size"]]
            tiny_external_callees[cs["name"]] = {
                "dsp_va": f"0x{cva:08x}",
                "size_bytes": cs["size"],
                "hex": cbody.hex(),
            }

    fgsf_meta = [
        {"name": s["name"], "stt": s["stt"], "dsp_va": f"0x{s['dsp_va']:08x}", "size": s["size"]}
        for s in syms
        if s["stt"] in (1, 2, 4) and ("fgsf" in s["tu"].lower() or "fgsf" in s["name"].lower())
    ]

    # 3. ARM64 <-> XM6 RPC bridge, +0x7c/+0x80 pairs, and 0x44a515c0 factory table
    rpc_bridge = scan_arm64_xm6_rpc_bridge(data, xm6_elf0_va, target_slots=(39, 40))
    arm64_7c_80 = scan_arm64_7c_80_pairs(data)
    factory_info = scan_factory_table_base(data)

    # 4. Optional Ghidra SQLite enrichment
    query_vas: List[Tuple[str, int]] = []
    for c in rpc_bridge.get("matched_slot_39_40_callers", []):
        query_vas.append(("rpc_slot_39_40_caller", int(c["caller_bl_va"], 16)))
    if not rpc_bridge.get("matched_slot_39_40_callers"):
        # Fallback: include external callers of the main RPC submit APIs (<= 10)
        for api_va_s, api_info in rpc_bridge.get("dsp_driver_external_apis", {}).items():
            for c in api_info.get("callers", [])[:8]:
                if not c["nearby_small_imms"]:
                    query_vas.append((f"unlabelled_rpc_caller->{api_va_s}", int(c["caller_bl_va"], 16)))
    for mt in rpc_bridge.get("marshaller_jump_tables", []):
        query_vas.append(("rpc_marshaller", int(mt["dispatch_site_va"], 16)))
    for p in arm64_7c_80:
        query_vas.append(("struct_7c_80_pair", int(p["va_7c"], 16)))
    for ref in factory_info.get("arm64_code_refs_to_true_base", []):
        query_vas.append(("factory_table_caller", int(ref["caller_va"], 16)))

    sqlite_info = (
        {"available": False, "reason": "disabled via --no-db"}
        if args.no_db
        else enrich_from_ghidra_sqlite(args.db, query_vas)
    )

    receipt = {
        "schema": "zf-c301-filmgrain-details-receipt-v2",
        "firmware_bytes": len(data),
        "firmware_sha256": hashlib.sha256(data).hexdigest(),
        "xm6_elf0_offset": f"0x{elf_off:x}",
        "xm6_elf0_arm64_va": f"0x{xm6_elf0_va:08x}",
        "xm6_sections": [
            {"index": s["index"], "name": s["name"], "dsp_va": f"0x{s['dsp_va']:08x}", "size": s["size"]}
            for s in secs
        ],
        "filmgrain_constant_pools": const_pools,
        "filmgrain_switch_tables": switch_tables,
        "filmgrain_functions": fn_details,
        "filmgrain_tiny_external_callees": tiny_external_callees,
        "adjacent_fgsf_symbols": fgsf_meta,
        "arm64_xm6_rpc_bridge": rpc_bridge,
        "arm64_struct_7c_80_access_pairs": arm64_7c_80,
        "arm64_factory_table_0x44a515c0": factory_info,
        "optional_ghidra_sqlite": sqlite_info,
    }

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    out_sz = os.path.getsize(args.json)
    total_dsp_fn_bytes = sum(v["size_bytes"] for v in fn_details.values())
    print(f"Wrote JSON receipt -> {args.json} ({out_sz:,} bytes)")
    print(f"  Constant pools decoded   : {len(const_pools)}")
    print(f"  Switch tables decoded    : {len(switch_tables)}")
    print(f"  DSP functions captured   : {len(fn_details)} ({total_dsp_fn_bytes:,} B total)")
    print(f"  ARM64 RPC slot39/40 hits : {len(rpc_bridge.get('matched_slot_39_40_callers', []))}")
    print(f"  ARM64 +0x7c/+0x80 hits   : {len(arm64_7c_80)}")
    print(f"  Ghidra SQLite attached   : {sqlite_info.get('available')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
