# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline C1 packed10 vs audited HIF decode, with job/frame/hash binding.

Code-value metrics only: no range rescaling, RGB, tone mapping or ISP claim.
CLI first binds file NAL/parameter bytes to the same-job lane hashes.
"""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from z8_heif_receipts import CODEC,C1,describe,require,sha_file,integer,c1_identity,codec_completed
from z8_hif_audit import HIF,sha

def read_json(path):
    require(path.stat().st_size<=2*1024*1024,'Receipt JSON budget');return json.loads(path.read_text())
def local_file(root,name):
    require(isinstance(name,str) and Path(name).name==name and name not in ('.','..'),'Source path must be a local filename')
    path=(root/name).resolve();require(path.is_relative_to(root.resolve()),'Source path escapes receipt');return path

def bind_file(hif,audit,codec):
    require(hif.stat().st_size<=512*1024*1024,'HIF size budget')
    require(audit.get('decoded') is True and sha_file(hif)==audit.get('input_sha256'),'HIF/audit hash mismatch')
    h=HIF(hif.read_bytes());plan=h.plan();index=integer(codec.get('source_c1_output'),0,2)
    if index==0:
        require((plan['width'],plan['height'],plan['columns'],plan['rows'])==(8256,5504,11,2),'Expected native main grid')
        ids=plan['tiles'];slots=[ids[(s//12)*11+min(s%12,10)] for s in range(24)];lanes=4
        decoded_hash=audit['primary_sha256']
    else:
        ids=[i for i,t in h.items.items() if t=='hvc1' and h.geometry(i)==(codec['width'],codec['height'])]
        require(len(ids)==1,'Unique auxiliary file geometry required');slots=ids;lanes=1
        decoded=[r for r in audit.get('auxiliary_hevc',[]) if r['item']==ids[0]]
        require(len(decoded)==1,'Missing audited auxiliary decode');decoded_hash=decoded[0]['decoded_sha256']
    require(len(codec['tiles'])==len(ids),'Codec/file logical tile count mismatch')
    c=h.codec(ids[0]);params=b''.join(b'\0\0\0\1'+c['parameters'][k] for k in (32,33,34))
    streams=[bytearray(params) for _ in range(lanes)]
    for n,i in enumerate(ids):
        nal=b'\0\0\0\1'+h.payload(i)[4:]
        require(sha(nal)==codec['tiles'][n]['dma_nal_sha256'],'Actual file NAL differs from codec job')
        require(h.codec(i)['parameters']==c['parameters'],'Unequal file parameter sets')
    for slot,i in enumerate(slots):streams[slot%lanes]+=b'\0\0\0\1'+h.payload(i)[4:]
    for lane,stream in enumerate(streams):
        require(len(stream)==codec['lanes'][lane]['bytes'] and sha(stream)==codec['lanes'][lane]['sha256'],'Actual file lane differs from codec job')
    return decoded_hash

def measure(source_path,job_path,decoded_path,expected_decoded_hash):
    source=read_json(source_path);job=read_json(job_path)
    require(source.get('verified') is True and job.get('verified') is True,'Unverified source/job receipt')
    require(source.get('packing')=='three-10bit-lsb-LE32; UV-U-first hypothesis','Unsupported packing/UV order')
    before=source['device_state'];require(before==source['device_state_after'],'Source export identity changed')
    cj,frame,spans,_=describe(before);codec=job['device_states'][CODEC]
    require(job['device_states']==job['device_states_after'],'Job receipt identity changed')
    current=job['device_states'][C1];describe(current)
    require(c1_identity(current)==c1_identity(before),'Job source differs from exported C1')
    require(codec_completed(codec),'Codec not complete')
    require(codec.get('source_c1_job')==cj and codec.get('source_frame')==frame and source.get('c1_job')==cj and source.get('frame')==frame,'Source/job frame identity mismatch')
    index=integer(codec.get('source_c1_output'),0,2);row=before['outputs'][index]
    w,h,stride=row['width'],row['height'],row['stride']
    require([codec.get('width'),codec.get('height')]==[w,h] and codec.get('input_sha256')==[row[p+'_sha256'] for p in ('y','uv')],'Codec input hash/geometry mismatch')
    require(decoded_path.stat().st_size==w*h*3 and sha_file(decoded_path)==expected_decoded_hash,'Decoded length/hash mismatch')
    files={}
    for plane in ('y','uv'):
        matches=[r for r in source['planes'] if r['output']==index and r['plane']==plane]
        require(len(matches)==1,'Unique source plane required');rec=matches[0]
        expected=next(s for s in spans if s['output']==index and s['plane']==plane)
        require(all(rec.get(k)==v for k,v in expected.items()),'Source descriptor mismatch')
        path=local_file(source_path.parent,rec['path'])
        require(path.stat().st_size==rec['bytes'] and sha_file(path)==rec['sha256'],'Source RAM hash mismatch');files[plane]=path
    decoded=np.memmap(decoded_path,dtype='<u2',mode='r');result={};base=0
    for name,plane,component,ph,pw in (('Y','y',None,h,w),('U','uv',0,h//2,w//2),('V','uv',1,h//2,w//2)):
        packed=np.memmap(files[plane],dtype='<u4',mode='r',shape=(h if plane=='y' else h//2,stride//4))
        dest=decoded[base:base+pw*ph].reshape(ph,pw);squared=0;max_error=0
        for start in range(0,ph,32):
            words=packed[start:start+32];n=words.shape[0]
            require(not np.any(words>>30),'Nonzero packed10 spare bits')
            samples=np.empty((n,words.shape[1]*3),dtype=np.int32)
            for k in range(3):samples[:,k::3]=(words>>(k*10))&1023
            samples=samples[:,:w]
            if component is not None:samples=samples[:,component::2]
            actual=dest[start:start+n].astype(np.int32);require(not np.any(actual>1023),'Decoded sample outside 10 bits')
            diff=samples-actual;max_error=max(max_error,int(np.abs(diff).max()))
            squared+=int(np.sum(diff.astype(np.int64)**2))
        count=pw*ph;mse=squared/count
        result[name]=dict(samples=count,squared_error_sum=squared,mse=mse,max_abs_error=max_error,
                          psnr_db=10*math.log10(1023**2/mse) if mse else 'infinity')
        base+=count;del packed
    del decoded
    for plane,path in files.items():require(sha_file(path)==row[plane+'_sha256'],'Source file changed during metrics')
    require(sha_file(decoded_path)==expected_decoded_hash,'Decoded file changed during metrics')
    return dict(c1_job=cj,frame=frame,codec_job=codec['jobs'],source_output=index,width=w,height=h,
                input_sha256=codec['input_sha256'],decoded_sha256=expected_decoded_hash,planes=result,
                peak=1023,bit_order_verified=False,uv_order_verified=False,scope='Actual exported C1 code values, no color/range transform; numerical packing hypothesis')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','job','hif','audit','decoded','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();codec=read_json(a.job)['device_states'][CODEC]
    digest=bind_file(a.hif,read_json(a.audit),codec)
    result=measure(a.source,a.job,a.decoded,digest)
    result.update(hif_sha256=sha_file(a.hif),source_receipt_sha256=sha_file(a.source),job_receipt_sha256=sha_file(a.job),audit_sha256=sha_file(a.audit))
    with a.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
