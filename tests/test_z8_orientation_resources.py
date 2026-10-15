# SPDX-License-Identifier: GPL-2.0-or-later
"""Orientation sampling is explicit synthetic input, not factory calibration."""
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_resources


class OrientationResources(unittest.TestCase):
    def test_opt_in_changes_only_the_four_sampling_fields(self):
        base = z8_resources.plan(native_sampling=True, battery_limits=True)
        changed = z8_resources.plan(native_sampling=True, battery_limits=True,
                                    orientation_sampling=True)
        self.assertEqual(len(base), len(changed))
        allowed = {i for off in (0xa420, 0xa42c, 0xa430, 0xa434) for i in range(off, off + 4)}
        for old, new in zip(base, changed):
            self.assertEqual((old['name'], old['address']), (new['name'], new['address']))
            if old['name'] != 'parameters':
                self.assertEqual(old, new)
                continue
            self.assertEqual(len(new['data']), 0x20000)
            self.assertEqual(new['data'][-4:], b'ENDM')
            self.assertTrue({i for i, (a, b) in enumerate(zip(old['data'], new['data'])) if a != b} <= allowed)
            self.assertEqual(struct.unpack_from('<I', new['data'], 0xa420)[0], 20)
            self.assertEqual(struct.unpack_from('<3I', new['data'], 0xa42c), (5, 1, 1))

    def test_sampling_requires_explicit_parameter_image(self):
        for kwargs in ({}, {'zero_parameters': True}):
            with self.assertRaises(ValueError):
                z8_resources.plan(orientation_sampling=True, **kwargs)

    def test_metadata_retains_the_hardware_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            guest = SimpleNamespace(out=Path(tmp), data={'firmware_sha256': z8_resources.MAIN_SHA}, command=[])
            record = z8_resources.attach(guest, native_sampling=True, orientation_sampling=True)
            item = record['orientation_sampling_derivation']
            self.assertFalse(record['hardware_calibration'])
            self.assertFalse(record['original_program_modified'])
            self.assertFalse(item['physical_calibration_verified'])
            self.assertEqual((item['window_samples'], item['discard_high'], item['discard_low']), (5, 1, 1))
            self.assertTrue(item['source_pcs'])


if __name__ == '__main__':
    unittest.main()
