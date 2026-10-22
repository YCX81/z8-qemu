# SPDX-License-Identifier: GPL-2.0-or-later
"""Real-sample consistency and malformed-descriptor rejection checks."""
import hashlib
import os,sys
from pathlib import Path
import struct
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from parse_models import FIRMWARE, SAMPLES, parse_blob

@unittest.skipUnless(os.environ.get('E7_FIRMWARE_DIR'),'Private pinned MAIN images required')
class ModelRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blobs={}
        for fw,offsets in [('z8-c311',[0x1bb0e30,0x1caf7e0]),
                           ('z9-c532',[0x1b6ef40,0x1c6d8f0])]:
            data=(FIRMWARE/f'{fw}-main.bin').read_bytes()
            if hashlib.sha256(data).hexdigest()!=SAMPLES[fw]:
                raise ValueError('test input identity changed')
            for kind,p in zip(['vehicle','animal'],offsets):
                ds,ps=struct.unpack_from('<II',data,p+64)
                cls.blobs[(fw,kind)]=data[p:p+64+ds+ps]

    def test_all_four_model_containers(self):
        for (fw,kind),blob in self.blobs.items():
            with self.subTest(fw=fw,kind=kind):
                r=parse_blob(blob)
                self.assertEqual(r['input']['shape_whc'],[384,256,3])
                self.assertEqual(r['parsed_records'],18 if kind=='animal' else 17)
                c=30 if kind=='animal' else 26
                self.assertEqual(r['terminal_shapes_whc'],[[24,16,c],[12,8,c]])

    def test_cross_camera_exact_bytes(self):
        for kind in ['animal','vehicle']:
            self.assertEqual(self.blobs[('z8-c311',kind)],self.blobs[('z9-c532',kind)])

    def test_branch_is_from_shared_feature(self):
        r=parse_blob(self.blobs[('z8-c311','animal')]);ls=r['layers']
        self.assertEqual(ls[13]['name'],'layer16-maxpool')
        self.assertEqual(ls[13]['parent_index'],8)
        self.assertEqual(ls[8]['name'],'layer9-conv')
        self.assertEqual(ls[13]['shape_whc'],[12,8,64])

    def test_fused_pool_internal_dimension_evidence(self):
        r=parse_blob(self.blobs[('z8-c311','animal')]);p=r['layers'][7]
        self.assertEqual(p['fused_pool']['serialized_prepool_width'],48)
        self.assertEqual(p['fused_pool']['serialized_prepool_elements'],48*32)
        self.assertEqual(p['shape_whc'],[24,16,64])

    def mutate_reject(self,pos,fmt,value):
        b=bytearray(self.blobs[('z8-c311','animal')]);struct.pack_into(fmt,b,pos,value)
        with self.assertRaises(ValueError):parse_blob(b,enforce_identity=False)

    def test_truncated_payload_rejected(self):
        with self.assertRaises(ValueError):parse_blob(self.blobs[('z8-c311','animal')][:-1],enforce_identity=False)

    def test_wrong_container_size_rejected(self):
        self.mutate_reject(0x44,'<I',0xffffffff)

    def test_wrong_layer_count_rejected(self):
        self.mutate_reject(0x62,'<I',17)

    def test_wrong_name_length_rejected(self):
        self.mutate_reject(0x12a5+2,'<I',11)

    def test_forward_parent_rejected(self):
        self.mutate_reject(0x12b7+7,'<I',100)

    def test_filter_channel_disagreement_rejected(self):
        self.mutate_reject(0x12f1,'<I',32)

    def test_pool_extent_disagreement_rejected(self):
        self.mutate_reject(0x1a65+9+11*4,'<I',47)

    def test_unreviewed_blob_rejected(self):
        b=bytearray(self.blobs[('z8-c311','animal')]);b[-1]^=1
        with self.assertRaises(ValueError):parse_blob(b)

    def test_disabled_pool_union_not_interpreted(self):
        b=bytearray(self.blobs[('z8-c311','animal')]);struct.pack_into('<I',b,0x12f1+9+13*4,0x12345678)
        r=parse_blob(b,enforce_identity=False)
        self.assertNotIn('fused_pool',r['layers'][1])
        self.assertEqual(r['layers'][1]['shape_whc'],[192,128,16])

if __name__=='__main__':unittest.main(verbosity=2)
