# SPDX-License-Identifier: GPL-2.0-or-later
"""Native main-menu metadata/publication contracts; not rendered GUI evidence."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc
import z8_picture_control_main_menu as main


class MainMenuGuards(unittest.TestCase):
    def test_unrecognized_main_is_rejected(self):
        with self.assertRaises(ValueError):
            main.build_main_menu_plan(bytes(16))


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to pinned C3.11 MAIN')
class NativeMainMenuContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def setUp(self):
        self.g = pc.PictureControl(self.source)
        self.addCleanup(self.g.close)

    def apply(self):
        plan = main.build_main_menu_plan(self.source, core_plan=self.g.plan)
        overlay = plan['core_data_overlay']
        self.assertEqual(bytes(self.g.uc.mem_read(overlay['address'], len(overlay['expected']))), overlay['expected'])
        self.g.uc.mem_write(overlay['address'], overlay['data'])
        for region in plan['regions']:
            self.g.uc.mem_map(region['address'], len(region['data']))
            self.g.uc.mem_write(region['address'], region['data'])
        for change in plan['patches']:
            self.assertEqual(bytes(self.g.uc.mem_read(change['address'], len(change['expected']))), change['expected'])
            self.g.uc.mem_write(change['address'], change['replacement'])
        # One test deliberately executed the original lookup first. Loader
        # plans are applied before execution in QEMU; discard old Unicorn TBs.
        self.g.uc.ctl_remove_cache(pc.BASE, pc.BASE + len(self.source))
        return plan

    def enable_native_publisher(self):
        from unicorn import UC_HOOK_CODE
        self.g.uc.hook_del(self.g._hook)
        def observe(uc, address, size, data):
            if address == 0x41014B0C:
                self.g.executed.add(address)
                return  # Execute original publisher; do not substitute its result.
            self.g._observe(uc, address, size, data)
        self.g._hook = self.g.uc.hook_add(UC_HOOK_CODE, observe)

    def test_original_cache_rejects_41_then_guarded_capacity_and_storage_accept_it(self):
        self.enable_native_publisher()
        descriptor = 0xA02983CB0
        self.g.uc.mem_write(pc.LIST0, struct.pack('<41I', *range(41)))
        self.g.uc.mem_write(descriptor + 8, struct.pack('<I', 41))
        self.assertEqual(self.g.call(0x41014B0C, descriptor), 0)
        self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(main.REGISTRY0 + 8, 4))[0], 0)
        self.apply()
        self.assertEqual(self.g.call(0x41014B0C, descriptor), 1)
        self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(main.REGISTRY0 + 8, 4))[0], 41)
        self.assertEqual(struct.unpack('<41I', self.g.uc.mem_read(main.CACHE0, 164)), tuple(range(41)))
        self.assertEqual(self.g.call(0x41014920, 0x07000633, pc.SCRATCH), 1)
        self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(pc.SCRATCH, 4))[0], 41)
        self.g.uc.mem_write(descriptor + 8, struct.pack('<I', 42))
        self.assertEqual(self.g.call(0x41014B0C, descriptor), 0)
        self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(main.REGISTRY0 + 8, 4))[0], 41)
        self.assertIn(0x41014BF8, self.g.executed)
        self.assertIn(0x41014C90, self.g.executed)

    def test_new_row_round_trips_code_keys_and_selectors_without_using_failure_id(self):
        self.assertEqual(self.g.call(0x40812000, 40), 8)  # Existing fallback is Auto.
        self.apply()
        self.assertNotEqual(main.C10_ITEM_ID, 0xD2)
        self.assertEqual(self.g.call(0x40812000, 40), main.C10_ITEM_ID)
        pointer = self.g.call(0x40811F50, main.C10_ITEM_ID)
        self.assertEqual(struct.unpack('<7I', self.g.uc.mem_read(pointer, 28)),
                         (main.C10_ITEM_ID, 40, 0x01007C00, 0x01007C02, 40, 90, 0xFFFF))
        self.assertEqual(self.g.call(0x408123B0, main.C10_ITEM_ID), 40)
        self.assertEqual(self.g.call(0x408124EC, main.C10_ITEM_ID), 90)
        self.assertEqual(self.g.call(0x408120C8, 0x01007C00), main.C10_ITEM_ID)
        self.assertEqual(self.g.call(0x40812190, 0x01007C02), main.C10_ITEM_ID)
        for selector, mode in ((40, 0), (90, 1)):
            self.assertEqual(self.g.call(0x40812308, selector, pc.SCRATCH), main.C10_ITEM_ID)
            self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(pc.SCRATCH, 4))[0], mode)

    def test_all_original_rows_and_unknown_fallbacks_are_preserved(self):
        self.apply()
        original = self.source[main.ORIGINAL_TABLE - pc.BASE:main.ORIGINAL_TABLE - pc.BASE + 40 * 28]
        self.assertEqual(bytes(self.g.uc.mem_read(main.TABLE, len(original))), original)
        for offset in range(0, len(original), 28):
            item, code, key0, key1, selector0, selector1, _base = struct.unpack_from('<7I', original, offset)
            self.assertEqual(self.g.call(0x40812000, code), item)
            self.assertEqual(self.g.call(0x408123B0, item), selector0)
            self.assertEqual(self.g.call(0x408124EC, item), selector1)
        self.assertEqual(self.g.call(0x40812000, 0x123456), 8)
        self.assertEqual(self.g.call(0x408120C8, 0xDEADBEEF), 0xD2)
        self.assertEqual(self.g.call(0x40811F50, 0xD2), 0)

    def test_native_main_menu_deduplicator_keeps_41_only_with_distinct_c10_metadata(self):
        values, count_pointer = pc.SCRATCH + 0x600, pc.SCRATCH + 0x700
        def convert_and_deduplicate():
            ids = [self.g.call(0x40812000, code) for code in range(41)]
            self.g.uc.mem_write(values, struct.pack('<41I', *ids))
            self.g.uc.mem_write(count_pointer, struct.pack('<I', 41))
            self.g.call(0x40813FB0, values, count_pointer, 0x408140D8)
            count = struct.unpack('<I', self.g.uc.mem_read(count_pointer, 4))[0]
            return count, struct.unpack('<' + 'I' * count, self.g.uc.mem_read(values, count * 4))
        self.assertEqual(convert_and_deduplicate()[0], 40)
        self.apply()
        count, ids = convert_and_deduplicate()
        self.assertEqual(count, 41)
        self.assertIn(main.C10_ITEM_ID, ids)
        self.assertEqual(len(set(ids)), 41)

    def test_native_name_parameter_serializer_uses_owned_c10_validity_and_real_name(self):
        self.g.import_np3(10, pc.make_np3('C10 MAIN NAME', 19))
        self.apply()
        self.assertEqual(self.g.call(0x407F54AC, main.C10_ITEM_ID, 0), 1)
        output = pc.SCRATCH + 0x900
        size = self.g.call(0x407F1D3C, main.C10_ITEM_ID, 0, output)
        self.assertTrue(4 < size <= 1024)
        payload = bytes(self.g.uc.mem_read(output, size))
        self.assertEqual(struct.unpack_from('<H', payload, 2)[0], main.C10_ITEM_ID)
        self.assertIn(b'C10 MAIN NAME', payload)
        self.g.uc.mem_write(pc._record(10, 0), struct.pack('<I', 2))
        self.assertEqual(self.g.call(0x407F54AC, main.C10_ITEM_ID, 0), 0)

    def test_fixed_stack_list_caller_handles_41_without_metadata_or_register_overlap(self):
        from unicorn import UC_HOOK_CODE
        self.enable_native_publisher()
        self.apply()
        self.g.uc.mem_write(pc.LIST0, struct.pack('<41I', *range(41)))
        self.g.uc.mem_write(0xA02983CB8, struct.pack('<I', 41))
        self.assertEqual(self.g.call(0x41014B0C, 0xA02983CB0), 1)
        request, output, callback = pc.SCRATCH + 0x800, pc.SCRATCH + 0x900, 0x460D2000
        self.g.uc.mem_write(request, struct.pack('<II', 0xFFFFFFFF, 0))
        r, seen = self.g.r, []
        def record_serializer(uc, _at, _size, _data):
            item = uc.reg_read(r.UC_ARM64_REG_X0)
            mode = uc.reg_read(r.UC_ARM64_REG_X1)
            target = uc.reg_read(r.UC_ARM64_REG_X2)
            seen.append((item, mode))
            uc.mem_write(target, struct.pack('<I', item))
            uc.reg_write(r.UC_ARM64_REG_X0, 4)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, record_serializer, begin=callback, end=callback)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.assertEqual(self.g.call(0x407F1C04, request, output, callback), 4 + 41 * 4)
        self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(output, 4))[0], 41)
        self.assertEqual(len(seen), 41)
        self.assertIn((main.C10_ITEM_ID, 0), seen)

    def test_only_verified_zero_core_gap_may_be_used(self):
        plan = self.apply()
        overlay = plan['core_data_overlay']
        self.assertEqual(overlay['address'], pc.DATA_BASE + 0x1C000)
        self.assertEqual(len(overlay['data']), 0x3000)
        self.assertEqual(overlay['expected'], bytes(0x3000))
        self.assertTrue(all(0x45009000 <= r['address'] < r['address'] + len(r['data']) <= 0x4500C000
                            for r in plan['regions']))
        bad_core = dict(self.g.plan, regions=[dict(r) for r in self.g.plan['regions']])
        region = next(r for r in bad_core['regions'] if r['address'] == pc.DATA_BASE)
        dirty = bytearray(region['data']); dirty[0x1C000] = 1; region['data'] = bytes(dirty)
        with self.assertRaises(ValueError):
            main.build_main_menu_plan(self.source, core_plan=bad_core)

    def test_lcd_definitions_keep_every_original_row_and_append_independent_events(self):
        plan = self.apply()
        for page in plan['lcd_pages']:
            descriptor = bytes(self.g.uc.mem_read(page['definition'], 0x22))
            self.assertEqual(struct.unpack_from('<H', descriptor, 0x20)[0], page['old_count'] + 1)
            rows, events = struct.unpack_from('<QQ', descriptor, 0x10)
            original_rows = self.source[page['old_rows'] - pc.BASE:page['old_rows'] - pc.BASE + page['old_count'] * 44]
            self.assertEqual(bytes(self.g.uc.mem_read(rows, len(original_rows))), original_rows)
            new_row = bytes(self.g.uc.mem_read(rows + len(original_rows), 44))
            expected = bytearray(original_rows[-44:])
            struct.pack_into('<I', expected, 0x14, 0x7FF0)
            struct.pack_into('<I', expected, 0x18, page['row_key'])
            self.assertEqual(new_row, expected)
            old_events = self.source[page['old_events'] - pc.BASE:page['old_events'] - pc.BASE + page['old_count'] * 8]
            self.assertEqual(bytes(self.g.uc.mem_read(events, len(old_events))), old_events)
            appended = struct.unpack('<Q', self.g.uc.mem_read(events + len(old_events), 8))[0]
            event_list = bytes(self.g.uc.mem_read(appended, 0x200))
            old_pointer = struct.unpack_from('<Q', old_events, len(old_events) - 8)[0]
            expected_events = bytearray(self.source[old_pointer - pc.BASE:old_pointer - pc.BASE + 0x200])
            for offset in (0xC8, 0x168):
                struct.pack_into('<Q', expected_events, offset, page['handler'])
            self.assertEqual(event_list, expected_events)

    def test_lcd_native_pair_search_finds_c10_without_expanding_unrelated_tables(self):
        plan = self.apply()
        for page in plan['lcd_pages']:
            pairs = page['pairs']
            self.assertEqual(self.g.call(0x403EB6A8, pairs, 40, page['selector'], 0xFFFFFFFF), 40)
            self.assertEqual(self.g.call(0x403EB788, pairs, 40, page['row_key'], 0xFFFFFFFF), 40)
            self.assertEqual(self.g.call(0x403EB6A8, page['old_pairs'], 40, page['selector'], 0xFFFFFFFF), 0xFFFFFFFF)
            old = self.source[page['old_pairs'] - pc.BASE:page['old_pairs'] - pc.BASE + 320]
            self.assertEqual(bytes(self.g.uc.mem_read(pairs, 320)), old)
            for index in range(40):
                selector, key = struct.unpack_from('<II', old, index * 8)
                self.assertEqual(self.g.call(0x403EB6A8, pairs, 40, selector, 0xFFFFFFFF), index)
                self.assertEqual(self.g.call(0x403EB788, pairs, 40, key, 0xFFFFFFFF), index)
            self.assertEqual(self.g.call(0x403EB6A8, pairs, 39, page['selector'], 0xFFFFFFFF), 0xFFFFFFFF)
        self.assertEqual(self.g.call(0x403EB6A8, 0, 40, 40, 0xFFFFFFFF), 0xFFFFFFFF)

    def test_lcd_native_getters_and_initial_focus_use_the_same_tenth_row(self):
        plan = self.apply()
        for getter, page in zip((0x40600EC0, 0x40601788), plan['lcd_pages']):
            pointer = self.g.call(getter, pc.SCRATCH)
            self.assertEqual(pointer, page['pairs'])
            self.assertEqual(struct.unpack('<I', self.g.uc.mem_read(pc.SCRATCH, 4))[0], 41)
            self.g.uc.reg_write(self.g.r.UC_ARM64_REG_X0, page['selector'])
            self.g.uc.reg_write(self.g.r.UC_ARM64_REG_X20, pointer)
            self.g.uc.reg_write(self.g.r.UC_ARM64_REG_SP, 0x460F0000)
            # Execute original focus selector->row search through the owned
            # lookup guard. Stop before the external widget-focus operation.
            self.g.uc.emu_start(0x405FEA1C, 0x405FEA40, count=1000)
            self.assertEqual(self.g.uc.reg_read(self.g.r.UC_ARM64_REG_PC), 0x405FEA40)
            self.assertEqual(self.g.uc.reg_read(self.g.r.UC_ARM64_REG_X22), page['row_key'])

    def test_lcd_event_thunks_keep_movie_inherit_write_and_native_selection_boundary(self):
        from unicorn import UC_HOOK_CODE
        plan, observed = self.apply(), []
        def environment(uc, address, _size, _data):
            if address in (0x403EC39C, 0x403F96A8):
                observed.append((address, uc.reg_read(self.g.r.UC_ARM64_REG_X0),
                                 uc.reg_read(self.g.r.UC_ARM64_REG_X1)))
                uc.reg_write(self.g.r.UC_ARM64_REG_X0, 1)
                uc.reg_write(self.g.r.UC_ARM64_REG_PC, uc.reg_read(self.g.r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, environment)
        self.addCleanup(self.g.uc.hook_del, hook)
        for page in plan['lcd_pages']:
            observed.clear()
            self.assertEqual(self.g.call(page['handler'], 0, 0), 1)
            expected = [(0x403F96A8, page['selector'], 0)]
            if page['selector'] == 90:
                expected.insert(0, (0x403EC39C, 0x0300122C, 0))
            self.assertEqual(observed, expected)

    def test_lcd_label_choices_do_not_index_nine_entry_bitmap_tables(self):
        self.apply()
        for state in (0, 1):
            ordinary = 0x7FF0 if state else 0x7FF3
            creative = 0x7FF2 if state else 0x7FF4
            for type_code in (1, 0x20, 0x801, 0x814):
                self.assertEqual(self.g.call(main.LCD_LABEL_CHOOSE, type_code, 0, state), ordinary)
            self.assertEqual(self.g.call(main.LCD_LABEL_CHOOSE, 1, 1, state), ordinary)
            self.assertEqual(self.g.call(main.LCD_LABEL_CHOOSE, 0x20, 1, state), 0x7FF1)
            for type_code in (0x801, 0x814):
                self.assertEqual(self.g.call(main.LCD_LABEL_CHOOSE, type_code, 1, state), creative)

    def test_lcd_c10_icon_marker_is_owned_and_reset_when_widget_pool_changes(self):
        plan = self.apply()
        uc, r = self.g.uc, self.g.r
        uc.mem_map(0xA03200000, 0x300000)
        obj, original_cache = pc.SCRATCH + 0x500, 0xA0337927A
        uc.mem_write(original_cache, bytes(range(64)))
        before = bytes(uc.mem_read(original_cache, 64))
        def fragment(entry, end, page, pool, value=0):
            uc.mem_write(obj + 0x48, struct.pack('<Q', pool))
            for number, v in ((0, value), (8, original_cache), (19, obj),
                              (20, page['pairs']), (27, 29)):
                uc.reg_write(getattr(r, f'UC_ARM64_REG_X{number}'), v)
            uc.reg_write(r.UC_ARM64_REG_SP, 0x460F0000)
            uc.emu_start(entry, end, count=500)
            self.assertEqual(uc.reg_read(r.UC_ARM64_REG_PC), end)
            return uc.reg_read(r.UC_ARM64_REG_X8)
        photo, movie = plan['lcd_pages']
        self.assertEqual(fragment(plan['lcd_cache_read'], 0x405FE8F4, photo, 0x123456), 0)
        fragment(plan['lcd_cache_write'], 0x405FE90C, photo, 0x123456, 71)
        self.assertEqual(fragment(plan['lcd_cache_read'], 0x405FE8F4, photo, 0x123456), 71)
        self.assertEqual(fragment(plan['lcd_cache_read'], 0x405FE8F4, movie, 0xABCDEF), 0)
        self.assertEqual(fragment(plan['lcd_cache_read'], 0x405FE8F4, photo, 0x999999), 0)
        self.assertEqual(bytes(uc.mem_read(original_cache, 64)), before)

    def test_original_deferred_service_keeps_c10_after_property_notification(self):
        from unicorn import UC_HOOK_CODE
        self.g.import_np3(10, pc.make_flexible_np3('PHOTO DEFERRED', 23), 0)
        self.g.import_np3(10, pc.make_flexible_np3('MOVIE DEFERRED', 71), 1)
        self.apply()
        follow_photo = 0
        def setting_input(uc, address, _size, _data):
            if address == 0x41012C84:
                key = uc.reg_read(self.g.r.UC_ARM64_REG_X0)
                values = {0x07000633: 40, 0x0700122B: 40, 0x0300122C: follow_photo}
                if key not in values:
                    raise AssertionError(f'Unexpected deferred service setting {key:#x}')
                uc.reg_write(self.g.r.UC_ARM64_REG_X0, values[key])
                uc.reg_write(self.g.r.UC_ARM64_REG_PC, uc.reg_read(self.g.r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, setting_input)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.g.uc.mem_write(pc.SCRATCH + 0x800, struct.pack('<III', 29, 0, 0))
        for target, follow, expected_selector, source in ((0, 0, 40, 0), (1, 0, 90, 1), (1, 1, 40, 0)):
            follow_photo = follow
            expected_extra = bytes(self.g.uc.mem_read(pc._record(10, source) + 0x370, 44))
            self.g.call(0x410A86DC, target, pc.SCRATCH + 0x800)
            selector, current = self.g.current_selection(target)
            self.assertEqual(selector, expected_selector)
            self.assertEqual(current[:0x114], bytes(self.g.uc.mem_read(pc._record(10, source) + 0x10, 0x114)))
            self.assertEqual(current[0x12C:0x158], expected_extra)

    def test_deferred_converter_preserves_every_legacy_code_and_unknown_fallback(self):
        codes = (*range(40), 41, 0xFFFFFFFF)
        original = [self.g.call(0x410AB09C, code) for code in codes]
        self.assertEqual(self.g.call(0x410AB09C, 40), 1)
        self.apply()
        self.assertEqual([self.g.call(0x410AB09C, code) for code in codes], original)
        self.assertEqual(self.g.call(0x410AB09C, 40), 40)

    def movie_indicator_fragment(self, selector, type_code, flag, state, key):
        from unicorn import UC_HOOK_CODE
        uc, r, seen = self.g.uc, self.g.r, []
        def comparison(uc, address, _size, _data):
            seen.append(uc.reg_read(r.UC_ARM64_REG_X0))
            uc.reg_write(r.UC_ARM64_REG_X0, state)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = uc.hook_add(UC_HOOK_CODE, comparison, begin=0x403F9E64, end=0x403F9E64)
        try:
            for number, value in ((0, type_code), (22, selector), (23, key), (26, pc.SCRATCH)):
                uc.reg_write(getattr(r, f'UC_ARM64_REG_X{number}'), value)
            uc.reg_write(r.UC_ARM64_REG_SP, 0x460F0000)
            uc.reg_write(r.UC_ARM64_REG_LR, 0x460FF000)
            uc.mem_write(0x460F0024, bytes((flag,)))
            # Original VIDEO indicator branch; stop immediately before its
            # native widget setter. Only the comparison state is environmental.
            uc.emu_start(0x40573224, 0x405732F8, count=1500)
            self.assertEqual(uc.reg_read(r.UC_ARM64_REG_PC), 0x405732F8)
            self.assertEqual(uc.reg_read(r.UC_ARM64_REG_SP), 0x460F0000)
            return uc.reg_read(r.UC_ARM64_REG_X1), seen
        finally:
            uc.hook_del(hook)

    def test_independent_movie_overview_indicator_has_its_own_c10_route(self):
        # The unpatched flexible table misses the new key and falls into C1's
        # bitmap. It is distinct from both the photo and VIDEO list-row paths.
        self.assertEqual(self.movie_indicator_fragment(90, 0x20, 1, 1, main.LCD_MOVIE_KEY)[0], 0xC27)
        self.apply()
        for state in (0, 1):
            for type_code, flag, expected in ((0x20, 1, 0x7FF1),
                    (1, 1, 0x7FF0 if state else 0x7FF3),
                    (0x801, 1, 0x7FF2 if state else 0x7FF4),
                    (0x20, 0, 0x7FF0 if state else 0x7FF3)):
                resource, comparisons = self.movie_indicator_fragment(90, type_code, flag, state, main.LCD_MOVIE_KEY)
                self.assertEqual(resource, expected)
                self.assertEqual(comparisons, [90])

    def test_movie_overview_indicator_keeps_non_c10_original_branch(self):
        cases = [(selector, kind, 1, selector & 1, 0x2E83)
                 for selector in (*range(90), 91, 0xFFFFFFFF)
                 for kind in (1, 0x20, 0x801)]
        original = [self.movie_indicator_fragment(*case) for case in cases]
        self.apply()
        self.assertEqual([self.movie_indicator_fragment(*case) for case in cases], original)


if __name__ == '__main__':
    unittest.main()
