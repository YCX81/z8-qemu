# SPDX-License-Identifier: GPL-2.0-or-later
import unittest
import x265_still_probe as probe

class ProbeGateTests(unittest.TestCase):
    def test_explicit_ptl_is_derived_from_sps_and_conflicts_rejected(self):
        sps=dict(profile_space=0,tier=0,profile_idc=2,compatibility=0x20000000,
                 constraint_flags_hex='900000000000',level_idc=120,luma_depth=10)
        row=dict(sps=sps);probe.normalize_ptl(row)
        self.assertEqual(row['ptl']['profile_idc'],2);self.assertNotIn('luma_depth',row['ptl'])
        row['ptl']['profile_idc']=4
        with self.assertRaises(ValueError):probe.normalize_ptl(row)
    def row(self,w,h,key='main10-k16-b2',**changes):
        row=dict(case=key,width=w,height=h,returncode=0,status='encoded',type19_compatible=True)
        row.update(changes);return row

    def test_gate_requires_same_config_at_both_required_sizes(self):
        self.assertEqual(probe.compatible_configs([self.row(320,224)]),[])
        self.assertEqual(probe.compatible_configs([self.row(320,224),self.row(768,2752,'other')]),[])
        self.assertEqual(probe.compatible_configs([self.row(320,224),self.row(768,2752)]),['main10-k16-b2'])

    def test_type20_timeout_or_failed_process_never_qualifies(self):
        for change in ({'type19_compatible':False},{'status':'timeout'},{'returncode':1}):
            self.assertEqual(probe.compatible_configs([self.row(320,224),self.row(768,2752,**change)]),[])

    def test_width_limit_probe_does_not_veto_grid_backend(self):
        rows=[self.row(320,224),self.row(768,2752),self.row(8256,224,status='encoder_open_failed',returncode=1)]
        self.assertEqual(probe.compatible_configs(rows),['main10-k16-b2'])

if __name__=='__main__':unittest.main()
