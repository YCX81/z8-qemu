# SPDX-License-Identifier: GPL-2.0-or-later
"""Real RAM A2/JPEG checks, native MMIO snapshots, no firmware execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tempfile
from PIL import Image
from test_z8_hlg_pipeline import Lab as PipelineLab, producer, configure, TARGETS
from test_z8_hlg_capture import sdr as source_sdr
from test_z8_producer_journal import require

OPTION='z8-capture-hlg-thumbnail-model';STATE='z8-capture-hlg-thumbnail-state'
TY,TUV=0x86c000540,0x86c010540

class Lab(PipelineLab):
    def __init__(self,binary,folder,enabled,c1,snapshot):
        super().__init__(binary,folder,True,c1);self.thumb_snapshot=snapshot
        props=['engine1542-ram-input-control-model','capture-thumbnail-nv16-model',
            'engine15e2-config-model','engine15e2-jpeg-model','engine15e2-limit-disabled-model','engine15e2-preflight-model']
        self.command[self.command.index('-M')+1]+=','+','.join([x+'=on' for x in props]+[OPTION+'='+('on' if enabled else 'off')])

def c1(g):
    producer(g);configure(g);g.write(0x15432004,0x101);g.write(0x15180030,1)
    require(g.state('z8-capture-hlg-pipeline-state')['jobs']==1,'C1 actual producer')
    g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
    require(not g.state('z8-capture-hlg-pipeline-state')['owned'],'C1 retired before A2')

def registers(g,index,values):
    base={2:0x15421200,7:0x15422400,19:0x15423400}[index]
    offsets=([0,4,8,0xc,0x10,0x14,0x18,0x38,0x3c,0x40,0x48,0x4c,0x50,0x54] if index==2 else
        [0,0xc,0x10,0x14,0x18,0x38,0x3c,0x40,0x48,0x60,0x6c,0x70] if index==7 else
        [0,4,8,0xc,0x10,0x14,0x18,0x1c,0x20,0x24,0x28,0x2c,0x30,0x34,0x38])
    for off in offsets:g.write(base+off,values.get(off,0))

def configure_thumb(g):
    blocks=g.thumb_snapshot['device_states']['engine1542-state']['blocks']
    for index in (2,7,19):registers(g,index,{int(k,16):v for k,v in blocks[index]['config'].items()})
    for off,pa in ((0x20,TARGETS[1][0]),(0x28,TARGETS[1][1])):g.pair(0x15421200+off,pa)
    for off,pa in ((0x20,TY),(0x28,TUV)):g.pair(0x15422400+off,pa)

def expected(g):
    iy=g.read_ram(TARGETS[1][0],448*212);iu=g.read_ram(TARGETS[1][1],448*106)
    def sample(buf,x,y):
        at=y*448+(x//3)*4
        return (int.from_bytes(buf[at:at+4],'little')>>((x%3)*10))&1023
    out=[bytearray(192*106),bytearray([128])*(192*106)]
    for y in range(106):
        for x in range(160):
            out[0][y*192+x]=min(255,(sample(iy,2*x,2*y)+2)//4)
            out[1][y*192+x]=min(255,(sample(iu,(x//2)*4+x%2,y)+2)//4)
    return out

def jpeg_config(g,height,count=False):
    head=(height-106)//2*192
    c={0:0x31,0xc:0x14,0x10:0x3c,0x14:0,0x24:4,0x28:0x70f,
        0x18:height,0x1c:160,0x400:0,0x404:2 if count else 0,0x408:0x10000,
        0x410:192,0x414:192,0x418:160,0x41c:height,0x430:(TY-head)>>4,
        0x434:(TUV-head)>>4,0x440:0x86d000000>>4,0x450:0,0x454:0,0x458:0 if count else 2048,0x484:0x0800c000}
    for off,value in c.items():g.write(0x15e20000+off,value)
    for off in range(0x100,0x180,4):g.write(0x15e20000+off,0x10101010)

def jpeg(g,height,count=False):
    jpeg_config(g,height,count);g.write(0x15e20004,1);g.write(0x15e20400,0x4000);g.write(0x15e20404,0x4002 if count else 0x4000)
    size=g.tx('readl 0x15e20020');require(size>0,'Actual encoded byte count')
    if not count:
        data=g.read_ram(0x86d000000,size)
        require(data[:2]==b'\xff\xd8' and data[-2:]==b'\xff\xd9','Complete JPEG')
        im=Image.open(io.BytesIO(data));im.load();require(im.size==(160,height),'Independent full JPEG decode')
    g.write(0x15e20480,0x0800c000);g.write(0x15e2002c,0x7fffffff)
    return size

def positive(g):
    c1(g);configure_thumb(g);out=expected(g)
    g.write(0x15421204,0x1001);s=g.state(STATE)
    require(s['jobs']==1 and s['valid'] and g.irq(0x6a),'Real A2 completion')
    for k,pa in enumerate((TY,TUV)):
        data=g.read_ram(pa,192*106);require(data==out[k],'All output samples/padding match independent conversion')
        require(hashlib.sha256(data).hexdigest()==s['uv_sha256' if k else 'y_sha256'],'Actual RAM hash')
    require(g.state('capture-thumbnail-nv16-state')['jobs']==0,'Separate HLG receipt, not SDR producer')
    g.write(0x1542121c,0);require(g.irq(0x6a),'W1C0 retains IRQ')
    g.write(0x1542121c,0x10);require(g.irq(0x6a),'First boundary remains')
    g.write(0x1542121c,1);require(not g.irq(0x6a),'Both boundaries ACK')
    # CPU-border fixture: model must never synthesize these bytes.
    for pa,value in ((TY,0),(TUV,128)):
        g.write_ram(pa-7*192,bytes([value])*(7*192));g.write_ram(pa+106*192,bytes([value])*(7*192))
    jpeg(g,120,True);jpeg(g,120)
    require(g.state(STATE)['jpeg_commits']==1,'Owner4 commit tracked independently')
    g.write_ram(TY-7*192,b'\x01');jpeg_config(g,120)
    before=g.state('engine15e2-jpeg-state')['jobs'];g.expected_unknown=0x15e20004;g.write(0x15e20004,1)
    require(g.state('engine15e2-jpeg-state')['jobs']==before,'Bad border cannot borrow old SDR ownership')
    return s

def rejected(g,mode):
    c1(g);configure_thumb(g)
    if mode=='source':
        old=g.read_ram(TARGETS[1][0],1);g.write_ram(TARGETS[1][0],bytes([old[0]^0x80]))
    if mode=='profile':g.write(0x15423420,0)
    if mode=='overlap':g.pair(0x15422420,TARGETS[1][0])
    for pa in (TY,TUV):g.write_ram(pa,b'\xa5'*64)
    g.expected_unknown=0x15421204;g.write(0x15421204,0x1001)
    require(g.state(STATE)['jobs']==0 and not g.irq(0x6a),'Invalid/default-off has no completion')
    for pa in (TY,TUV):require(g.read_ram(pa,64)==b'\xa5'*64,'Rejected conversion preserves output')
    return g.state(STATE)

def sdr_thumbnail(g):
    source_sdr(g);g.write(0x15100044,2)
    g.write(0x15180014,1)
    for off,v in {8:0x39f00000,0x80:(16<<16)|64,0x88:64,0x8c:64,0xb0:256,0xb4:256,0xbc:2,0xc0:1,0xc4:0x20,0xd4:0x20}.items():g.write(0x15180000+off,v)
    for off,pa in ((0x100,0x840100000),(0x108,0x840102000),(0x110,0xe0000000),(0x118,0xe0000080)):g.pair(0x15180000+off,pa)
    for off,v in {0x10:1,0x18:0x11111,0x38:256,0x3c:256,0x4c:64,0x50:16}.items():g.write(0x15421100+off,v)
    g.pair(0x15421120,0xe0000000);g.pair(0x15421128,0xe0000080)
    for j,(w,h,shift) in enumerate(((64,16,0),(32,8,1),(16,4,2))):
        base=0x15422300+j*0x100;fbase=0x15423300+j*0x100;pitch=max(32,w)
        for off,v in {0xc:0x41000010,0x10:1,0x38:pitch,0x3c:pitch,0x40:0x22}.items():g.write(base+off,v)
        g.pair(base+0x20,TARGETS[j][0]);g.pair(base+0x28,TARGETS[j][1])
        for off,v in {0:2,4:1,0x10:16384,0x14:16384,0x18:64,0x1c:16,0x20:shift,0x2c:w,0x30:h}.items():g.write(fbase+off,v)
    for off,v in ((4,0x103),(8,2),(0xc,0),(0x10,0x20)):g.write(0x15432000+off,v)
    for row in range(4):
        g.pair(0x15432200+row*0x80,0xe0000000 if row==0 else 0)
        g.pair(0x15432208+row*0x80,0xe0000080 if row==0 else 0)
    g.write(0x15432004,0x101);g.write(0x15180030,1)
    require(g.state('capture-nv16-pipeline-state')['jobs']==1,'Real SDR C1 producer')
    g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
    registers(g,2,{4:0x1002,8:0x10000000,0xc:0x71000010,0x10:1,0x18:0x11111,0x38:32,0x3c:32,0x40:0xf000022,0x48:0x11,0x4c:32,0x50:8})
    registers(g,7,{0xc:0x41000010,0x10:1,0x38:32,0x3c:32,0x40:0x22})
    registers(g,19,{0:2,4:1,0x10:16384,0x14:16384,0x18:32,0x1c:8,0x20:1,0x2c:16,0x30:4})
    for base,ys,us in ((0x15421200,*TARGETS[1]),(0x15422400,TY,TUV)):
        g.pair(base+0x20,ys);g.pair(base+0x28,us)
    g.write(0x15421204,0x1001)
    require(g.state(STATE)['jobs']==0 and g.state('capture-thumbnail-nv16-state')['jobs']==1,'SDR uses unchanged thumbnail path')
    return [hashlib.sha256(g.read_ram(pa,128)).hexdigest() for pa in (TY,TUV)]

def immutable(g):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as error:require('immutable' in str(error),'Immutable option')
    else:raise AssertionError('Live thumbnail option changed')
    return g.state(STATE)

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--c1-snapshot',type=Path,required=True);a=p.parse_args()
    snapshot=json.loads(a.snapshot.read_text());c1snap=json.loads(a.c1_snapshot.read_text())
    require('addr=0x15421204 ' in snapshot['first_unknown'],'Use run04 A2 snapshot')
    a.output.mkdir(parents=True,exist_ok=False);cases=[]
    plans=[('a2-jpeg',True,positive),('default-off',False,lambda g:rejected(g,'off')),
        ('bad-profile',True,lambda g:rejected(g,'profile')),('overlap',True,lambda g:rejected(g,'overlap')),
        ('changed-source',True,lambda g:rejected(g,'source')),('immutable',True,immutable),
        ('sdr-off',False,sdr_thumbnail),('sdr-on',True,sdr_thumbnail)]
    for name,on,body in plans:
        row=dict(name=name,passed=False);folder=a.output/name;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='z8-hlg-thumb-') as tmp:
            g=Lab(a.qemu,Path(tmp),on,c1snap,snapshot)
            try:g.start();row['observed']=body(g);require(not g.qmp.command('query-status')['running'],'Stopped CPU');row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:
                g.close();row['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
                (folder/'stderr.txt').write_text(g.text());(folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        cases.append(row);print(json.dumps({k:v for k,v in row.items() if k!='observed'}),flush=True)
    eq=all(x['passed'] for x in cases[-2:]) and cases[-2].get('observed')==cases[-1].get('observed')
    result=dict(passed=eq and all(x['passed'] and x['owned_qemu_stopped'] for x in cases),cases=cases,
        sdr_equivalent=eq,native_mmio_fixture=True,firmware_executed=False,
        snapshot_sha256=hashlib.sha256(a.snapshot.read_bytes()).hexdigest(),c1_snapshot_sha256=hashlib.sha256(a.c1_snapshot.read_bytes()).hexdigest(),
        binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest())
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n');raise SystemExit(0 if result['passed'] else 1)
if __name__=='__main__':main()
