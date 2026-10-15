# SPDX-License-Identifier: GPL-2.0-or-later
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_orientation import POSES, set_pose, read_orientation
from z8_resources import MAIN_SHA


class Peer:
    def __init__(self):self.calls=[];self.running=False;self.sample='000000004000';self.mismatch=False
    def command(self,op,**args):
        self.calls.append((op,args))
        if op=='query-status':return {'running':self.running}
        if op=='qom-set':self.sample=args['value'];return None
        if args['property']=='orientation-input':return 'bad' if self.mismatch else self.sample
        return json.dumps({'fixture':True,'sample_hex':self.sample,'reads':1})


class Orientation(unittest.TestCase):
    def test_resample_property_is_explicit_and_boolean(self):
        from z8_guest import validate_properties
        for value in ('on','off'):
            self.assertEqual(validate_properties(['z8-secondary-resample-model='+value]),
                             ['z8-secondary-resample-model='+value])
        with self.assertRaises(ValueError):validate_properties(['z8-secondary-resample-model=guess'])
        with self.assertRaises(ValueError):validate_properties(['z8-secondary-resample-model=on']*2)
    def guest(self):
        peer=Peer();return SimpleNamespace(qmp=peer,data={'firmware_sha256':MAIN_SHA},
            log=lambda:'',read_bytes=lambda a,n:bytes(n))
    def test_presets_use_observed_virtual_axes(self):
        self.assertEqual([POSES[p]['sample_hex'] for p in ('landscape','left','right')],
                         ['c00000000000','0000c0000000','000040000000'])
        self.assertEqual([POSES[p]['classification'] for p in ('landscape','left','right')],[0,1,2])
    def test_setter_changes_only_device_input_and_records_readback(self):
        g=self.guest();record=set_pose(g,'left')
        self.assertEqual(g.qmp.sample,'0000c0000000')
        self.assertEqual(record['pose'],'left')
        self.assertFalse(record['native_ui_verified'])
        self.assertEqual(g.data['orientation_inputs'],[record])
        writes=[args for op,args in g.qmp.calls if op=='qom-set']
        self.assertEqual(writes,[{'path':'/machine','property':'orientation-input','value':'0000c0000000'}])
    def test_reject_unknown_pose_identity_running_guest_and_fault(self):
        for kind in ('pose','identity','running','fault'):
            g=self.guest();pose='landscape'
            if kind=='pose':pose='upside-down'
            if kind=='identity':g.data['firmware_sha256']='bad'
            if kind=='running':g.qmp.running=True
            if kind=='fault':g.log=lambda:'Z9_UNKNOWN'
            with self.subTest(kind=kind),self.assertRaises((ValueError,RuntimeError)):set_pose(g,pose)
            self.assertFalse(any(op=='qom-set' for op,_ in g.qmp.calls))
    def test_readback_failure_is_not_accepted(self):
        g=self.guest();g.qmp.mismatch=True
        with self.assertRaises(RuntimeError):set_pose(g,'right')
        self.assertNotIn('orientation_inputs',g.data)
    def test_read_native_fields_separately_from_input(self):
        g=self.guest();b=bytearray(0x1c8)
        struct.pack_into('<3h',b,0x28,0,-1024,0);struct.pack_into('<3f',b,0xb8,90,180,0)
        struct.pack_into('<3f',b,0xcc,0,-1,0);struct.pack_into('<I',b,0xc4,1)
        g.read_bytes=lambda a,n:bytes(b[:n])
        r=read_orientation(g)
        self.assertEqual(r['native_raw_xyz'],[0,-1024,0]);self.assertEqual(r['angles_degrees'],[90,180,0])
        self.assertEqual(r['classification'],1);self.assertEqual(r['filtered_xyz'],[0,-1,0])
    def test_short_native_read_rejected(self):
        g=self.guest();g.read_bytes=lambda a,n:b''
        with self.assertRaises(RuntimeError):read_orientation(g)


if __name__=='__main__':unittest.main()
