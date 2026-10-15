# SPDX-License-Identifier: GPL-2.0-or-later
"""Z8's deterministic external RTC frame is not a guest condition edit."""
from datetime import datetime
from types import SimpleNamespace
import unittest
from mcu_frames import frames
from z8_resources import MAIN_SHA


class RTCInput(unittest.TestCase):
    def test_pinned_date_fields_and_bounds(self):
        from z8_rtc_mcu import status_body
        stamp = datetime(2026, 10, 14, 12, 34, 56)
        body = status_body(stamp)
        self.assertEqual(body, bytes.fromhex('010000001a090d0c223800000000'))
        self.assertEqual(sum(frames(body, family=7)[0]) & 255, 255)
        self.assertEqual(status_body(datetime(2000, 2, 29))[4:7], bytes((0, 1, 28)))
        for bad in (None, '2026-10-14', datetime(1999, 1, 1), datetime(2256, 1, 1)):
            with self.assertRaises(ValueError): status_body(bad)

    def test_queue_preserves_busy_transport_and_gates_firmware(self):
        from z8_rtc_mcu import enqueue
        from service_z9_mcu import Peer
        peer = Peer.__new__(Peer)
        peer.g = SimpleNamespace(data={'firmware_sha256': MAIN_SHA})
        peer.tx, peer.rx, peer.rx_retiring = object(), None, False
        peer.pending, peer.record = [], {}
        enqueue(peer, datetime(2026, 10, 14, 12, 34, 56))
        self.assertEqual(peer.pending[0][0], 7)
        self.assertIsNone(peer.rx)
        self.assertFalse(peer.record['z8_rtc_inputs'][0]['physical_protocol_verified'])
        peer.g.data['firmware_sha256'] = 'wrong'
        with self.assertRaises(ValueError): enqueue(peer, datetime(2026, 10, 14))
        self.assertEqual(len(peer.pending), 1)
