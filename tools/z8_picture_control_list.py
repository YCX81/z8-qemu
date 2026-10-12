# SPDX-License-Identifier: GPL-2.0-or-later
"""Simulation-only adapter for original nine-byte Picture Control list callers.

The stock 410d951c builds only nine availability bytes. Expanding the original
410aac98 loop to ten therefore requires a new input object, not a tenth read
from that caller. Owned records are authoritative after isolated media LOAD.
This plan leaves the original list writer, notifier and mutex tail in place.
"""
from z8_picture_control import (BASE, RECORDS, MAIN_SHA256,
                                _assemble, _check_source, _constant, build_plan)

LIST_CODE = 0x45003a00
LIST_BYTES = 0x600
TRAMPOLINE = LIST_CODE + 0x100
NATIVE_LIST = 0x410aac98
NATIVE_REBUILD = 0x410d951c


def build_list_plan(source, *, core_plan=None):
    """Return one guarded entry patch and code in the reserved zero core tail.

    Root composition must remove [45003a00,45004000) from the core loader
    region before adding this region. The independently built core remains
    unchanged. No caller bitmap byte is read, including for the tenth slot.
    Mode 0/1 uses owned bank 0/1, u32 validity exactly 1, stride 400. Other
    modes retain the original function's no-list-update path.
    """
    source = _check_source(source)
    if core_plan is None:
        core_plan = build_plan(source)
    owners = [r for r in core_plan['regions'] if r['permissions'] == 'rx' and
              r['address'] <= LIST_CODE and
              LIST_CODE + LIST_BYTES <= r['address'] + len(r['data'])]
    if len(owners) != 1:
        raise ValueError('List adapter requires the reserved zero core tail')
    owner = owners[0]
    tail = owner['data'][LIST_CODE - owner['address']:LIST_CODE - owner['address'] + LIST_BYTES]
    if any(tail):
        raise ValueError('List adapter code reservation is not zero in the core plan')
    for address in (0x410aad14, 0x410aad80):
        matches = [p for p in core_plan['patches'] if p['address'] == address]
        if len(matches) != 1 or matches[0]['replacement'] != _assemble('cmp x9,#10', address):
            raise ValueError('List adapter requires the guarded ten-entry core loops')
    displaced = source[NATIVE_LIST - BASE:NATIVE_LIST - BASE + 4]
    if displaced != bytes.fromhex('f553bea9'):
        raise ValueError('Original list prologue is not the verified stack instruction')
    code = bytearray(LIST_BYTES)
    body = _assemble(f'''
        cmp w1,#1; b.hi #{TRAMPOLINE};
        str x30,[sp,#-32]!;
        {_constant('x8', RECORDS + 64)}
        uxtw x9,w1; mov x10,#0x2800; madd x8,x9,x10,x8;
        mov x9,#0; add x11,sp,#16;
        scan: ldr w10,[x8]; cmp w10,#1; cset w10,eq;
        strb w10,[x11,x9]; add x8,x8,#0x400;
        add x9,x9,#1; cmp x9,#10; b.ne scan;
        mov x0,x11; bl #{TRAMPOLINE};
        ldr x30,[sp],#32; ret;
    ''', LIST_CODE)
    if len(body) > TRAMPOLINE - LIST_CODE:
        raise ValueError('List adapter exceeds its reserved entry capacity')
    code[:len(body)] = body
    trampoline = displaced + _assemble(f'b #{NATIVE_LIST + 4}', TRAMPOLINE + 4)
    code[TRAMPOLINE - LIST_CODE:TRAMPOLINE - LIST_CODE + len(trampoline)] = trampoline
    patch = dict(address=NATIVE_LIST, source_offset=NATIVE_LIST - BASE,
                 expected=displaced, replacement=_assemble(f'b #{LIST_CODE}', NATIVE_LIST),
                 name='Rebuild ten-byte availability from owned records for original list callers')
    return dict(main_sha256=MAIN_SHA256, simulation_only=True,
                entries=dict(list_adapter=LIST_CODE, original_list_trampoline=TRAMPOLINE),
                patches=[patch], regions=[dict(address=LIST_CODE, data=bytes(code), permissions='rx')],
                core_tail_reservation=dict(address=LIST_CODE, bytes=LIST_BYTES, verified_zero=True),
                caller_bitmap_bytes_read=0, owned_record_validity=1,
                original_rebuild_unchanged=NATIVE_REBUILD,
                limitations=['Original mutex and notification behavior require whole-guest validation',
                             'Owned record restoration does not establish physical camera persistence'])
