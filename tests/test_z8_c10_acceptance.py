# SPDX-License-Identifier: GPL-2.0-or-later
"""Host orchestration contracts only; mocks are not full-system evidence."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_c10_acceptance as run


class Session:
    def __init__(self):
        self.g = self
        self.calls = []
        self.getter_result = 1
        self.import_result = 1
        self.return_length = 0x158
        self.records = {(slot, mode): bytes([slot + mode * 20]) * 0x400
                        for mode in (0, 1) for slot in range(1, 11)}
        self.selector = 40
        self.current = b'P' * 0x158
        self.import_corrupt = None
        self.select_corrupt = False
        self.selection_result = 1
        self.setting_values = {}
        self.import_flexible = False
        self.omit_extra = False

    def call(self, address, *args, **kwargs):
        self.calls.append((address, args, kwargs))
        if address in (0x410A7B58, 0x410A7D10):
            return self.getter_result
        if address == 0x45000000:
            _, _, slot, mode = args
            result = bytearray(0x400)
            struct.pack_into('<III', result, 0, 1, *run._keys(slot, mode))
            result[0x10:0x24] = f'C{slot} LAB M{mode}'.encode().ljust(20, b'\0')
            result[0x24] = 1
            struct.pack_into('<H', result, 0x120, 32 if self.import_flexible else 1)
            if self.import_flexible and not self.omit_extra:
                result[0x370:0x39C] = run.expected_flexible_extra(slot + mode * 31)
            self.records[slot, mode] = bytes(result)
            if self.import_corrupt is not None:
                self.records[self.import_corrupt] = bytes(0x400)
            return self.import_result
        if address == 0x410DF224:
            return self.setting_values[args[0]]
        if address == 0x41015D6C:
            key, value, changed = args
            if changed != run.DATA_BASE + 0x3C200:
                raise AssertionError('Missing owned changed-byte output')
            self.setting_values[key] = value
            return 1
        if address in (0x45000400, 0x403F96A8):
            if address == 0x403F96A8:
                selector = args[0]; mode = int(selector >= 81)
                slot = selector - 30 - mode * 50
                self.setting_values[0x07000633 if mode == 0 else 0x0700122B] = 40 if slot == 10 else slot + 27
            else:
                slot, mode = args
            self.selector = 30 + slot + mode * 50
            self.current = self.records[slot, mode][0x10:0x124] + bytes(0x44)
            if self.select_corrupt:
                self.current = bytes(0x158)
            return self.selection_result if address == 0x403F96A8 else run._record(slot, mode)
        raise AssertionError(f'Unexpected host call {address:#x}')

    def rd(self, address):
        return self.selector

    def read_bytes(self, address, size):
        if address == run.DATA_BASE + 0x3C010:
            return self.current[:self.return_length]
        for (slot, mode), value in self.records.items():
            if address == run._record(slot, mode):
                return value[:size]
        raise AssertionError(f'Unexpected read {address:#x}')

    def check(self, name, condition):
        if not condition:
            raise AssertionError(name)


class AcceptanceContracts(unittest.TestCase):
    def test_settled_selection_rechecks_both_objects_after_scheduler_runs(self):
        session = Session(); session.data = {}
        expected = {'selectors': [40, 40], 'primary_sha256': ['photo', 'photo'],
                    'extra_sha256': ['flexible', 'flexible'], 'sources': [0, 0]}
        state = dict(expected)
        session.run = lambda seconds: state.update(extra_sha256=['flexible', 'stale'])
        with patch.object(run, 'selection_snapshot', side_effect=lambda _: dict(state)):
            with self.assertRaises(AssertionError):
                run.verify_settled_selection(session, expected, 'after_MCU')
        self.assertNotIn('c10_settled_selection', session.data)
        session.run = lambda seconds: state.update(expected)
        with patch.object(run, 'selection_snapshot', side_effect=lambda _: dict(state)):
            run.verify_settled_selection(session, expected, 'after_MCU')
        self.assertEqual(session.data['c10_settled_selection'][0]['selection'], expected)

    def test_flexible_import_checks_native_extra_not_just_the_name(self):
        session = Session(); session.import_flexible = True
        with patch.object(run, 'write_bytes'):
            run.import_fixture(session, 0x45000000, 10, 0, flexible=True)
        session = Session(); session.import_flexible = True; session.omit_extra = True
        with patch.object(run, 'write_bytes'), self.assertRaises(AssertionError):
            run.import_fixture(session, 0x45000000, 10, 0, flexible=True)

    def test_integer_setting_uses_required_changed_byte_output_pointer(self):
        session = Session()
        run.set_native_integer(session, 0x0300122C, 1)
        self.assertEqual(session.calls[0][:2],
                         (0x41015D6C, (0x0300122C, 1, run.DATA_BASE + 0x3C200)))

    def test_native_heap_records_must_exclude_reserved_tail(self):
        session = Session(); session.data = {}
        good = (struct.pack('<QIIQ8x', 0xA04B00000, 0xAC0000, 0, 0xA10C2B000) +
                struct.pack('<QIIQ8x', 0xA04B00100, 0x400000, 0, 0xA1172B000))
        with patch.object(session, 'read_bytes', return_value=good):
            run.verify_memory_reservation(session)
        self.assertTrue(session.data['c10_ram_reservation_verified'])
        for offset, value, fmt in ((0, 0, '<Q'), (8, 0xB00000, '<I'),
                                    (0x30, 0xA116EB000, '<Q')):
            bad = bytearray(good); struct.pack_into(fmt, bad, offset, value)
            with self.subTest(offset=offset), patch.object(session, 'read_bytes', return_value=bad):
                with self.assertRaises(AssertionError):
                    run.verify_memory_reservation(session)

    def test_current_getter_bool_failure_cannot_accept_stale_c10_selector(self):
        session = Session(); session.getter_result = 0
        with self.assertRaises(RuntimeError):
            run.current_selection(session, 0)

    def test_current_getter_uses_two_output_pointers_and_exact_object_size(self):
        session = Session()
        self.assertEqual(run.current_selection(session, 1), (40, b'P' * 0x158))
        self.assertEqual(session.calls[0][0:2],
                         (0x410A7D10, (run.DATA_BASE + 0x3C000, run.DATA_BASE + 0x3C010)))
        session.return_length = 0x114
        with self.assertRaises(RuntimeError):
            run.current_selection(session, 1)

    def test_invalid_mode_does_not_call_native_getter(self):
        session = Session()
        with self.assertRaises(ValueError):
            run.current_selection(session, 2)
        self.assertEqual(session.calls, [])

    def test_fixture_import_checks_all_other_slots_and_both_modes(self):
        session = Session()
        with patch.object(run, 'write_bytes') as write:
            run.import_fixture(session, 0x45000000, 10, 0)
        self.assertEqual(write.call_args.args[:2], (session, run.INPUT))
        self.assertEqual(session.calls[0][1][2:], (10, 0))
        for victim in ((5, 0), (1, 1), (10, 1)):
            with self.subTest(victim=victim):
                session = Session(); session.import_corrupt = victim
                with patch.object(run, 'write_bytes'), self.assertRaises(AssertionError):
                    run.import_fixture(session, 0x45000000, 10, 0)

    def test_failed_import_is_not_followed_by_selection(self):
        session = Session(); session.import_result = 0
        with patch.object(run, 'write_bytes'), self.assertRaises(AssertionError):
            run.import_fixture(session, 0x45000000, 10, 0)
        self.assertEqual(len(session.calls), 1)

    def test_selection_compares_native_current_parameters_not_only_selector(self):
        session = Session()
        run.select_control(session, 0x45000400, 10, 1)
        self.assertEqual(session.selector, 90)
        session.select_corrupt = True
        with self.assertRaises(AssertionError):
            run.select_control(session, 0x45000400, 10, 1)

    def test_high_selection_uses_internal_selector_and_reads_native_setting(self):
        session = Session()
        run.select_control(session, 0x403F96A8, 10, 1)
        self.assertEqual(session.calls[0][:2], (0x403F96A8, (90,)))
        self.assertIn((0x410DF224, (0x0700122B,), {}), session.calls)
        session = Session(); session.selection_result = 0
        with self.assertRaises(AssertionError):
            run.select_control(session, 0x403F96A8, 10, 0)

    def test_selection_snapshot_uses_persisted_source_group_for_mirror(self):
        session = Session()
        read = session.read_bytes
        def memory(address, size):
            return struct.pack('<4I', 10, 10, 0, 0) if address == run.RECORDS + 48 else read(address, size)
        params = session.records[10, 0][0x10:0x124] + bytes(0x44)
        with patch.object(session, 'read_bytes', side_effect=memory), patch.object(run, 'current_selection', return_value=(40, params)):
            state = run.selection_snapshot(session)
        self.assertEqual(state['sources'], [0, 0])
        self.assertEqual(state['selectors'], [40, 40])
        with patch.object(session, 'read_bytes', side_effect=memory), patch.object(run, 'current_selection', return_value=(90, params)):
            with self.assertRaises(AssertionError):
                run.selection_snapshot(session)


if __name__ == '__main__':
    unittest.main()
