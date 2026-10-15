# SPDX-License-Identifier: GPL-2.0-or-later
"""C1 reprogramming after actual NV16/JPEG consumption, with no firmware."""
import argparse,io,json,tempfile
from pathlib import Path
from PIL import Image
from check_z8_machine import sha256
from test_z8_producer_journal import Lab as BaseLab,configure_d980,next_d980,run151,A,B
from test_z8_d980_internal import require

OPTION='capture-stream-rearm-model'
STATE='capture-stream-rearm-state'
C1=0x15432000

class Lab(BaseLab):
    def __init__(self,binary,folder,*,enabled=True):
        super().__init__(binary,folder,enabled=False)
        opts=['engine1543-capture-control-model','capture-nv16-pipeline-model',
              'engine15e2-config-model','engine15e2-jpeg-model']
        if enabled:opts.append(OPTION)
        self.command[self.command.index('-M')+1]+=','+','.join(x+'=on' for x in opts)
    def pipeline(self):return self.state('capture-nv16-pipeline-state')
    def block(self):return self.state('engine1543-control-state')['blocks'][1]
    def prepare(self,index=0,*,hold=True):
        if index==0:configure_d980(self)
        next_d980(self,index+1)
        y,uv,_=run151(self,A if index==0 else B,index,initial=index==0)
        actual_y=self.read_ram(y,1024);actual_uv=self.read_ram(uv,1024)
        src=0x15180000
        self.write(src+0x14,1);self.write(src+0x14,0)
        fields={0x80:16<<16|64,0x88:64,0x8c:64,0xb0:256,0xb4:256,
                0xbc:2,0xc0:1,0xc4:0x20,0xd4:0x20}
        for off,value in fields.items():self.write(src+off,value)
        for off,pa in ((0x100,y),(0x108,uv),(0x110,0xe0000000),(0x118,0xe0000080)):
            self.pair(src+off,pa)
        a=0x15421100
        for off,value in ((4,0x1002),(0x10,1),(0x38,256),(0x3c,256),(0x4c,64),(0x50,16)):
            self.write(a+off,value)
        self.pair(a+0x20,0xe0000000);self.pair(a+0x28,0xe0000080)
        self.outputs=[]
        for j,(w,h,stride) in enumerate(((64,16,64),(32,16,32),(16,8,32))):
            b=0x15422300+j*0x100;f=0x15423300+j*0x100
            py=0x850000000+j*0x10000;pu=py+0x4000
            shift=1 if max(64//w,16//h)>=4 else 0
            for off,value in ((0xc,0x41000010),(0x38,stride),(0x3c,stride),(0x40,0x22)):
                self.write(b+off,value)
            self.pair(b+0x20,py);self.pair(b+0x28,pu)
            for off,value in {0:2,4:1,8:0,0xc:0,0x10:(64//w)<<(14-shift),0x14:(16//h)<<(14-shift),
                 0x18:64,0x1c:16,0x20:shift,0x24:0,0x28:0,0x2c:w,0x30:h}.items():self.write(f+off,value)
            self.outputs.append((py,pu,w,h,stride))
        for off,value in ((4,0x103),(8,2),(0xc,0),(0x10,0x20)):self.write(C1+off,value)
        for row in range(4):
            self.pair(C1+0x200+row*0x80,0xe0000000)
            self.pair(C1+0x208+row*0x80,0xe0000080)
        self.write(src+0x30,1);self.write(C1+4,0x101)
        state=self.pipeline();require(state['jobs']==index+1,'No actual NV16 generation')
        for py,pu,w,h,stride in self.outputs:
            ey=bytearray(stride*h);eu=bytearray(b'\x80'*(stride*h))
            for row in range(h):
                source_row=row*(16//h)*64
                for col in range(w):ey[row*stride+col]=actual_y[source_row+col*(64//w)]
                for col in range(0,w,2):
                    at=source_row+(col*(64//w)&~1)
                    eu[row*stride+col:row*stride+col+2]=actual_uv[at:at+2]
            require(self.read_ram(py,stride*h)==bytes(ey),'Actual Y output differs from source samples')
            require(self.read_ram(pu,stride*h)==bytes(eu),'Actual UV output differs from source samples')
        if hold:self.write(C1+4,0x102)
        return state
    def acknowledge(self,omit=None):
        for label,address,value in (('input',0x1542111c,0x11),('receiver',C1+0x14,0x20),('source',0x1518000c,1<<20)):
            if label!=omit:self.write(address,value)
    def jpeg(self,j,*,ack='both'):
        y,uv,w,h,stride=self.outputs[j];target=0x850100000+j*0x10000
        fields={0:0x31,0xc:0x14,0x10:0x3c,0x14:0,0x18:h,0x1c:w,0x24:4,0x28:0x70f,
            0x400:0,0x404:0,0x408:0x10000,0x410:stride,0x414:stride,0x418:w,0x41c:h,
            0x430:y>>4,0x434:uv>>4,0x440:target>>4,0x450:0,0x454:0,0x458:16,0x484:0x0800c000}
        for off,value in fields.items():self.write(0x15e20000+off,value)
        for off in range(0x100,0x180,4):self.write(0x15e20000+off,0x10101010)
        self.write(0x15e20004,1);self.write(0x15e20400,0x4000);self.write(0x15e20404,0x4000)
        state=self.state('engine15e2-jpeg-state');data=self.read_ram(target,state['last_bytes'])
        with Image.open(io.BytesIO(data)) as image:
            image.load();require(image.size==(w,h),'Real JPEG dimensions differ')
        if ack in ('both','status'):self.write(0x15e20480,0x0800c000)
        if ack in ('both','core'):self.write(0x15e2002c,0x7fffffff)

def case(binary,name,body,*,enabled=True,reject=False):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-c1-rearm-') as tmp:
        g=Lab(binary,Path(tmp),enabled=enabled)
        if reject:g.expected_unknown=C1+4
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==reject,'Unexpected strict boundary')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row

def ready(g,*,hold=True):
    g.prepare(hold=hold);g.acknowledge()
    for j in range(3):g.jpeg(j)
    require([x['jpeg_consumed_job'] for x in g.pipeline()['outputs']]==[1,1,1],'Three real JPEG consumers required')

def accepted(g):
    ready(g);before=g.pipeline();g.write(C1+4,0x103)
    require(not g.block()['active'] and not g.block()['held'],'Held C1 did not rearm')
    require(g.pipeline()==before,'Rearm fabricated pixels, ownership release or completion')
    return g.block()

def run(binary,red=False):
    if red:return [case(binary,'old active-held C1 cannot rearm',accepted,enabled=False)]
    rows=[case(binary,'three actual JPEG consumers allow reprogramming',accepted)]
    def two(g):
        accepted(g);g.prepare(1);g.acknowledge()
        for j in range(3):g.jpeg(j)
        require(g.pipeline()['jobs']==2,'No second actual generation')
        g.write(C1+4,0x103);require(g.state(STATE)['rearms']==2,'Rearm generations collapsed')
    rows.append(case(binary,'two actual NV16 and JPEG generations',two))
    def incomplete(g,missing=0,pending=None):
        g.prepare();g.acknowledge(omit=pending)
        for j in range(3):
            if j!=missing:g.jpeg(j)
        before=g.block();pipeline=g.pipeline();g.write(C1+4,0x103)
        require(g.block()==before and g.pipeline()==pipeline,'Rejected rearm changed held owner')
    for missing in range(3):rows.append(case(binary,'missing JPEG '+str(missing),lambda g,j=missing:incomplete(g,j),reject=True))
    for pending in ('source','receiver','input'):
        rows.append(case(binary,'pending '+pending,lambda g,p=pending:incomplete(g,-1,p),reject=True))
    def jpeg_pending(g,ack):
        g.prepare();g.acknowledge();g.jpeg(0);g.jpeg(1);g.jpeg(2,ack=ack)
        before=g.block();pending=g.state('engine15e2-jpeg-state');g.write(C1+4,0x103)
        require(g.block()==before and g.state('engine15e2-jpeg-state')==pending,'Pending JPEG owner released')
    for ack in ('status','core'):
        rows.append(case(binary,'JPEG '+ack+' ACK alone cannot rearm',lambda g,a=ack:jpeg_pending(g,a),reject=True))
    def unheld(g):
        ready(g,hold=False);before=g.block();g.write(C1+4,0x103)
        require(g.block()==before,'Active unheld receiver rearmed')
    rows.append(case(binary,'active receiver must be held',unheld,reject=True))
    def wrong_control(g):
        ready(g);before=g.block();g.write(C1+4,3)
        require(g.block()==before,'Unobserved mode3 control accepted')
    rows.append(case(binary,'only observed 103 control',wrong_control,reject=True))
    def video_header(g):
        ready(g);g.write(C1+8,0x302);before=g.block();pipeline=g.pipeline()
        g.write(C1+4,0x103)
        require(g.block()==before and g.pipeline()==pipeline,'Video header admitted as finite capture')
    rows.append(case(binary,'video header cannot use finite capture rearm',video_header,reject=True))
    def off(g):
        ready(g);before=g.block();g.write(C1+4,0x103);require(g.block()==before,'Default-off behavior changed')
    rows.append(case(binary,'default off stays strict',off,enabled=False,reject=True))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as e:require('immutable' in str(e),'Wrong setter rejection')
        else:raise AssertionError('Live rearm toggle accepted')
    rows.append(case(binary,'immutable option',immutable))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline)
    report=dict(passed=all(r['passed'] for r in rows),qemu_sha256=sha256(a.qemu),cases=rows,
                native_firmware_verified=False,raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=report['passed'],cases=len(rows),failures=[dict(name=r['name'],error=r.get('error')) for r in rows if not r['passed']])))
    raise SystemExit(not report['passed'])
