# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in read-only HEIF receipts at original stopped-CPU boundaries.

No stop/cont, register writes, QOM setters or IRQ operations. The caller owns
the existing breakpoint. QOM and image budgets are separate from 64 KiB ABI.
"""
import hashlib
import json
from pathlib import Path
import re

C1='z8-capture-hlg-pipeline-state';CODEC='z8-still-hevc-codec-state';PROTOCOL='z8-still-hevc-state'
MAX_PHOTO_BYTES=96*1024*1024;MAX_RUN_BYTES=2*MAX_PHOTO_BYTES;CHUNK=16*1024*1024
MAX_QOM_SINGLE=128*1024;MAX_QOM_TOTAL=2*1024*1024;MAX_JOBS=8

def require(value,message):
    if not value:raise ValueError(message)
def sha_file(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def integer(v,lo=0,hi=(1<<63)-1):
    require(type(v) is int and lo<=v<=hi,'Invalid bounded integer');return v
def c1_identity(c1):
    return {k:c1.get(k) for k in ('jobs','source_job','source_frame','consumed151_job','input_y','input_uv',
                                  'input_y_sha256','input_uv_sha256','lut_sha256','outputs')}
def codec_completed(c):
    if type(c.get('jobs')) is not int or c['jobs']<1:return False
    if c.get('enabled') is not True or c.get('busy') is not False or c.get('failed') is not False:return False
    if c.get('result_valid') is True:return True
    # Original retirement may discard result_valid before a later observer.
    # Only the retained receipt of this exact completed job may substitute.
    return any(r.get('job')==c.get('jobs') and all(r.get(k)==c.get(k) for k in
        ('source_c1_job','source_frame','source_c1_output','width','height','input_sha256'))
        for r in c.get('history',[]))

def describe(c1):
    require(c1.get('enabled') is True and all(c1.get(k) is False for k in ('owned','requested','failed')),'C1 not retired')
    job=integer(c1.get('jobs'),1);frame=integer(c1.get('source_frame'),1)
    require(integer(c1.get('retired'))==job and integer(c1.get('source_job'),1)>0,'C1 job identity')
    rows=c1.get('outputs');require(isinstance(rows,list) and len(rows)==3,'Three C1 outputs required')
    spans=[];total=0
    for index,row in enumerate(rows):
        w=integer(row.get('width'),2,8256);h=integer(row.get('height'),2,5504)
        stride=integer(row.get('stride'),64,11008)
        require(not (w|h)&1 and stride==(((w*4+2)//3+63)&~63),'Packed10 stride/geometry')
        require(row.get('bits')==10 and row.get('chroma')=='420','C1 packed10 420 required')
        for plane,height in (('y',h),('uv',h//2)):
            size=integer(row.get(plane+'_bytes'),1,MAX_PHOTO_BYTES);pa=integer(row.get(plane))
            require(size==stride*height and not pa&31 and 0x800000000<=pa<=0x900000000-size,'C1 RAM span')
            digest=row.get(plane+'_sha256');require(isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest),'C1 source hash')
            for prior in spans:require(not(pa<prior['address']+prior['bytes'] and prior['address']<pa+size),'C1 span overlap')
            spans.append(dict(output=index,plane=plane,address=pa,bytes=size,sha256=digest));total+=size
    require(total<=MAX_PHOTO_BYTES,'Per-photo 96 MiB budget exceeded')
    return job,frame,spans,total

class Session:
    def __init__(self,guest,export_c1=False):
        self.g=guest;self.export_c1=export_c1;self.qom_bytes=0;self.image_bytes=0
        self.sources={};self.source_states={};self.source_attempts=0;self.last_job=0
    def paused(self):require(self.g.qmp.command('query-status').get('running') is False,'HEIF export requires paused guest')
    def qom(self,name):
        require(self.qom_bytes<MAX_QOM_TOTAL,'QOM receipt budget exhausted')
        raw=self.g.qmp.command('qom-get',path='/machine',property=name)
        text=raw if isinstance(raw,str) else json.dumps(raw)
        n=len(text.encode('utf-8'));self.qom_bytes+=n
        require(n<=MAX_QOM_SINGLE and self.qom_bytes<=MAX_QOM_TOTAL,'QOM receipt budget exhausted')
        obj=json.loads(text);require(isinstance(obj,dict),'QOM state object required');return obj
    def save(self,path,row):
        with path.open('x',encoding='utf-8') as f:json.dump(row,f,indent=2);f.write('\n')
        return path.relative_to(self.g.out).as_posix()
    def capture_job(self):
        self.paused()
        before={n:self.qom(n) for n in (CODEC,PROTOCOL,C1)};c=before[CODEC];p=before[PROTOCOL]
        job=integer(c.get('jobs'),1,MAX_JOBS);cj,frame,_,_=describe(before[C1])
        require(job==self.last_job+1 and p.get('jobs')==job,'Missing, repeated or out-of-order codec job')
        require(codec_completed(c),'Codec result not completed')
        index=integer(c.get('source_c1_output'),0,2);source=before[C1]['outputs'][index]
        require(c.get('source_c1_job')==cj and c.get('source_frame')==frame and
                [c.get('width'),c.get('height')]==[source['width'],source['height']] and
                c.get('input_sha256')==[source[p+'_sha256'] for p in ('y','uv')],'Codec/C1 source identity mismatch')
        after={n:self.qom(n) for n in before};require(before==after,'Codec identity changed during receipt')
        self.paused();folder=Path(self.g.out)/'heif-jobs';folder.mkdir(exist_ok=True)
        path=self.save(folder/f'job-{job:03}.json',dict(schema=1,verified=True,pc='0x40c99030',
            device_states=before,device_states_after=after,c1_export=self.sources.get((cj,frame)),
            scope='Read-only pre-consumer QOM; byte hashes are model receipts, not RAM dumps'))
        self.last_job=job;return path
    def capture_source(self):
        if not self.export_c1:return None
        self.paused();before=self.qom(C1);job,frame,spans,total=describe(before);key=(job,frame)
        if key in self.sources:
            require(c1_identity(before)==c1_identity(self.source_states[key]),'Same C1 job changed after source export');return self.sources[key]
        require(self.source_attempts<2 and self.image_bytes+total<=MAX_RUN_BYTES,'C1 two-photo/run byte budget exhausted')
        self.source_attempts+=1
        self.image_bytes+=total # reserve before any failed read; never refund
        folder=Path(self.g.out)/'heif-c1'/f'job-{job:03}-frame-{frame:03}';folder.mkdir(parents=True,exist_ok=False)
        records=[];after=None
        try:
            for span in spans:
                path=folder/f"output-{span['output']}-{span['plane']}.bin"
                with path.open('xb') as target:
                    for at in range(0,span['bytes'],CHUNK):
                        part=folder/f"part-{span['output']}-{span['plane']}-{at:08x}.bin";size=min(CHUNK,span['bytes']-at)
                        self.g.qmp.command('pmemsave',val=span['address']+at,size=size,filename=str(part.resolve()))
                        require(part.is_file() and part.stat().st_size==size,'Short C1 RAM export')
                        target.write(part.read_bytes());part.unlink() # own bounded temporary only
                require(sha_file(path)==span['sha256'],'C1 exported RAM hash mismatch')
                records.append(dict(span,path=path.name))
            after=self.qom(C1);require(before==after,'C1 identity changed during RAM export');self.paused()
            path=self.save(folder/'source.json',dict(schema=1,verified=True,pc='0x40c978b4',
                c1_job=job,frame=frame,bytes=total,device_state=before,device_state_after=after,planes=records,
                bit_order_verified=False,uv_order_verified=False,packing='three-10bit-lsb-LE32; UV-U-first hypothesis'))
            self.sources[key]=path;self.source_states[key]=before;return path
        except Exception as e:
            self.save(folder/'error.json',dict(verified=False,error=repr(e),bytes_budget_reserved=total,
                device_state_before=before,device_state_after=after,planes_written=records));raise
