# SPDX-License-Identifier: GPL-2.0-or-later
"""Readiness observations cannot fabricate readiness or acknowledge IRQs."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_capture_readiness as readiness


class ReadinessTests(unittest.TestCase):
    def guest(self):
        calls = []
        def read(address, size):
            calls.append(('ram', address, size))
            if address == 0xa03bd060c: return (4).to_bytes(size, 'little')
            if address == 0xa03bd0618: return (8).to_bytes(size, 'little')
            return bytes(size)
        def rd(address):
            calls.append(('mmio', address, 4))
            return 0x04000000 if address == 0x150c0214 else 0
        return SimpleNamespace(read_bytes=read, rd=rd, calls=calls)

    def test_records_actual_hold_and_unacknowledged_disabled_interrupt(self):
        g = self.guest()
        with patch.object(readiness, 'load_bindings', return_value={'main_sha256': 'a' * 64}):
            result = readiness.capture(g, primary_mmio=True)
        self.assertEqual(result['derived']['full_worker_hold_union'], 12)
        self.assertTrue(result['derived']['d980_pending'])
        self.assertFalse(result['derived']['d980_enabled_pending'])
        self.assertFalse(result['derived']['d980_irq_enabled'])
        self.assertFalse(result['derived']['d980_software_subscribed'])
        self.assertFalse(result['camera_ready_verified'])
        self.assertFalse(result['native_capture_verified'])
        self.assertFalse(result['guest_state_modified'])
        self.assertTrue(all(kind in ('ram', 'mmio') for kind, _, _ in g.calls))

    def test_default_capture_is_ram_only(self):
        g = self.guest()
        with patch.object(readiness, 'load_bindings', return_value={'main_sha256': 'a' * 64}):
            result = readiness.capture(g)
        self.assertEqual(result['primary_mmio'], {})
        self.assertIsNone(result['derived']['d980_pending'])
        self.assertTrue(all(kind == 'ram' for kind, _, _ in g.calls))

    def test_rejects_short_original_reads(self):
        g = self.guest()
        g.read_bytes = lambda address, size: bytes(max(size - 1, 0))
        with patch.object(readiness, 'load_bindings', return_value={'main_sha256': 'a' * 64}), self.assertRaises(ValueError):
            readiness.capture(g)

    def test_identity_failure_stops_before_any_read(self):
        g = self.guest()
        with patch.object(readiness, 'load_bindings', side_effect=ValueError('identity')), self.assertRaises(ValueError):
            readiness.capture(g)
        self.assertEqual(g.calls, [])


if __name__ == '__main__':
    unittest.main()
