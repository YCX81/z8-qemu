# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free capture observer identity and safe-read contracts."""
import copy
from pathlib import Path
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_capture_bindings as capture


class CaptureBindings(unittest.TestCase):
    def setUp(self):
        self.profile = capture.load_profile()

    def test_identity_and_evidence_scope_stay_explicit(self):
        self.assertEqual(self.profile['main_bytes'], 81807709)
        self.assertEqual(self.profile['bindings']['key_consumer'], 0x40d271e4)
        self.assertEqual(self.profile['bindings']['capture_full_request'], 0x40fb875c)
        self.assertFalse(self.profile['native_capture_runtime_verified'])
        self.assertFalse(self.profile['nef_output_verified'])
        self.assertFalse(self.profile['physical_hardware_verified'])

    def test_foreign_profile_and_unaligned_code_are_rejected(self):
        for field, value in (('main_sha256', '0' * 64), ('main_bytes', 81823629),
                             ('model', 'Nikon Z9'), ('version', 'C5.32')):
            candidate = dict(self.profile, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                capture.validate_profile(candidate)
        candidate = copy.deepcopy(self.profile)
        candidate['bindings']['key_consumer'] += 1
        with self.assertRaises(ValueError):
            capture.validate_profile(candidate)

    def test_guest_identity_is_checked_before_reading_memory(self):
        guest = SimpleNamespace(data={'firmware_sha256': '0' * 64}, firmware=Path('unused'))
        with patch.object(capture, 'validate_firmware') as validate:
            with self.assertRaises(ValueError):
                capture.load_bindings(guest)
        validate.assert_not_called()
        guest.data['firmware_sha256'] = self.profile['main_sha256']
        with patch.object(capture, 'validate_firmware') as validate:
            self.assertEqual(capture.load_bindings(guest), self.profile)
        validate.assert_called_once()

    def test_contacts_are_decoded_from_observed_44_byte_payload(self):
        for half, full in ((False, False), (True, False), (True, True)):
            blob = struct.pack('<11I', 0x200 | (half << 31) | (full << 30), *([0] * 10))
            actual = capture.decode_key_state(blob)
            self.assertEqual((actual['half'], actual['full']), (half, full))
            self.assertEqual(actual['words'][0] & 0x200, 0x200)
        for length in (0, 43, 45):
            with self.assertRaises(ValueError):
                capture.decode_key_state(bytes(length))

    def fake_guest(self):
        gpio = self.profile['gpio']
        memory = {gpio['logical_table_pointer']: struct.pack('<Q', gpio['logical_table_rom'])}
        for index, row in enumerate(gpio['contacts']):
            descriptor = 0xa00020000 + index * 0x100
            irq = descriptor + 0x40
            memory[gpio['object_table'] + row['index'] * 8] = struct.pack('<Q', descriptor)
            data = bytearray(0x30)
            struct.pack_into('<III', data, 0, row['index'], row['bank'], row['bit'])
            data[0xc] = 1
            struct.pack_into('<Q', data, 0x20, irq)
            memory[descriptor] = bytes(data)
            irq_data = bytearray(0x40)
            struct.pack_into('<III', irq_data, 4, row['irq_bank'], row['irq_bit'], row['intid'])
            struct.pack_into('<Q', irq_data, 0x18, row['callback'] or 0)
            memory[irq] = bytes(irq_data)
            memory[gpio['input_register_table'] + row['bank'] * 8] = struct.pack('<Q', row['input_register'])
        guest = SimpleNamespace(data={'firmware_sha256': self.profile['main_sha256']},
                                firmware=Path('unused'), reads=[])
        def read(address, size):
            guest.reads.append((address, size))
            self.assertEqual(len(memory[address]), size)
            return memory[address]
        guest.read_bytes = read
        return guest, memory

    def test_native_gpio_mapping_is_read_and_checked_without_mmio_reads(self):
        guest, _ = self.fake_guest()
        with patch.object(capture, 'validate_firmware'):
            rows = capture.read_gpio_bindings(guest)
        self.assertEqual([(r['logical_id'], r['bank'], r['bit'], r['intid']) for r in rows[:2]],
                         [(0x23, 3, 4, 59), (0x24, 3, 5, 60)])
        self.assertTrue(all(0xa00000000 <= address < 0xa20000000 for address, _ in guest.reads))

    def test_gpio_mismatch_and_out_of_ram_pointer_stop_before_unsafe_read(self):
        for pointer in (0, 0x15080000, 0xa20000000 - 16):
            guest, memory = self.fake_guest()
            gpio = self.profile['gpio']
            memory[gpio['object_table'] + gpio['contacts'][0]['index'] * 8] = struct.pack('<Q', pointer)
            with patch.object(capture, 'validate_firmware'), self.assertRaises(ValueError):
                capture.read_gpio_bindings(guest)
            self.assertNotIn((pointer, 0x30), guest.reads)
        guest, memory = self.fake_guest()
        memory[self.profile['gpio']['logical_table_pointer']] = struct.pack('<Q', 0x43de711c)
        with patch.object(capture, 'validate_firmware'), self.assertRaises(ValueError):
            capture.read_gpio_bindings(guest)
        self.assertEqual(len(guest.reads), 1)

    def test_unknown_bindings_are_omitted_from_breakpoints(self):
        points = capture.observation_points(self.profile)
        self.assertEqual(points[0x40d271e4], 'key_consumer')
        self.assertNotIn(None, points)
        self.assertTrue(self.profile['unresolved'])

    def test_changed_irq_callback_or_register_is_not_silently_accepted(self):
        first = self.profile['gpio']['contacts'][0]
        for field in ('bank', 'disabled', 'irq_pointer', 'irq_number', 'callback', 'register', 'short'):
            guest, memory = self.fake_guest()
            desc = 0xa00020000
            irq = desc + 0x40
            if field in ('bank', 'disabled', 'irq_pointer'):
                blob = bytearray(memory[desc])
                if field == 'bank': struct.pack_into('<I', blob, 4, 4)
                elif field == 'disabled': blob[0xc] = 0
                else: struct.pack_into('<Q', blob, 0x20, 0x150c0000)
                memory[desc] = bytes(blob)
            elif field in ('irq_number', 'callback'):
                blob = bytearray(memory[irq])
                if field == 'irq_number': struct.pack_into('<I', blob, 12, 60)
                else: struct.pack_into('<Q', blob, 0x18, 0x40fd9518)
                memory[irq] = bytes(blob)
            elif field == 'register':
                memory[self.profile['gpio']['input_register_table'] + first['bank'] * 8] = struct.pack('<Q', 0x150c0000)
            else:
                guest.read_bytes = lambda address, size: bytes(max(0, size - 1))
            with self.subTest(field=field), patch.object(capture, 'validate_firmware'), self.assertRaises(ValueError):
                capture.read_gpio_bindings(guest)

    def test_only_known_distinct_gpio_ids_can_be_requested(self):
        for ids in ((0x23, 0x23), (0x99,)):
            guest, _ = self.fake_guest()
            with patch.object(capture, 'validate_firmware'), self.assertRaises(ValueError):
                capture.read_gpio_bindings(guest, ids)

    def test_w1c_and_event_boundaries_are_model_specific(self):
        self.assertEqual(self.profile['bindings']['primary_before_w1c'], 0x40c1f83c)
        self.assertEqual(self.profile['producer_observation']['engine151']['callback_events'],
                         [0x41050001, 0x41050002])
        self.assertEqual(self.profile['processing_index']['capture_selector_rom_address'],
                         self.profile['processing_index']['rom_base'] + 0xdaf40 + 2)


if __name__ == '__main__':
    unittest.main()
