# SPDX-License-Identifier: GPL-2.0-or-later
"""C10 must use independent guest records and execute original A64 routines."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_picture_control as pc


class FixtureContract(unittest.TestCase):
    def test_fixture_has_distinct_name_and_auxiliary_payload(self):
        a = pc.make_np3('C10 ALPHA', seed=17)
        b = pc.make_np3('C10 BETA', seed=31)
        self.assertEqual(a[:4], b'NCP\0')
        self.assertNotEqual(a, b)
        self.assertIn(b'C10 ALPHA', a)

    def test_fixture_refuses_names_not_supported_by_native_format(self):
        for name in ('', 'x' * 20, 'bad\0name', '中文'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                pc.make_np3(name)
        with self.assertRaises(ValueError):
            pc.make_flexible_np3('BAD FLAG', auxiliary_enabled=1)

    def test_wrong_original_is_rejected_before_emulator_construction(self):
        with self.assertRaisesRegex(ValueError, 'C3.11'):
            pc.PictureControl(bytes(128))


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to the original C3.11 MAIN')
class OriginalA64Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def setUp(self):
        self.g = pc.PictureControl(self.source)
        self.addCleanup(self.g.close)

    def test_original_np3_parser_accepts_fixture_and_rejects_bad_magic(self):
        parsed = self.g.parse_np3(pc.make_np3('C10 TEST', seed=29))
        self.assertEqual(parsed.main[:8], b'C10 TEST')
        self.assertEqual(len(parsed.main), 0x114)
        self.assertEqual(len(parsed.auxiliary), 0x242)
        self.assertIn(0x410ADC64, self.g.executed)
        self.assertIn(0x410AC6D0, self.g.executed)
        self.assertIn(0x410DAC30, self.g.executed)
        self.assertEqual(parsed.main[:20], b'C10 TEST'.ljust(20, b'\0'))
        self.assertEqual(parsed.main[0x14], 1)
        self.assertEqual(struct.unpack_from('<H', parsed.main, 0x110)[0], 1)
        with self.assertRaises(ValueError):
            self.g.parse_np3(b'BAD!' + pc.make_np3('INVALID')[4:])

    def test_native_conversion_preserves_known_parameter_in_all_three_banks(self):
        blob = pc.make_np3('PARAM FIELD')
        tag = struct.pack('>II', 0x600, 2)
        changed = blob.replace(tag + b'\x80\x04', tag + b'\x84\x04')
        self.assertNotEqual(blob, changed)
        parsed = self.g.import_np3(10, changed)
        for offset in (0x24, 0x74, 0xC4):
            self.assertEqual(struct.unpack_from('<I', parsed.main, offset)[0], 132)
        self.assertEqual(self.g.current_selection()[1][:0x114], parsed.main)

    def test_tenth_record_is_independent_and_uses_native_setter_and_getter(self):
        original = [self.g.import_np3(i, pc.make_np3('SLOT %d' % i, seed=i))
                    for i in range(1, 10)]
        other = self.g.import_np3(1, pc.make_np3('OTHER BANK', seed=101), mode=1)
        self.g.import_np3(10, pc.make_np3('TENTH SLOT', seed=210))
        self.assertEqual(self.g.select(10).main[:10], b'TENTH SLOT')
        for i, expected in enumerate(original, 1):
            self.assertEqual(self.g.select(i), expected)
        self.assertEqual(self.g.select(1, mode=1), other)
        self.assertIn(0x41015EC0, self.g.executed)
        self.assertIn(0x41015B30, self.g.executed)
        self.assertIn(pc.CODE_BASE, self.g.executed)
        self.assertTrue(self.g.guards_intact())

    def test_native_list_includes_ten_without_overwriting_second_mode(self):
        for slot in range(1, 11):
            self.g.import_np3(slot, pc.make_np3('S%d' % slot, seed=slot))
        self.assertEqual(len(self.g.slots()), 10)
        self.assertEqual(self.g.slots(mode=1), [])
        self.assertIn(0x410AAC98, self.g.executed)
        self.assertTrue(self.g.guards_intact())

    def test_original_current_selection_getter_returns_tenth_parameters(self):
        expected = self.g.import_np3(10, pc.make_np3('CURRENT TEN', seed=45))
        selector, current = self.g.current_selection()
        self.assertEqual(selector, 40)
        self.assertEqual(current[:0x114], expected.main)
        self.assertIn(0x410A7B58, self.g.executed)
        self.g.import_np3(10, pc.make_np3('MODE ONE', seed=67), mode=1)
        self.assertEqual(self.g.current_selection(mode=1)[0], 90)
        self.assertEqual(self.g.current_selection()[0], 40)

    @staticmethod
    def enabled_aux_np3(name='FULL CURRENT', seed=19):
        blob = pc.make_np3(name, seed)
        # Original a9fbc requires main+30 and main+34 to equal 1.
        for tag in (0x800, 0x900):
            marker = struct.pack('>II', tag, 2)
            blob = blob.replace(marker + b'\x80\x04', marker + b'\x01\x04')
        return blob

    def test_complete_current_object_matches_native_c9_except_owned_cache_identity(self):
        for mode in (0, 1):
            for enabled in (False, True):
                with self.subTest(mode=mode, auxiliary_enabled=enabled):
                    blob = self.enabled_aux_np3() if enabled else pc.make_np3('FULL CURRENT', 19)
                    self.g.import_np3(9, blob, mode)
                    self.assertEqual(self.g.call(0x410A7BCC if mode == 0 else 0x410A7D84,
                                                 39 + 50 * mode), 1)
                    baseline = self.g.current_selection(mode)[1]
                    self.g.import_np3(10, blob, mode)
                    current = self.g.current_selection(mode)[1]
                    self.assertNotEqual(struct.unpack_from('<Q', baseline, 0x118)[0], 0)
                    self.assertEqual(current[0x12C:], b'\xFF' * 0x2C)
                    if enabled:
                        cache = pc.DATA_BASE + 0x31B00 + mode * 0x248
                        self.assertEqual(struct.unpack_from('<Q', current, 0x120)[0], cache)
                        generation = struct.unpack_from('<I', self.g.uc.mem_read(cache + 0x244, 4))[0]
                        self.assertGreater(generation, 0)
                        self.assertEqual(struct.unpack_from('<I', current, 0x128)[0], generation)
                        expected = bytearray(baseline)
                        expected[0x120:0x12C] = current[0x120:0x12C]
                        self.assertEqual(current, expected)
                    else:
                        self.assertEqual(struct.unpack_from('<QI', current, 0x120), (0, 0))
                        self.assertEqual(current, baseline)
        self.assertIn(0x410A7C44, self.g.executed)
        self.assertIn(0x410A83F4, self.g.executed)
        self.assertIn(0x410A9FBC, self.g.executed)

    def test_tenth_auxiliary_cache_is_independent_and_reimport_advances_generation(self):
        self.g.import_np3(9, self.enabled_aux_np3('NINTH', 9))
        old_cache = bytes(self.g.uc.mem_read(0xA04702A80 + 8 * 0x248, 0x248))
        other = self.g.import_np3(10, self.enabled_aux_np3('OTHER', 30), mode=1)
        other_cache = bytes(self.g.uc.mem_read(pc.DATA_BASE + 0x31D48, 0x248))
        first = self.g.import_np3(10, self.enabled_aux_np3('TENTH', 10))
        cache = pc.DATA_BASE + 0x31B00
        before_generation = struct.unpack('<I', self.g.uc.mem_read(cache + 0x244, 4))[0]
        self.assertEqual(bytes(self.g.uc.mem_read(cache, 0x242)), first.auxiliary)
        second = self.g.import_np3(10, self.enabled_aux_np3('TENTH NEW', 11))
        self.assertEqual(bytes(self.g.uc.mem_read(cache, 0x242)), second.auxiliary)
        self.assertGreater(struct.unpack('<I', self.g.uc.mem_read(cache + 0x244, 4))[0], before_generation)
        self.assertEqual(bytes(self.g.uc.mem_read(0xA04702A80 + 8 * 0x248, 0x248)), old_cache)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.DATA_BASE + 0x31D48, 0x248)), other_cache)
        self.assertEqual(self.g.select(10, mode=1), other)

    def test_incomplete_0310_flexible_fields_are_rejected_before_record_write(self):
        self.g.import_np3(10, pc.make_np3('PRESERVE', 9))
        before = self.g.export_records()
        current = self.g.current_selection()[1]
        blob = pc.make_np3('CLASS32', 11)
        blob = blob.replace(struct.pack('>II', 0x100, 4) + b'0300', struct.pack('>II', 0x100, 4) + b'0310')
        marker = struct.pack('>II', 0x300, 2)
        blob = blob.replace(marker + b'\0\1', marker + b'\0\x20')
        with self.assertRaises(ValueError):
            self.g.import_np3(10, blob)
        self.assertEqual(self.g.export_records(), before)
        self.assertEqual(self.g.current_selection()[1], current)

    def test_flexible_native_parser_builds_nonzero_extra_from_real_tag_handlers(self):
        parsed = self.g.parse_np3(pc.make_flexible_np3('FLEX PARSED', seed=19, auxiliary_enabled=True))
        expected = bytes.fromhex('010101010193636465666768696a6b6c6d6e6f707172737475767778797a1380837024808471358085728373')
        self.assertEqual(struct.unpack_from('<H', parsed.main, 0x110)[0], 32)
        self.assertEqual(parsed.extra, expected)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.PARSED + 0x12C, 44)), expected)
        self.assertIn(0x410AF260, self.g.executed)
        self.assertIn(0x410AEA68, self.g.executed)

    def test_flexible_c10_matches_native_c9_full_current_and_preserves_extra_defaults(self):
        for mode in (0, 1):
            for enabled in (False, True):
                with self.subTest(mode=mode, auxiliary_enabled=enabled):
                    blob = pc.make_flexible_np3('FLEX BASELINE', 19, auxiliary_enabled=enabled)
                    ninth = self.g.import_np3(9, blob, mode)
                    self.assertEqual(self.g.call(0x410A7BCC if mode == 0 else 0x410A7D84, 39 + mode * 50), 1)
                    baseline = self.g.current_selection(mode)[1]
                    tenth = self.g.import_np3(10, blob, mode)
                    current = self.g.current_selection(mode)[1]
                    self.assertEqual(current[0x12C:], ninth.extra)
                    self.assertEqual(current[0x12C:], tenth.extra)
                    self.assertNotEqual(tenth.extra, bytes(44))
                    normalized = bytearray(baseline)
                    if enabled:
                        cache = pc.C10_CACHE0 if mode == 0 else pc.C10_CACHE1
                        self.assertEqual(struct.unpack_from('<Q', current, 0x120)[0], cache)
                        self.assertEqual(bytes(self.g.uc.mem_read(cache, 0x242)), tenth.auxiliary)
                        normalized[0x120:0x12C] = current[0x120:0x12C]
                    else:
                        self.assertEqual(struct.unpack_from('<QI', current, 0x120), (0, 0))
                    self.assertEqual(current, normalized)
                    for slot in (9, 10):
                        self.assertEqual(self.g.call(0x410A9E90, 30 + slot + mode * 50, pc.SCRATCH), 1)
                        key = struct.unpack('<I', self.g.uc.mem_read(pc.SCRATCH, 4))[0]
                        self.assertEqual(key, pc._extra_key(slot, mode))
                        self.assertEqual(self.g.call(0x41015618, key, 44, pc.SCRATCH + 0x500), 1)
                        self.assertEqual(bytes(self.g.uc.mem_read(pc.SCRATCH + 0x500, 44)), tenth.extra)

    def test_flexible_nonzero_extra_and_mirrored_current_survive_guest_save_reload(self):
        self.g.import_np3(9, pc.make_flexible_np3('FLEX C9', 9))
        photo = self.g.import_np3(10, pc.make_flexible_np3('FLEX PHOTO', 19))
        movie = self.g.import_np3(10, pc.make_flexible_np3('FLEX MOVIE', 75), mode=1)
        self.assertEqual(self.g.call(0x410A7D84, 40), 1)
        before = self.g.export_records()[64:64 + 20 * 0x400]
        fresh, image, _current = self._guest_save_reload()
        self.assertEqual(image[64:64 + 20 * 0x400], before)
        self.assertEqual(fresh.current_selection(mode=1)[0], 40)
        self.assertEqual(fresh.current_selection(mode=1)[1][0x12C:], photo.extra)
        self.assertEqual(fresh.current_selection(mode=0)[1][0x12C:], photo.extra)
        self.assertEqual(fresh.select(10, mode=1), movie)
        self.assertEqual(fresh.current_selection(mode=1)[1][0x12C:], movie.extra)

    def test_flexible_missing_tag_or_bad_mode_cannot_reuse_previous_parsed_extra(self):
        good = pc.make_flexible_np3('FLEX VALID', 19)
        self.g.import_np3(10, good)
        before = self.g.export_records()
        missing = good.replace(struct.pack('>II', 0x1900, 2) + b'\1\1', b'')
        malformed = bytearray(good)
        at = good.index(struct.pack('>II', 0x2000, 20)) + 8 + 13
        malformed[at] = 0  # Native required flag, not a host format check.
        for bad in (missing, bytes(malformed)):
            with self.assertRaises(ValueError):
                self.g.import_np3(10, bad)
            self.assertEqual(self.g.export_records(), before)

    def test_original_selector_mapping_and_block_getter_reach_tenth(self):
        expected = self.g.import_np3(10, pc.make_np3('PROPERTY C10', seed=20))
        self.assertEqual(self.g.call(0x410A95E8, 40, pc.SCRATCH, pc.SCRATCH + 4, pc.SCRATCH + 8), 1)
        key = struct.unpack('<I', self.g.uc.mem_read(pc.SCRATCH, 4))[0]
        self.assertEqual(key, 0x01007C00)
        self.assertEqual(self.g.call(0x41015618, key, 0x114, pc.SCRATCH + 0x100), 1)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.SCRATCH + 0x100, 0x114)), expected.main)
        self.assertEqual(self.g.call(0x410A7E70, 40, pc.SCRATCH + 0x300), 1)
        self.assertEqual(bytes(self.g.uc.mem_read(pc.SCRATCH + 0x300, 0x114)), expected.main)

    def test_native_menu_binding_preserves_builtins_and_reserves_unique_c10_code(self):
        for mode, descriptor, old_base, new_base in ((0, 0xA02983CB0, 0xA02983D04, pc.LIST0),
                                                    (1, 0xA02983CC8, 0xA02983DA4, pc.LIST1)):
            prefix_offset = 0x4B13000 + old_base - 0xA02808000
            self.assertEqual(bytes(self.g.uc.mem_read(new_base, 31 * 4)),
                             self.source[prefix_offset:prefix_offset + 31 * 4])
            self.assertEqual(struct.unpack('<Q', self.g.uc.mem_read(descriptor + 16, 8))[0], new_base)
            self.assertEqual(self.g.call(0x410AABC0, 40, mode), 40 if mode == 0 else 90)
        self.assertEqual(self.g.call(0x410AABC0, 37, 0), 8)

    def test_existing_descriptor_defaults_are_preserved(self):
        for group, index in ((0x0D, 8), (0x14, 16), (0x0F, 0), (0x0F, 17),
                             (0x0D, 0x14), (0x0D, 0x1C), (0x14, 0x14), (0x14, 0x1C)):
            first = struct.unpack_from('<H', self.source, 0x440244C8 - pc.BASE + group * 48 + 24)[0]
            offset = 0x4400A5C0 - pc.BASE + (first + index) * 24
            expected = self.source[offset:offset + 24]
            actual = bytes(self.g.uc.mem_read(pc.ITEMS + (first + index) * 24, 24))
            self.assertEqual(actual[:8], expected[:8])
            self.assertEqual(actual[16:24], expected[16:24])

    def test_original_selection_entry_accepts_tenth_in_each_mode(self):
        expected = self.g.import_np3(10, pc.make_np3('SELECT C10', seed=10))
        self.g.import_np3(1, pc.make_np3('SELECT C1', seed=1))
        self.assertEqual(self.g.current_selection()[0], 31)
        self.assertEqual(self.g.call(0x410A7BCC, 40), 1)
        self.assertEqual(self.g.current_selection()[0], 40)
        self.assertEqual(self.g.current_selection()[1][:0x114], expected.main)
        other = self.g.import_np3(10, pc.make_np3('SECOND C10', seed=101), mode=1)
        self.g.import_np3(1, pc.make_np3('SECOND C1', seed=91), mode=1)
        self.assertEqual(self.g.call(0x410A7D84, 90), 1)
        self.assertEqual(self.g.current_selection(mode=1)[1][:0x114], other.main)

    def test_guest_record_image_reloads_in_fresh_native_context(self):
        self.g.import_np3(9, pc.make_np3('NINTH', seed=9))
        tenth = self.g.import_np3(10, pc.make_np3('PERSIST C10', seed=10))
        image = self.g.export_records()
        fresh = pc.PictureControl(self.source)
        self.addCleanup(fresh.close)
        fresh.restore_records(image)
        self.assertEqual(fresh.slots(), [9, 10])
        self.assertEqual(fresh.select(10), tenth)
        self.assertEqual(fresh.current_selection()[0], 40)
        self.assertIn(0x41015B30, fresh.executed)
        damaged = bytearray(image)
        damaged[-1] ^= 1
        with self.assertRaises(ValueError):
            fresh.restore_records(damaged)

    def test_guest_load_rebuilds_enabled_auxiliary_cache_after_prior_selection(self):
        from unicorn import UC_HOOK_CODE
        restored = self.g.import_np3(10, self.enabled_aux_np3('SAVED AUX', 21))
        image = self.g.export_records()[:-32]
        self.g.import_np3(10, self.enabled_aux_np3('LATER AUX', 99))
        r = self.g.r
        def media_read(uc, _address, _size, _data):
            pointer = uc.reg_read(r.UC_ARM64_REG_X0)
            self.assertEqual(pointer, pc.LOAD_BUFFER)
            self.assertEqual(uc.reg_read(r.UC_ARM64_REG_X1), len(image))
            uc.mem_write(pointer, image)
            uc.reg_write(r.UC_ARM64_REG_X0, len(image))
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, media_read, begin=pc.LOAD_CALLBACK, end=pc.LOAD_CALLBACK)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.assertEqual(self.g.call(pc.LOAD_ENTRY), 1)
        selector, current = self.g.current_selection()
        self.assertEqual(selector, 40)
        self.assertEqual(current[:0x114], restored.main)
        cache = struct.unpack_from('<Q', current, 0x120)[0]
        self.assertEqual(cache, pc.C10_CACHE0)
        self.assertEqual(bytes(self.g.uc.mem_read(cache, 0x242)), restored.auxiliary)
        self.assertEqual(struct.unpack_from('<I', current, 0x128)[0],
                         struct.unpack('<I', self.g.uc.mem_read(cache + 0x244, 4))[0])
        self.assertIn(pc.CACHE_INVALIDATE, self.g.executed)
        self.assertIn(pc.CACHE_GET, self.g.executed)

    def _high_selection_recorders(self):
        # Queue/history/peripheral boundaries only record transactions. Native
        # ID checks, integer writes, selection and notification construction run.
        from unicorn import UC_HOOK_CODE
        r, u = self.g.r, self.g.uc
        callback = 0x460D0000
        events, writes = [], []
        u.mem_write(0xA03B9D858, struct.pack('<Q', callback))
        def observe(_u, address, _size, _data):
            args = [u.reg_read(getattr(r, f'UC_ARM64_REG_X{i}')) for i in range(4)]
            value = None
            if address == 0x41014524:
                value = pc.SCRATCH + 0xA00
            elif address == 0x4101438C:
                writes.append((args[1], args[2])); value = 0
            elif address in (0x410143B8, 0x410145BC, 0x40D71254):
                value = 0
            elif address == 0x40D2C698:
                value = 1
            elif address == 0x40F270E0:
                u.mem_write(args[1], bytes(2)); value = 1
            elif address == callback:
                count = struct.unpack('<I', u.mem_read(args[1], 4))[0]
                events.append((args[0], struct.unpack('<' + 'I' * count, u.mem_read(args[1] + 4, count * 4))))
                value = 1
            if value is not None:
                u.reg_write(r.UC_ARM64_REG_X0, value)
                u.reg_write(r.UC_ARM64_REG_PC, u.reg_read(r.UC_ARM64_REG_LR))
        hook = u.hook_add(UC_HOOK_CODE, observe)
        self.addCleanup(u.hook_del, hook)
        return events, writes

    def test_native_high_selection_mirrors_photo_c10_into_movie_current(self):
        photo = self.g.import_np3(10, self.enabled_aux_np3('PHOTO SOURCE', 13))
        movie = self.g.import_np3(10, self.enabled_aux_np3('MOVIE OWN', 29), mode=1)
        self.g.import_np3(1, pc.make_np3('OTHER PHOTO', 2))
        events, writes = self._high_selection_recorders()
        self.assertEqual(self.g.call(0x41015D6C, 0x0300122C, 1, pc.SCRATCH + 0x900), 1)
        self.assertEqual(self.g.call(0x403F96A8, 40), 1)
        self.assertEqual(self.g.call(0x410DF224, 0x07000633), 40)
        self.assertIn((0x07000633, 40), writes)
        self.assertIn((0x01001E28, 40), writes)
        self.assertEqual(events, [(0x04300001, (0x40000410,))] * 2)
        self.assertEqual(self.g.current_selection(mode=1)[0], 40)
        self.assertEqual(self.g.current_selection(mode=1)[1], self.g.current_selection(mode=0)[1])
        self.assertEqual(self.g.current_selection(mode=1)[1][:0x114], photo.main)
        self.assertNotEqual(photo.main, movie.main)
        self.assertEqual(struct.unpack_from('<4I', self.g.export_records(), 48), (10, 10, 0, 0))
        self.assertEqual(self.g.call(0x403F96A8, 40), 1)  # No-change is still success.

    def test_schema_two_guest_save_load_restores_source_bank_for_mirrored_movie(self):
        from unicorn import UC_HOOK_CODE
        photo = self.g.import_np3(10, self.enabled_aux_np3('PHOTO SAVED', 17))
        movie = self.g.import_np3(10, self.enabled_aux_np3('MOVIE SAVED', 93), mode=1)
        self.assertEqual(self.g.call(0x410A7D84, 40), 1)
        r, media = self.g.r, []
        def save(uc, _address, _size, _data):
            pointer, size = uc.reg_read(r.UC_ARM64_REG_X0), uc.reg_read(r.UC_ARM64_REG_X1)
            media.append(bytes(uc.mem_read(pointer, size)))
            uc.reg_write(r.UC_ARM64_REG_X0, size)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, save, begin=pc.SAVE_CALLBACK, end=pc.SAVE_CALLBACK)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.assertEqual(self.g.call(pc.SAVE_ENTRY), 1)
        self.assertEqual(struct.unpack_from('<I', media[0], 8)[0], 2)
        fresh = pc.PictureControl(self.source)
        self.addCleanup(fresh.close)
        def load(uc, _address, _size, _data):
            pointer, size = uc.reg_read(r.UC_ARM64_REG_X0), uc.reg_read(r.UC_ARM64_REG_X1)
            uc.mem_write(pointer, media[0])
            uc.reg_write(r.UC_ARM64_REG_X0, size)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = fresh.uc.hook_add(UC_HOOK_CODE, load, begin=pc.LOAD_CALLBACK, end=pc.LOAD_CALLBACK)
        self.addCleanup(fresh.uc.hook_del, hook)
        self.assertEqual(fresh.call(pc.LOAD_ENTRY), 1)
        selector, current = fresh.current_selection(mode=1)
        self.assertEqual(selector, 40)
        self.assertEqual(current[:0x114], photo.main)
        self.assertEqual(struct.unpack_from('<Q', current, 0x120)[0], pc.C10_CACHE0)
        self.assertEqual(bytes(fresh.uc.mem_read(pc.C10_CACHE0, 0x242)), photo.auxiliary)
        self.assertEqual(bytes(fresh.uc.mem_read(pc._record(10, 1) + 0x10, 0x114)), movie.main)
        self.assertEqual(fresh.select(10, mode=1), movie)
        self.assertEqual(struct.unpack_from('<4I', fresh.export_records(), 48), (10, 10, 0, 1))

    def test_schema_two_rejects_invalid_source_bank_and_empty_mirrored_source(self):
        self.g.import_np3(10, pc.make_np3('MOVIE ONLY', 4), mode=1)
        baseline = self.g.export_records()[:-32]
        for offset, value in ((8, 1), (56, 1), (60, 2), (60, 0)):
            with self.subTest(offset=offset, value=value):
                image = bytearray(baseline)
                struct.pack_into('<I', image, offset, value)
                self.g.uc.mem_write(pc.LOAD_BUFFER, bytes(image))
                self.assertEqual(self.g.call(pc.VALIDATE_ENTRY, pc.LOAD_BUFFER), 0)

    def test_full_selection_rejects_invalid_or_reverse_bank_mappings_without_changes(self):
        self.g.import_np3(10, pc.make_np3('PHOTO', 1))
        self.g.import_np3(10, pc.make_np3('MOVIE', 2), mode=1)
        records = self.g.export_records()
        current = [self.g.current_selection(mode)[1] for mode in (0, 1)]
        for args in ((10, 1, 0), (10, 2, 1), (10, 0, 2), (0, 0, 1), (11, 0, 1)):
            with self.subTest(args=args):
                self.assertEqual(self.g.call(pc.SELECT_FULL_ENTRY, *args), 0)
                self.assertEqual(self.g.export_records(), records)
                self.assertEqual([self.g.current_selection(mode)[1] for mode in (0, 1)], current)

    def _guest_save_reload(self):
        from unicorn import UC_HOOK_CODE
        r, media = self.g.r, []
        def save(uc, _address, _size, _data):
            pointer, size = uc.reg_read(r.UC_ARM64_REG_X0), uc.reg_read(r.UC_ARM64_REG_X1)
            media.append(bytes(uc.mem_read(pointer, size)))
            uc.reg_write(r.UC_ARM64_REG_X0, size)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = self.g.uc.hook_add(UC_HOOK_CODE, save, begin=pc.SAVE_CALLBACK, end=pc.SAVE_CALLBACK)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.assertEqual(self.g.call(pc.SAVE_ENTRY), 1)
        fresh = pc.PictureControl(self.source)
        self.addCleanup(fresh.close)
        before = [fresh.current_selection(mode) for mode in (0, 1)]
        def load(uc, _address, _size, _data):
            pointer, size = uc.reg_read(r.UC_ARM64_REG_X0), uc.reg_read(r.UC_ARM64_REG_X1)
            uc.mem_write(pointer, media[0])
            uc.reg_write(r.UC_ARM64_REG_X0, size)
            uc.reg_write(r.UC_ARM64_REG_PC, uc.reg_read(r.UC_ARM64_REG_LR))
        hook = fresh.uc.hook_add(UC_HOOK_CODE, load, begin=pc.LOAD_CALLBACK, end=pc.LOAD_CALLBACK)
        self.addCleanup(fresh.uc.hook_del, hook)
        self.assertEqual(fresh.call(pc.LOAD_ENTRY), 1)
        return fresh, media[0], before

    def test_save_snapshots_native_high_c9_and_its_movie_mirror(self):
        expected = self.g.import_np3(9, self.enabled_aux_np3('NATIVE C9', 9))
        self.g.import_np3(10, self.enabled_aux_np3('C10 OLD', 10))
        self.g.import_np3(10, self.enabled_aux_np3('MOVIE OWN', 88), mode=1)
        self._high_selection_recorders()
        self.assertEqual(self.g.call(0x41015D6C, 0x0300122C, 1, pc.SCRATCH + 0x900), 1)
        self.assertEqual(self.g.call(0x403F96A8, 39), 1)
        self.assertEqual([self.g.current_selection(mode)[0] for mode in (0, 1)], [39, 39])
        fresh, image, _before = self._guest_save_reload()
        self.assertEqual(struct.unpack_from('<4I', image, 48), (9, 9, 0, 0))
        for mode in (0, 1):
            selector, current = fresh.current_selection(mode)
            self.assertEqual(selector, 39)
            self.assertEqual(current[:0x114], expected.main)

    def test_builtin_selection_clears_stale_extension_current_without_erasing_c10(self):
        self.g.import_np3(10, pc.make_np3('KEEP PHOTO', 1))
        self.g.import_np3(10, pc.make_np3('KEEP MOVIE', 2), mode=1)
        self._high_selection_recorders()
        self.assertEqual(self.g.call(0x41015D6C, 0x0300122C, 1, pc.SCRATCH + 0x900), 1)
        self.assertEqual(self.g.call(0x403F96A8, 0), 1)
        fresh, image, before = self._guest_save_reload()
        self.assertEqual(struct.unpack_from('<4I', image, 48), (0, 0, 0, 1))
        self.assertEqual([fresh.current_selection(mode) for mode in (0, 1)], before)
        self.assertEqual(fresh.slots(), [10])
        self.assertEqual(fresh.slots(mode=1), [10])

    def test_save_rejects_invalid_native_custom_source_before_media_callback(self):
        from unicorn import UC_HOOK_CODE
        self.g.import_np3(10, pc.make_np3('KEEP', 1))
        before = self.g.export_records()
        media_calls = []
        def unexpected_media(*args):
            media_calls.append(1)
        hook = self.g.uc.hook_add(UC_HOOK_CODE, unexpected_media, begin=pc.SAVE_CALLBACK, end=pc.SAVE_CALLBACK)
        self.addCleanup(self.g.uc.hook_del, hook)
        for selector in (31, 90):  # Empty C1; source1 is invalid for photo target.
            with self.subTest(selector=selector):
                self.g.uc.mem_write(0xA02983CF8, struct.pack('<I', selector))
                self.assertEqual(self.g.call(pc.SAVE_ENTRY), 0)
                self.assertEqual(self.g.export_records(), before)
                self.assertEqual(media_calls, [])

    def test_media_entries_fail_closed_without_guest_media_driver(self):
        self.g.import_np3(10, pc.make_np3('UNSAVED', seed=3))
        self.assertEqual(self.g.call(pc.SAVE_ENTRY), 0)
        self.assertEqual(self.g.call(pc.LOAD_ENTRY), 0)

    def test_guest_save_and_load_entries_restore_current_state_with_explicit_media_fixture(self):
        # This tests the guest ABI, not NAND or process persistence. The root
        # integration supplies the actual guest controller transaction code.
        from unicorn import UC_HOOK_CODE
        media = []
        r = self.g.r
        expected = self.g.import_np3(10, pc.make_np3('GUEST MEDIA', seed=83))

        def save(u, _address, _size, _data):
            pointer, size = u.reg_read(r.UC_ARM64_REG_X0), u.reg_read(r.UC_ARM64_REG_X1)
            self.assertEqual(size, pc.RECORD_BYTES)
            media.append(bytes(u.mem_read(pointer, size)))
            u.reg_write(r.UC_ARM64_REG_X0, size)
            u.reg_write(r.UC_ARM64_REG_PC, u.reg_read(r.UC_ARM64_REG_LR))

        hook = self.g.uc.hook_add(UC_HOOK_CODE, save, begin=pc.SAVE_CALLBACK, end=pc.SAVE_CALLBACK)
        self.addCleanup(self.g.uc.hook_del, hook)
        self.assertEqual(self.g.call(pc.SAVE_ENTRY), 1)
        fresh = pc.PictureControl(self.source)
        self.addCleanup(fresh.close)

        def load(u, _address, _size, _data):
            pointer, size = u.reg_read(r.UC_ARM64_REG_X0), u.reg_read(r.UC_ARM64_REG_X1)
            self.assertEqual(pointer, pc.LOAD_BUFFER)
            self.assertEqual(size, len(media[0]))
            u.mem_write(pointer, media[0])
            u.reg_write(r.UC_ARM64_REG_X0, size)
            u.reg_write(r.UC_ARM64_REG_PC, u.reg_read(r.UC_ARM64_REG_LR))

        hook = fresh.uc.hook_add(UC_HOOK_CODE, load, begin=pc.LOAD_CALLBACK, end=pc.LOAD_CALLBACK)
        self.addCleanup(fresh.uc.hook_del, hook)
        self.assertEqual(fresh.call(pc.LOAD_ENTRY), 1)
        self.assertEqual(fresh.current_selection()[0], 40)
        self.assertEqual(fresh.select(10), expected)
        self.assertIn(pc.VALIDATE_ENTRY, fresh.executed)

    def test_guest_load_validation_rejects_wrong_record_keys(self):
        self.g.import_np3(10, pc.make_np3('UNCHANGED', seed=3))
        image = bytearray(self.g.export_records()[:-32])
        struct.pack_into('<I', image, 64 + 9 * 0x400 + 4, 0x01000d08)
        self.g.uc.mem_write(pc.LOAD_BUFFER, bytes(image))
        self.assertEqual(self.g.call(pc.VALIDATE_ENTRY, pc.LOAD_BUFFER), 0)
        self.assertEqual(self.g.current_selection()[1][:9], b'UNCHANGED')

    def test_guest_load_rejects_selection_of_an_empty_slot(self):
        self.g.import_np3(1, pc.make_np3('EXISTS', seed=1))
        image = bytearray(self.g.export_records()[:-32])
        struct.pack_into('<I', image, 48, 10)
        self.g.uc.mem_write(pc.LOAD_BUFFER, bytes(image))
        self.assertEqual(self.g.call(pc.VALIDATE_ENTRY, pc.LOAD_BUFFER), 0)

    def test_import_failure_preserves_all_guest_records(self):
        self.g.import_np3(10, pc.make_np3('GOOD C10', seed=10))
        before = self.g.export_records()
        for bad in (b'NCP\0', pc.make_np3('BAD', seed=11)[:-5]):
            with self.assertRaises(ValueError):
                self.g.import_np3(10, bad)
        self.assertEqual(self.g.export_records(), before)
        for slot in (0, 11):
            with self.assertRaises(ValueError):
                self.g.select(slot)


if __name__ == '__main__':
    unittest.main()
