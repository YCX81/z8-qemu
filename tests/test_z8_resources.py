# SPDX-License-Identifier: GPL-2.0-or-later
"""Contracts for explicit virtual ROM inputs; no proprietary bytes needed."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_resources


class ResourcePlan(unittest.TestCase):
    def test_inputs_are_disjoint_and_outside_program(self):
        plan = z8_resources.plan()
        spans = sorted((item['address'],item['address']+len(item['data'])) for item in plan)
        self.assertTrue(all(a >= 0x45000000 and b <= 0x48000000 for a,b in spans))
        self.assertTrue(all(a[1] <= b[0] for a,b in zip(spans,spans[1:])))
        self.assertTrue(all(item['synthetic'] and item['meaning'] for item in plan))

    def test_zero_parameters_are_explicit_separate_option(self):
        self.assertFalse(any(p['name']=='parameters' for p in z8_resources.plan()))
        block = next(p for p in z8_resources.plan(zero_parameters=True) if p['name']=='parameters')
        self.assertEqual(len(block['data']),0x20000)
        self.assertEqual(block['data'][:-4],bytes(0x1fffc))
        self.assertEqual(block['data'][-4:],b'ENDM')

    def test_attachment_preserves_identity_and_records_derivation(self):
        with tempfile.TemporaryDirectory() as folder:
            guest=SimpleNamespace(out=Path(folder),data={'firmware_sha256':z8_resources.MAIN_SHA},command=[])
            z8_resources.attach(guest)
            self.assertTrue(guest.command)
            record=guest.data['z8_virtual_rom_inputs']
            self.assertFalse(record['hardware_calibration'])
            self.assertTrue(all((guest.out/p['file']).read_bytes() for p in record['regions']))
            with self.assertRaises(FileExistsError):z8_resources.attach(guest)

    def test_other_firmware_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            guest=SimpleNamespace(out=Path(folder),data={'firmware_sha256':'0'*64},command=[])
            with self.assertRaises(ValueError):z8_resources.attach(guest)
            self.assertEqual(list(guest.out.iterdir()),[])
            self.assertEqual(guest.command,[])

    def test_native_sampling_reconstructs_original_state_without_overlapping_loaders(self):
        plan = z8_resources.plan(native_sampling=True)
        block = next(p for p in plan if p['name']=='parameters')
        self.assertEqual(len(block['data']),0x20000)
        self.assertEqual(block['data'][0xb870:0xb878],bytes.fromhex('1400000004000000'))
        self.assertEqual(block['data'][-4:],b'ENDM')
        spans = sorted((p['address'],p['address']+len(p['data'])) for p in plan)
        self.assertTrue(all(a[1]<=b[0] for a,b in zip(spans,spans[1:])))
        with self.assertRaises(ValueError):
            z8_resources.plan(zero_parameters=True,native_sampling=True)

    def test_native_sampling_record_distinguishes_derived_input_from_calibration(self):
        with tempfile.TemporaryDirectory() as folder:
            guest=SimpleNamespace(out=Path(folder),data={'firmware_sha256':z8_resources.MAIN_SHA},command=[])
            result=z8_resources.attach(guest,native_sampling=True)
            self.assertFalse(result['hardware_calibration'])
            self.assertFalse(result['zero_parameter_experiment'])
            source=result['native_sampling_derivation']
            self.assertEqual(source['interval'],20)
            self.assertEqual(source['history_mask'],15)
            self.assertEqual((1<<source['history_samples'])-1,source['history_mask'])

    def test_battery_limits_are_resource_values_with_mode1_preserved(self):
        with self.assertRaises(ValueError):z8_resources.plan(battery_limits=True)
        plan=z8_resources.plan(native_sampling=True,battery_limits=True)
        block=next(p for p in plan if p['name']=='parameters')
        self.assertEqual(block['data'][0xb7e0:0xb7e8],bytes.fromhex('580000004d000000'))
        self.assertEqual(block['data'][0xb870:0xb878],bytes.fromhex('1400000004000000'))
        self.assertEqual(block['data'][-4:],b'ENDM')
        with tempfile.TemporaryDirectory() as folder:
            guest=SimpleNamespace(out=Path(folder),data={'firmware_sha256':z8_resources.MAIN_SHA},command=[])
            result=z8_resources.attach(guest,native_sampling=True,battery_limits=True)
            self.assertFalse(result['battery_limit_derivation']['active_mode_changed'])
            self.assertFalse(result['hardware_calibration'])


if __name__ == '__main__':unittest.main()
