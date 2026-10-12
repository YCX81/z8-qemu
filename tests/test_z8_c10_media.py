# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute authored A64 media code against an explicit NAND register fixture."""
import struct
import sys
from pathlib import Path
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_c10_media import build_media_code, SAVE, LOAD, VALIDATE, BLOCKS, RECORD_BYTES, HEADER_BUFFER, IO_ERROR
from z8_picture_control import _dependencies, DATA_BASE, RECORDS, LOAD_BUFFER
_dependencies()
try:
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
    from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X30, UC_ARM64_REG_PSTATE
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


class Device:
    def __init__(self, pages=None):
        self.pages = {} if pages is None else {k: bytes(v) for k, v in pages.items()}
        self.fail_next_program = False
        self.fail_read_pages = set()
        self.fail_read_occurrences = set()
        self.read_counts = {}
        self.commands = []
        self.mmio_daif = []
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(0x45000000, 0x10000)
        self.uc.mem_map(DATA_BASE, 0x40000)
        self.uc.mem_map(0x17800000, 0x2000)
        self.uc.mem_map(0x17a00000, 0x1000)
        self.uc.mem_write(0x17a0032c, struct.pack('<I', 1))
        self.uc.mem_write(0x17a00300, struct.pack('<I', 7))
        region = build_media_code()
        self.uc.mem_write(region['address'], region['data'])
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self.write, begin=0x17800000, end=0x17800003)
        for first, last in ((0x17800000, 0x17801FFF), (0x17A00000, 0x17A00FFF)):
            self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.observe_mask, begin=first, end=last)
    def observe_mask(self, uc, access, address, size, value, data):
        self.mmio_daif.append(uc.reg_read(UC_ARM64_REG_PSTATE) & 0x3C0)
    def word(self, offset): return struct.unpack('<I', self.uc.mem_read(0x17800000 + offset, 4))[0]
    def write(self, uc, access, address, size, command, data):
        first = self.word(4); count = (command & 255) + 1
        self.commands.append((command, first))
        ok = True
        if command == 0x40001000:
            for page in range(first, first + 64): self.pages.pop(page, None)
        elif command & ~255 in (0x40202100, 0x40202200):
            per_page = self.word(0x404) & 65535
            physical = 0xa00000000 + self.word(8) - 0x40000000
            if command & ~255 == 0x40202200:
                self.read_counts[first] = self.read_counts.get(first, 0) + 1
            read_failed = first in self.fail_read_pages or (first, self.read_counts.get(first, 0)) in self.fail_read_occurrences
            if command & ~255 == 0x40202200 and read_failed:
                ok = False
            elif command & ~255 == 0x40202100 and self.fail_next_program:
                self.fail_next_program = False; ok = False
            else:
                for i in range(count):
                    old = self.pages.get(first + i, bytes([255]) * 2048)
                    if command & ~255 == 0x40202200:
                        uc.mem_write(physical + i * per_page, old[:per_page])
                    else:
                        incoming = uc.mem_read(physical + i * per_page, per_page)
                        self.pages[first + i] = bytes(a & b for a, b in zip(old[:per_page], incoming))
        else: ok = False
        uc.mem_write(0x17800014, struct.pack('<I', 0x8000 if ok else 0xc000))
    def call(self, entry, pointer, size=RECORD_BYTES, *, daif=None):
        if daif is not None:
            status = self.uc.reg_read(UC_ARM64_REG_PSTATE)
            self.uc.reg_write(UC_ARM64_REG_PSTATE, status & ~0x3C0 | daif)
        self.uc.reg_write(UC_ARM64_REG_SP, DATA_BASE + 0x3fff0)
        self.uc.reg_write(UC_ARM64_REG_X0, pointer)
        self.uc.reg_write(UC_ARM64_REG_X1, size)
        self.uc.reg_write(UC_ARM64_REG_X30, 0x4500fff0)
        self.uc.emu_start(entry, 0x4500fff0, count=12000000)
        if self.uc.reg_read(UC_ARM64_REG_PC) != 0x4500fff0: raise RuntimeError('Guest did not return')
        return self.uc.reg_read(UC_ARM64_REG_X0)


