# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored synthetic contracts, never a hardware or model-quality oracle."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pcnn_packets as packets
import animal_channels as channels
import parse_models


class PacketContracts(unittest.TestCase):
    def test_empty_unknown_and_unterminated_rejected(self):
        for raw in (b'',struct.pack('<H',0xffff),struct.pack('<H',0x7000)):
            with self.assertRaises(ValueError):packets.parse(raw)

    def test_authored_termination_and_control(self):
        events=packets.parse(struct.pack('<3H',0x7001,0x4080,0))
        self.assertEqual([e['words'] for e in events],[(0x7001,),(0x4080,),(0,)])
        self.assertEqual([e['offset'] for e in events],[0,2,4])

    def test_authored_dense_matrix_boundaries(self):
        raw=struct.pack('<HI',0xa600,0)+bytes(512+32)+bytes(2)
        events=packets.parse(raw)
        self.assertEqual((events[0]['matrix_bytes'],events[0]['bias_offset'],events[0]['batch']),(512,518,0))
        self.assertEqual(events[-1]['offset'],550)

    def test_truncated_payload_and_unfinished_accumulator(self):
        with self.assertRaises(ValueError):packets.parse(struct.pack('<HI',0xa600,0))
        with self.assertRaises(ValueError):packets.parse(struct.pack('<HI',0xa200,0)+bytes(544)+bytes(2))

    def test_container_marker_and_geometry_guards(self):
        with self.assertRaises(ValueError):parse_models.parse_blob(bytes(256))
        self.assertEqual(parse_models.output_dim(32,3,2,1,1,1),16)
        with self.assertRaises(ValueError):parse_models.output_dim(32,3,0,1,1,1)


class PublicChannelContracts(unittest.TestCase):
    def test_scalar_rounding_and_sigmoid(self):
        self.assertEqual(channels.sigmoid(0),0.5)
        self.assertEqual(channels.bits(channels.from_bits(0x3f800000)),0x3f800000)
        self.assertAlmostEqual(channels.exp_approx(0),1,places=6)

    def test_authored_head_contract_and_shape_guard(self):
        spec=dict(anchors=[(2,2),(4,4),(8,8),(16,16)],pairs=[(0,2),(1,2),(0x12,0),(0x13,0)],
                  thresholds=[0.5,0.4,0.8],reference=[1]*10,
                  curves=[[i/1000 for i in range(1000)] for _ in range(4)])
        heads=[[-12.]*(w*h*c) for w,h,c in channels.SHAPES]
        self.assertEqual(channels.decode_candidates(heads,spec)['before_suppression'],[])
        area=24*16
        heads[0][4*area]=8;heads[0][7*area]=8
        result=channels.decode_candidates(heads,spec)['before_suppression']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['class'],2)
        with self.assertRaises(ValueError):channels.decode_candidates([[],[]],spec)

    def test_classwise_center_suppression(self):
        a=dict(cx=.5,cy=.5,w=.2,h=.2,score=900,**{'class':2})
        b={**a,'score':800};c={**b,'class':3}
        self.assertEqual(len(channels.suppress([a,b],.8)),1)
        self.assertEqual(len(channels.suppress([a,c],.8)),2)

    def test_private_calibration_is_explicit(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):channels.load_spec()


class HelpContracts(unittest.TestCase):
    def test_metadata_clis_need_no_private_inputs_for_help(self):
        for module in ('parse_models.py','pcnn_inventory.py','pcnn_replay.py'):
            with self.subTest(module=module):
                result=subprocess.run([sys.executable,str(ROOT/module),'--help'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('--output',result.stdout)
