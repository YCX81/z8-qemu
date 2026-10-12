# SPDX-License-Identifier: GPL-2.0-or-later
"""C10 has genuine guest text resources, never the C9 bitmap under a new ID."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc
import z8_picture_control_labels as labels


class LabelGuards(unittest.TestCase):
    def test_unknown_firmware_is_rejected(self):
        with self.assertRaises(ValueError):
            labels.build_label_plan(bytes(20))


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to pinned C3.11 MAIN')
class NativeLabelContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def setUp(self):
        self.g = pc.PictureControl(self.source)
        self.addCleanup(self.g.close)
        self.g.uc.mem_map(0xA03200000, 0x100000)
        self.plan = labels.build_label_plan(self.source, style_ids=(245, 246, 247, 248, 249))
        for region in self.plan['regions']:
            self.g.uc.mem_map(region['address'], len(region['data']))
            self.g.uc.mem_write(region['address'], region['data'])
        for patch in self.plan['patches']:
            self.assertEqual(bytes(self.g.uc.mem_read(patch['address'], 4)), patch['expected'])
            self.g.uc.mem_write(patch['address'], patch['replacement'])
        self.object = labels.LABEL_DATA + 0x800
        self.request = labels.LABEL_DATA + 0xA00
        self.g.uc.mem_write(self.object, bytes(0x180))
        self.g.uc.mem_write(self.object + 12, struct.pack('<I', 1))

    def word(self, address):
        return struct.unpack('<I', self.g.uc.mem_read(address, 4))[0]

    def resolve(self, resource):
        self.g.uc.mem_write(self.request, struct.pack('<I', resource))
        self.g.call(0x4032E70C, self.request, self.request + 16, self.request + 24, self.request + 28)
        pointer = struct.unpack('<Q', self.g.uc.mem_read(self.request + 16, 8))[0]
        length, style = struct.unpack('<II', self.g.uc.mem_read(self.request + 24, 8))
        return pointer, length, style

    def test_five_new_ids_use_native_text_setter_and_distinct_resource_records(self):
        pointers = []
        for index, resource in enumerate(labels.C10_LABEL_IDS):
            self.assertEqual(self.g.call(0x403F645C, self.object, resource), 1)
            self.assertEqual(self.word(self.object + 12), 3)
            self.assertEqual(self.word(self.object + 8), resource)
            pointer, length, style = self.resolve(resource)
            self.assertEqual(bytes(self.g.uc.mem_read(pointer, length + 1)), b'C-10\0')
            self.assertEqual((length, style), (4, 245 + index))
            pointers.append(pointer)
        self.assertEqual(len(set(pointers)), 5)
        self.assertIn(0x40325D48, self.g.executed)

    def test_reused_row_returns_to_original_bitmap_kind_for_c9(self):
        self.g.call(0x403F645C, self.object, labels.C10_LABEL_IDS[0])
        self.assertEqual(self.g.call(0x403F645C, self.object, 0x046A), 1)
        self.assertEqual(self.word(self.object + 12), 1)
        self.assertEqual(self.word(self.object + 8), 0x046A)
        self.assertIn(0x4032B9EC, self.g.executed)

    def test_original_ascii_c9_resource_is_unchanged(self):
        bank = 0xA032BD4A8
        self.g.uc.mem_write(0xA02F2AC00, struct.pack('<Q', bank))
        self.g.uc.mem_write(bank + 0xB3 * 24, struct.pack('<IIQII', 3, 0, 0x42B04888, 250, 0))
        pointer, length, style = self.resolve(0xB3)
        self.assertEqual((pointer, length, style), (0x42B04888, 3, 250))
        self.assertEqual(bytes(self.g.uc.mem_read(pointer, 4)), b'C-9\0')

    def test_default_uses_verified_lcd_style_without_uninitialized_dynamic_b3(self):
        default = labels.build_label_plan(self.source)
        for region in default['regions']:
            self.g.uc.mem_write(region['address'], region['data'])
        self.g.uc.ctl_remove_cache(labels.LABEL_CODE, labels.LABEL_CODE + 0x1000)
        self.g.uc.mem_write(0xA02F2AC00, bytes(8))
        for resource in labels.C10_LABEL_IDS:
            pointer, length, style = self.resolve(resource)
            self.assertEqual((length, style), (4, 0x21F))
            self.assertEqual(bytes(self.g.uc.mem_read(pointer, 5)), b'C-10\0')

    def test_native_style_indices_are_not_limited_to_eight_bit_font_faces(self):
        labels.build_label_plan(self.source, style_ids=(0, 255, 256, 0x21F, 0x652))
        for style in (256, 0x21F, 0x652):
            self.g.uc.mem_write(labels.TABLE + 12, struct.pack('<I', style))
            pointer, length, result_style = self.resolve(labels.C10_LABEL_IDS[0])
            self.assertEqual((length, result_style), (4, style))
            self.assertEqual(bytes(self.g.uc.mem_read(pointer, 5)), b'C-10\0')
        self.g.uc.mem_write(labels.TABLE + 12, struct.pack('<I', 0x653))
        self.assertEqual(self.resolve(labels.C10_LABEL_IDS[0]), (0, 0, 0))

    def test_unvalidated_style_ids_are_rejected(self):
        for styles in ((1,), (1, 2, 3, 4, -1), (1, 2, 3, 4, 0x653)):
            with self.subTest(styles=styles), self.assertRaises(ValueError):
                labels.build_label_plan(self.source, style_ids=styles)

    def test_plans_are_disjoint_and_new_text_is_owned_not_c9_alias(self):
        for region in self.plan['regions']:
            start, end = region['address'], region['address'] + len(region['data'])
            self.assertTrue(0x45007000 <= start < end <= 0x45008000 or
                            pc.DATA_BASE + 0x37000 <= start < end <= pc.DATA_BASE + 0x38000)
        core = {p['address'] for p in self.g.plan['patches']}
        self.assertFalse(core.intersection(p['address'] for p in self.plan['patches']))


if __name__ == '__main__':
    unittest.main()