@unittest.skipUnless(AVAILABLE, 'Install requirements-c10.txt for authored A64 execution')
class GuestMediaTests(unittest.TestCase):
    def test_cold_load_without_valid_record_fails(self):
        device = Device()
        self.assertEqual(device.call(LOAD, LOAD_BUFFER), 0)

    def test_save_and_load_in_a_new_cpu_instance(self):
        first = Device(); payload = bytes((i * 7) & 255 for i in range(RECORD_BYTES))
        first.uc.mem_write(RECORDS, payload)
        self.assertEqual(first.call(SAVE, RECORDS), RECORD_BYTES)
        second = Device(first.pages)
        self.assertEqual(second.call(LOAD, LOAD_BUFFER), RECORD_BYTES)
        self.assertEqual(bytes(second.uc.mem_read(LOAD_BUFFER, RECORD_BYTES)), payload)

    def test_failed_replacement_keeps_previous_generation(self):
        device = Device(); old = b'A' * RECORD_BYTES
        device.uc.mem_write(RECORDS, old); self.assertEqual(device.call(SAVE, RECORDS), RECORD_BYTES)
        device.uc.mem_write(RECORDS, b'B' * RECORD_BYTES); device.fail_next_program = True
        self.assertEqual(device.call(SAVE, RECORDS), 0)
        cold = Device(device.pages)
        self.assertEqual(cold.call(LOAD, LOAD_BUFFER), RECORD_BYTES)
        self.assertEqual(bytes(cold.uc.mem_read(LOAD_BUFFER, RECORD_BYTES)), old)

    def test_corrupt_latest_payload_falls_back(self):
        device = Device()
        for value in (b'A', b'B'):
            device.uc.mem_write(RECORDS, value * RECORD_BYTES)
            self.assertEqual(device.call(SAVE, RECORDS), RECORD_BYTES)
        device.pages[BLOCKS[1] + 1] = b'X' * 2048
        cold = Device(device.pages)
        self.assertEqual(cold.call(LOAD, LOAD_BUFFER), RECORD_BYTES)
        self.assertEqual(bytes(cold.uc.mem_read(LOAD_BUFFER, RECORD_BYTES)), b'A' * RECORD_BYTES)

    def test_wrong_size_never_touches_controller(self):
        device = Device()
        self.assertEqual(device.call(SAVE, RECORDS, 17), 0)
        self.assertEqual(device.commands, [])

    def test_scan_read_error_never_erases_or_programs_either_generation(self):
        seed = Device()
        for value in (b'A', b'B'):
            seed.uc.mem_write(RECORDS, value * RECORD_BYTES)
            self.assertEqual(seed.call(SAVE, RECORDS), RECORD_BYTES)
        for page in (BLOCKS[0], BLOCKS[0] + 1, BLOCKS[1], BLOCKS[1] + 1):
            with self.subTest(failing_page=page):
                device = Device(seed.pages)
                before = dict(device.pages)
                device.fail_read_pages.add(page)
                device.uc.mem_write(RECORDS, b'C' * RECORD_BYTES)
                self.assertEqual(device.call(SAVE, RECORDS), 0)
                self.assertEqual(device.pages, before)
                self.assertTrue(all(command & ~255 == 0x40202200
                                    for command, _ in device.commands))

    def test_scan_read_error_is_not_successful_load_or_invalid_empty_media(self):
        seed = Device()
        seed.uc.mem_write(RECORDS, b'A' * RECORD_BYTES)
        self.assertEqual(seed.call(SAVE, RECORDS), RECORD_BYTES)
        for page in (BLOCKS[0], BLOCKS[0] + 1, BLOCKS[1]):
            with self.subTest(failing_page=page):
                device = Device(seed.pages)
                device.fail_read_pages.add(page)
                device.uc.mem_write(RECORDS, b'Z' * RECORD_BYTES)
                self.assertEqual(device.call(LOAD, RECORDS), 0)
                self.assertEqual(bytes(device.uc.mem_read(RECORDS, RECORD_BYTES)), b'Z' * RECORD_BYTES)

    def test_save_rejects_any_overlap_with_scan_scratch_before_io(self):
        for pointer in (LOAD_BUFFER, LOAD_BUFFER - 16,
                        LOAD_BUFFER + RECORD_BYTES - 16,
                        HEADER_BUFFER, HEADER_BUFFER - RECORD_BYTES + 16,
                        HEADER_BUFFER + 0x7F0):
            with self.subTest(pointer=hex(pointer)):
                device = Device()
                # Do not seed an invalid pointer: rejection must precede dereference.
                self.assertEqual(device.call(SAVE, pointer), 0)
                self.assertEqual(device.commands, [])

    def test_validate_distinguishes_erased_media_from_read_error(self):
        device = Device()
        self.assertEqual(device.call(VALIDATE, BLOCKS[0]), 0)
        device.fail_read_pages.add(BLOCKS[0])
        self.assertEqual(device.call(VALIDATE, BLOCKS[0]), IO_ERROR)

    def test_load_read_error_after_successful_scan_does_not_copy_scratch_as_success(self):
        seed = Device()
        for value in (b'A', b'B'):
            seed.uc.mem_write(RECORDS, value * RECORD_BYTES)
            self.assertEqual(seed.call(SAVE, RECORDS), RECORD_BYTES)
        for page in (BLOCKS[1], BLOCKS[1] + 1):
            with self.subTest(failing_page=page):
                device = Device(seed.pages)
                device.fail_read_occurrences.add((page, 2))
                device.uc.mem_write(RECORDS, b'Z' * RECORD_BYTES)
                self.assertEqual(device.call(LOAD, RECORDS), 0)
                self.assertEqual(bytes(device.uc.mem_read(RECORDS, RECORD_BYTES)), b'Z' * RECORD_BYTES)

    def test_generation_may_not_increment_into_io_error_sentinel(self):
        device = Device()
        device.uc.mem_write(RECORDS, b'A' * RECORD_BYTES)
        self.assertEqual(device.call(SAVE, RECORDS), RECORD_BYTES)
        header = bytearray(device.pages[BLOCKS[0]])
        struct.pack_into('<Q', header, 16, IO_ERROR - 1)
        struct.pack_into('<I', header, 28, zlib.crc32(header[:28]))
        device.pages[BLOCKS[0]] = bytes(header)
        before = dict(device.pages)
        device.commands.clear()
        self.assertEqual(device.call(SAVE, RECORDS), 0)
        self.assertEqual(device.pages, before)
        self.assertTrue(all(command & ~255 == 0x40202200 for command, _ in device.commands))

    def test_successful_save_and_load_mask_irq_fiq_only_and_restore_original_daif(self):
        for incoming in (0, 0x40, 0x80, 0xC0, 0x100, 0x200, 0x300, 0x3C0):
            with self.subTest(incoming_daif=hex(incoming)):
                first = Device()
                first.uc.mem_write(RECORDS, b'M' * RECORD_BYTES)
                self.assertEqual(first.call(SAVE, RECORDS, daif=incoming), RECORD_BYTES)
                self.assertTrue(first.mmio_daif)
                self.assertEqual(set(first.mmio_daif), {incoming | 0xC0})
                self.assertEqual(first.uc.reg_read(UC_ARM64_REG_PSTATE) & 0x3C0, incoming)
                second = Device(first.pages)
                self.assertEqual(second.call(LOAD, LOAD_BUFFER, daif=incoming), RECORD_BYTES)
                self.assertTrue(second.mmio_daif)
                self.assertEqual(set(second.mmio_daif), {incoming | 0xC0})
                self.assertEqual(second.uc.reg_read(UC_ARM64_REG_PSTATE) & 0x3C0, incoming)

    def test_failed_save_and_load_restore_unmasked_irq_after_mmio_or_preflight_failure(self):
        for kind in ('save_read', 'save_program', 'load_read', 'load_empty', 'save_size', 'load_size'):
            with self.subTest(failure=kind):
                device = Device()
                device.uc.mem_write(RECORDS, b'F' * RECORD_BYTES)
                if kind.endswith('read'):
                    device.fail_read_pages.add(BLOCKS[0])
                if kind == 'save_program':
                    device.fail_next_program = True
                entry, pointer = (SAVE, RECORDS) if kind.startswith('save') else (LOAD, LOAD_BUFFER)
                size = 17 if kind.endswith('size') else RECORD_BYTES
                self.assertEqual(device.call(entry, pointer, size, daif=0x300), 0)
                self.assertEqual(device.uc.reg_read(UC_ARM64_REG_PSTATE) & 0x3C0, 0x300)
                if kind.endswith('size'):
                    self.assertEqual(device.mmio_daif, [])
                else:
                    self.assertTrue(device.mmio_daif)
                    self.assertEqual(set(device.mmio_daif), {0x3C0})


if __name__ == '__main__': unittest.main()
