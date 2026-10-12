# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded C10 RAM reservation in a simulation-only derived MAIN.

The original General heap descriptor is shortened by 256 KiB. Its removed
tail belongs to the experimental extension; the adjacent Heap is unchanged.
This emits a patch description, never writes the supplied firmware or a file.
Runtime allocator construction and other arena users require separate proof.
"""
import hashlib
import struct

MAIN_BYTES = 81_807_709
MAIN_SHA256 = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
IMAGE_BASE = 0x40160000
GENERAL_BASE, GENERAL_BYTES = 0xA10C2B000, 0xB00000
SECOND_HEAP_BASE, SECOND_HEAP_BYTES = 0xA1172B000, 0x400000
DATA_BASE, DATA_BYTES = 0xA116EB000, 0x40000
RESERVED_END = DATA_BASE + DATA_BYTES
GENERAL_USABLE_BYTES = DATA_BASE - GENERAL_BASE
DESCRIPTOR_ADDRESS = 0x43F93CE8
DESCRIPTOR_OFFSET = DESCRIPTOR_ADDRESS - IMAGE_BASE
PATCH_ADDRESS = DESCRIPTOR_ADDRESS + 8
PATCH_OFFSET = DESCRIPTOR_OFFSET + 8
NATIVE_HEAP_RECORDS = 0xA03203DC8


def reservation_patch(source):
    """Return ``{'patch': guarded_patch, 'metadata': evidence}``.

    Only the pinned original input is accepted. Already-derived inputs and
    changed adjacent heap records are rejected before any output is returned.
    """
    if not isinstance(source, (bytes, bytearray)) or len(source) != MAIN_BYTES:
        raise ValueError('C10 reservation requires the pinned MAIN size and binary type')
    identity = hashlib.sha256(source).hexdigest()
    if identity != MAIN_SHA256:
        raise ValueError('C10 reservation requires the pinned original MAIN identity')
    expected_descriptor = (
        struct.pack('<QI12s', GENERAL_BASE, GENERAL_BYTES, b'General') +
        struct.pack('<QI12s', SECOND_HEAP_BASE, SECOND_HEAP_BYTES, b'Heap'))
    if source[DESCRIPTOR_OFFSET:DESCRIPTOR_OFFSET + 48] != expected_descriptor:
        raise ValueError('Original General/Heap descriptor differs from the reviewed layout')
    change = dict(address=PATCH_ADDRESS, source_offset=PATCH_OFFSET,
                  expected=struct.pack('<I', GENERAL_BYTES),
                  replacement=struct.pack('<I', GENERAL_USABLE_BYTES),
                  name='simulation C10 reservation: remove General heap tail 256 KiB')
    metadata = dict(simulation_only=True, source_sha256=identity,
                    runtime_allocator_verified=False, physical_camera_verified=False,
                    general_before=dict(address=hex(GENERAL_BASE), bytes=GENERAL_BYTES,
                                        end=hex(GENERAL_BASE + GENERAL_BYTES)),
                    general_after=dict(address=hex(GENERAL_BASE), bytes=GENERAL_USABLE_BYTES,
                                       end=hex(DATA_BASE)),
                    reserved=dict(address=hex(DATA_BASE), bytes=DATA_BYTES, end=hex(RESERVED_END)),
                    adjacent_heap=dict(address=hex(SECOND_HEAP_BASE), bytes=SECOND_HEAP_BYTES,
                                       unchanged=True),
                    original_constructor_chain=['0x40d695b0', '0x40d2c388', '0x4016def0'],
                    runtime_record_check=dict(address=hex(NATIVE_HEAP_RECORDS), stride=0x20,
                                              handle_offset=0, size_offset=8, base_offset=0x10),
                    scope='Derived QEMU image only; not a physical camera free-space claim')
    return dict(patch=change, metadata=metadata)
