# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_z8_heif_receipts import fixture
from z8_heif_receipts import Session
import z8_heif_psnr as p
from test_z8_hif_audit import fixture as hif_fixture
from z8_hif_audit import HIF,sha

class PSNRTests(unittest.TestCase):
    def test_file_nal_and_parameter_hashes_bind_before_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'synthetic.hif';file.write_bytes(hif_fixture());h=HIF(file.read_bytes())
            codec=dict(source_c1_output=1,width=320,height=224,
                tiles=[dict(dma_nal_sha256=sha(b'\0\0\0\1'+h.payload(2)[4:]))],
                lanes=[dict(bytes=len(h.tile_stream(2)),sha256=sha(h.tile_stream(2)))])
            audit=dict(decoded=True,input_sha256=p.sha_file(file),auxiliary_hevc=[dict(item=2,decoded_sha256='a'*64)])
            self.assertEqual(p.bind_file(file,audit,codec),'a'*64)
            codec['lanes'][0]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'lane'):p.bind_file(file,audit,codec)

    def test_file_nal_change_rejected_even_with_new_file_hash(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'synthetic.hif';raw=hif_fixture();h=HIF(raw)
            codec=dict(source_c1_output=1,width=320,height=224,
                tiles=[dict(dma_nal_sha256=sha(b'\0\0\0\1'+h.payload(2)[4:]))],lanes=[])
            file.write_bytes(raw.replace(b'\x26\x01\x80',b'\x26\x01\x81'))
            audit=dict(decoded=True,input_sha256=p.sha_file(file),auxiliary_hevc=[dict(item=2,decoded_sha256='a'*64)])
            with self.assertRaisesRegex(ValueError,'NAL'):p.bind_file(file,audit,codec)
    def files(self,folder):
        g,s,m,c=fixture(folder);session=Session(g,export_c1=True)
        source=g.out/session.capture_source();job=g.out/session.capture_job()
        decoded=g.out/'decoded.yuv';decoded.write_bytes(bytes(6*4*3))
        return source,job,decoded
    def test_exact_and_known_error_per_plane(self):
        with tempfile.TemporaryDirectory() as d:
            source,job,decoded=self.files(d)
            row=p.measure(source,job,decoded,p.sha_file(decoded))
            self.assertEqual(row['planes']['Y']['psnr_db'],'infinity')
            raw=np.zeros(36,dtype='<u2');raw[0]=3;raw[24]=2;raw[30]=1;decoded.write_bytes(raw.tobytes())
            row=p.measure(source,job,decoded,p.sha_file(decoded))
            for plane,error,n in (('Y',3,24),('U',2,6),('V',1,6)):
                self.assertEqual(row['planes'][plane]['max_abs_error'],error)
                self.assertAlmostEqual(row['planes'][plane]['mse'],error*error/n)
    def test_hash_identity_stride_truncation_and_path_rejected(self):
        for problem in ('hash','frame','stride','truncated','path','decoded_hash','uv_order'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as d:
                source,job,decoded=self.files(d);s=json.loads(source.read_text());j=json.loads(job.read_text())
                if problem=='hash':(source.parent/s['planes'][0]['path']).write_bytes(b'\1'*256)
                if problem=='frame':j['device_states']['z8-still-hevc-codec-state']['source_frame']=2
                if problem=='stride':s['device_state']['outputs'][0]['stride']=128
                if problem=='truncated':decoded.write_bytes(bytes(70))
                if problem=='path':s['planes'][0]['path']='../escape.bin'
                if problem=='uv_order':s['packing']='UV-V-first'
                source.write_text(json.dumps(s));job.write_text(json.dumps(j))
                with self.assertRaises(ValueError):p.measure(source,job,decoded,'0'*64 if problem=='decoded_hash' else p.sha_file(decoded))
if __name__=='__main__':unittest.main()
