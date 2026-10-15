# SPDX-License-Identifier: GPL-2.0-or-later
"""Stopped-CPU real RAM/IRQ tests using an explicitly supplied native MMIO fixture.

No Nikon program is executed. The private snapshot includes native LUT data;
it is not embedded in this test or shipped as a new firmware fixture.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import time
from test_z8_hlg_capture import Lab as PixelLab, sdr
from test_z8_producer_journal import configure151, A, require
from test_z8_d980_internal import PROFILE

OPTION='z8-capture-hlg-pipeline-model'
STATE='z8-capture-hlg-pipeline-state'
Y,UV=0x848000000,0x84d000000
TARGETS=((0x860000000,0x864000000),(0x868000000,0x868100000),(0x869000000,0x86a000000))

class Lab(PixelLab):
    def __init__(self,binary,folder,enabled,snapshot):
        super().__init__(binary,folder,True)
        self.snapshot=snapshot
        self.command[self.command.index('-M')+1]+=','+','.join([
            'engine1543-capture-control-model=on','capture-nv16-pipeline-model=on',
            OPTION+'='+('on' if enabled else 'off')])
        values=[(x*137+y*241)%16000 for y in range(32) for x in range(64)]
        (folder/'pixels.raw').write_bytes(struct.pack('<8s6I',b'NZ9RAW1\0',64,32,14,0,0,16383)+struct.pack('<2048H',*values))

    def start(self):
        super().start();self.sock.settimeout(300);self.qmp.deadline=time.monotonic()+1200

    def saved_hash(self,pa,n,name):
        dest=self.root/name
        self.qmp.command('human-monitor-command',**{'command-line':f'pmemsave {pa:#x} {n} "{dest}"'})
        require(dest.stat().st_size==n,'Complete actual RAM dump')
        with dest.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def producer(g):
    c=dict(PROFILE);c.update({0:2,4:8280<<16,8:5520<<16,0x100:12448,0x118:0})
    for i,pa in enumerate((A,A,A,0)):
        c[0x120+i*8]=pa&0xffffffff;c[0x124+i*8]=pa>>32
    g.configure(c,0x4000);g.write(0x150c020c,0x4000000);g.enable(0x4000);g.periodic();g.advance()
    require(g.state('primary-d980-state')['frames']==1,'Actual D980 generation')
    configure151(g,A,0,True)
    for off,value in {0x200:0x10000,0x21c:1,0x45c:2,0x400:0x15902058,
        0x450:0x15802040,0x454:0x8000c,0x430:12448,0x480:12384,0x484:12384}.items():
        g.write(0x15100000+off,value)
    g.pair(0x15100980,Y);g.pair(0x151009c0,UV)
    g.write(0x15100008,1);require(g.state('z8-capture-hlg-pixels-state')['jobs']==1,'Real packed12 producer')
    g.write(0x15100044,2)

def lut(g,corrupt=False):
    rows=g.snapshot['device_states']['engine1518-state']['instances'][0]['luts']
    for bank,selector in enumerate((0,8,1,9,2,10,3,11)):
        g.write(0x151800f4,selector<<8)
        for n,word in enumerate(rows[bank]):
            if corrupt and bank==0 and n==0:word^=1
            g.write(0x151800f8,word&0xffffffff);g.write(0x151800fc,word>>32)

def configure(g,corrupt_lut=False):
    d=g.snapshot['device_states'];src=d['engine1518-state']['instances'][0]['config']
    g.write(0x15180014,1)
    # Reset/control/status/indirect ports are replayed by their own protocol.
    for i,value in enumerate(src[:0x220//4]):
        off=i*4
        if value and off not in (0x30,0xf4,0xf8,0xfc,0x100,0x104,0x108,0x10c):g.write(0x15180000+off,value)
    g.pair(0x15180100,Y);g.pair(0x15180108,UV);lut(g,corrupt_lut)
    blocks=d['engine1542-state']['blocks']
    for index in (1,6,7,8,18,19,20):
        row=blocks[index];base=row['base']
        for key,value in row['config'].items():
            off=int(key,16)
            if 6<=index<=8 and off in (0x20,0x24,0x28,0x2c):continue
            g.write(base+off,value)
        if 6<=index<=8:
            for off,pa in zip((0x20,0x28),TARGETS[index-6]):g.pair(base+off,pa)
    # Capture C1: four header pairs must be explicitly programmed, including
    # zero banks. This is NOT a video ring pattern or a change to its semantics.
    for off,value in ((4,0x103),(8,2),(0xc,0),(0x10,0x20)):g.write(0x15432000+off,value)
    for row in range(4):
        g.pair(0x15432200+row*0x80,0xe0000000 if row==0 else 0)
        g.pair(0x15432208+row*0x80,0xe0003080 if row==0 else 0)
    require(g.state('engine1543-control-state')['blocks'][1]['ready'],'Original capture readiness, no bypass')

def guards(g):
    producer(g);configure(g)
    for p in TARGETS:
        for pa in p:g.write_ram(pa,b'\xa5'*64)
    plans=[(0x151800c4,0x20,0x22),(0x1542230c,0x41000010,0x01000200),
        (0x15422338,11040,11008),(0x15423300,2,0),
        (0x15422320,Y&0xffffffff,TARGETS[0][0]&0xffffffff),
        (0x15422320,(TARGETS[0][0]&0xffffffff)+1,TARGETS[0][0]&0xffffffff),
        (0x15432008,0x302,2)]
    g.expected_unknown=0x15432004
    for addr,value,restore in plans:
        require(g.tx(f'readl {addr:#x}')==restore,'Each negative case must start from its valid restored register')
        g.write(addr,value);g.write(0x15432004,0x101)
        require(g.state(STATE)['jobs']==0 and not g.irq(0x6a) and not g.irq(0xf9),'Rejected photo/video tuple has no completion')
        for p in TARGETS:
            for pa in p:require(g.read_ram(pa,64)==b'\xa5'*64,'Rejected DMA preserves destinations')
        g.write(addr,restore)
    return g.state(STATE)

def decode12(g,pa,stride,x,y):
    pair=g.read_ram(pa+y*stride+(x//2)*3,3)
    return ((pair[1]>>4)|(pair[2]<<4)) if x&1 else pair[0]|((pair[1]&15)<<8)

def decode10(g,pa,stride,x,y):
    word=int.from_bytes(g.read_ram(pa+y*stride+(x//3)*4,4),'little')
    return (word>>((x%3)*10))&1023

def verify_samples(g,row):
    blocks=g.snapshot['device_states']['engine1542-state']['blocks']
    for j,item in enumerate(row['outputs']):
        f={int(k,16):v for k,v in blocks[18+j]['config'].items()}
        def coord(at,axis):return ((f.get(0xc if axis else 8,0)+at*f[0x14 if axis else 0x10])<<f.get(0x20,0))>>14
        w,h,dp=item['width'],item['height'],item['stride']
        for x,y in ((0,0),(2,2),(w//2,h//2),(w-1,h-1)):
            v=decode12(g,Y,12384,coord(x,False),coord(y,True))
            require(decode10(g,item['y'],dp,x,y)==min(1023,(v+2)//4),'Independent full-resolution luma sample')
        for x,y in ((0,0),(w-2,h//2-1)):
            sx=coord(x,False)&~1;sy0=coord(y*2,True);sy1=coord(y*2+1,True)
            for k in (0,1):
                v0=decode12(g,UV,12384,sx+k,sy0);v1=decode12(g,UV,12384,sx+k,sy1)
                require(decode10(g,item['uv'],dp,x+k,y)==min(1023,(v0+v1+4)//8),'Independent paired-row chroma')

def positive(g):
    producer(g);configure(g);g.write(0x15432004,0x101)
    require(g.state(STATE)['requested'] and g.state(STATE)['jobs']==0,'C1 awaits source gate')
    g.write(0x15180008,0);g.write(0x15421118,0);g.write(0x15432010,0)
    g.write(0x15180030,1)
    row=g.state(STATE)
    require(row['jobs']==1 and row['owned'] and not row['failed'],'Actual C1 DMA completes')
    require(not g.irq(0x6a) and not g.irq(0xf9),'Masked pending does not raise either line')
    require(g.state('capture-nv16-pipeline-state')['jobs']==0,'HLG never published as NV16')
    for j,item in enumerate(row['outputs']):
        for plane in ('y','uv'):
            require(g.saved_hash(item[plane],item[plane+'_bytes'],f'{j}-{plane}.bin')==item[plane+'_sha256'],'Independent complete-plane RAM SHA')
    verify_samples(g,row)
    g.write(0x15180008,0x39f00000);g.write(0x15421118,0x11111);g.write(0x15432010,0x20)
    require(g.irq(0x6a) and g.irq(0xf9),'Unmask reveals existing pending')
    for pa in (0x1518000c,0x1542111c,0x15432014):g.write(pa,0)
    require(g.state(STATE)['owned'],'W1C zero cannot retire')
    g.write(0x15432014,0x20);require(g.irq(0x6a),'A1 still pending after C1 ACK')
    g.write(0x1542111c,0x11);require(not g.irq(0x6a) and g.state(STATE)['owned'],'Source still owns completion')
    g.write(0x1518000c,1<<20);require(not g.irq(0xf9) and not g.state(STATE)['owned'],'Final source ACK retires')
    g.write(0x15180030,0);g.write(0x1518000c,1<<21)
    g.write(0x15432004,0x100);g.write(0x15432004,0x103)
    g.expected_unknown=0x15432004;g.write(0x15432004,0x101)
    end=g.state(STATE)
    require(end['jobs']==1 and end['last_error']=='producer_not_complete_or_fresh','Same 151 generation cannot be reused')
    return row

def disabled(g):
    producer(g);configure(g);g.expected_unknown=0x15432004;g.write(0x15432004,0x101)
    require(g.state(STATE)['jobs']==0 and not g.irq(0x6a),'Default off retains strict stop')
    return g.state(STATE)

def cancel(g):
    producer(g);configure(g);g.write(0x15432004,0x101);g.write(0x15432004,0x100)
    g.write(0x15180030,1)
    row=g.state(STATE);require(row['jobs']==0 and row['cancels']==1 and not row['requested'],'Cancel before gate has no late DMA')
    require(not g.irq(0x6a) and not g.irq(0xf9),'Cancel does not forge ring STOP or frame IRQ')
    return row

def corrupt_source(g):
    producer(g);configure(g);g.write(0x15432004,0x101)
    g.write_ram(Y,b'\x5a\xa5\x5a');g.write(0x15180030,1)
    row=g.state(STATE);require(row['jobs']==0 and row['failed'] and row['last_error']=='source_hash_mismatch','Changed producer RAM rejected')
    require(not g.irq(0x6a) and not g.irq(0xf9),'Hash failure produces no success IRQ')
    return row

def corrupt_lut(g):
    producer(g);configure(g,True);g.expected_unknown=0x15432004;g.write(0x15432004,0x101)
    row=g.state(STATE);require(row['jobs']==0 and row['last_error']=='unobserved_lut','Complete but altered LUT refused')
    return row

def frozen_config(g):
    producer(g);configure(g);g.write(0x15432004,0x101)
    g.expected_unknown=0x15422320;g.write(0x15422320,0x10000000)
    require(g.state('engine1542-state')['blocks'][6]['config']['0x20']==TARGETS[0][0]&0xffffffff,'No in-flight destination retarget')
    return g.state(STATE)

def immutable(g):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as error:require('immutable' in str(error),'Immutable after realize')
    else:raise AssertionError('Live pipeline option changed')
    return g.state(STATE)

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--snapshot',type=Path,required=True)
    a=p.parse_args();snapshot=json.loads(a.snapshot.read_text())
    require(snapshot['first_unknown'].find('addr=0x15432004 ')>=0,'Use native run03 C1 first fault')
    a.output.mkdir(parents=True,exist_ok=False);cases=[]
    plans=[('native-profile',True,positive),('default-off',False,disabled),('rejected-tuples',True,guards),
        ('cancel-before-gate',True,cancel),('changed-source',True,corrupt_source),('changed-lut',True,corrupt_lut),
        ('frozen-config',True,frozen_config),('immutable',True,immutable),('sdr-off',False,sdr),('sdr-on',True,sdr)]
    for name,on,body in plans:
        row=dict(name=name,passed=False);folder=a.output/name;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='z8-hlg-c1-') as tmp:
            g=Lab(a.qemu,Path(tmp),on,snapshot)
            try:
                g.start();row['observed']=body(g);require(not g.qmp.command('query-status')['running'],'No CPU execution')
                row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:
                g.close();row['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
                (folder/'stderr.txt').write_text(g.text());(folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        cases.append(row);print(json.dumps({k:v for k,v in row.items() if k!='observed'}),flush=True)
    equivalent=all(x['passed'] for x in cases[-2:]) and cases[-2].get('observed')==cases[-1].get('observed')
    result=dict(passed=equivalent and all(x['passed'] and x['owned_qemu_stopped'] for x in cases),cases=cases,
        sdr_equivalent=equivalent,firmware_executed=False,native_mmio_fixture=True,
        fixture_sha256=hashlib.sha256(a.snapshot.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest())
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
