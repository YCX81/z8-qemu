# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive a local emulator MAIN from stock and exact external patch bytes.

This does not build an update container or operate a card/camera. Original bytes
are read from the caller's hash-checked input and are never part of this source.
"""
import argparse
import json
from pathlib import Path
import struct

from z8_he_v3_audit import load_prebuilt, PIN
from z8_he_history_audit import need, digest

BASE = 0x40160000


def branch(at, to):
    delta = to - at
    need(delta % 4 == 0 and -(1 << 27) <= delta < (1 << 27), 'Branch range')
    return struct.pack('<I', 0x94000000 | ((delta // 4) & 0x3ffffff))


def derive(stock, toolkit):
    pin = json.loads(PIN.read_text())
    need(digest(stock) == pin['stock_main_sha256'], 'Only exact stock MAIN is accepted')
    code, _, profile = load_prebuilt(toolkit)
    need(len(stock) == profile['payload_bytes'] - 2, 'MAIN must exclude the module CRC trailer')
    for region in profile['ranges']:
        a, b = int(region['start'], 16), int(region['end'], 16)
        need(digest(stock[a:b]) == region['sha256'], 'Dependency window differs: ' + region['purpose'])
    entries = {k: int(v, 16) for k, v in profile['entries'].items()}
    targets = {
        0x40f6fe90: 'precapture_history_entry', 0x40ea3638: 'precapture_write_file',
        0x40eaeb60: 'precapture_main_rc', 0x40f6ff38: 'precapture_current_entry',
        0x41048e1c: 'precapture_y_size_entry', 0x40ea9fac: 'precapture_y_alloc_entry',
        0x40eaeeec: 'precapture_main_step', 0x40eaef10: 'precapture_main_step',
    }
    values = {at - BASE: branch(at, entries[name]) for at, name in targets.items()}
    # Authored MOV X0,X23; BL; NOP and text-copy MOV/MOVK, not extracted guards.
    values[0xd4915c] = struct.pack('<I', 0xaa1703e0) + branch(0x40ea9160, entries['precapture_tdec_release']) + struct.pack('<I', 0xd503201f)
    values.update({0x4d2ffc0: struct.pack('<I', 0x1082600), 0x4d2ffcc: struct.pack('<I', 0x1082600),
                   0x800000: code, 0x1024d8c: struct.pack('<II', 0xd2800008, 0xf2a812e8)})
    result = bytearray(stock)
    rows = []
    last = 0
    for row in sorted(profile['patches'], key=lambda x: int(x['offset'], 16)):
        at, n = int(row['offset'], 16), row['bytes']
        replacement = values[at]
        need(at >= last and at + n <= len(stock), 'Overlapping/out-of-range edits')
        need(digest(stock[at:at+n]) == row['old_sha256'], 'Patch input differs')
        need(len(replacement) == n and digest(replacement) == row['new_sha256'], 'Authored edit differs from reviewed recipe')
        need(result[last:at] == stock[last:at], 'Undeclared edit')
        result[at:at+n] = replacement
        last = at + n
        rows.append(dict(offset=at, bytes=n, purpose=row['purpose'], sha256=digest(replacement)))
    need(result[last:] == stock[last:], 'Trailing undeclared edit')
    return bytes(result), dict(schema=1, upstream_commit=pin['commit'], stock_sha256=digest(stock),
                              executed_main_sha256=digest(result), prebuilt_sha256=digest(code), edits=rows,
                              classification='Local derived emulator MAIN; not an update container',
                              camera_update_container=False, hardware_verified=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stock-main', type=Path, required=True)
    p.add_argument('--toolkit', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    need(not a.output.exists(), 'New output directory required')
    result, receipt = derive(a.stock_main.read_bytes(), a.toolkit)
    a.output.mkdir(parents=True)
    (a.output/'he-v3-main.bin').write_bytes(result)
    (a.output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__': main()
