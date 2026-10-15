# SPDX-License-Identifier: GPL-2.0-or-later
"""QTest fixture sequence regression; no model or firmware execution."""
import unittest
import test_z8_heif_visible_input as visible
from test_z8_d980_internal import Lab as Driver

class Bus:
    configure=Driver.configure
    enable=Driver.enable
    periodic=Driver.periodic
    def __init__(self,latched=True):
        self.calls=[];self.words={};self.epoch=0;self.latched=latched
    def write(self,address,value):
        self.calls.append((address,value));self.words[address]=value
        if address==0x150c0008:self.epoch+=1
    def state(self,name):
        if name=='primary-d980-single-frame-state':return {'epoch':self.epoch}
        if name=='engine150c-state':return dict(configured=0x4000,latched=0x4000 if self.latched else 0,active=0x4000 if self.epoch else 0)
        if name=='primary-d980-state':return dict(submits=1,frames=0,latched={hex(a-0x150d9800):v for a,v in self.words.items() if 0x150d9800<=a<0x150d9a00})
        raise AssertionError(name)

class VisibleSequenceTests(unittest.TestCase):
    def test_exact_packed14_pitch_and_public_submit_latch_start_order(self):
        bus=Bus();visible.configure_input(bus)
        self.assertEqual(bus.words[0x150d9900],224)
        controls=[x for x in bus.calls if x[0]<0x150d9800]
        self.assertEqual(controls,[(0x150c0060,0x4000),(0x150c0064,0x4000),
            (0x150c020c,0x4000000),(0x150c0008,0x4000),(0x15080010,1),(0x15080028,1)])
        self.assertEqual(bus.epoch,1)
    def test_missing_latch_is_caught_before_enable(self):
        bus=Bus(latched=False)
        with self.assertRaisesRegex(AssertionError,'latch'):visible.configure_input(bus)
        self.assertNotIn(0x150c0008,bus.words)
    def test_roi_uses_packed_pitch_and_checks_complete_frame(self):
        raw=b''.join(bytes([row])*224 for row in range(64))
        self.assertEqual(visible.visible_region(raw),b''.join(bytes([row])*112 for row in range(32,48)))
        with self.assertRaises(AssertionError):visible.visible_region(bytes(256*64))
    def test_independent_roi_decode_rejects_wrong_frame_or_bit(self):
        upper=sum((14335 if x<2 else 2047)<<(14*x) for x in range(64)).to_bytes(112,'little')
        lower=sum((2047 if x<2 else 14335)<<(14*x) for x in range(64)).to_bytes(112,'little')
        roi=upper*8+lower*8
        visible.verify_marker(roi,1,True)
        visible.verify_marker(bytes(1792),2,False)
        with self.assertRaises(AssertionError):visible.verify_marker(roi,2,True)
        bad=bytearray(roi);bad[0]^=1
        with self.assertRaises(AssertionError):visible.verify_marker(bad,1,True)
if __name__=='__main__':unittest.main()
