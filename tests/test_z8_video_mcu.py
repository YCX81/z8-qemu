# SPDX-License-Identifier: GPL-2.0-or-later
"""Only the observed status query may receive the declared boot input."""
import struct
import unittest


class Status68InputTests(unittest.TestCase):
    def test_status68_queue_preserves_busy_mcu_ownership(self):
        from z8_video_mcu import enqueue_status68
        from mcu_frames import status_body
        from service_z9_mcu import Peer
        peer = Peer.__new__(Peer)
        peer.g = SimpleNamespace(data={'firmware_sha256': MAIN_SHA})
        peer.tx = object()
        peer.rx = None
        peer.rx_retiring = False
        peer.pending = []
        peer.record = {}
        enqueue_status68(peer, status_body({0x1c: 2}), 1)
        self.assertEqual(len(peer.pending), 1)
        family, body, wire = peer.pending[0]
        self.assertEqual((family, body[0x1c], body[0x68]), (1, 2, 1))
        self.assertEqual(wire, frames(body, family=1)[0])
        self.assertIsNone(peer.rx)
        self.assertFalse(peer.record['z8_status68_inputs'][0]['physical_semantics_verified'])
        peer.g.data['firmware_sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            enqueue_status68(peer, body, 2)
        self.assertEqual(len(peer.pending), 1)

    def test_status68_changes_only_the_declared_external_enum(self):
        from z8_video_mcu import status68_input
        from mcu_frames import status_body
        previous = status_body({0x1c: 2, 0x69: 4})
        for value in (0, 1, 2):
            changed = status68_input(previous, value)
            self.assertEqual(changed[0x68], value)
            self.assertEqual(changed[:0x68], previous[:0x68])
            self.assertEqual(changed[0x69:], previous[0x69:])

    def test_status68_rejects_unknown_values_and_wrong_body(self):
        from z8_video_mcu import status68_input
        from mcu_frames import status_body
        for value in (-1, 3, 255, True, '1'):
            with self.assertRaises(ValueError):
                status68_input(status_body(), value)
        for body in (bytes(128), bytes([3]) + bytes(126), bytearray(status_body())):
            with self.assertRaises(ValueError):
                status68_input(body, 1)
from types import SimpleNamespace
from mcu_frames import frames
from z8_resources import MAIN_SHA
from z8_video_mcu import reply_for,attach


class StatusReply(unittest.TestCase):
    def query(self,padding=False):
        wire=bytearray(frames(struct.pack('<II',2,1),family=4,channel=2)[0])
        if padding:wire[16:248]=bytes((i*17)&255 for i in range(232))
        wire[-1]=(~sum(wire[:-1]))&255
        return bytes(wire)

    def test_declared_payload_ignores_hashed_stale_tx_padding(self):
        status=bytes([3])+bytes(111)
        for padding in (False,True):
            body,wire=reply_for(self.query(padding),status)
            self.assertEqual(body,status)
            self.assertEqual(wire,frames(status,family=4)[0])
            self.assertEqual(sum(wire)&255,255)

    def test_unknown_requests_and_bad_wire_do_not_get_a_reply(self):
        status=bytes([3])+bytes(111)
        for offset,value in ((0,1),(1,1),(2,9),(8,4),(12,2),(252,2),(253,2),(254,1)):
            wire=bytearray(self.query());wire[offset]=value;wire[-1]=(~sum(wire[:-1]))&255
            self.assertIsNone(reply_for(bytes(wire),status))
        wire=bytearray(self.query());wire[-1]^=1
        self.assertIsNone(reply_for(bytes(wire),status))
        self.assertIsNone(reply_for(self.query()[:-1],status))

    def test_new_battery_meanings_cannot_be_invented(self):
        for status in (b'',bytes(112),bytes([3,1])+bytes(110),bytes([3])+bytes(112)):
            with self.assertRaises(ValueError):reply_for(self.query(),status)

    def test_peer_serializes_only_request_bound_status(self):
        status=bytes([3])+bytes(111)
        original=[]
        peer=SimpleNamespace(g=SimpleNamespace(data=dict(firmware_sha256=MAIN_SHA,
            z8_boot=dict(battery_status_input_body_hex=status.hex()))),record={},pending=[],
            tx={'row':{'index':7}},queue_reply=original.append)
        attach(peer)
        peer.queue_reply(self.query(True))
        self.assertEqual(len(peer.pending),1)
        self.assertEqual(peer.pending[0],(4,status,frames(status,family=4)[0]))
        self.assertEqual(peer.record['z8_status_query_peer']['replies'][0]['tx_index'],7)
        other=frames(struct.pack('<II',4,1),family=4,channel=2)[0]
        peer.queue_reply(other)
        self.assertEqual(original,[other])
        self.assertEqual(len(peer.pending),1)
        with self.assertRaises(ValueError):attach(peer)

    def test_wrong_firmware_never_installs_status_peer(self):
        peer=SimpleNamespace(g=SimpleNamespace(data={'firmware_sha256':'wrong'}),record={})
        with self.assertRaises(ValueError):attach(peer)
        self.assertEqual(peer.record,{})


if __name__=='__main__':unittest.main()
