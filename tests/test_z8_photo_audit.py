# SPDX-License-Identifier: GPL-2.0-or-later
"""Small authored TIFF fixtures exercise file/pixel mapping counterexamples."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_photo_audit import audit_photos
from z8_precapture_audit import DecodedRaw
from z8_capture_bindings import load_profile

def sha(data): return hashlib.sha256(data).hexdigest()

class PhotoAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.snapshots=[];files=[];photos=[];card=bytearray(1024)
        self.arrays={}
        for i in (1,2):
            raw=np.arange(8,dtype='<u2').reshape(2,4)+i
            p=raw.tobytes();(self.root/f'raw{i}.bin').write_bytes(p)
            # Minimal Nikon TIFF accepted only by injected test decoder.
            nef=b'II*\0'+struct.pack('<I',8)+struct.pack('<H',1)+struct.pack('<HHII',271,2,6,26)+bytes(4)+b'NIKON\0'+bytes([i])
            name=f'file{i}.NEF';(self.root/name).write_bytes(nef);self.arrays[name]=raw
            card[i*64:i*64+len(nef)]=nef
            snap=dict(main_sha256=load_profile()['main_sha256'],kind='engine151_raw_output',
                paused_during_snapshot=True,device_identity_unchanged=True,guest_state_modified=False,
                device_job_counter=i,device_frame_id=i+10,
                producer=dict(origin='guest_producer_dma',path=f'raw{i}.bin',sha256=sha(p),
                    encoding='u16le',width=4,height=2,stride_samples=4,bit_depth=14),
                photo_sequence=dict(job=i,frame=i+10,previous_job=i-1,previous_bytes=(i-1)*64,per_job_bytes=64,cumulative_bytes=i*64))
            self.snapshots.append(snap)
            files.append(dict(file_id=f'f{i}',path=name,sha256=sha(nef),bytes=len(nef),export_status='exported',
                              extents=[dict(offset=i*64,length=len(nef))]))
            photos.append(dict(job=i,file_id=f'f{i}'))
        (self.root/'card.raw').write_bytes(card)
        native=dict(firmware_sha256=load_profile()['main_sha256'],owned_qemu_stopped=True,
            error=None,cpu_register_redirection=False,ram_patches=[],raw_output_snapshots=self.snapshots)
        exported=dict(schema='z8-nef-export-v1',status='exported',source_main_sha256=load_profile()['main_sha256'],
            executed_program_sha256=load_profile()['main_sha256'],
            storage=dict(kind='guest_nvme_export',catalog_complete=True,card_path='card.raw',nef_files=files))
        self.native=native;self.exported=exported
        self.manifest=dict(schema='z8-photo-audit-v1',photos=photos)
    def run_audit(self):
        for name,body,key in (('result.json',self.native,'run'),('export.json',self.exported,'export')):
            data=json.dumps(body).encode();(self.root/name).write_bytes(data)
            self.manifest[key]=dict(path=name,sha256=sha(data))
        return audit_photos(self.manifest,self.root,decoder=lambda path:DecodedRaw(self.arrays[path.name],16383))
    def test_each_photo_all_samples_and_synthetic_scope(self):
        r=self.run_audit();self.assertEqual(r['status'],'synthetic_checks_passed')
        self.assertEqual(r['samples_compared'],16);self.assertFalse(r['native_accepted'])
        self.assertFalse(r['raw_precapture_verified'])
    def test_cross_photo_file_mapping_cannot_pass_pixels(self):
        self.manifest['photos'][0]['file_id']='f2';self.manifest['photos'][1]['file_id']='f1'
        self.assertEqual(self.run_audit()['status'],'failed')
    def test_missing_duplicate_skipped_or_stale_job(self):
        for mutate in (lambda:self.manifest['photos'].pop(),
                       lambda:self.manifest['photos'][1].update(file_id='f1'),
                       lambda:self.snapshots[1].update(device_job_counter=3),
                       lambda:self.snapshots[1]['photo_sequence'].update(previous_bytes=0),
                       lambda:self.snapshots[1].update(device_frame_id=11)):
            old=copy.deepcopy((self.native,self.exported,self.manifest))
            mutate();self.assertEqual(self.run_audit()['status'],'failed')
            self.native,self.exported,self.manifest=old;self.snapshots=self.native['raw_output_snapshots']
    def test_card_pixel_and_identity_corruption(self):
        for filename in ('card.raw','raw1.bin','file1.NEF'):
            path=self.root/filename;old=path.read_bytes();path.write_bytes(bytes(len(old)))
            self.assertEqual(self.run_audit()['status'],'failed');path.write_bytes(old)
        self.native['owned_qemu_stopped']=False
        self.assertEqual(self.run_audit()['status'],'failed')

if __name__=='__main__':unittest.main()
