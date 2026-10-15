# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual two-bank JPEG MMIO, producer generations and independent IRQ/leases."""
import argparse,hashlib,io,json,tempfile,random,struct
from pathlib import Path
from PIL import Image
from test_z8_capture_stream_rearm import Lab as PipelineLab,C1
from test_z8_producer_journal import configure_d980,next_d980,run151,configure151,A,B,require

OPTION='engine15e2-bank1-jpeg-model'
STATE='engine15e2-bank-state'
BASE=0x15e20000
TARGET=0x850100000
OLD_STATES=('engine15e2-jpeg-state','engine15e2-deferred-input-state','engine15e2-preflight-state','engine15e2-limit-disabled-state','output-buffer-ownership-state')

class Lab(PipelineLab):
    def __init__(self,binary,folder,*,enabled=True,explicit_off=False,preflight=False,noisy=False,omit=()):
        super().__init__(binary,folder)
        pos=self.command.index('-M')+1
        self.command[pos]+=',output-buffer-ownership-model=on,engine15e2-deferred-input-model=on'
        if enabled or explicit_off:self.command[pos]+=','+OPTION+'='+('on' if enabled else 'off')
        if preflight:self.command[pos]+=',engine15e2-limit-disabled-model=on,engine15e2-preflight-model=on,engine15e2-result474-model=on,capture-thumbnail-nv16-model=on,engine1542-ram-input-control-model=on'
        if noisy:
            # The authored frame tag covers all64x16 samples of this small
            # source; keep noise for this capacity-only fixture instead.
            self.command[pos]=','.join(x for x in self.command[pos].split(',') if not x.startswith('sensor-frame-tag-model='))
            self.command[pos]+=',sensor-frame-tag-model=off'
            rng=random.Random(1537)
            payload=struct.pack('<8s6I',b'NZ9RAW1\0',64,32,14,0,0,16383)
            payload+=struct.pack('<2048H',*(rng.randrange(16384) for _ in range(2048)))
            (folder/'pixels.raw').write_bytes(payload)
        if omit:self.command[pos]=','.join(x for x in self.command[pos].split(',') if x.split('=')[0] not in omit)
    def banks(self):return self.state(STATE)['banks']
    def leases(self):return {r['slot']:r for r in self.state('output-buffer-ownership-state')['slots']}
    def program(self,bank,source,target=None,capacity=16,count=False,quant=0x10101010):
        y,uv,w,h,stride=source;target=TARGET+bank*0x20000 if target is None else target;b=BASE+bank*0x1000
        fields={0:0x31,0xc:0x14,0x10:0x3c,0x14:0,0x18:h,0x1c:w,0x24:4,0x28:0x70f,
                0x400:0,0x404:2 if count else 0,0x408:0x10000,0x410:stride,0x414:stride,
                0x418:w,0x41c:h,0x430:y>>4,0x434:uv>>4,0x440:target>>4,
                0x450:0,0x454:0,0x458:0 if count else capacity,0x484:0x0800c000}
        for off,value in fields.items():self.write(b+off,value)
        for off in range(0x100,0x180,4):self.write(b+off,quant)
        return target
    def begin(self,bank):self.write(BASE+bank*0x1000+4,1)
    def gates(self,bank,count=False):
        self.write(BASE+bank*0x1000+0x400,0x4000);self.write(BASE+bank*0x1000+0x404,0x4002 if count else 0x4000)
    def ack(self,bank,*,status=True,core=True):
        if status:self.write(BASE+bank*0x1000+0x480,0x0800c000)
        if core:self.write(BASE+bank*0x1000+0x2c,0x7fffffff)
    def jpeg_bytes(self,bank):
        b=self.banks()[bank];data=self.read_ram(b['output'],b['result_bytes'])
        with Image.open(io.BytesIO(data)) as image:image.load();size=image.size
        require(size==(b['request'][0x418//4],b['request'][0x41c//4]),'Real JPEG geometry')
        return data


def main_source(g,index):
    if index==0:configure_d980(g)
    next_d980(g,index+1);y,uv,_=run151(g,A if index==0 else B,index,initial=index==0)
    return y,uv,64,16,64


def deferred_pipeline(g):
    # Actual151 parent first, then C1 accepts a resize while1518 gate remains off.
    y,uv,w,h,stride=main_source(g,0);src=0x15180000
    g.write(src+0x14,1);g.write(src+0x14,0)
    for off,v in {0x80:h<<16|w,0x88:stride,0x8c:stride,0xb0:256,0xb4:256,0xbc:2,0xc0:1,0xc4:0x20,0xd4:0x20}.items():g.write(src+off,v)
    for off,pa in ((0x100,y),(0x108,uv),(0x110,0xe0000000),(0x118,0xe0000080)):g.pair(src+off,pa)
    for off,v in ((4,0x1002),(0x10,1),(0x38,256),(0x3c,256),(0x4c,64),(0x50,16)):g.write(0x15421100+off,v)
    g.pair(0x15421120,0xe0000000);g.pair(0x15421128,0xe0000080)
    outputs=[]
    for j,(ow,oh,pitch) in enumerate(((64,16,64),(32,16,32),(16,8,32))):
        b=0x15422300+j*0x100;f=0x15423300+j*0x100;py=0x850000000+j*0x10000;pu=py+0x4000
        shift=1 if max(64//ow,16//oh)>=4 else 0
        for off,v in ((0xc,0x41000010),(0x38,pitch),(0x3c,pitch),(0x40,0x22)):g.write(b+off,v)
        g.pair(b+0x20,py);g.pair(b+0x28,pu)
        for off,v in {0:2,4:1,8:0,0xc:0,0x10:(64//ow)<<(14-shift),0x14:(16//oh)<<(14-shift),0x18:64,0x1c:16,0x20:shift,0x24:0,0x28:0,0x2c:ow,0x30:oh}.items():g.write(f+off,v)
        outputs.append((py,pu,ow,oh,pitch))
    for off,v in ((4,0x103),(8,2),(0xc,0),(0x10,0x20)):g.write(C1+off,v)
    for row in range(4):g.pair(C1+0x200+row*0x80,0xe0000000);g.pair(C1+0x208+row*0x80,0xe0000080)
    g.write(C1+4,0x101);require(g.pipeline()['requested'] and g.pipeline()['jobs']==0,'Prepared resize invented pixels')
    return outputs


def run(binary,output,baseline=None):
    output.mkdir(parents=True,exist_ok=False);rows=[]
    def case(name,body,*,reject=None,**options):
        row=dict(name=name,passed=False)
        with tempfile.TemporaryDirectory(prefix='z8-jpeg-banks-') as tmp:
            g=Lab(binary,Path(tmp),**options)
            if reject is not None:g.expected_unknown=reject
            try:
                g.start();row['observed']=body(g)
                require(('Z9_UNKNOWN' in g.text())==(reject is not None),'Unexpected strict fault')
                row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:g.close();(output/(name+'.log')).write_text(g.text())
        rows.append(row);print(json.dumps({k:v for k,v in row.items() if k!='observed'}),flush=True)
    def dual(g):
        first=main_source(g,0);g.program(0,first);g.begin(0)
        require(g.banks()[0]['jobs']==0 and g.leases()[6]['held'],'START requires gates, owns only submitted capacity')
        second=main_source(g,1);g.program(1,second);g.begin(1)
        require([b['frame'] for b in g.banks()]==[1,2],'Latest global frame replaced bound parent')
        g.gates(1);g.gates(0)
        for bank in (0,1):g.jpeg_bytes(bank)
        require(g.irq(121) and g.irq(122),'Independent IRQ121/122')
        before=g.banks()[1];g.ack(0,status=True,core=False)
        require(g.leases()[6]['held'] and g.leases()[7]['held'],'Raw ACK released output before core')
        require(not g.irq(121) and g.irq(122) and g.banks()[1]==before,'Cross-bank IRQ/state contamination')
        g.write(BASE+0x484,0);require(g.leases()[6]['held'],'Mask retired output')
        g.ack(0,status=False,core=True);require(not g.leases()[6]['held'] and g.leases()[7]['held'],'Core retirement released another slot')
        g.ack(1,status=False,core=True);require(g.leases()[7]['held'],'Core alone retired output')
        g.ack(1,status=True,core=False);require(not g.leases()[7]['held'],'Both completions did not retire')
        return g.state(STATE)
    case('two-live-generations-irq-and-leases',dual)
    def off(g):
        source=main_source(g,0);g.program(1,source);g.begin(1)
        require(6 not in g.leases() and 7 not in g.leases(),'Off exposed JPEG slots')
    case('off-bank1-start-unknown',off,enabled=False,reject=BASE+0x1004)
    def overlap(g):
        g.prepare();g.program(0,g.outputs[0]);g.begin(0);g.gates(0);before=g.jpeg_bytes(0)
        owner=g.state('output-buffer-ownership-state');g.program(1,g.outputs[1],TARGET+512);g.begin(1)
        require(g.state('output-buffer-ownership-state')==owner and g.jpeg_bytes(0)==before,'Failed reserve changed another job')
        require(g.banks()[1]['requests']==0 and not g.irq(122),'Rejected overlap completed')
    case('cross-bank-output-overlap',overlap,reject=BASE+0x1004)
    def replay(g):
        g.prepare();g.program(0,g.outputs[0]);g.begin(0);g.gates(0);g.ack(0)
        g.program(1,g.outputs[0]);g.begin(1);require(g.banks()[1]['failure']=='source_role_consumed_or_claimed','Replay not identified')
    case('shared-consumption-no-cross-bank-replay',replay,reject=BASE+0x1004)
    def tamper(g):
        source=main_source(g,0);target=g.program(1,source);g.write_ram(target,b'\x75'*8192);g.begin(1)
        g.write_ram(source[0],b'\xff');g.gates(1)
        require(g.read_ram(target,8192)==b'\x75'*8192 and not g.banks()[1]['result_valid'] and not g.irq(122),'Tampered source produced JPEG')
        require(not g.leases()[7]['held'] and g.banks()[1]['failed'],'Failure did not cancel only its lease')
    case('actual-ram-sha-tamper',tamper,reject=BASE+0x1404)
    def capacity(g):
        # An optimized tiny black JPEG can fit512B. Measure the same actual
        # noisy NV16/quantizer role first; prove the finite reservation is short.
        g.prepare();source=g.outputs[1]
        g.program(0,source,count=True,quant=0x01010101);g.begin(0);g.gates(0,count=True)
        measured=g.banks()[0]['result_bytes'];require(measured>512,'Capacity-negative fixture needs a stream larger than one hardware unit')
        g.ack(0);units=(measured-1)//512
        require(0<units*512<measured,'Negative reservation is not strictly short')
        target=g.program(1,source,capacity=units,quant=0x01010101)
        sentinel=b'\x75'*(measured+512);g.write_ram(target,sentinel);g.begin(1);g.gates(1)
        require(g.read_ram(target,len(sentinel))==sentinel and not g.irq(122) and not g.banks()[1]['result_valid'],'Small capacity partially committed')
        require(not g.leases()[7]['held'] and g.banks()[1]['failed'],'Capacity failure did not cancel its output lease')
    case('capacity-failure-atomic',capacity,preflight=True,noisy=True,reject=BASE+0x1404)
    def deferred(g):
        outputs=deferred_pipeline(g)
        for b,j in ((0,1),(1,0)):g.program(b,outputs[j]);g.begin(b);g.gates(b)
        require(all(b['requested'] and b['jobs']==0 for b in g.banks()),'Deferred intent invented completion')
        require(not g.irq(121) and not g.irq(122),'Missing producer IRQ')
        g.write(0x15180030,1)
        require(all(b['jobs']==1 and b['frame']==1 for b in g.banks()),'Producer notification missed a bank')
        require([b['producer_index'] for b in g.banks()]==[1,0],'Deferred identity rebound to another output')
        for b in (0,1):g.jpeg_bytes(b);g.ack(b)
        return g.state(STATE)
    case('two-deferred-tokens-published-once',deferred)
    def cancelled_intent(g):
        outputs=deferred_pipeline(g);g.program(1,outputs[0]);g.begin(1)
        g.write(0x15180030,0)
        b=g.banks()[1]
        require(b['requested'] and b['failed'] and b['failure']=='source_intent_invalidated','Cancelled producer silently rebound or waited without diagnosis')
        require(not g.leases()[7]['held'] and b['jobs']==0 and not g.irq(122),'Cancellation fabricated completion or leaked lease')
    case('deferred-producer-cancel-before-gates',cancelled_intent)
    def no_intent(g):
        source=(0x850000000,0x850004000,64,16,64);g.program(1,source);g.begin(1)
        require(g.banks()[1]['failure']=='no_source_or_intent' and g.banks()[1]['requests']==0,'Address-only waiter accepted')
    case('no-address-only-deferred',no_intent,reject=BASE+0x1004)
    def source_lease(g):
        source=main_source(g,0);before=g.read_ram(source[0],1024);g.program(1,source);g.begin(1)
        next_d980(g,2);configure151(g,B,1);g.pair(0x15100980,source[0]);g.pair(0x151009c0,source[1]);g.write(0x15100008,1)
        require(g.read_ram(source[0],1024)==before and g.banks()[1]['jobs']==0,'Held read generation overwritten by151')
    case('source-read-lease-blocks-aba-writer',source_lease,reject=0x15100008)
    def aba(g):
        first=main_source(g,0);g.program(0,first);g.begin(0);g.gates(0);old=g.banks()[0]['generation'];g.ack(0)
        next_d980(g,2);configure151(g,B,1);g.pair(0x15100980,first[0]);g.pair(0x151009c0,first[1]);g.write(0x15100008,1);g.write(0x15100044,2)
        g.program(1,first);g.begin(1);g.gates(1)
        require(g.banks()[1]['generation']!=old and g.banks()[1]['frame']==2,'Same-address producer generation collapsed')
        g.jpeg_bytes(1);g.ack(1)
    case('same-address-new-generation',aba)
    def count(g):
        g.prepare();target=g.program(0,g.outputs[1],count=True);g.write_ram(target,b'\x66'*8192);g.begin(0);g.gates(0,count=True)
        require(g.read_ram(target,8192)==b'\x66'*8192 and not g.leases()[6]['held'],'Count wrote output or reserved allocation')
        require(g.tx(f'readl {BASE+0x474:#x}')==g.banks()[0]['result_bytes'],'Count length not actual codec bytes');g.ack(0)
        g.program(1,g.outputs[1]);g.begin(1);g.gates(1);g.jpeg_bytes(1);g.ack(1)
        require(g.banks()[0]['count_jobs']==1 and g.banks()[1]['count_jobs']==0,'Bank0 counter became aggregate')
    case('count-then-cross-bank-commit',count,preflight=True)
    def rearm(g):
        g.prepare();g.acknowledge();g.jpeg(0);g.jpeg(1)
        g.program(1,g.outputs[2]);g.begin(1);g.gates(1);g.ack(1,status=True,core=False)
        before=g.block();g.write(C1+4,0x103);require(g.block()==before and g.leases()[7]['held'],'C1 rearmed with bank1 core owned')
    case('c1-rearm-sees-bank1-core',rearm,reject=C1+4)
    for name,action,offset in (
        ('gate-order',lambda g:g.write(BASE+0x1404,0x4000),0x1404),
        ('busy-config',lambda g:g.write(BASE+0x1418,64),0x1418),
        ('duplicate-start',lambda g:g.begin(1),0x1004)):
        def invalid(g,action=action):source=main_source(g,0);g.program(1,source);g.begin(1);action(g);require(g.banks()[1]['jobs']==0,'Invalid protocol completed')
        case(name,invalid,reject=BASE+offset)
    for name,offset,value in (('wrong-w1c',0x1480,1),('wrong-core-complement',0x102c,0xffffffff)):
        def wrong(g,offset=offset,value=value):
            source=main_source(g,0);g.program(1,source);g.begin(1);g.gates(1);before=g.state('output-buffer-ownership-state');g.write(BASE+offset,value)
            require(g.state('output-buffer-ownership-state')==before,'Wrong ACK retired lease')
        case(name,wrong,reject=BASE+offset)
    for offset,size in ((0x1500,4),(0x1800,4),(0x1030,4),(0x1410,8)):
        def gap(g,off=offset,n=size):g.tx(f'{"writeq" if n==8 else "writel"} {BASE+off:#x} 0')
        case('unknown-'+hex(offset)+'-'+str(size),gap,reject=BASE+offset)
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as e:require('creation' in str(e),'Wrong immutability failure')
        else:raise AssertionError('Changed live bank1 option')
    case('immutable',immutable)
    for omit in ('engine15e2-jpeg-model','engine15e2-config-model','output-buffer-ownership-model'):
        with tempfile.TemporaryDirectory(prefix='jpeg-dependency-') as tmp:
            g=Lab(binary,Path(tmp),omit=(omit,));passed=False
            try:g.start()
            except (RuntimeError,OSError):
                # A failed realize can close QMP before connect. Still require
                # the exact intended model diagnostic and abnormal process exit.
                code=g.proc.wait(timeout=5) if g.proc is not None else None
                expected=('engine15e2-deferred-input-model requires original JPEG model' if omit=='engine15e2-jpeg-model'
                          else OPTION+' requires JPEG/config and output ownership')
                passed=code is not None and code!=0 and expected in g.text()
            finally:g.close();(output/('dependency-'+omit+'.log')).write_text(g.text())
            rows.append(dict(name='dependency-'+omit,passed=passed))
    def legacy(binary,explicit):
        with tempfile.TemporaryDirectory(prefix='jpeg-off-') as tmp:
            g=Lab(binary,Path(tmp),enabled=False,explicit_off=explicit)
            try:
                g.start();g.prepare();g.jpeg(0)
                result=[g.qmp.command('qom-get',path='/machine',property=k) for k in OLD_STATES]
                require('Z9_UNKNOWN' not in g.text(),'Off baseline strict failure');return result
            finally:g.close()
    a=legacy(binary,False);b=legacy(binary,True)
    rows.append(dict(name='implicit-explicit-off-exact-json',passed=a==b))
    if baseline:rows.append(dict(name='i3-bank0-exact-json',passed=a==legacy(baseline,False)))
    result=dict(passed=all(r['passed'] for r in rows),cases=rows,qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                firmware_execution=False,physical_codec_verified=False,baseline_compared=bool(baseline))
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result['passed']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline-qemu',type=Path)
    a=p.parse_args();raise SystemExit(not run(a.qemu,a.output,a.baseline_qemu))
