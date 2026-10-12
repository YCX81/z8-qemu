# SPDX-License-Identifier: GPL-2.0-or-later
"""Native GUI-array/selection contracts, separate from pixel acceptance."""
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc
import z8_picture_control_menu as menu


class GuardContract(unittest.TestCase):
    def test_plan_rejects_unidentified_firmware(self):
        with self.assertRaises(ValueError):
            menu.build_menu_plan(bytes(16))


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to original C3.11 MAIN')
class NativeMenuContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def setUp(self):
        self.g = pc.PictureControl(self.source)
        self.addCleanup(self.g.close)
        self.plan = menu.build_menu_plan(self.source)
        self.g.uc.mem_map(0xA03200000, 0x300000)
        for region in self.plan['regions']:
            self.g.uc.mem_map(region['address'], (len(region['data']) + 4095) & ~4095)
            self.g.uc.mem_write(region['address'], region['data'])
        for patch in self.plan['patches']:
            self.assertEqual(bytes(self.g.uc.mem_read(patch['address'], len(patch['expected']))), patch['expected'])
            self.g.uc.mem_write(patch['address'], patch['replacement'])
        from unicorn import UC_HOOK_CODE
        self.environment_calls = []
        self.enabled, self.monochrome = True, False

        def environment(uc, address, size, _):
            # Fixed enabled/not-monochrome GUI policy input. Record lookup,
            # parser output, selector mapping and constructor remain guest code.
            fixture = {0x403F9E64: int(self.enabled), 0x403F9880: 0x20 if self.monochrome else 0}
            if address in fixture:
                self.environment_calls.append(address)
                uc.reg_write(self.g.r.UC_ARM64_REG_X0, fixture[address])
                uc.reg_write(self.g.r.UC_ARM64_REG_PC, uc.reg_read(self.g.r.UC_ARM64_REG_LR))
        self.environment_hook = self.g.uc.hook_add(UC_HOOK_CODE, environment)
        self.addCleanup(lambda: self.g.uc.hook_del(self.environment_hook) if self.g.uc else None)

    def read32(self, address):
        return struct.unpack('<I', self.g.uc.mem_read(address, 4))[0]

    def test_regions_do_not_overlap_core_or_root_scratch(self):
        for region in self.plan['regions']:
            start, end = region['address'], region['address'] + len(region['data'])
            self.assertTrue(0x45004000 <= start < end <= 0x45007000 or
                            pc.DATA_BASE + 0x32000 <= start < end <= pc.DATA_BASE + 0x38000)
        old = pc.build_plan(self.source)
        touched = {p['address'] + i for p in old['patches'] for i in range(len(p['expected']))}
        for patch in self.plan['patches']:
            self.assertFalse(any(patch['address'] + i in touched for i in range(len(patch['expected']))))

    def test_bad_patch_site_definition_fails_before_a_plan_is_returned(self):
        broken = dict(menu.LITERALS)
        broken[0xA03417898] = (0x40160000,)
        with patch.object(menu, 'LITERALS', broken), self.assertRaisesRegex(ValueError, 'audited 64-bit MOVZ'):
            menu.build_menu_plan(self.source)

    def test_unexpected_assembler_width_cannot_replace_adjacent_instruction(self):
        with patch.object(menu, '_assemble', return_value=bytes(8)), self.assertRaisesRegex(ValueError, 'one original instruction'):
            menu.build_menu_plan(self.source)

    def test_native_constructor_retains_nine_and_adds_distinct_c10(self):
        for slot in (1, 9, 10):
            self.g.import_np3(slot, pc.make_np3('ORIGINAL %d' % slot, seed=slot))
        count = menu.MENU_DATA + 0x4F00
        self.g.call(0x4045341C, 1, count)
        self.assertEqual(self.read32(count), 3)
        self.assertEqual(tuple(struct.unpack('<3I', self.g.uc.mem_read(menu.SELECTORS, 12))), (31, 39, 40))
        labels = struct.unpack('<3H', self.g.uc.mem_read(menu.LABELS + 31 * 2, 6))
        self.assertEqual(labels[:2], (0x045A, 0x046A))
        self.assertEqual(labels[2], menu.C10_LABEL_IDS[0])
        self.assertIn(0x40453544, self.g.executed)
        self.assertEqual(self.g.call(0x404A7040, 40, 1), 33)
        self.assertEqual(self.g.call(0x404A7040, 28, 1), 31)
        self.assertEqual(self.g.call(0x404A7040, 36, 1), 32)

    def test_mode_one_uses_90_and_never_borrows_builtin_code37(self):
        self.g.import_np3(10, pc.make_np3('VIDEO TENTH', seed=91), mode=1)
        count = menu.MENU_DATA + 0x4F00
        self.g.call(0x4045341C, 0, count)
        self.assertEqual(self.read32(count), 1)
        self.assertEqual(self.read32(menu.SELECTORS), 90)
        self.assertEqual(self.g.call(0x404A7040, 40, 0), 31)
        self.assertEqual(self.g.call(0x410AABC0, 37, 1), 58)

    def test_gui_parameter_getter_reads_c10_name_without_changing_selection(self):
        self.g.import_np3(1, pc.make_np3('KEEP C1', seed=1))
        self.g.import_np3(10, pc.make_np3('VISIBLE C10', seed=10))
        self.g.select(1)
        target = menu.MENU_DATA + 0x4800
        self.assertEqual(self.g.call(0x403F9724, 40, target), 1)
        self.assertEqual(bytes(self.g.uc.mem_read(target, 11)), b'VISIBLE C10')
        self.assertEqual(self.g.current_selection()[0], 31)

    def test_empty_c10_is_not_appended_and_owned_guards_survive(self):
        count = menu.MENU_DATA + 0x4F00
        self.g.import_np3(9, pc.make_np3('NINTH', seed=9))
        self.g.call(0x4045341C, 1, count)
        self.assertEqual(self.read32(count), 1)
        self.assertEqual(self.read32(menu.SELECTORS), 39)
        self.assertEqual(bytes(self.g.uc.mem_read(menu.GUARD, 16)), b'C10-MENU-GUARD!!')

    def test_native_keyboard_tables_include_full_tenth_row(self):
        for slot in range(1, 11):
            self.g.import_np3(slot, pc.make_np3('NATIVE %d' % slot, seed=slot))
        self.g.call(0x4045341C, 1, menu.MENU_DATA + 0x4F00)
        self.assertEqual(self.read32(menu.MENU_DATA + 0x4F00), 10)
        self.assertEqual(self.g.call(0x404A7040, 40, 1), 40)
        self.assertEqual(self.g.call(0x403EB788, menu.CLICK_PAIR0, 41, 40, 0), 40)
        self.assertEqual(self.g.call(0x403EB6A8, menu.CLICK_PAIR1, 41, 90, 0), 40)
        self.assertEqual(struct.unpack('<II', self.g.uc.mem_read(menu.CLICK_PAIR1 + 40 * 8, 8)), (90, 40))
        self.assertEqual(self.g.call(menu.ROW_SELECTOR_ENTRY, 40), 40)

    def test_native_keyboard_uses_compact_c10_position_in_sparse_lists(self):
        from unicorn import UC_HOOK_CODE
        requested = []
        mode = 0

        def settings_boundary(uc, address, size, _):
            # Camera policy and property dispatch are explicit environment
            # inputs. Native table search, compact-row conversion, bounds and
            # direction arithmetic execute unchanged. Record the selector
            # requested at the setting-service boundary; do not fake pixels.
            if address == 0x403EC398:
                prop = uc.reg_read(self.g.r.UC_ARM64_REG_X0)
                values = {0x218C: 1, 0x0300122C: 1 - mode,
                          0x07000633: 40, 0x0700122B: 40}
                if prop not in values:
                    raise AssertionError(f'Unexpected camera policy {prop:#x}')
                uc.reg_write(self.g.r.UC_ARM64_REG_X0, values[prop])
            elif address == 0x403F96A8:
                requested.append(uc.reg_read(self.g.r.UC_ARM64_REG_X0))
                uc.reg_write(self.g.r.UC_ARM64_REG_X0, 1)
            else:
                return
            uc.reg_write(self.g.r.UC_ARM64_REG_PC, uc.reg_read(self.g.r.UC_ARM64_REG_LR))

        hook = self.g.uc.hook_add(UC_HOOK_CODE, settings_boundary)
        try:
            for mode in (0, 1):
                self.g.import_np3(10, pc.make_np3('SPARSE C10', seed=10), mode=mode)
                count = menu.MENU_DATA + 0x4F00
                self.g.call(0x4045341C, 1 - mode, count)
                self.assertEqual(self.read32(count), 1)
                self.g.uc.mem_write(0xA032F8214, struct.pack('<I', 32))
                delta = count + 4
                requested.clear()
                self.assertEqual(self.g.call(0x404E4FE0, 0, 0x46, 1, 0, delta), 1)
                pair = menu.CLICK_PAIR0 if mode == 0 else menu.CLICK_PAIR1
                self.assertEqual(requested, [self.read32(pair + 30 * 8)])
                self.assertEqual(self.read32(delta), 1)
        finally:
            self.g.uc.hook_del(hook)

    def test_second_native_photo_constructor_gets_its_own_41_cells(self):
        self.g.import_np3(10, pc.make_np3('SECOND PAGE', seed=15))
        count = menu.MENU_DATA + 0x4F00
        self.g.call(0x4048EDD4, count)
        self.assertEqual(self.read32(count), 1)
        self.assertEqual(self.read32(menu.SELECTORS), 40)
        self.assertNotEqual(menu.CELLS_ALT, menu.CELLS)
        self.assertEqual(bytes(self.g.uc.mem_read(menu.CELLS_ALT + 40 * 24, 24)),
                         self.source[0x43A83948 - pc.BASE:0x43A83948 - pc.BASE + 24])

    def test_label_variant_policy_uses_only_dedicated_c10_ids(self):
        self.g.import_np3(10, pc.make_np3('C10 STYLES', seed=10))
        record = pc.RECORDS + 64 + 9 * 0x400
        for type_value, enabled, monochrome, index in ((1, True, False, 0),
                (1, True, True, 1), (0x801, True, False, 2),
                (1, False, False, 3), (0x801, False, False, 4)):
            # This is an explicit GUI-format policy input, not evidence that
            # a modern Flexible Color NP3 was parsed by the baseline fixture.
            self.g.uc.mem_write(record + 0x10 + 0x110, struct.pack('<H', type_value))
            self.enabled, self.monochrome = enabled, monochrome
            self.g.call(0x4045341C, 1, menu.MENU_DATA + 0x4F00)
            self.assertEqual(struct.unpack('<H', self.g.uc.mem_read(menu.LABELS + 62, 2))[0],
                             menu.C10_LABEL_IDS[index])

    def test_existing_custom_parameter_conversion_matches_original_gui_getter(self):
        baseline = pc.PictureControl(self.source)
        self.addCleanup(baseline.close)
        baseline.uc.mem_map(0xA03200000, 0x300000)
        blob = pc.make_np3('COMPARE C9', seed=9)
        baseline.import_np3(9, blob)
        self.g.import_np3(9, blob)
        self.assertEqual(baseline.call(0x403F9724, 39, pc.SCRATCH + 0x100), 1)
        self.assertEqual(self.g.call(0x403F9724, 39, pc.SCRATCH + 0x100), 1)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.SCRATCH + 0x100, 0x158)),
                         bytes(baseline.uc.mem_read(pc.SCRATCH + 0x100, 0x158)))


if __name__ == '__main__':
    unittest.main()
