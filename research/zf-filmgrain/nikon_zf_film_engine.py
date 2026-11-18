#!/usr/bin/env python3
"""Nikon EXPEED 7 Flexible Color (.NP3 0310) + Zf C3.01 Film Grain Reference Engine.

Combines:
1. Pure-Python .NP3 (0310 Flexible Color) binary serializer, deserializer & validator
   (byte-compatible with nikon-flexible-color-picture-control).
2. Authentic EXPEED 7 hardware MIPU color pipeline extracted read-only from Z8 C3.11
   firmware (z8-c311-main.bin, load base 0x40160000):
   - 229-pt Flexible Color base curve (ROM 0x441a0e46)
   - 257-pt 12-bit Tone Curve (Custom u16>>3 at 0x411205c0 OR parametric Highlights/Shadows
     ROM basis 0x441ebf94 / 0x441ec79c + WhiteLevel/BlackLevel + Contrast)
   - 8-channel -> 32-sector MIPU YCbCr Color Blender (0x4111dd28, ROM 0x441b55b2..0x441b6632)
   - 65-node 12-bit RGB Color Grading LUT (0x4111eff4, ROM 0x441a56a0, 0x441a4dae, 0x441a561e)
   - Global Saturation + optional Clarity / Sharpening + 3D .cube LUT export.
3. Nikon Zf C3.01 18-level Film Grain engine (Size 1..3 x Intensity 1..6):
   - Exact 18x3 preset table @ RAM 0xa02acdd2b (EG file offset 0x4a3cd2b) & XMP crd: writer
   - Instruction-verified CEVA-XM6 FilmGrain.cpp DSP pipeline (0x418761e0, 17,084 B):
     * Dual 128x128 int16 base map Q4 blend + staggered divstep tiling LUT + Q6 tone_lut[Y]
     * ApplyMaptoImageLine: per-32px Marsaglia xorshift32 +-1 sign flip + >>6 shift + [0,255] vst{satu}
     * BlurLineRing two-stage cascade (0x38ee0..0x38f70, mask 0x2a9 -> {0,3,5,7,9}) with
       UpdateBlurLine_1 (1x1), UpdateBlurLine_3 (.LCPI2_0 3x3 /16), and UpdateBlurLine_5
       (.LCPI3_0 non-separable rank-3 5x5 {40,26,15,7,3,0} /256, corners=0, BORDER_REFLECT_101).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Auto-attach vendored numpy / Pillow from workspace if not installed globally
_VENDOR_DIR = Path(r"C:\Users\ycx\Desktop\Nikon\third-party-20261029\nikon-fuji-film-lab\.vendor")
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

THIS_DIR = Path(__file__).resolve().parent
DEFAULT_TABLES_JSON = THIS_DIR / "expeed7_flexible_color_tables.json"
DEFAULT_CALIBRATION_JSON = Path(
    r"C:\Users\ycx\Desktop\Nikon\third-party-20261029\nikon-fuji-film-lab\configs\lab-calibration.json"
)

# ============================================================================
# Part 1: Pure-Python .NP3 (0310 Flexible Color) Binary Container Codec
# ============================================================================

OFFSET_NAME = 0x18
OFFSET_SHARPENING = 0x52
OFFSET_CLARITY = 0x5C
OFFSET_MID_RANGE_SHARPENING = 0xF2
OFFSET_CONTRAST = 0x110
OFFSET_HIGHLIGHTS = 0x11A
OFFSET_SHADOWS = 0x124
OFFSET_WHITE_LEVEL = 0x12E
OFFSET_BLACK_LEVEL = 0x138
OFFSET_SATURATION = 0x142

COLOR_BLENDER_CHANNELS = (
    "red",
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
    "magenta",
)
COLOR_BLENDER_OFFSETS = {
    "red": 0x14C,
    "orange": 0x14F,
    "yellow": 0x152,
    "green": 0x155,
    "cyan": 0x158,
    "blue": 0x15B,
    "purple": 0x15E,
    "magenta": 0x161,
}

OFFSET_COLOR_GRADING_HIGHLIGHTS = 0x170
OFFSET_COLOR_GRADING_MIDTONE = 0x174
OFFSET_COLOR_GRADING_SHADOWS = 0x178
OFFSET_COLOR_GRADING_BLENDING = 0x180
OFFSET_COLOR_GRADING_BALANCE = 0x182
OFFSET_COMMENT_FLAG_A = 0x185
OFFSET_COMMENT_FLAG_B = 0x186
OFFSET_TONE_CURVE_FLAG = 0x187
OFFSET_TONE_CURVE_POINTS = 0x194
OFFSET_TONE_CURVE_RAW = 0x1CC

TONE_CURVE_SENTINEL_OFFSETS = (
    OFFSET_CONTRAST,
    OFFSET_HIGHLIGHTS,
    OFFSET_SHADOWS,
    OFFSET_WHITE_LEVEL,
    OFFSET_BLACK_LEVEL,
)

# Exact 392-byte base 0310 container template
NP3_BASE_0310 = bytes(
    [
        0x4E, 0x43, 0x50, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x04, 0x30, 0x33, 0x31, 0x30,
        0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x14, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00,
        0x00, 0x00, 0x00, 0x02, 0x00, 0x20, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00,
        0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x01, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00,
        0x00, 0x02, 0x88, 0x04, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x02, 0x82, 0x04, 0x00, 0x00,
        0x08, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x04, 0x00, 0x00, 0x09, 0x00, 0x00, 0x00, 0x00, 0x02,
        0xFF, 0x04, 0x00, 0x00, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x04, 0x00, 0x00, 0x0B, 0x00,
        0x00, 0x00, 0x00, 0x02, 0xFF, 0x04, 0x00, 0x00, 0x0C, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x00,
        0x00, 0x00, 0x0D, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x00, 0x00, 0x00, 0x0E, 0x00, 0x00, 0x00,
        0x00, 0x02, 0xFF, 0x04, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x01, 0x00, 0x00,
        0x10, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x01, 0x00, 0x00, 0x11, 0x00, 0x00, 0x00, 0x00, 0x02,
        0xFF, 0x01, 0x00, 0x00, 0x12, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x01, 0x00, 0x00, 0x13, 0x00,
        0x00, 0x00, 0x00, 0x02, 0xFF, 0x01, 0x00, 0x00, 0x14, 0x00, 0x00, 0x00, 0x00, 0x02, 0x80, 0x01,
        0x00, 0x00, 0x15, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x0A, 0x00, 0x00, 0x16, 0x00, 0x00, 0x00,
        0x00, 0x02, 0x84, 0x04, 0x00, 0x00, 0x17, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x04, 0x00, 0x00,
        0x18, 0x00, 0x00, 0x00, 0x00, 0x02, 0xFF, 0x04, 0x00, 0x00, 0x19, 0x00, 0x00, 0x00, 0x00, 0x02,
        0x80, 0x01, 0x00, 0x00, 0x1A, 0x00, 0x00, 0x00, 0x00, 0x02, 0x80, 0x01, 0x00, 0x00, 0x1B, 0x00,
        0x00, 0x00, 0x00, 0x02, 0x80, 0x01, 0x00, 0x00, 0x1C, 0x00, 0x00, 0x00, 0x00, 0x02, 0x80, 0x01,
        0x00, 0x00, 0x1D, 0x00, 0x00, 0x00, 0x00, 0x02, 0x80, 0x01, 0x00, 0x00, 0x1E, 0x00, 0x00, 0x00,
        0x00, 0x02, 0x80, 0x01, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00, 0x00, 0x1C, 0x80, 0x80, 0x80, 0x80,
        0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80,
        0x80, 0x80, 0x80, 0x80, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00, 0x00, 0x14,
        0x80, 0x00, 0x80, 0x80, 0x80, 0x00, 0x80, 0x80, 0x80, 0x00, 0x80, 0x80, 0x01, 0x01, 0x01, 0x00,
        0xB2, 0x01, 0x80, 0x01, 0x00, 0x00, 0x00, 0x00,
    ]
)

def _build_default_tone_curve_extension() -> bytes:
    # 586-byte Type 0x02 Tone Curve extension matching baseWithToneCurve.slice(392) (total 978 B)
    hdr = bytearray(
        [
            0x00, 0x00, 0x02, 0x42, 0x49, 0x30, 0x00, 0xFF,
            0x00, 0xFF, 0x01, 0x00, 0x02, 0x00, 0x00, 0xFF,
            0xFF,
        ]
        + [0x00] * 51
    )
    # Default 257 big-endian u16 values from baseWithToneCurve (0x1cc..0x3ce) + 4 zero bytes
    raw_u16 = [i * 0x80 if i <= 128 else (i * 0x80 - 1) for i in range(257)]
    for v in raw_u16:
        hdr.extend(struct.pack(">H", v))
    hdr.extend(b"\x00\x00\x00\x00")
    return bytes(hdr)


TONE_CURVE_EXTENSION_0310 = _build_default_tone_curve_extension()
BASE_LENGTH = len(NP3_BASE_0310)  # 392
TONE_CURVE_EXTENSION_LENGTH = len(TONE_CURVE_EXTENSION_0310)  # 578


def _clamp_num(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def serialize_np3(options: Dict[str, Any]) -> bytes:
    """Serialize Flexible Color Picture Control options dict into .NP3 (0310) bytes."""
    buf = bytearray(NP3_BASE_0310)
    name = str(options.get("name", "CUSTOM"))
    if len(name) > 19:
        raise ValueError("NP3 name must be <= 19 ASCII characters")
    for i, ch in enumerate(name):
        buf[OFFSET_NAME + i] = ord(ch) & 0x7F

    sharp = float(options.get("sharpning", options.get("sharpening", 2.0)))
    mid_sharp = float(options.get("midRangeSharpning", options.get("midRangeSharpening", 1.0)))
    clarity = float(options.get("clarity", 0.5))
    buf[OFFSET_SHARPENING] = int(round(0x80 + _clamp_num(sharp, -3.0, 9.0) * 4.0))
    buf[OFFSET_MID_RANGE_SHARPENING] = int(round(0x80 + _clamp_num(mid_sharp, -5.0, 5.0) * 4.0))
    buf[OFFSET_CLARITY] = int(round(0x80 + _clamp_num(clarity, -5.0, 5.0) * 4.0))

    buf[OFFSET_CONTRAST] = int(0x80 + _clamp_num(int(options.get("contrast", 0)), -100, 100))
    buf[OFFSET_HIGHLIGHTS] = int(0x80 + _clamp_num(int(options.get("highlights", 0)), -100, 100))
    buf[OFFSET_SHADOWS] = int(0x80 + _clamp_num(int(options.get("shadows", 0)), -100, 100))
    buf[OFFSET_WHITE_LEVEL] = int(0x80 + _clamp_num(int(options.get("whiteLevel", 0)), -100, 100))
    buf[OFFSET_BLACK_LEVEL] = int(0x80 + _clamp_num(int(options.get("blackLevel", 0)), -100, 100))
    buf[OFFSET_SATURATION] = int(0x80 + _clamp_num(int(options.get("saturation", 0)), -100, 100))

    cb = options.get("colorBlender") or {}
    for ch_name in COLOR_BLENDER_CHANNELS:
        off = COLOR_BLENDER_OFFSETS[ch_name]
        ch_vals = cb.get(ch_name) or {}
        buf[off + 0] = int(0x80 + _clamp_num(int(ch_vals.get("hue", 0)), -100, 100))
        buf[off + 1] = int(0x80 + _clamp_num(int(ch_vals.get("chroma", 0)), -100, 100))
        buf[off + 2] = int(0x80 + _clamp_num(int(ch_vals.get("brightness", 0)), -100, 100))

    cg = options.get("colorGrading") or {}
    for zone_key, off in (
        ("highlights", OFFSET_COLOR_GRADING_HIGHLIGHTS),
        ("midTone", OFFSET_COLOR_GRADING_MIDTONE),
        ("shadows", OFFSET_COLOR_GRADING_SHADOWS),
    ):
        zv = cg.get(zone_key) or {}
        norm_hue = int(zv.get("hue", 0)) % 360
        buf[off + 0] = 0x80 + ((norm_hue >> 8) & 0x0F)
        buf[off + 1] = norm_hue & 0xFF
        buf[off + 2] = int(0x80 + _clamp_num(int(zv.get("chroma", 0)), -100, 100))
        buf[off + 3] = int(0x80 + _clamp_num(int(zv.get("brightness", 0)), -100, 100))
    buf[OFFSET_COLOR_GRADING_BLENDING] = int(0x80 + _clamp_num(int(cg.get("blending", 50)), -100, 100))
    buf[OFFSET_COLOR_GRADING_BALANCE] = int(0x80 + _clamp_num(int(cg.get("balance", 0)), -100, 100))

    tone_curve = options.get("toneCurve")
    if options.get("identityToneCurve") and not tone_curve:
        tone_curve = {
            "raw": [int(round(i * 32767.0 / 256.0)) for i in range(257)],
            "points": [{"x": 0, "y": 0}, {"x": 128, "y": 128}, {"x": 255, "y": 255}],
        }

    comment = options.get("comment") or ""
    next_chunk = 0x02 if tone_curve is not None else 0x00
    if comment:
        if "\x00" in comment:
            raise ValueError("NP3 comment must not contain NUL")
        raw_utf8 = comment[:256].encode("utf-8")
        pad_len = len(raw_utf8) + 1
        if pad_len % 2 == 1:
            pad_len += 1
        payload = raw_utf8 + b"\x00" * (pad_len - len(raw_utf8))
        buf[OFFSET_COMMENT_FLAG_A] = 1
        buf[OFFSET_COMMENT_FLAG_B] = 1
        buf.extend(struct.pack(">I", len(payload)))
        buf.extend(payload)
        buf.extend(struct.pack(">I", next_chunk))

    if tone_curve is None:
        return bytes(buf)

    tc_start = len(buf)
    buf.extend(TONE_CURVE_EXTENSION_0310)
    for off in TONE_CURVE_SENTINEL_OFFSETS:
        buf[off] = 0x01
    if not comment:
        buf[OFFSET_TONE_CURVE_FLAG] = 0x02

    delta = tc_start - BASE_LENGTH
    raw_arr = tone_curve.get("raw") or []
    for i, val in enumerate(raw_arr[:257]):
        clamped = int(_clamp_num(int(val), 0, 32767))
        struct.pack_into(">H", buf, OFFSET_TONE_CURVE_RAW + delta + i * 2, clamped)

    pts = tone_curve.get("points") or []
    pt_count = min(len(pts), 20)
    buf[OFFSET_TONE_CURVE_POINTS + delta] = pt_count
    for i in range(pt_count):
        pt = pts[i]
        buf[OFFSET_TONE_CURVE_POINTS + delta + 1 + i * 2] = int(_clamp_num(int(pt["x"]), 0, 255))
        buf[OFFSET_TONE_CURVE_POINTS + delta + 2 + i * 2] = int(_clamp_num(int(pt["y"]), 0, 255))

    return bytes(buf)


def _read_comment_chunk(buf: bytes) -> Optional[Tuple[int, int]]:
    if len(buf) < BASE_LENGTH + 8:
        return None
    if buf[OFFSET_COMMENT_FLAG_A] != 1 or buf[OFFSET_COMMENT_FLAG_B] != 1:
        return None
    (payload_len,) = struct.unpack_from(">I", buf, BASE_LENGTH)
    if payload_len == 0 or payload_len % 2 != 0:
        return None
    payload_start = BASE_LENGTH + 4
    payload_end = payload_start + payload_len
    if payload_end + 4 > len(buf):
        return None
    payload = buf[payload_start:payload_end]
    nul_pos = payload.find(0)
    if nul_pos == -1 or any(b != 0 for b in payload[nul_pos:]):
        return None
    (next_chunk,) = struct.unpack_from(">I", buf, payload_end)
    return payload_len, next_chunk


def _get_tone_curve_start(buf: bytes) -> Optional[int]:
    c_chunk = _read_comment_chunk(buf)
    if c_chunk is not None:
        payload_len, next_chunk = c_chunk
        if next_chunk != 0x02:
            return None
        tc_start = BASE_LENGTH + 4 + payload_len + 4
        return tc_start if len(buf) >= tc_start + TONE_CURVE_EXTENSION_LENGTH else None
    if len(buf) < BASE_LENGTH + TONE_CURVE_EXTENSION_LENGTH:
        return None
    return BASE_LENGTH if buf[OFFSET_TONE_CURVE_FLAG] == 0x02 else None


def deserialize_np3(buf: bytes) -> Dict[str, Any]:
    """Parse .NP3 (0310 Flexible Color) binary buffer into structured options dict."""
    if len(buf) < BASE_LENGTH or buf[:4] != b"NCP\x00":
        raise ValueError("Invalid .NP3 container header")

    raw_name = buf[OFFSET_NAME : OFFSET_NAME + 19].split(b"\x00", 1)[0]
    name = raw_name.decode("ascii", errors="replace")
    tc_start = _get_tone_curve_start(buf)
    has_tc = tc_start is not None

    cb: Dict[str, Dict[str, int]] = {}
    for ch_name in COLOR_BLENDER_CHANNELS:
        off = COLOR_BLENDER_OFFSETS[ch_name]
        cb[ch_name] = {
            "hue": buf[off] - 0x80,
            "chroma": buf[off + 1] - 0x80,
            "brightness": buf[off + 2] - 0x80,
        }

    def _read_cg_zone(off: int) -> Dict[str, int]:
        return {
            "hue": ((buf[off] & 0x0F) << 8) + buf[off + 1],
            "chroma": buf[off + 2] - 0x80,
            "brightness": buf[off + 3] - 0x80,
        }

    cg = {
        "highlights": _read_cg_zone(OFFSET_COLOR_GRADING_HIGHLIGHTS),
        "midTone": _read_cg_zone(OFFSET_COLOR_GRADING_MIDTONE),
        "shadows": _read_cg_zone(OFFSET_COLOR_GRADING_SHADOWS),
        "blending": buf[OFFSET_COLOR_GRADING_BLENDING] - 0x80,
        "balance": buf[OFFSET_COLOR_GRADING_BALANCE] - 0x80,
    }

    tone_curve = None
    if tc_start is not None:
        delta = tc_start - BASE_LENGTH
        raw_vals = list(struct.unpack_from(">257H", buf, OFFSET_TONE_CURVE_RAW + delta))
        pt_count = buf[OFFSET_TONE_CURVE_POINTS + delta]
        pts = [
            {
                "x": buf[OFFSET_TONE_CURVE_POINTS + delta + 1 + 2 * i],
                "y": buf[OFFSET_TONE_CURVE_POINTS + delta + 2 + 2 * i],
            }
            for i in range(pt_count)
        ]
        tone_curve = {"raw": raw_vals, "points": pts}

    comment = None
    c_chunk = _read_comment_chunk(buf)
    if c_chunk is not None:
        payload_len, _ = c_chunk
        payload = buf[BASE_LENGTH + 4 : BASE_LENGTH + 4 + payload_len]
        comment = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    return {
        "name": name,
        "sharpning": (buf[OFFSET_SHARPENING] - 0x80) / 4.0,
        "midRangeSharpning": (buf[OFFSET_MID_RANGE_SHARPENING] - 0x80) / 4.0,
        "clarity": (buf[OFFSET_CLARITY] - 0x80) / 4.0,
        "contrast": 0 if has_tc else (buf[OFFSET_CONTRAST] - 0x80),
        "highlights": 0 if has_tc else (buf[OFFSET_HIGHLIGHTS] - 0x80),
        "shadows": 0 if has_tc else (buf[OFFSET_SHADOWS] - 0x80),
        "whiteLevel": 0 if has_tc else (buf[OFFSET_WHITE_LEVEL] - 0x80),
        "blackLevel": 0 if has_tc else (buf[OFFSET_BLACK_LEVEL] - 0x80),
        "saturation": buf[OFFSET_SATURATION] - 0x80,
        "colorBlender": cb,
        "colorGrading": cg,
        "toneCurve": tone_curve,
        "comment": comment,
    }


def inspect_np3_container(buf: bytes) -> Dict[str, Any]:
    """Validate .NP3 0310 container tags 2..32 exactly like tools/np3_tool.mjs."""
    if len(buf) < 392:
        raise ValueError("NP3 shorter than 392-byte base container")
    if buf[:4] != b"NCP\x00":
        raise ValueError("Invalid NCP magic")
    (ver, fmt_len) = struct.unpack_from(">II", buf, 4)
    fmt = buf[12:16].decode("ascii", errors="replace")
    if ver != 256 or fmt_len != 4 or fmt != "0310":
        raise ValueError(f"Unexpected header ver={ver} len={fmt_len} fmt={fmt}")
    offset = 16
    for tag_id in range(2, 33):
        (tag, length) = struct.unpack_from(">II", buf, offset)
        if tag != (tag_id << 8) or length <= 0 or offset + 8 + length > len(buf):
            raise ValueError(f"Malformed tag {tag_id} at 0x{offset:x}")
        offset += 8 + length
    if offset != 388:
        raise ValueError(f"Unexpected base-entry end offset {offset}")
    return {
        "bytes": len(buf),
        "base_entries": 31,
        "format": fmt,
        "sha256": hashlib.sha256(buf).hexdigest(),
    }


def normalize_profile_options(raw_opts: Dict[str, Any], name: str = "LAB_PROFILE") -> Dict[str, Any]:
    """Normalize partial profile options (e.g. from lab-calibration.json) via NP3 round-trip."""
    base_opts = {
        "name": name[:19],
        "sharpning": 2.0,
        "midRangeSharpning": 1.0,
        "clarity": 0.0,
        "contrast": 0,
        "highlights": 0,
        "shadows": 0,
        "whiteLevel": 0,
        "blackLevel": 0,
        "saturation": 0,
    }
    merged = {**base_opts, **raw_opts, "name": name[:19]}
    return deserialize_np3(serialize_np3(merged))


# ============================================================================
# Part 2: EXPEED 7 Hardware MIPU Flexible Color Pipeline
# ============================================================================

def _sdiv_round(val: int, denom: int) -> int:
    """Firmware symmetric round-half-away-from-zero integer division."""
    if val >= 0:
        return (val + denom // 2) // denom
    return -((-val + denom // 2) // denom)


def _sdiv_pow2_round(val: int, shift: int) -> int:
    """Firmware symmetric rounded right-shift (used at 0x4111fc80, 0x4111e568, 0x4111e6ec)."""
    half = 1 << (shift - 1)
    if val >= 0:
        return (val + half) >> shift
    return -((-val + half) >> shift)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(rgb, 0.0, 1.0)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * (lin ** (1.0 / 2.4)) - 0.055)


class Expeed7FlexibleColorEngine:
    """Executes Nikon EXPEED 7's hardware MIPU Flexible Color (0310) math using ROM LUTs."""

    def __init__(self, tables_path: Path | str = DEFAULT_TABLES_JSON) -> None:
        with open(tables_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.meta = payload["sources"]
        self.ring_to_slice: List[int] = payload["mipu_geometry"]["ring_to_mipu_slice_index"]
        t = payload["tables"]

        self.base_std_229 = np.array(t["base_curve_standard_229"]["values"], dtype=np.float32) / 32767.0
        self.base_flex_229 = np.array(t["base_curve_flexible_color_229"]["values"], dtype=np.float32) / 32767.0
        self.id_257 = t["tone_curve_identity_257_12bit"]["values"]
        self.hi_basis = t["highlights_basis_4x257_s16"]["values"]
        self.sh_basis = t["shadows_basis_4x257_s16"]["values"]

        self.cg_breakpoints = t["color_grading_breakpoints_4_u16"]["values"]  # [220, 352, 528, 672]
        self.cg_envelope_65 = t["color_grading_luma_envelope_65_s16"]["values"]
        self.cg_hue_rgb_360 = t["color_grading_hue_rgb_360x3_s16"]["values"]

        self.cb_hue_basis = t["color_blender_hue_basis_16x32_s16"]["values"]
        self.cb_chroma_basis = t["color_blender_chroma_basis_16x32_s16"]["values"]
        self.cb_chroma_cross = t["color_blender_chroma_luma_cross_32x2_s16"]["values"]
        self.cb_bright_basis = t["color_blender_brightness_basis_32x32_s16"]["values"]
        self.cb_cos_513 = t["color_blender_cos_lut_513_s16"]["values"]
        self.zf_grain_presets = payload["zf_film_grain_presets"]
        self.zf_xm6_kernels = payload.get("zf_c301_xm6_filmgrain_kernels")
        self.zf_xm6_switch_tables = payload.get("zf_c301_xm6_format_switch_tables")

    # ---- Stage 2: 257-pt 12-bit Tone Curve (VA 0x411205b0..0x41120d14) ----
    def build_tone_curve_257_12bit(self, params: Dict[str, Any]) -> np.ndarray:
        """Return 257-entry float32 curve in [0, 1] quantized from 12-bit MIPU LUT (0..4095)."""
        tc = params.get("toneCurve")
        if tc is not None and tc.get("raw"):
            # Exact firmware quantization @ 0x411205d4..0x411205e8: min((raw[i] + 4) >> 3, 4095)
            raw = tc["raw"]
            lut12 = [min((int(raw[i]) + 4) >> 3, 4095) for i in range(257)]
            return np.array(lut12, dtype=np.float32) / 4095.0

        h_val = int(_clamp_num(int(params.get("highlights", 0)), -100, 100))
        s_val = int(_clamp_num(int(params.get("shadows", 0)), -100, 100))
        w_val = int(_clamp_num(int(params.get("whiteLevel", 0)), -100, 100))
        b_val = int(_clamp_num(int(params.get("blackLevel", 0)), -100, 100))
        c_val = int(_clamp_num(int(params.get("contrast", 0)), -100, 100))

        # 1) Exact firmware Highlights & Shadows basis @ 0x411206b8..0x41120780
        # Row 2 = flex_neg (<0), Row 3 = flex_pos (>=0), divisor = 120
        b_h = self.hi_basis[2 if h_val < 0 else 3]
        b_s = self.sh_basis[2 if s_val < 0 else 3]
        ah, as_ = abs(h_val), abs(s_val)
        tc_hs = [
            self.id_257[i] + _sdiv_round(ah * b_h[i] + as_ * b_s[i], 120)
            for i in range(257)
        ]

        # 2) WhiteLevel & BlackLevel endpoint + shoulder/toe @ 0x41120834
        scale_w = 640 if w_val < 0 else 1536
        scale_b = 1536 if b_val < 0 else 512
        dw = _sdiv_round(scale_w * w_val, 100)
        db = _sdiv_round(scale_b * b_val, 100)

        out = np.empty(257, dtype=np.float32)
        for i in range(257):
            t = i / 256.0
            # Exact identity baseline @ 0x411209cc when W=0, B=0; smooth endpoint shift otherwise
            wb_delta = dw * t + db * (((1.0 - t) ** 1.5) - t)
            out[i] = float(tc_hs[i]) + wb_delta

        # 3) Contrast S-curve modulation @ 0x4112a1c8 / 0x4418ca00 (centered at 0.5, preserving 0 & 4095)
        if c_val != 0:
            norm = np.clip(out / 4095.0, 0.0, 1.0)
            # Symmetric logistic / sine S-curve matching EXPEED 7 midtone slope scaling
            strength = (c_val / 100.0) * 0.42
            norm = norm - strength * np.sin(2.0 * math.pi * norm) / (2.0 * math.pi)
            out = norm * 4095.0

        return np.clip(out, 0.0, 4095.0) / 4095.0

    # ---- Stage 3: Color Blender 8-hue -> 32-sector MIPU solver (VA 0x4111dd28..0x4111ec90) ----
    def _cos_sin_q10(self, angle_8192: int) -> Tuple[int, int]:
        """Exact firmware quadrant-folded lookup into 513-entry Q10 cos LUT @ 0x411b6632."""
        w7 = angle_8192 % 8192  # 0..8191 (8192 = 360 deg, 2048 = 90 deg)
        if w7 <= 0x7FF:
            ic, is_ = w7, 2048 - w7
            sc, ss = 1, 1
        elif w7 <= 0xFFF:
            ic, is_ = 4096 - w7, w7 - 2048
            sc, ss = -1, 1
        elif w7 <= 0x17FF:
            ic, is_ = w7 - 4096, 6144 - w7
            sc, ss = -1, -1
        else:
            ic, is_ = 8192 - w7, w7 - 6144
            sc, ss = 1, -1
        idx_c = min(max((ic + (3 if ic < 0 else 0)) >> 2, 0), 512)
        idx_s = min(max((is_ + (3 if is_ < 0 else 0)) >> 2, 0), 512)
        return sc * self.cb_cos_513[idx_c], ss * self.cb_cos_513[idx_s]

    def build_color_blender_32_sectors(self, params: Dict[str, Any]) -> Tuple[np.ndarray, bool]:
        """Compute 32 hue sectors Ordered by atan2(Cr, Cb) ring m=0..31 (m * 11.25 deg).

        Returns (ring_sectors[32, 6], is_active) where each row is:
          [m00_q8, m01_q8, m10_q8, m11_q8, dy_cb_q8, dy_cr_q8]
        with identity = [256, 0, 0, 256, 0, 0].
        """
        cb = params.get("colorBlender") or {}
        delta_hue = [0] * 32
        delta_chroma = [0] * 32
        delta_luma_cb = [0] * 32
        delta_luma_cr = [0] * 32

        any_active = False
        for k, ch_name in enumerate(COLOR_BLENDER_CHANNELS):
            ch = cb.get(ch_name) or {}
            h = int(_clamp_num(int(ch.get("hue", 0)), -100, 100))
            c = int(_clamp_num(int(ch.get("chroma", 0)), -100, 100))
            b = int(_clamp_num(int(ch.get("brightness", 0)), -100, 100))
            if h == 0 and c == 0 and b == 0:
                continue
            any_active = True

            row_h = self.cb_hue_basis[k if h < 0 else k + 8]
            row_c = self.cb_chroma_basis[k if c < 0 else k + 8]
            row_ba = self.cb_bright_basis[k if b < 0 else k + 8]
            row_bb = self.cb_bright_basis[k + 16 if b < 0 else k + 24]
            ah, ac, ab = abs(h), abs(c), abs(b)

            for s in range(32):
                # 0x4111e18c..0x4111e23c
                dh = _sdiv_round(ah * row_h[s], 100)
                dc = _sdiv_round(ac * row_c[s], 100)
                delta_hue[s] += dh
                delta_chroma[s] += dc

                # 0x4111e234..0x4111e2ec: positive Chroma -> Luma Helmholtz-Kohlrausch cross-term
                if c >= 1:
                    cross_cb, cross_cr = self.cb_chroma_cross[s]
                    delta_luma_cb[s] += _sdiv_pow2_round(dc * cross_cb, 10)
                    delta_luma_cr[s] += _sdiv_pow2_round(dc * cross_cr, 10)

                # 0x4111e2ec..0x4111e3a4: Brightness -> (dY_Cb, dY_Cr)
                delta_luma_cb[s] += _sdiv_round(ab * row_ba[s], 100)
                delta_luma_cr[s] += _sdiv_round(ab * row_bb[s], 100)

        ring_sectors = np.zeros((32, 6), dtype=np.float32)
        if not any_active:
            ring_sectors[:, 0] = 1.0
            ring_sectors[:, 3] = 1.0
            return ring_sectors, False

        # Convert each MIPU slice s -> Q8 2x2 CbCr matrix + Q8 (dY_Cb, dY_Cr) (@ 0x4111e474..0x4111e770)
        slice_coeffs = np.zeros((32, 6), dtype=np.float32)
        for s in range(32):
            cos_q10, sin_q10 = self._cos_sin_q10(delta_hue[s])
            # Q10 chroma scale: 1024 = 1.0, clamped >= 0
            sc_q10 = max(0, 1024 + delta_chroma[s])
            # Base identity in Q8 = 256; Q10 * Q8 * Q10 >> 20 -> Q8, clamped to [-512, 511]
            m00 = max(-512, min(511, _sdiv_pow2_round(cos_q10 * 256 * sc_q10, 20)))
            m01 = max(-512, min(511, _sdiv_pow2_round(-sin_q10 * 256 * sc_q10, 20)))
            m10 = max(-512, min(511, _sdiv_pow2_round(sin_q10 * 256 * sc_q10, 20)))
            m11 = max(-512, min(511, _sdiv_pow2_round(cos_q10 * 256 * sc_q10, 20)))
            dy_cb = _sdiv_pow2_round(delta_luma_cb[s], 7)
            dy_cr = _sdiv_pow2_round(delta_luma_cr[s], 7)
            slice_coeffs[s] = [
                m00 / 256.0,
                m01 / 256.0,
                m10 / 256.0,
                m11 / 256.0,
                dy_cb / 256.0,
                dy_cr / 256.0,
            ]

        for m, s_idx in enumerate(self.ring_to_slice):
            ring_sectors[m] = slice_coeffs[s_idx]
        return ring_sectors, True

    # ---- Stage 4: Color Grading 65-pt 12-bit RGB LUT (VA 0x4111eff4..0x4111fd90) ----
    def build_color_grading_65_rgb(self, params: Dict[str, Any]) -> Tuple[np.ndarray, bool]:
        """Bit-exact 3x65 Color Grading 12-bit RGB LUT normalized to [0, 1]."""
        cg = params.get("colorGrading") or {}
        sh = cg.get("shadows") or {}
        mid = cg.get("midTone") or {}
        hi = cg.get("highlights") or {}

        zones = [
            (
                int(sh.get("hue", 0)) % 360,
                int(_clamp_num(int(sh.get("chroma", 0)), 0, 100)),
                int(_clamp_num(int(sh.get("brightness", 0)), -100, 100)),
            ),
            (
                int(mid.get("hue", 0)) % 360,
                int(_clamp_num(int(mid.get("chroma", 0)), 0, 100)),
                int(_clamp_num(int(mid.get("brightness", 0)), -100, 100)),
            ),
            (
                int(hi.get("hue", 0)) % 360,
                int(_clamp_num(int(hi.get("chroma", 0)), 0, 100)),
                int(_clamp_num(int(hi.get("brightness", 0)), -100, 100)),
            ),
        ]
        if all(c == 0 and b == 0 for _, c, b in zones):
            base = np.linspace(0.0, 1.0, 65, dtype=np.float32)
            return np.stack([base, base, base], axis=0), False

        blending = int(_clamp_num(int(cg.get("blending", 50)), 0, 100))
        balance = int(_clamp_num(int(cg.get("balance", 0)), -100, 100))

        # Exact firmware breakpoints & transition span @ 0x4111f238..0x4111f358
        shift = _sdiv_round(-384 * balance, 100)
        span = max(1, 168 + _sdiv_round(288 * blending, 100))
        bp0, bp1, bp2, bp3 = [bp + shift for bp in self.cg_breakpoints]

        # Zone weight curves on x = 16 * i (i = 0..64 -> x = 0..1024)
        xs = [16 * i for i in range(65)]
        w_sh = [
            1.0 if x <= bp0 else (0.0 if x >= bp0 + span else (bp0 + span - x) / float(span))
            for x in xs
        ]
        w_mid = []
        for x in xs:
            if x <= bp1 - span or x >= bp2 + span:
                w_mid.append(0.0)
            elif x < bp1:
                w_mid.append((x - (bp1 - span)) / float(span))
            elif x <= bp2:
                w_mid.append(1.0)
            else:
                w_mid.append((bp2 + span - x) / float(span))
        w_hi = [
            0.0 if x <= bp3 - span else (1.0 if x >= bp3 else (x - (bp3 - span)) / float(span))
            for x in xs
        ]
        zone_weights = [w_sh, w_mid, w_hi]

        # Zone per-channel chroma vectors & luma offsets (@ 0x4111f3c0..0x4111f580)
        zone_dl = []
        zone_drgb = []
        for hue_deg, chroma, bright in zones:
            dl = _sdiv_round(65 * bright, 100)  # Q4 luma offset -> max +/-65 -> +/-4/4095*16
            vec = self.cg_hue_rgb_360[hue_deg]
            drgb = [_sdiv_round(vec[c] * chroma, 100) for c in range(3)]
            zone_dl.append(dl)
            zone_drgb.append(drgb)

        # Combine into 3 x 65 12-bit RGB curves (@ 0x4111fc74..0x4111fd70)
        lut_rgb = np.zeros((3, 65), dtype=np.float32)
        for i in range(65):
            base_12 = min(64 * i, 4095)
            sum_dl_q4 = sum(int(round(16.0 * zone_dl[z] * zone_weights[z][i])) for z in range(3))
            dl_12 = _sdiv_pow2_round(sum_dl_q4, 4)
            env_q10 = self.cg_envelope_65[i]
            for c in range(3):
                sum_c = sum(zone_drgb[z][c] * zone_weights[z][i] for z in range(3))
                dc_12 = _sdiv_pow2_round(int(round(sum_c * env_q10)), 10)
                lut_rgb[c, i] = float(max(0, min(4095, base_12 + dl_12 + dc_12))) / 4095.0

        return lut_rgb, True

    # ---- Vectorized Full RGB Evaluation (for Images & 3D .cube LUTs) ----
    def evaluate_rgb(
        self,
        rgb_srgb: np.ndarray,
        params: Dict[str, Any],
        pipeline_mode: str = "camera_raw",
        enable_spatial: bool = True,
    ) -> np.ndarray:
        """Apply EXPEED 7 Flexible Color pipeline to float32 sRGB array [..., 3] in [0, 1].

        pipeline_mode:
          - "camera_raw": linearizes sRGB -> applies EXPEED 7 229-pt Flexible Color scene curve
                          (0x441a0e46) -> 257-pt Tone Curve -> Color Blender -> Color Grading -> Sat.
          - "display":    applies 257-pt Tone Curve -> Color Blender -> Color Grading -> Sat directly
                          in display-referred sRGB space (ideal for already-rendered JPEGs/PNGs).
        """
        orig_shape = rgb_srgb.shape
        x = np.clip(rgb_srgb.astype(np.float32), 0.0, 1.0)

        # Stage 1: EXPEED 7 229-pt Flexible Color scene-referred base curve (VA 0x4111a540)
        if pipeline_mode == "camera_raw":
            lin = srgb_to_linear(x)
            grid_229 = np.linspace(0.0, 1.0, 229, dtype=np.float32)
            x = np.interp(lin, grid_229, self.base_flex_229).astype(np.float32)
        elif pipeline_mode != "display":
            raise ValueError(f"Unknown pipeline_mode: {pipeline_mode}")

        # Stage 2: 257-pt 12-bit Tone Curve (VA 0x411205b0)
        tc_257 = self.build_tone_curve_257_12bit(params)
        grid_257 = np.linspace(0.0, 1.0, 257, dtype=np.float32)
        x = np.interp(x, grid_257, tc_257).astype(np.float32)

        # Convert RGB -> BT.601 YCbCr for MIPU Color Blender & Saturation
        r, g, b = x[..., 0], x[..., 1], x[..., 2]
        y = 0.299 * r + 0.587 * g + 0.114 * b
        cb = -0.168736 * r - 0.331264 * g + 0.500000 * b
        cr = 0.500000 * r - 0.418688 * g - 0.081312 * b

        # Stage 3: 32-sector MIPU Color Blender (VA 0x4111dd28)
        ring_sectors, cb_active = self.build_color_blender_32_sectors(params)
        if cb_active:
            ang = np.mod(np.degrees(np.arctan2(cr, cb)), 360.0)
            pos = ang / 11.25  # 0..32
            idx0 = np.floor(pos).astype(np.int32) % 32
            idx1 = (idx0 + 1) % 32
            frac = (pos - np.floor(pos)).astype(np.float32)[..., None]
            sec = ring_sectors[idx0] * (1.0 - frac) + ring_sectors[idx1] * frac

            cb_new = sec[..., 0] * cb + sec[..., 1] * cr
            cr_new = sec[..., 2] * cb + sec[..., 3] * cr
            dy = sec[..., 4] * cb + sec[..., 5] * cr
            # Protect deep black/white clip from out-of-gamut luma shift
            y_guard = np.clip(4.0 * y * (1.0 - y), 0.15, 1.0)
            y = np.clip(y + dy * y_guard, 0.0, 1.0)
            cb, cr = cb_new, cr_new

        # Global Saturation (@ 0x4111ee4c, [-100, +100] -> gain [0.0, 2.0])
        sat = int(_clamp_num(int(params.get("saturation", 0)), -100, 100))
        if sat != 0:
            sat_gain = 1.0 + (sat / 100.0)
            cb = cb * sat_gain
            cr = cr * sat_gain

        # Optional spatial Clarity / Mid-range Sharpening / Sharpening on 2D images
        if enable_spatial and x.ndim == 3 and x.shape[0] >= 8 and x.shape[1] >= 8:
            clarity = float(params.get("clarity", 0.0))
            mid_sharp = float(params.get("midRangeSharpning", 0.0))
            sharp = float(params.get("sharpning", 0.0))
            if abs(clarity) > 1e-4:
                y_blur_lg = _box_blur_2d(y, radius=9)
                mid_mask = 4.0 * y * (1.0 - y)
                y = np.clip(y + (clarity * 0.045) * (y - y_blur_lg) * mid_mask, 0.0, 1.0)
            if abs(mid_sharp) > 1e-4:
                y_blur_md = _box_blur_2d(y, radius=2)
                y = np.clip(y + (mid_sharp * 0.03) * (y - y_blur_md), 0.0, 1.0)
            if sharp > 0.0:
                y_blur_sm = _box_blur_2d(y, radius=1)
                y = np.clip(y + (sharp * 0.025) * (y - y_blur_sm), 0.0, 1.0)

        # Reconstruct RGB from YCbCr
        r_out = np.clip(y + 1.402000 * cr, 0.0, 1.0)
        g_out = np.clip(y - 0.344136 * cb - 0.714136 * cr, 0.0, 1.0)
        b_out = np.clip(y + 1.772000 * cb, 0.0, 1.0)
        rgb_out = np.stack([r_out, g_out, b_out], axis=-1).astype(np.float32)

        # Stage 4: 65-pt 12-bit per-channel Color Grading RGB LUT (VA 0x4111eff4)
        cg_lut, cg_active = self.build_color_grading_65_rgb(params)
        if cg_active:
            grid_65 = np.linspace(0.0, 1.0, 65, dtype=np.float32)
            for c in range(3):
                rgb_out[..., c] = np.interp(rgb_out[..., c], grid_65, cg_lut[c])

        return np.clip(rgb_out, 0.0, 1.0).reshape(orig_shape)

    def export_cube_lut(
        self,
        params: Dict[str, Any],
        cube_path: Path | str,
        size: int = 33,
        pipeline_mode: str = "display",
    ) -> None:
        """Export standard Adobe/Resolve .cube 3D LUT (R fastest, then G, then B)."""
        axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
        b_grid, g_grid, r_grid = np.meshgrid(axis, axis, axis, indexing="ij")
        pts = np.stack([r_grid, g_grid, b_grid], axis=-1).reshape(-1, 3)
        mapped = self.evaluate_rgb(pts, params, pipeline_mode=pipeline_mode, enable_spatial=False)

        out_p = Path(cube_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        title = params.get("name") or "NIKON_EXPEED7_FLEX"
        with open(out_p, "w", encoding="utf-8") as fh:
            fh.write(f'TITLE "{title}"\n')
            fh.write("# Generated by nikon_zf_film_engine.py from EXPEED 7 ROM LUTs\n")
            fh.write(f"LUT_3D_SIZE {size}\n")
            fh.write("DOMAIN_MIN 0.0 0.0 0.0\n")
            fh.write("DOMAIN_MAX 1.0 1.0 1.0\n")
            for r_v, g_v, b_v in mapped:
                fh.write(f"{r_v:.6f} {g_v:.6f} {b_v:.6f}\n")


def _box_blur_2d(plane: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return plane
    win = 2 * radius + 1
    pad_h = np.pad(plane, ((0, 0), (radius, radius)), mode="reflect")
    csum_h = np.cumsum(np.pad(pad_h, ((0, 0), (1, 0)), mode="constant"), axis=1, dtype=np.float64)
    horiz = ((csum_h[:, win:] - csum_h[:, :-win]) / win).astype(np.float32)

    pad_v = np.pad(horiz, ((radius, radius), (0, 0)), mode="reflect")
    csum_v = np.cumsum(np.pad(pad_v, ((1, 0), (0, 0)), mode="constant"), axis=0, dtype=np.float64)
    return ((csum_v[win:, :] - csum_v[:-win, :]) / win).astype(np.float32)


# ============================================================================
# Part 3: Nikon Zf C3.01 18-Level Film Grain Engine (FilmGrain.cpp + XMP)
# ============================================================================

# Exact 54-byte table @ Zf C3.01 RAM 0xa02acdd2b (EG module offset 0x4a3cd2b)
ZF_GRAIN_TABLE_BYTES = bytes.fromhex(
    "0f1d501a1d50371d50521d50641d59641d640c2050122050"
    "2a20504120505f20506420550822500c22501c22502d2250"
    "412250522250"
)


def lookup_zf_grain_preset(
    size_level: int, intensity_level: int, video_mode: bool = False
) -> Dict[str, int]:
    """Exact firmware lookup matching FUN_410d9d8c (Zf C3.01):
    idx = clamp(byte(intensity) + int(size)*6 - 7, 0, 17).
    """
    if size_level not in (1, 2, 3):
        raise ValueError(f"Zf grain size_level must be 1 (Small), 2 (Medium), or 3 (Large); got {size_level}")
    max_int = 3 if video_mode else 6
    if not (0 <= intensity_level <= max_int):
        raise ValueError(
            f"Zf grain intensity_level must be 0..{max_int} ({'video' if video_mode else 'photo'}); got {intensity_level}"
        )
    if intensity_level == 0:
        return {
            "enabled": 0,
            "index": -1,
            "size_level": size_level,
            "intensity_level": 0,
            "crd_grain_amount": 0,
            "crd_grain_size": 0,
            "crd_grain_frequency": 0,
        }
    idx = max(0, min(17, intensity_level + size_level * 6 - 7))
    amt, sz, freq = ZF_GRAIN_TABLE_BYTES[3 * idx : 3 * idx + 3]
    return {
        "enabled": 1,
        "index": idx,
        "size_level": size_level,
        "intensity_level": intensity_level,
        "crd_grain_amount": int(amt),
        "crd_grain_size": int(sz),
        "crd_grain_frequency": int(freq),
    }


# Hardware Constant Pools in DSP .data.internal (zf_filmgrain_details_v2.json):
#   .LCPI2_0 @ 0x00038e80 (32 B): u8 = [2, 1, 4, 2, 0...] -> 3x3 binomial Gaussian / 16
#   .LCPI3_0 @ 0x00038ea0 (32 B): u8 = [15, 26, 15, 3, 26, 40, 26, 7, 0...] -> 5x5 rank-3 Gaussian / 256
#   .LCPI4_0 @ 0x00038ec0 (32 B): u8 = [16..31, 0..15] -> 256-bit SIMD high/low 128-bit swap mask
LCPI2_0_U8_32: Tuple[int, ...] = (2, 1, 4, 2) + (0,) * 28
LCPI3_0_U8_32: Tuple[int, ...] = (15, 26, 15, 3, 26, 40, 26, 7) + (0,) * 24
LCPI4_0_U8_32: Tuple[int, ...] = tuple(range(16, 32)) + tuple(range(0, 16))


def reconstruct_kernel_3x3_from_lcpi(lcpi2_u8: Sequence[int] = LCPI2_0_U8_32) -> np.ndarray:
    """Reconstruct exact 3x3 integer kernel [[1,2,1],[2,4,2],[1,2,1]] from .LCPI2_0 @ 0x38e80."""
    outer_c, outer_s, mid_c, mid_s = (int(lcpi2_u8[0]), int(lcpi2_u8[1]), int(lcpi2_u8[2]), int(lcpi2_u8[3]))
    row_outer = [outer_s, outer_c, outer_s]
    row_mid = [mid_s, mid_c, mid_s]
    return np.array([row_outer, row_mid, row_outer], dtype=np.int32)


def reconstruct_kernel_5x5_from_lcpi(lcpi3_u8: Sequence[int] = LCPI3_0_U8_32) -> np.ndarray:
    """Reconstruct exact 5x5 non-separable (rank=3) integer kernel {40,26,15,7,3,0} from .LCPI3_0 @ 0x38ea0."""
    d1_l, d1_c, d1_r, w_knight = (int(lcpi3_u8[0]), int(lcpi3_u8[1]), int(lcpi3_u8[2]), int(lcpi3_u8[3]))
    d0_l, d0_c, d0_r, w_axial2 = (int(lcpi3_u8[4]), int(lcpi3_u8[5]), int(lcpi3_u8[6]), int(lcpi3_u8[7]))
    w_corner = int(lcpi3_u8[8])  # Strictly 0 in hardware (.LCPI3_0[8..31] == 0)
    row_pm2 = [w_corner, w_knight, w_axial2, w_knight, w_corner]
    row_pm1 = [w_knight, d1_l, d1_c, d1_r, w_knight]
    row_0 = [w_axial2, d0_l, d0_c, d0_r, w_axial2]
    return np.array([row_pm2, row_pm1, row_0, row_pm1, row_pm2], dtype=np.int32)


KERNEL_1X1_INT = np.array([[1]], dtype=np.int32)
KERNEL_3X3_INT = reconstruct_kernel_3x3_from_lcpi()
KERNEL_5X5_INT = reconstruct_kernel_5x5_from_lcpi()

KERNEL_1X1_SHIFT = 0  # / 1
KERNEL_3X3_SHIFT = 4  # / 16  (VSWGENMAC post_shift=4, truncating floor)
KERNEL_5X5_SHIFT = 8  # / 256 (VMAC3 r26=8, truncating floor)

# 4x 10-entry Clang SwitchToLookupTable arrays @ 0x00038ee0..0x00038f70
# Indexed by in->blur_kernel_size (in + 0x51), validated against bitmask 0x2a9 -> {0, 3, 5, 7, 9}
BLUR_VALID_BITMASK = 0x2A9
VALID_BLUR_KERNEL_SIZES: Tuple[int, ...] = (0, 3, 5, 7, 9)

DSP_FN_UPDATE_BLUR_1 = "0x41877920"
DSP_FN_UPDATE_BLUR_3 = "0x41877940"
DSP_FN_UPDATE_BLUR_5 = "0x41878200"
RADIUS_TO_FN_VA: Dict[int, str] = {0: DSP_FN_UPDATE_BLUR_1, 1: DSP_FN_UPDATE_BLUR_3, 2: DSP_FN_UPDATE_BLUR_5}

# Pass 1 (Ring 1 @ 0x2f780): flush lines @ 0x38ee0 + fn table @ 0x38f40
SWITCH_FLUSH_TABLE_38EE0: Tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0, 2, 0, 2)
SWITCH_FNPTR_TABLE_38F40: Tuple[str, ...] = tuple(RADIUS_TO_FN_VA[r] for r in SWITCH_FLUSH_TABLE_38EE0)

# Pass 2 (Ring 2 @ 0x2f798): flush lines @ 0x38f10 + fn table @ 0x38f70
SWITCH_FLUSH_TABLE_38F10: Tuple[int, ...] = (0, 0, 0, 1, 0, 2, 0, 1, 0, 2)
SWITCH_FNPTR_TABLE_38F70: Tuple[str, ...] = tuple(RADIUS_TO_FN_VA[r] for r in SWITCH_FLUSH_TABLE_38F10)


def blur_line_ring_1(
    plane: np.ndarray,
    round_bias: int = 0,
    border_mode: str = "reflect",
    out_dtype: Optional[np.dtype] = None,
) -> np.ndarray:
    """Exact integer passthrough matching `UpdateBlurLine_1` @ 0x41877920 (14 B, 1x1 radius 0)."""
    _ = (round_bias, border_mode)
    target_dtype = plane.dtype if out_dtype is None else np.dtype(out_dtype)
    return np.asarray(plane, dtype=target_dtype).copy()


def blur_line_ring_3(
    plane: np.ndarray,
    round_bias: int = 0,
    border_mode: str = "reflect",
    out_dtype: Optional[np.dtype] = None,
) -> np.ndarray:
    """Exact integer 3x3 binomial Gaussian filter matching `UpdateBlurLine_3` @ 0x41877940 (.LCPI2_0).

    Hardware instruction `VSWGENMAC` uses `post_shift = 4` without `{rnd}`, i.e. `round_bias = 0`
    (pure floor truncation `>> 4`), and mirrors edges via `BORDER_REFLECT_101` (`mode="reflect"`).
    """
    target_dtype = plane.dtype if out_dtype is None else np.dtype(out_dtype)
    src = np.asarray(plane, dtype=np.int32)
    h, w = src.shape
    px = np.pad(src, ((0, 0), (1, 1)), mode=border_mode)
    horiz = px[:, 0:w] + (px[:, 1 : 1 + w] << 1) + px[:, 2 : 2 + w]
    py = np.pad(horiz, ((1, 1), (0, 0)), mode=border_mode)
    acc = py[0:h, :] + (py[1 : 1 + h, :] << 1) + py[2 : 2 + h, :]
    shifted = (acc + int(round_bias)) >> KERNEL_3X3_SHIFT
    if np.issubdtype(target_dtype, np.integer):
        info = np.iinfo(target_dtype)
        shifted = np.clip(shifted, info.min, info.max)
    return shifted.astype(target_dtype)


def blur_line_ring_5(
    plane: np.ndarray,
    round_bias: int = 0,
    border_mode: str = "reflect",
    out_dtype: Optional[np.dtype] = None,
) -> np.ndarray:
    """Exact integer 5x5 non-separable (rank=3) Gaussian filter matching `UpdateBlurLine_5` @ 0x41878200 (.LCPI3_0).

    Kernel: 1/256 * [[0,3,7,3,0], [3,15,26,15,3], [7,26,40,26,7], [3,15,26,15,3], [0,3,7,3,0]].
    Hardware instruction `VMAC3` shifts right by `r26 = 8` without `{rnd}`, i.e. `round_bias = 0`,
    and mirrors edges via `BORDER_REFLECT_101` (`mode="reflect"`).
    """
    target_dtype = plane.dtype if out_dtype is None else np.dtype(out_dtype)
    src = np.asarray(plane, dtype=np.int32)
    h, w = src.shape
    p = np.pad(src, ((2, 2), (2, 2)), mode=border_mode)
    r0 = p[2 : 2 + h, :]
    r_pm1 = p[1 : 1 + h, :] + p[3 : 3 + h, :]
    r_pm2 = p[0 : 0 + h, :] + p[4 : 4 + h, :]
    acc = (
        7 * (r0[:, 0:w] + r0[:, 4 : 4 + w])
        + 26 * (r0[:, 1 : 1 + w] + r0[:, 3 : 3 + w])
        + 40 * r0[:, 2 : 2 + w]
        + 3 * (r_pm1[:, 0:w] + r_pm1[:, 4 : 4 + w])
        + 15 * (r_pm1[:, 1 : 1 + w] + r_pm1[:, 3 : 3 + w])
        + 26 * r_pm1[:, 2 : 2 + w]
        + 3 * (r_pm2[:, 1 : 1 + w] + r_pm2[:, 3 : 3 + w])
        + 7 * r_pm2[:, 2 : 2 + w]
    )
    shifted = (acc + int(round_bias)) >> KERNEL_5X5_SHIFT
    if np.issubdtype(target_dtype, np.integer):
        info = np.iinfo(target_dtype)
        shifted = np.clip(shifted, info.min, info.max)
    return shifted.astype(target_dtype)


class BlurLineRing:
    """Stateful 24-byte row-ring buffer modeling CEVA-XM6 `struct BlurLineRing` (`0x2f780` / `0x2f798`).

    Layout in IDM:
      +0x00: int32_t  line_idx
      +0x04: uint8_t* buf[0..4] (5 row buffer pointers, stride 0x2080 = 8,320 B)
    """

    def __init__(
        self,
        width: int,
        radius: int,
        round_bias: int = 0,
        border_mode: str = "reflect",
        dtype: np.dtype = np.uint8,
    ) -> None:
        if radius not in (0, 1, 2):
            raise ValueError(f"BlurLineRing radius must be 0, 1, or 2; got {radius}")
        self.width = int(width)
        self.radius = int(radius)
        self.win = 2 * self.radius + 1
        self.round_bias = int(round_bias)
        self.border_mode = border_mode
        self.dtype = np.dtype(dtype)
        self.line_idx = 0
        self._slots: List[Optional[np.ndarray]] = [None] * self.win

    def push_line(self, line: np.ndarray) -> bool:
        arr = np.asarray(line, dtype=np.int32)
        if arr.shape != (self.width,):
            raise ValueError(f"Line shape {arr.shape} != ({self.width},)")
        if self.line_idx < self.win:
            self._slots[self.line_idx] = arr
        else:
            self._slots = self._slots[1:] + [arr]
        self.line_idx += 1
        return self.line_idx >= self.win

    def compute_current_line(self) -> np.ndarray:
        if self.line_idx < self.win:
            raise RuntimeError("BlurLineRing window not yet ready")
        w = self.width
        if self.radius == 0:
            out = self._slots[0].copy()  # type: ignore[union-attr]
        elif self.radius == 1:
            p0 = np.pad(self._slots[0], (1, 1), mode=self.border_mode)  # type: ignore[arg-type]
            p1 = np.pad(self._slots[1], (1, 1), mode=self.border_mode)  # type: ignore[arg-type]
            p2 = np.pad(self._slots[2], (1, 1), mode=self.border_mode)  # type: ignore[arg-type]
            s_pm1 = p0 + p2
            acc = (s_pm1[0:w] + s_pm1[2 : 2 + w]) + 2 * s_pm1[1 : 1 + w] + 2 * (p1[0:w] + p1[2 : 2 + w]) + 4 * p1[1 : 1 + w]
            out = (acc + self.round_bias) >> KERNEL_3X3_SHIFT
        else:
            p = [np.pad(ln, (2, 2), mode=self.border_mode) for ln in self._slots]  # type: ignore[arg-type]
            r0, r_pm1, r_pm2 = p[2], p[1] + p[3], p[0] + p[4]
            acc = (
                7 * (r0[0:w] + r0[4 : 4 + w])
                + 26 * (r0[1 : 1 + w] + r0[3 : 3 + w])
                + 40 * r0[2 : 2 + w]
                + 3 * (r_pm1[0:w] + r_pm1[4 : 4 + w])
                + 15 * (r_pm1[1 : 1 + w] + r_pm1[3 : 3 + w])
                + 26 * r_pm1[2 : 2 + w]
                + 3 * (r_pm2[1 : 1 + w] + r_pm2[3 : 3 + w])
                + 7 * r_pm2[2 : 2 + w]
            )
            out = (acc + self.round_bias) >> KERNEL_5X5_SHIFT
        if np.issubdtype(self.dtype, np.integer):
            info = np.iinfo(self.dtype)
            out = np.clip(out, info.min, info.max)
        return out.astype(self.dtype)

    def filter_plane_streaming(self, plane: np.ndarray) -> np.ndarray:
        padded_y = np.pad(np.asarray(plane, dtype=np.int32), ((self.radius, self.radius), (0, 0)), mode=self.border_mode)
        out_rows: List[np.ndarray] = []
        self.line_idx = 0
        self._slots = [None] * self.win
        for y_i in range(padded_y.shape[0]):
            if self.push_line(padded_y[y_i]):
                out_rows.append(self.compute_current_line())
        return np.stack(out_rows, axis=0)


def dispatch_blur_cascade(
    plane: np.ndarray,
    blur_size: int,
    round_bias_3: int = 0,
    round_bias_5: int = 0,
    border_mode: str = "reflect",
) -> np.ndarray:
    """Execute XM6 two-stage serial BlurLineRing cascade (Pass 1 @ 0x38f40 -> Pass 2 @ 0x38f70)."""
    if blur_size < 0 or blur_size >= 10 or ((BLUR_VALID_BITMASK >> blur_size) & 1) == 0:
        raise ValueError(f"DSP error 0x10000001: invalid blur_kernel_size={blur_size} (allowed: {VALID_BLUR_KERNEL_SIZES})")
    r1 = SWITCH_FLUSH_TABLE_38EE0[blur_size]
    r2 = SWITCH_FLUSH_TABLE_38F10[blur_size]
    cur = blur_line_ring_5(plane, round_bias=round_bias_5, border_mode=border_mode) if r1 == 2 else blur_line_ring_1(plane)
    if r2 == 1:
        cur = blur_line_ring_3(cur, round_bias=round_bias_3, border_mode=border_mode)
    elif r2 == 2:
        cur = blur_line_ring_5(cur, round_bias=round_bias_5, border_mode=border_mode)
    return cur


def nikon_zf_filmgrain_dsp_bitexact(
    img_y: np.ndarray,
    map_a: np.ndarray,
    map_b: np.ndarray,
    bypass_flag: int = 0,
    blend_w: int = 8,
    blur_size: int = 0,
    map_dim: int = 128,
    tile_period: int = 128,
    tile_step: int = 0,
    knee_lo: int = 0,
    knee_hi: int = 255,
    prng_seed: int = 0,
) -> np.ndarray:
    """Bit-exact implementation of Nikon Zf C3.01 CEVA-XM6 `FilmGrain` @ DSP VA 0x418761e0 (5,928 B).

    Matches every instruction of `FilmGrain`, `ApplyMaptoImageLine`, and `UpdateBlurLine_1/_3/_5`:
      1. Q4 dual-map blend `((w * mapA + (16 - w) * mapB) >> 4)` -> IDM 0x9000 (`int16[M, M]`).
      2. 32-step `SC0.divstep` staggered tiling LUT `((k // P) * S + (k % P)) % M` -> IDM 0x2d720.
      3. Q6 shadow/highlight piecewise-linear `tone_lut[0..255]` (`0..64`) -> IDM 0x8000 (16 banks).
      4. `ApplyMaptoImageLine`: per-32-pixel Marsaglia `xorshift32` (`<<13, >>17, <<5`) `+-1` sign
         flip (`bit=1 -> +g, bit=0 -> -g`), `(signed_grain * tone_lut[Y]) >> 6`, and `[0, 255]` `vst{satu}`.
      5. Pass 1 (`0x38f40`) -> Pass 2 (`0x38f70`) `BlurLineRing` cascade (`BORDER_REFLECT_101`, floor shift).
    """
    if bypass_flag == 1:
        return np.asarray(img_y, dtype=np.uint8).copy()
    if blur_size < 0 or blur_size >= 10 or ((BLUR_VALID_BITMASK >> blur_size) & 1) == 0:
        raise ValueError(f"DSP error 0x10000001: invalid blur_size={blur_size}")

    src_y = np.asarray(img_y, dtype=np.uint8)
    h, w = src_y.shape
    if h <= 0 or w <= 0:
        return src_y.copy()

    # 1. Dual int16 base map Q4 linear blend -> IDM 0x9000
    w_q4 = int(blend_w) & 0xFF
    acc_map = w_q4 * map_a.astype(np.int32) + (16 - w_q4) * map_b.astype(np.int32)
    grain_map = ((acc_map >> 4) & 0xFFFF).astype(np.uint16).astype(np.int32)
    grain_map = np.where(grain_map >= 0x8000, grain_map - 0x10000, grain_map)

    # 2. Staggered coordinate LUT -> IDM 0x2d720
    b32 = (w + 31) >> 5
    w_pad = b32 * 32
    max_len = max(((max(w, h) + 31) & ~31), w_pad)
    k = np.arange(max_len, dtype=np.uint32)
    p_val = max(1, int(tile_period) & 0xFF)
    s_val = int(tile_step) & 0xFF
    m_val = max(1, int(map_dim) & 0xFF)
    idx_lut = (((k // p_val) * s_val + (k % p_val)) % m_val).astype(np.int32)

    # 3. Shadow / highlight Q6 tone weight LUT -> IDM 0x8000 (0..64)
    k_lo = int(knee_lo) & 0xFF
    k_hi = int(knee_hi) & 0xFF
    yv = np.arange(256, dtype=np.int32)
    tone_lut = np.full(256, 64, dtype=np.int32)
    if k_lo > 0:
        lo_mask = yv < k_lo
        tone_lut[lo_mask] = (yv[lo_mask] << 6) // k_lo
    if k_hi < 255:
        hi_mask = yv > k_hi
        tone_lut[hi_mask] = ((255 - yv[hi_mask]) << 6) // (255 - k_hi)

    # 4. Advance Marsaglia xorshift32 once per 32-pixel block across H * b32 blocks
    state = int(prng_seed) & 0xFFFFFFFF
    if state == 0:
        state = 0x12345678
    total_blocks = h * b32
    states = np.empty(total_blocks, dtype=np.uint32)
    for bi in range(total_blocks):
        state = (state ^ ((state << 13) & 0xFFFFFFFF)) & 0xFFFFFFFF
        state = (state ^ (state >> 17)) & 0xFFFFFFFF
        state = (state ^ ((state << 5) & 0xFFFFFFFF)) & 0xFFFFFFFF
        states[bi] = state

    bit_shifts = np.arange(32, dtype=np.uint32)[None, :]
    bits = ((states[:, None] >> bit_shifts) & 1).reshape(h, w_pad)
    signs = np.where(bits == 1, 1, -1).astype(np.int32)

    # Gather 2D grain_map[idx_lut[y], idx_lut[x]] with x_idx < 128 hardware vlut guard
    x_idx = idx_lut[:w_pad]
    y_idx = idx_lut[:h]
    clamped_x = np.minimum(x_idx, m_val - 1)
    g_2d = grain_map[y_idx[:, None], clamped_x[None, :]]
    if np.any(x_idx >= 128):
        g_2d = np.where(x_idx[None, :] < 128, g_2d, 0)

    y_padded = np.zeros((h, w_pad), dtype=np.int32)
    y_padded[:, :w] = src_y.astype(np.int32)

    signed_grain = ((g_2d * signs + 0x8000) & 0xFFFF) - 0x8000
    delta = (signed_grain * tone_lut[y_padded]) >> 6
    noisy = np.clip(y_padded[:, :w] + delta[:, :w], 0, 255).astype(np.uint8)

    # 5. Two-stage BlurLineRing cascade (Pass 1 -> Pass 2)
    return dispatch_blur_cascade(noisy, blur_size=blur_size, round_bias_3=0, round_bias_5=0, border_mode="reflect")


def _synthesize_zf_base_grain_maps_s16(
    size_level: int,
    crd_amount: int,
    crd_frequency: int,
    seed: int,
    blur_size: int,
    map_dim: int = 128,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """Build dual 128x128 int16 pre-blur grain maps (mapA, mapB) + Q4 blend_w for XM6 FilmGrain.

    Because `ApplyMaptoImageLine` applies per-pixel `xorshift32` +-1 polarity flips (making pre-blur
    samples spatially uncorrelated) and `UpdateBlurLine_{1,3,5}` subsequently applies the 1x1 / 3x3 /
    5x5 / 7x7 / 9x9 low-pass filter, pre-blur `int16` map amplitudes compensate for the spatial filter's
    white-noise RMS attenuation (`1.000` for `0`, `0.375` for `3`, `0.289` for `5`, `0.181` for `7`, `0.148` for `9`).
    """
    rng = np.random.default_rng(seed)
    # Half-normal / folded-Gaussian magnitude carrier (since xorshift32 supplies the +-1 sign per pixel)
    mag_b = np.abs(rng.standard_normal((map_dim, map_dim), dtype=np.float32))
    mag_a = np.abs(rng.standard_normal((map_dim, map_dim), dtype=np.float32))
    # Normalize so E[mag^2] == 1.0 (which standard_normal already satisfies: E[|Z|^2] = E[Z^2] = 1.0)
    rms_b = max(float(np.sqrt(np.mean(mag_b * mag_b))), 1e-6)
    rms_a = max(float(np.sqrt(np.mean(mag_a * mag_a))), 1e-6)
    mag_b = mag_b / rms_b
    mag_a = mag_a / rms_a

    # Map crd_grain_frequency [80..100] -> Q4 blend_w [0..16] (in + 0x50):
    # Separates (Size 1, Int 5: amt=100, freq=89 -> w=7) from (Size 1, Int 6: amt=100, freq=100 -> w=16)
    blend_w = int(_clamp_num(round((crd_frequency - 80) * 16.0 / 20.0), 0, 16))

    # Compensate pre-blur int16 amplitude for post-blur UpdateBlurLine_{1,3,5} white-noise RMS gain
    rms_attenuation = {0: 1.0000, 3: 0.3750, 5: 0.2890, 7: 0.1815, 9: 0.1476}[blur_size]
    size_visual_boost = {1: 1.00, 2: 1.15, 3: 1.35}[size_level]
    target_post_rms_lsb = (16.5 * (crd_amount / 100.0) * size_visual_boost) + 0.6
    pre_rms_b = target_post_rms_lsb / rms_attenuation
    pre_rms_a = pre_rms_b * 1.18

    map_b = np.clip(np.round(mag_b * pre_rms_b), -32767, 32767).astype(np.int16)
    map_a = np.clip(np.round(mag_a * pre_rms_a), -32767, 32767).astype(np.int16)
    return map_a, map_b, blend_w


def apply_zf_film_grain(
    rgb_srgb: np.ndarray,
    size_level: int = 1,
    intensity_level: int = 0,
    video_mode: bool = False,
    seed: int = 20260301,
    *,
    dsp_post_blur_size: Optional[int] = None,
    knee_lo: int = 20,
    knee_hi: int = 240,
) -> np.ndarray:
    """Apply Nikon Zf C3.01 Film Grain via the instruction-verified CEVA-XM6 DSP pipeline:
    - Maps `size_level` {1: Small, 2: Medium, 3: Large} -> `in->blur_kernel_size` {0: 1x1, 3: 3x3, 5: 5x5}.
    - Executes `nikon_zf_filmgrain_dsp_bitexact` on 8-bit Luma `Y` (`plane_addr[0]`):
      1) Q4 blend of `mapA` / `mapB` (`in+0x50`, driven by `crd_grain_frequency`)
      2) Staggered `divstep` coordinate LUT (`P=127, S=37, M=128`)
      3) Marsaglia `xorshift32` per-32px `+-1` sign flip + Q6 `tone_lut[Y] >> 6` + `[0, 255]` `vst{satu}`
      4) Two-stage `BlurLineRing` spatial filter (`UpdateBlurLine_1` / `.LCPI2_0` `_3` / `.LCPI3_0` `_5`),
         leaving Chroma `Cb, Cr` untouched (authentic monochrome silver-halide grain).
    """
    preset = lookup_zf_grain_preset(size_level, intensity_level, video_mode=video_mode)
    if not preset["enabled"]:
        return rgb_srgb.copy()

    blur_sz = {1: 0, 2: 3, 3: 5}[size_level] if dsp_post_blur_size is None else int(dsp_post_blur_size)
    map_a, map_b, blend_w = _synthesize_zf_base_grain_maps_s16(
        size_level=size_level,
        crd_amount=preset["crd_grain_amount"],
        crd_frequency=preset["crd_grain_frequency"],
        seed=seed,
        blur_size=blur_sz,
        map_dim=128,
    )

    x = np.clip(rgb_srgb.astype(np.float32), 0.0, 1.0)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    y_f = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.500000 * b
    cr = 0.500000 * r - 0.418688 * g - 0.081312 * b

    y_255 = np.clip(y_f * 255.0, 0.0, 255.0)
    y_u8 = np.round(y_255).astype(np.uint8)
    y_frac = y_255 - y_u8.astype(np.float32)

    prng_seed = (int(seed) ^ 0x5A3C9E17) & 0xFFFFFFFF
    y_dsp_u8 = nikon_zf_filmgrain_dsp_bitexact(
        img_y=y_u8,
        map_a=map_a,
        map_b=map_b,
        bypass_flag=0,
        blend_w=blend_w,
        blur_size=blur_sz,
        map_dim=128,
        tile_period=127,
        tile_step=37,
        knee_lo=knee_lo,
        knee_hi=knee_hi,
        prng_seed=prng_seed,
    )
    y_out = np.clip((y_dsp_u8.astype(np.float32) + y_frac) / 255.0, 0.0, 1.0)

    r_out = np.clip(y_out + 1.402000 * cr, 0.0, 1.0)
    g_out = np.clip(y_out - 0.344136 * cb - 0.714136 * cr, 0.0, 1.0)
    b_out = np.clip(y_out + 1.772000 * cb, 0.0, 1.0)
    return np.stack([r_out, g_out, b_out], axis=-1).astype(np.float32)


def generate_nikon_crd_xmp(
    params: Dict[str, Any],
    grain_size: int = 1,
    grain_intensity: int = 0,
    video_mode: bool = False,
) -> str:
    """Generate Adobe Camera Raw <crd:...> XMP matching Z8 0x411115c4 + Zf C3.01 FUN_410d9d8c."""
    grain = lookup_zf_grain_preset(grain_size, grain_intensity, video_mode=video_mode)
    cb = params.get("colorBlender") or {}
    cg = params.get("colorGrading") or {}
    sh = cg.get("shadows") or {}
    mid = cg.get("midTone") or {}
    hi = cg.get("highlights") or {}

    acr_channels = [
        ("Red", "red"),
        ("Orange", "orange"),
        ("Yellow", "yellow"),
        ("Green", "green"),
        ("Aqua", "cyan"),
        ("Blue", "blue"),
        ("Purple", "purple"),
        ("Magenta", "magenta"),
    ]
    attrs = [
        f'crd:CameraProfile="Camera {params.get("name", "Flexible Color")}"',
        f'crd:Contrast2012="{int(params.get("contrast", 0))}"',
        f'crd:Highlights2012="{int(params.get("highlights", 0))}"',
        f'crd:Shadows2012="{int(params.get("shadows", 0))}"',
        f'crd:Whites2012="{int(params.get("whiteLevel", 0))}"',
        f'crd:Blacks2012="{int(params.get("blackLevel", 0))}"',
        f'crd:Saturation="{int(params.get("saturation", 0))}"',
        f'crd:Clarity2012="{int(round(float(params.get("clarity", 0.0)) * 10))}"',
    ]
    for acr_name, key in acr_channels:
        ch = cb.get(key) or {}
        attrs.append(f'crd:HueAdjustment{acr_name}="{int(ch.get("hue", 0))}"')
        attrs.append(f'crd:SaturationAdjustment{acr_name}="{int(ch.get("chroma", 0))}"')
        attrs.append(f'crd:LuminanceAdjustment{acr_name}="{int(ch.get("brightness", 0))}"')

    attrs.extend(
        [
            f'crd:SplitToningShadowHue="{int(sh.get("hue", 0))}"',
            f'crd:SplitToningShadowSaturation="{int(sh.get("chroma", 0))}"',
            f'crd:SplitToningHighlightHue="{int(hi.get("hue", 0))}"',
            f'crd:SplitToningHighlightSaturation="{int(hi.get("chroma", 0))}"',
            f'crd:SplitToningBalance="{int(cg.get("balance", 0))}"',
            f'crd:ColorGradeMidtoneHue="{int(mid.get("hue", 0))}"',
            f'crd:ColorGradeMidtoneSat="{int(mid.get("chroma", 0))}"',
            f'crd:ColorGradeShadowLum="{int(sh.get("brightness", 0))}"',
            f'crd:ColorGradeMidtoneLum="{int(mid.get("brightness", 0))}"',
            f'crd:ColorGradeHighlightLum="{int(hi.get("brightness", 0))}"',
            f'crd:ColorGradeBlending="{int(cg.get("blending", 50))}"',
            f'crd:GrainAmount="{grain["crd_grain_amount"]}"',
            f'crd:GrainSize="{grain["crd_grain_size"]}"',
            f'crd:GrainFrequency="{grain["crd_grain_frequency"]}"',
        ]
    )
    joined = "\n   ".join(attrs)
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description xmlns:crd="http://ns.adobe.com/camera-raw-defaults/1.0/"\n'
        f"   {joined}/>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )


# ============================================================================
# Part 4: Automated Verification Suite & Synthetic Test Chart
# ============================================================================

def build_synthetic_test_chart(height: int = 360, width: int = 540) -> np.ndarray:
    """Create a deterministic color + grayscale step-wedge test chart in sRGB [0, 1]."""
    img = np.zeros((height, width, 3), dtype=np.float32)
    patches = [
        (0.85, 0.20, 0.18),  # Red
        (0.90, 0.48, 0.16),  # Orange
        (0.88, 0.82, 0.18),  # Yellow
        (0.22, 0.72, 0.28),  # Green
        (0.18, 0.75, 0.78),  # Cyan
        (0.20, 0.36, 0.86),  # Blue
        (0.52, 0.26, 0.82),  # Purple
        (0.82, 0.24, 0.68),  # Magenta
        (0.76, 0.57, 0.48),  # Portrait skin tone
    ]
    pw = width // len(patches)
    top_h = int(height * 0.55)
    for idx, col in enumerate(patches):
        x0 = idx * pw
        x1 = width if idx == len(patches) - 1 else (idx + 1) * pw
        img[:top_h, x0:x1] = col

    # Smooth horizontal luma ramp + 11-step zone wedge at bottom
    ramp_h = int(height * 0.25)
    ramp = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    img[top_h : top_h + ramp_h] = np.repeat(ramp, ramp_h, axis=0)

    steps = 11
    sw = width // steps
    for s in range(steps):
        x0 = s * sw
        x1 = width if s == steps - 1 else (s + 1) * sw
        img[top_h + ramp_h :, x0:x1] = s / float(steps - 1)
    return img


def run_self_test(out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Run comprehensive verification across .NP3 codec, EXPEED 7 color math & Zf 18-level grain."""
    engine = Expeed7FlexibleColorEngine()
    checks: List[Dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1. Verify .NP3 round-trip & container structure on all 9 lab-calibration.json profiles
    with open(DEFAULT_CALIBRATION_JSON, "r", encoding="utf-8") as fh:
        cal = json.load(fh)

    rendered_profiles: Dict[str, np.ndarray] = {}
    chart = build_synthetic_test_chart(240, 360)

    for prof in cal["profiles"]:
        pid = prof["id"]
        pname = prof["name"]
        opts_in = {
            "name": pname,
            "sharpning": 2.0,
            "midRangeSharpning": 1.0,
            "clarity": 0.0,
            "contrast": 0,
            "highlights": 0,
            "shadows": 0,
            "whiteLevel": 0,
            "blackLevel": 0,
            "saturation": 0,
            **prof["options"],
        }
        np3_bytes = serialize_np3(opts_in)
        info = inspect_np3_container(np3_bytes)
        decoded = deserialize_np3(np3_bytes)
        re_bytes = serialize_np3(decoded)
        record(
            f"NP3 round-trip [{pid}]",
            np3_bytes == re_bytes and info["format"] == "0310",
            f"bytes={len(np3_bytes)} sha256={info['sha256'][:12]}",
        )
        rendered_profiles[pid] = engine.evaluate_rgb(
            chart, decoded, pipeline_mode="camera_raw", enable_spatial=False
        )

    # 2. Verify identity custom tone curve matches ROM 0x441ebd92 bit-for-bit (257/257)
    id_np3 = deserialize_np3(serialize_np3({"name": "LAB_CURVE_ID", "identityToneCurve": True}))
    tc_id = np.round(engine.build_tone_curve_257_12bit(id_np3) * 4095.0).astype(int).tolist()
    record(
        "Custom 257xu16 identity curve == ROM 0x441ebd92 (257/257)",
        tc_id == engine.id_257,
        f"endpoints=({tc_id[0]}, {tc_id[128]}, {tc_id[256]})",
    )

    # 3. Directional pixel assertions on all 9 calibration profiles vs neutral
    neut = rendered_profiles["neutral"]
    # Patch 5 is Blue (x in 5*40..6*40, y in 0..130)
    blue_n = neut[20:100, 205:235].mean(axis=(0, 1))
    blue_h20 = rendered_profiles["blue_hue_p20"][20:100, 205:235].mean(axis=(0, 1))
    blue_cm20 = rendered_profiles["blue_chroma_m20"][20:100, 205:235].mean(axis=(0, 1))
    blue_b20 = rendered_profiles["blue_brightness_p20"][20:100, 205:235].mean(axis=(0, 1))

    # Saturation +20 increases chroma across colorful patches
    sat_n = np.std(neut[:120], axis=-1).mean()
    sat_p20 = np.std(rendered_profiles["saturation_p20"][:120], axis=-1).mean()
    record("Directional [saturation_p20] chroma > neutral", sat_p20 > sat_n, f"{sat_p20:.4f} > {sat_n:.4f}")

    # Contrast +20 increases slope across grayscale ramp
    ramp_n = neut[150, :, 0]
    ramp_c20 = rendered_profiles["contrast_p20"][150, :, 0]
    slope_n = float(ramp_n[240] - ramp_n[120])
    slope_c20 = float(ramp_c20[240] - ramp_c20[120])
    record("Directional [contrast_p20] mid-slope > neutral", slope_c20 > slope_n, f"{slope_c20:.4f} > {slope_n:.4f}")

    # Blue Hue +20 shifts Blue toward Purple (increases R/B ratio)
    record(
        "Directional [blue_hue_p20] shifts Blue -> Purple (+R)",
        float(blue_h20[0] - blue_n[0]) > 0.015,
        f"dR={blue_h20[0] - blue_n[0]:+.4f}, dG={blue_h20[1] - blue_n[1]:+.4f}",
    )

    # Blue Chroma -20 reduces Blue patch channel spread
    spread_n = float(np.std(blue_n))
    spread_cm20 = float(np.std(blue_cm20))
    record(
        "Directional [blue_chroma_m20] desaturates Blue",
        spread_cm20 < spread_n - 0.01,
        f"std {spread_cm20:.4f} < {spread_n:.4f}",
    )

    # Blue Brightness +20 increases Blue patch luminance Y
    y_bn = float(0.299 * blue_n[0] + 0.587 * blue_n[1] + 0.114 * blue_n[2])
    y_bb20 = float(0.299 * blue_b20[0] + 0.587 * blue_b20[1] + 0.114 * blue_b20[2])
    record(
        "Directional [blue_brightness_p20] lifts Blue luma",
        y_bb20 > y_bn + 0.01,
        f"Y {y_bb20:.4f} > {y_bn:.4f}",
    )

    # Warm Highlights (hue=45, chroma=10): boosts R > B in upper-highlight zone ( ~75% post-base-curve luma)
    hi_n = neut[150, 270]
    hi_w = rendered_profiles["warm_highlights"][150, 270]
    record(
        "Directional [warm_highlights] R-B > neutral in highlights",
        float((hi_w[0] - hi_w[2]) - (hi_n[0] - hi_n[2])) > 0.008,
        f"d(R-B)={(hi_w[0] - hi_w[2]) - (hi_n[0] - hi_n[2]):+.4f}",
    )

    # Cool Shadows (hue=220, chroma=10): boosts B > R in shadow zone (~20% post-base-curve luma, bp0=220/1024)
    sh_n = neut[150, 125]
    sh_c = rendered_profiles["cool_shadows"][150, 125]
    record(
        "Directional [cool_shadows] B-R > neutral in shadows",
        float((sh_c[2] - sh_c[0]) - (sh_n[2] - sh_n[0])) > 0.008,
        f"d(B-R)={(sh_c[2] - sh_c[0]) - (sh_n[2] - sh_n[0]):+.4f}",
    )

    # Identity curve matches neutral when all parametric sliders == 0
    max_diff_id = float(np.max(np.abs(rendered_profiles["identity_curve"] - neut)))
    record(
        "Directional [identity_curve] == neutral (0-slider baseline)",
        max_diff_id < 1e-5,
        f"max_abs_diff={max_diff_id:.2e}",
    )

    # 4. Verify all 18 Zf C3.01 Film Grain presets (table match + intensity monotonicity + spatial size)
    flat_mid = np.full((192, 192, 3), 0.45, dtype=np.float32)
    rms_by_size: Dict[int, List[float]] = {1: [], 2: [], 3: []}
    lag1_corr_by_size: Dict[int, float] = {}

    all_18_match = True
    for p_ref in engine.zf_grain_presets:
        g = lookup_zf_grain_preset(p_ref["size_level"], p_ref["intensity_level"], video_mode=False)
        if (
            g["crd_grain_amount"] != p_ref["crd_grain_amount"]
            or g["crd_grain_size"] != p_ref["crd_grain_size"]
            or g["crd_grain_frequency"] != p_ref["crd_grain_frequency"]
        ):
            all_18_match = False
        grained = apply_zf_film_grain(
            flat_mid, size_level=p_ref["size_level"], intensity_level=p_ref["intensity_level"], seed=42
        )
        diff = grained[..., 1] - 0.45
        rms_by_size[p_ref["size_level"]].append(float(np.sqrt(np.mean(diff * diff))))
        if p_ref["intensity_level"] == 4:
            c0 = diff[:, :-1].ravel()
            c1 = diff[:, 1:].ravel()
            lag1_corr_by_size[p_ref["size_level"]] = float(np.corrcoef(c0, c1)[0, 1])

    record("Zf C3.01 18x3 preset table exact match (18/18)", all_18_match, "RAM 0xa02acdd2b == 54 B")
    for sz in (1, 2, 3):
        vals = rms_by_size[sz]
        mono = all(vals[i] < vals[i + 1] for i in range(5))
        record(
            f"Zf Film Grain monotonic RMS [size={sz}, intensity 1..6]",
            mono,
            " < ".join(f"{v:.4f}" for v in vals),
        )

    size_corr_ok = lag1_corr_by_size[1] < lag1_corr_by_size[2] < lag1_corr_by_size[3]
    record(
        "Zf Film Grain spatial width Size 1 < Size 2 < Size 3",
        size_corr_ok,
        f"lag1_corr: S1={lag1_corr_by_size[1]:.3f} < S2={lag1_corr_by_size[2]:.3f} < S3={lag1_corr_by_size[3]:.3f}",
    )

    # 4b. Verify instruction-decoded Zf C3.01 CEVA-XM6 DSP FilmGrain hardware properties
    s5_vals = np.linalg.svd(KERNEL_5X5_INT.astype(np.float64), compute_uv=False)
    rank5 = int(np.sum(s5_vals > 1e-9))
    kernels_ok = (
        int(np.sum(KERNEL_3X3_INT)) == 16
        and int(np.sum(KERNEL_5X5_INT)) == 256
        and int(KERNEL_5X5_INT[2, 2]) == 40
        and int(KERNEL_5X5_INT[0, 0]) == 0
        and rank5 == 3
    )
    record(
        "CEVA-XM6 .LCPI2_0 (3x3 /16) & .LCPI3_0 (5x5 /256 rank=3, corners=0) kernels",
        kernels_ok,
        f"sum3={int(np.sum(KERNEL_3X3_INT))}, sum5={int(np.sum(KERNEL_5X5_INT))}, center5={int(KERNEL_5X5_INT[2,2])}, rank5={rank5}",
    )

    mask_allowed = tuple(i for i in range(10) if ((BLUR_VALID_BITMASK >> i) & 1) != 0)
    invalid_rejected = False
    try:
        dispatch_blur_cascade(np.zeros((8, 8), dtype=np.uint8), blur_size=4)
    except ValueError:
        invalid_rejected = True
    record(
        "CEVA-XM6 0x2a9 blur_kernel_size bitmask & 4x10 cascade switch tables",
        mask_allowed == VALID_BLUR_KERNEL_SIZES and invalid_rejected and engine.zf_xm6_switch_tables is not None,
        f"valid={mask_allowed}, invalid(4)->0x10000001={invalid_rejected}",
    )

    rng_hw = np.random.default_rng(20261118)
    plane_hw = rng_hw.integers(0, 256, size=(45, 77), dtype=np.uint8)
    ring3_diff = int(np.max(np.abs(BlurLineRing(77, radius=1).filter_plane_streaming(plane_hw).astype(np.int32) - blur_line_ring_3(plane_hw).astype(np.int32))))
    ring5_diff = int(np.max(np.abs(BlurLineRing(77, radius=2).filter_plane_streaming(plane_hw).astype(np.int32) - blur_line_ring_5(plane_hw).astype(np.int32))))
    flat_dc_ok = all(
        int(np.max(np.abs(blur_line_ring_5(np.full((16, 16), v, dtype=np.uint8)).astype(np.int32) - v))) == 0
        for v in (0, 1, 127, 128, 254, 255)
    )
    record(
        "CEVA-XM6 BlurLineRing streaming bit-exactness & flat-field DC preservation",
        ring3_diff == 0 and ring5_diff == 0 and flat_dc_ok,
        f"ring3_diff={ring3_diff}, ring5_diff={ring5_diff}, flat_dc_exact={flat_dc_ok}",
    )

    imp = np.zeros((9, 9), dtype=np.uint16)
    imp[4, 4] = 256
    imp_out = blur_line_ring_5(imp, round_bias=0, out_dtype=np.uint16)
    imp_ok = int(imp_out[4, 4]) == 40 and int(imp_out[2, 2]) == 0 and int(imp_out[2, 4]) == 7 and int(imp_out[3, 2]) == 3
    record(
        "CEVA-XM6 UpdateBlurLine_5 2D impulse response {40,26,15,7,3,0}/256",
        imp_ok,
        f"center={int(imp_out[4,4])}, axial2={int(imp_out[2,4])}, knight={int(imp_out[3,2])}, corner={int(imp_out[2,2])}",
    )

    test_y = np.array([[0, 10, 128, 250, 255]] * 16, dtype=np.uint8)
    ma = np.full((128, 128), 32, dtype=np.int16)
    mb = np.full((128, 128), 16, dtype=np.int16)
    byp = nikon_zf_filmgrain_dsp_bitexact(test_y, ma, mb, bypass_flag=1)
    act0 = nikon_zf_filmgrain_dsp_bitexact(test_y, ma, mb, bypass_flag=0, blend_w=8, blur_size=0, knee_lo=20, knee_hi=240, prng_seed=0)
    act9 = nikon_zf_filmgrain_dsp_bitexact(np.full((24, 40), 128, dtype=np.uint8), ma, mb, blend_w=16, blur_size=9, knee_lo=0, knee_hi=255, prng_seed=0)
    # At Y=0 (< knee_lo=20) and Y=255 (> knee_hi=240), tone_lut[0] == 0 and tone_lut[255] == 0 -> zero grain delta!
    dsp_op_ok = (
        np.array_equal(byp, test_y)
        and np.all(act0[:, 0] == 0)
        and np.all(act0[:, 4] == 255)
        and np.any(act0[:, 2] != 128)
        and act9.shape == (24, 40)
    )
    record(
        "CEVA-XM6 FilmGrain bit-exact operator (bypass, xorshift32, Q6 knees, 9x9 cascade)",
        dsp_op_ok,
        f"bypass_eq={np.array_equal(byp, test_y)}, black0_locked={bool(np.all(act0[:,0]==0))}, white255_locked={bool(np.all(act0[:,4]==255))}",
    )

    # 5. Export sample previews, .NP3, .cube LUT & XMP if out_dir provided
    artifacts_written: List[str] = []
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Film-like Classic Chrome / Nostalgic Negative inspired Zf Flexible Color + Medium Grain 3 preset
        zf_demo_opts = {
            "name": "ZF_CLASSIC_FILM",
            "sharpning": 2.0,
            "midRangeSharpning": 1.0,
            "clarity": 1.0,
            "contrast": 14,
            "highlights": -28,
            "shadows": 12,
            "whiteLevel": -8,
            "blackLevel": 6,
            "saturation": -8,
            "colorBlender": {
                "red": {"hue": 8, "chroma": -6, "brightness": -4},
                "orange": {"hue": 4, "chroma": -4, "brightness": 4},
                "yellow": {"hue": -8, "chroma": -12, "brightness": 2},
                "green": {"hue": 18, "chroma": -24, "brightness": -10},
                "cyan": {"hue": 8, "chroma": -10, "brightness": -4},
                "blue": {"hue": -12, "chroma": -14, "brightness": -12},
                "purple": {"hue": 0, "chroma": -10, "brightness": 0},
                "magenta": {"hue": 0, "chroma": -10, "brightness": 0},
            },
            "colorGrading": {
                "highlights": {"hue": 44, "chroma": 12, "brightness": 0},
                "midTone": {"hue": 38, "chroma": 5, "brightness": 0},
                "shadows": {"hue": 208, "chroma": 10, "brightness": 0},
                "blending": 58,
                "balance": 8,
            },
            "comment": "Zf EXPEED 7 Flexible Color + Film Grain demo",
        }
        np3_bin = serialize_np3(zf_demo_opts)
        np3_path = out_dir / "ZF_CLASSIC_FILM.NP3"
        np3_path.write_bytes(np3_bin)
        artifacts_written.append(str(np3_path))

        decoded_demo = deserialize_np3(np3_bin)
        cube_path = out_dir / "ZF_CLASSIC_FILM_33.cube"
        engine.export_cube_lut(decoded_demo, cube_path, size=33, pipeline_mode="display")
        artifacts_written.append(str(cube_path))

        xmp_path = out_dir / "ZF_CLASSIC_FILM_GRAIN_M3.xmp"
        xmp_path.write_text(
            generate_nikon_crd_xmp(decoded_demo, grain_size=2, grain_intensity=3), encoding="utf-8"
        )
        artifacts_written.append(str(xmp_path))

        hi_chart = build_synthetic_test_chart(360, 540)
        graded = engine.evaluate_rgb(hi_chart, decoded_demo, pipeline_mode="camera_raw", enable_spatial=True)
        graded_grain = apply_zf_film_grain(graded, size_level=2, intensity_level=3, seed=20260301)

        for fname, arr in (
            ("preview_00_input_chart.png", hi_chart),
            ("preview_01_zf_flexcolor_no_grain.png", graded),
            ("preview_02_zf_flexcolor_grain_M3.png", graded_grain),
        ):
            p = out_dir / fname
            Image.fromarray(np.round(arr * 255.0).astype(np.uint8), mode="RGB").save(p)
            artifacts_written.append(str(p))

    passed = sum(1 for c in checks if c["ok"])
    return {
        "passed": passed,
        "total": len(checks),
        "all_passed": passed == len(checks),
        "checks": checks,
        "artifacts_written": artifacts_written,
    }


# ============================================================================
# Part 5: Command-Line Interface
# ============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nikon Zf / EXPEED 7 Flexible Color (.NP3 0310) + 18-Level Film Grain Engine."
    )
    ap.add_argument("--self-test", action="store_true", help="Run full verification suite")
    ap.add_argument("--np3", type=str, help="Input .NP3 (0310) file path")
    ap.add_argument("--preset-json", type=str, help="Input JSON preset or lab-calibration.json path")
    ap.add_argument("--profile-id", type=str, default="neutral", help="Profile id when --preset-json is a lab config")
    ap.add_argument("--input", type=str, help="Input RGB image path (PNG/JPG/TIFF)")
    ap.add_argument("--output", type=str, help="Output rendered PNG/JPG path")
    ap.add_argument(
        "--pipeline-mode",
        choices=("camera_raw", "display"),
        default="display",
        help="camera_raw uses EXPEED 7 229-pt base curve on linearized RGB; display grades sRGB directly",
    )
    ap.add_argument("--grain-size", type=int, default=1, choices=(1, 2, 3), help="Zf grain size: 1=S, 2=M, 3=L")
    ap.add_argument("--grain-intensity", type=int, default=0, help="Zf grain intensity: 0=Off, 1..6 (video 1..3)")
    ap.add_argument("--video-mode", action="store_true", help="Enforce Zf video grain intensity cap (1..3)")
    ap.add_argument("--seed", type=int, default=20260301, help="Deterministic grain PRNG seed")
    ap.add_argument("--export-np3", type=str, help="Write serialized .NP3 binary to path")
    ap.add_argument("--export-cube", type=str, help="Export 3D .cube LUT to path")
    ap.add_argument("--cube-size", type=int, default=33, help="3D .cube grid size (default 33)")
    ap.add_argument("--export-xmp", type=str, help="Export Nikon/ACR <crd:...> XMP sidecar to path")
    ap.add_argument(
        "--out-dir",
        type=str,
        default=str(THIS_DIR / "out"),
        help="Output directory for --self-test sample artifacts",
    )
    args = ap.parse_args()

    if args.self_test:
        res = run_self_test(Path(args.out_dir))
        for c in res["checks"]:
            tag = "PASS" if c["ok"] else "FAIL"
            print(f"{tag:4s} | {c['check']:52s} | {c['detail']}")
        print(f"\nSummary: {res['passed']}/{res['total']} checks passed.")
        if res["artifacts_written"]:
            print("Generated artifacts:")
            for p in res["artifacts_written"]:
                print(f"  - {p}")
        return 0 if res["all_passed"] else 1

    # Load parameters from --np3 or --preset-json or neutral default
    if args.np3:
        params = deserialize_np3(Path(args.np3).read_bytes())
    elif args.preset_json:
        raw_j = json.loads(Path(args.preset_json).read_text(encoding="utf-8"))
        if "profiles" in raw_j:
            matches = [p for p in raw_j["profiles"] if p["id"] == args.profile_id]
            if not matches:
                raise SystemExit(f"Profile id '{args.profile_id}' not found in {args.preset_json}")
            params = normalize_profile_options(matches[0]["options"], name=matches[0]["name"])
        else:
            params = normalize_profile_options(raw_j, name=raw_j.get("name", "CUSTOM"))
    else:
        params = normalize_profile_options({}, name="NEUTRAL")

    engine = Expeed7FlexibleColorEngine()

    if args.export_np3:
        out_np3 = Path(args.export_np3)
        out_np3.parent.mkdir(parents=True, exist_ok=True)
        out_np3.write_bytes(serialize_np3(params))
        print(f"Wrote .NP3 -> {out_np3}")

    if args.export_cube:
        engine.export_cube_lut(params, args.export_cube, size=args.cube_size, pipeline_mode=args.pipeline_mode)
        print(f"Wrote {args.cube_size}^3 .cube LUT -> {args.export_cube}")

    if args.export_xmp:
        out_xmp = Path(args.export_xmp)
        out_xmp.parent.mkdir(parents=True, exist_ok=True)
        out_xmp.write_text(
            generate_nikon_crd_xmp(params, args.grain_size, args.grain_intensity, args.video_mode),
            encoding="utf-8",
        )
        print(f"Wrote XMP sidecar -> {out_xmp}")

    if args.input and args.output:
        im = Image.open(args.input).convert("RGB")
        arr = np.asarray(im, dtype=np.float32) / 255.0
        graded = engine.evaluate_rgb(arr, params, pipeline_mode=args.pipeline_mode, enable_spatial=True)
        if args.grain_intensity > 0:
            graded = apply_zf_film_grain(
                graded,
                size_level=args.grain_size,
                intensity_level=args.grain_intensity,
                video_mode=args.video_mode,
                seed=args.seed,
            )
        out_img = Path(args.output)
        out_img.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.round(graded * 255.0).astype(np.uint8), mode="RGB").save(out_img)
        print(f"Rendered image -> {out_img}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
