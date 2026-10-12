# SPDX-License-Identifier: GPL-2.0-or-later
"""Original runtime MMU mapping after its gap-unmap phase."""
import struct
from probe_adc_capture import at
from z8_native_call import invoke

def prepare_code_mapping(guest, layout):
    """Register RX code and RW/NX data after the original gap unmap.

    The runtime mapper owns its page-table bitmap and performs native TLB
    maintenance. Mapping alone does not reserve the data from other heaps.
    """
    guest.rsp('z1,40160000,4')
    cpu, registers = at(guest, 0x4016e6a8)
    context = 0xa04b58d00
    before = guest.read_bytes(context, 0x40)
    root, pool, pages = struct.unpack_from('<QQI', before)
    with guest.firmware.open('rb') as stream:
        stream.seek(0x8000)
        descriptor = stream.read(0x160)
    root_bytes = (struct.unpack_from('<Q', descriptor, 0x100)[0] + 4095) & ~4095
    pool_bytes = (struct.unpack_from('<Q', descriptor, 0x118)[0] + 4095) & ~4095
    if (cpu != 0 or before[0x38] != 0 or
            root != struct.unpack_from('<Q', descriptor, 0x108)[0] or
            pool != root + root_bytes or pages != pool_bytes // 4096):
        raise RuntimeError('Original runtime MMU context does not match the pinned layout')
    # These 128 bitmap bits are explicitly initialized by 40172110/40172118.
    free = [n for n in range(min(pages, 128))
            if not (before[0x14 + n // 8] & (1 << (n % 8)))]
    if len(free) < 4:
        raise RuntimeError('Original runtime page-table pool has insufficient free pages')
    stack_base, stack_size = struct.unpack_from('<QQ', descriptor, 8)
    if not stack_base + 0x1000 <= registers['SP'] <= stack_base + stack_size:
        raise RuntimeError('Runtime mapping requires the verified CPU0 startup stack')

    def leaf(va):
        table = root
        for level, shift in ((1, 30), (2, 21), (3, 12)):
            if not (root <= table < root + root_bytes or pool <= table < pool + pool_bytes):
                raise RuntimeError('Page walk escaped the original table allocation')
            address = table + ((va >> shift) & 511) * 8
            value = struct.unpack('<Q', guest.read_bytes(address, 8))[0]
            if not value & 1 or level == 3 or value & 3 != 3:
                return dict(level=level, address=hex(address), value=hex(value))
            table = value & 0xffffff000

    ranges = ((layout.code_base, layout.code_bytes, 0xf, 0x7cf),
              (layout.data_base, layout.reserved_bytes, 0xc, 0x6000000000074f))
    preserved = {hex(va): leaf(va) for va in (0x40168000, 0x41647000)}
    before_walks = {}
    for va, size, _, _ in ranges:
        for page in range(va, va + size, 4096):
            row = leaf(page)
            if int(row['value'], 16) & 1:
                raise RuntimeError('C10 simulation range already has a valid mapping')
            before_walks[hex(page)] = row
    for va, size, flags, _ in ranges:
        call = invoke(guest, 0x40172b88,
                      [context, va, size, va, flags, 1, 0xffffffff],
                      cpu=cpu, stack_top=registers['SP'], isolated=True)
        if call['result_x0'] != 0:
            raise RuntimeError('Original runtime mapper rejected the C10 mapping')
    after_walks = {}
    for va, size, _, attributes in ranges:
        for page in range(va, va + size, 4096):
            row = leaf(page)
            if row['level'] != 3 or int(row['value'], 16) != page | attributes:
                raise RuntimeError('C10 PTE physical address or RX/RW-NX attributes differ')
            after_walks[hex(page)] = row
    if any(leaf(int(va, 16)) != old for va, old in preserved.items()):
        raise RuntimeError('C10 mapping changed original text or rodata permissions')
    guest.data['c10_code_mapping'] = dict(native_mapper='0x40172b88',
        after_native_gap_unmap_pc='0x4016e6a8', context=hex(context),
        context_before=before.hex(), context_after=guest.read_bytes(context, 0x40).hex(),
        checked_pool_bytes=pool_bytes, free_pages_before=len(free),
        mappings=[dict(address=hex(va), physical=hex(va), bytes=size, flags=flags)
                  for va, size, flags, _ in ranges],
        ptes_before=before_walks, ptes_after=after_walks,
        original_permissions_preserved=preserved,
        physical_camera_region_verified=False, native_data_allocator_reservation_verified=False)
    # Stock boot's transport entry cleanup expects this breakpoint to exist.
    guest.rsp('Z1,40160000,4')
