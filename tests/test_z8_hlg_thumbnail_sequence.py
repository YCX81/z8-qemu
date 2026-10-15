# SPDX-License-Identifier: GPL-2.0-or-later
"""Address-routing regression tests for the QTest driver, not device execution."""
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import test_z8_hlg_thumbnail as thumb
from test_z8_hlg_pipeline import TARGETS

class Bus:
    def __init__(self):
        self.words={};self.c1_start=None
    def write(self,pa,value):
        self.words[pa]=value
        if pa==0x15432004 and value==0x101:self.c1_start=self.words.copy()
    def pair(self,pa,value):
        self.write(pa,value&0xffffffff);self.write(pa+4,value>>32)
    def state(self,name):return {'jobs':0 if name==thumb.STATE else 1}
    def read_ram(self,pa,size):return bytes(size)

class ThumbnailSequenceTests(unittest.TestCase):
    def setUp(self):
        source=(Path(__file__).resolve().parents[1]/'qemu/nikon-z9-lab.c').read_text()
        table=source.split('engine1542_bases[40] = {',1)[1].split('};',1)[0]
        self.bases=[int(s,16) for s in re.findall(r'0x[0-9a-f]+',table)]
        self.assertEqual(self.bases[6:9],[0x15422300,0x15422400,0x15422500])

    def test_thumbnail_registers_target_the_same_index_as_model(self):
        g=Bus();thumb.registers(g,7,{0x38:192,0xc:0x41000010})
        self.assertEqual(g.words.get(self.bases[7]+0x38),192)
        self.assertNotIn(self.bases[8]+0x38,g.words)

    def test_sdr_programs_all_three_outputs_before_c1_start(self):
        g=Bus()
        with patch.object(thumb,'source_sdr',return_value=None):thumb.sdr_thumbnail(g)
        self.assertIsNotNone(g.c1_start)
        for j,pitch in enumerate((64,32,32)):
            base=self.bases[6+j]
            self.assertEqual(g.c1_start.get(base+0x38),pitch)
            self.assertEqual(g.c1_start.get(base+0x20),TARGETS[j][0]&0xffffffff)
        self.assertEqual(g.words.get(self.bases[7]+0x20),thumb.TY&0xffffffff)

    def test_replayed_thumbnail_addresses_follow_snapshot_block_index(self):
        g=Bus();g.thumb_snapshot={'device_states':{'engine1542-state':{'blocks':[
            {'base':base,'config':{}} for base in self.bases]}}}
        thumb.configure_thumb(g)
        self.assertEqual(g.words.get(self.bases[7]+0x20),thumb.TY&0xffffffff)
        self.assertEqual(g.words.get(self.bases[7]+0x28),thumb.TUV&0xffffffff)
        self.assertEqual(g.words.get(self.bases[2]+0x20),TARGETS[1][0]&0xffffffff)
