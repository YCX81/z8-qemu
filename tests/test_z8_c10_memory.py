# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic guard tests; original firmware integration is checked separately."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_c10_memory as memory


class ReservedHeapTail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = bytearray(81_807_709)
        source[0x3e33ce8:0x3e33d18] = (
            struct.pack('<QI12s', 0xa10c2b000, 0xb00000, b'General') +
            struct.pack('<QI12s', 0xa1172b000, 0x400000, b'Heap'))
        cls.source = bytes(source)
        cls.sha = hashlib.sha256(cls.source).hexdigest()

    def generate(self, source=None):
        source = self.source if source is None else source
        # A synthetic fixture is never represented as Nikon firmware evidence.
        with patch.object(memory, 'MAIN_SHA256', hashlib.sha256(source).hexdigest()):
            return memory.reservation_patch(source)

    def test_exact_tail_is_removed_from_general_and_second_heap_is_unchanged(self):
        result = self.generate()
        change = result['patch']
        self.assertEqual(change['address'], 0x43f93cf0)
        self.assertEqual(change['source_offset'], 0x3e33cf0)
        self.assertEqual(change['expected'], struct.pack('<I', 0xb00000))
        self.assertEqual(change['replacement'], struct.pack('<I', 0xac0000))
        derived = bytearray(self.source)
        offset = change['source_offset']
        derived[offset:offset + 4] = change['replacement']
        start, size = struct.unpack_from('<QI', derived, 0x3e33ce8)
        self.assertEqual(start + size, memory.DATA_BASE)
        self.assertEqual(memory.DATA_BASE + memory.DATA_BYTES, 0xa1172b000)
        self.assertEqual(derived[0x3e33d00:0x3e33d18], self.source[0x3e33d00:0x3e33d18])
        self.assertEqual(derived[:offset], self.source[:offset])
        self.assertEqual(derived[offset + 4:], self.source[offset + 4:])
        self.assertEqual(hashlib.sha256(self.source).hexdigest(), self.sha)

    def test_metadata_records_source_guard_and_unverified_runtime_boundary(self):
        metadata = self.generate()['metadata']
        self.assertEqual(metadata['source_sha256'], self.sha)
        self.assertEqual(metadata['general_before']['bytes'], 0xb00000)
        self.assertEqual(metadata['general_after']['bytes'], 0xac0000)
        self.assertEqual(metadata['reserved']['address'], hex(memory.DATA_BASE))
        self.assertEqual(metadata['reserved']['bytes'], 0x40000)
        self.assertFalse(metadata['runtime_allocator_verified'])
        self.assertFalse(metadata['physical_camera_verified'])
        self.assertTrue(metadata['simulation_only'])

    def test_rejects_unpinned_identity_even_when_descriptor_matches(self):
        with self.assertRaisesRegex(ValueError, 'identity'):
            memory.reservation_patch(self.source)

    def test_rejects_wrong_length_and_type_before_reading_descriptor(self):
        for source in (b'', self.source[:-1], 'not binary', None):
            with self.subTest(kind=type(source).__name__), self.assertRaises(ValueError):
                memory.reservation_patch(source)

    def test_descriptor_bases_names_sizes_and_adjacent_heap_are_guarded(self):
        for offset in (0x3e33ce8, 0x3e33cf0, 0x3e33cf4, 0x3e33d00, 0x3e33d08, 0x3e33d0c):
            changed = bytearray(self.source)
            changed[offset] ^= 1
            with self.subTest(offset=hex(offset)), self.assertRaisesRegex(ValueError, 'descriptor'):
                self.generate(changed)

    def test_reapplying_shrink_is_rejected_not_subtracted_twice(self):
        changed = bytearray(self.source)
        struct.pack_into('<I', changed, 0x3e33cf0, 0xac0000)
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            self.generate(changed)


if __name__ == '__main__':
    unittest.main()
