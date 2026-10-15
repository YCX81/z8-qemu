# SPDX-License-Identifier: GPL-2.0-or-later
"""Native video START order: C1 101 -> ISP0 START -> 152 -> 1518 gates -> 1530/152c START -> 150c ff00."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from test_z8_video_data import configure,PACKET,CAPACITY,OPTION,STATE
# i2-video-a-01/result.json at the rejected START (VM stopped by Z9_UNKNOWN)
A1   ={0x4:0x202,0x8:0x0f010000,0xc:0x71200410,0x10:1,0x14:0x100,0x18:0x11111,
       0x38:0x1700,0x3c:0x1700,0x40:0x0f000022,0x4c:0x780,0x50:0x438}
C1_ROWS=[(0xe0000000,0xe0000b80),(0xe000e100,0xe000ec80),(0xe0000000,0xe0000b80),(0xe000e100,0xe000ec80)]
SRC0 ={0x8:0x39f00000,0xb0:0x1680,0xb4:0x1680,0xbc:1,0xc0:1,0xc4:0x22,0xd4:0x22}
# Derived from the profile and C1/A1 snapshot (mode words equal O's native 4K latch):
ISP0 ={0x1c:0x3f,0x260:0x1fff,0x264:0x3ff,0x200:1,0x204:1,0x214:1,0x218:1,0x21c:5,0x220:2,
       0x400:0x04380780,0x450:0x04380780,0x430:0x1680,0x434:0x1680,0x480:0x1700,0x484:0x1700,
       0x740:0xe0000000,0x744:0,0x9c0:0xe0000b80,0x9c4:0}
PROPS=('image-bank-model','engine150c-control-model','engine150c-stop-model','config150d-model',
       'engine150d-control-model','statistics-model','engine1543-control-model','engine151-control-model',
       'engine152-control-model','raw-pipeline-model','z8-video-interface-model','event1508-front-model','event1508-timing-model',
       'event1508-channel-model','event1508-aux-model','event1508-global1-model')

def program(m,*,isp=ISP0,a1=A1,src=SRC0,c1_header=(0x302,0,0x29),c1_start=True):
    w=lambda a,v:m.q(f'writel {a:#x} {v:#x}')
    w(0x0d154340,0x400);w(0x0d154240,0x400);w(0x15200010,1)
    for off,v in a1.items():w(0x15421100+off,v)
    w(0x151c0014,1);w(0x151c0014,0)
    w(0x15180014,1);w(0x15180014,0)                        # 1518[0] reset pulse, gate stays 0
    for off,v in src.items():w(0x15180000+off,v)
    b=0x15100000
    for off,v in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff)):w(b+off,v)
    for off in range(0x268,0x320,8):w(b+off,0x1000);w(b+off+4,0xa)
    for off,v in isp.items():w(b+off,v)
    m.write(4,0x103,1)                                      # native mode3 while inactive (i2 L19082)
    for off,v in zip((8,12,16),c1_header):m.write(off,v,1)
    for row,(y,uv) in enumerate(C1_ROWS):
        for k,v in enumerate((y,0,uv,0)):m.write(0x200+row*128+k*4,v,1)
    if c1_start:m.write(4,0x101,1)                          # i2 L19494 / O L19331

# Stage the existing O fixture's MMIO values while changing only submission order.
# ISP0 1080p fields above are inferred from the documented A1/C1 tuple; the
# new native START log must confirm them. This is not a recorded full bus trace.
def front_program(m, wide=False):
    commands=[]
    class Recorder:
        def q(self,command):commands.append(command)
    if wide:
        configure(Recorder(),width=8280,height=4688,capacity=0x414800)
    else:
        configure(Recorder())
    controls={0x152c0000:[],0x15300000:[]}
    for command in commands:
        _,address,value=command.split();address=int(address,0)
        if address in (0x150c0060,0x150c0064,0x150c0008):continue
        owner=next((b for b in controls if address-b in (8,0x14,0x404)),None)
        if owner is None:m.q(command)
        else:controls[owner].append(command)
    # configure()'s final decoder hold exists only to inspect packet RAM; native
    # worker releases that gate before START. Replay the four original controls.
    require(len(controls[0x15300000])==5,'O fixture decoder sequence changed')
    controls[0x15300000].pop()
    # +60 submits one configuration unit at a time (same prerequisites as
    # test_z8_d980_internal.Lab.configure). It is not the aggregate latch.
    for bit in (0x100,0x200,0x400,0x800,0x1000,0x2000,0x4000,0x8000):
        m.q(f'writel 0x150c0060 {bit:#x}')
    return controls


def front_start(m,controls,ports=(0x15300000,0x152c0000)):
    for base in ports:
        m.q(f'writel {base+8:#x} 0')
        for command in controls[base]:m.q(command)


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);rows=[]
    with tempfile.TemporaryDirectory(prefix='z8-native-order-') as t:
        root=Path(t);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        props=tuple(n+'=on' for n in PROPS)+('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000',OPTION+'=on')
        def state(m,key=STATE):return json.loads(m.qmp.command('qom-get',path='/machine',property=key))
        def advance(m):m.qmp.command('cont');m.q('clock_step 70000000');m.qmp.command('stop')
        def check_no_data(m):
            v=state(m);require(v['packets']==v['decoded']==v['output_frames']==0,'Admission invented data before source activation')
        def case(name,body,*,extra=(),reject=None):
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props+extra) as m:
                m.observe_irq()
                try:
                    body(m);log=m.log_path.read_text()
                    require(('Z9_UNKNOWN' in log)==bool(reject),name+': strict mismatch')
                    if reject:require(reject in log,name+': missing rejection diagnostic '+reject)
                    rows.append(dict(name=name,passed=True,state=state(m)))
                finally:(output/(name+'.log')).write_text(m.log_path.read_text())
        def native(m):
            controls=front_program(m);program(m);m.q('writel 0x15100008 1')
            require(state(m,'engine151-control-state')['instances'][0]['active'] and state(m)['isp_video_owner'],'START before source gate rejected')
            check_no_data(m)
            # Real worker proceeds through152 before opening1518[2] then[0].
            m.q('writel 0x15200008 1')
            m.q('writel 0x151c0030 1');m.q('writel 0x15180030 1')
            front_start(m,controls)
            require(state(m)['front_modes']==[2,2],'Frontends require future150c START')
            check_no_data(m)
            m.q('writel 0x150c0064 0xff00');m.q('writel 0x150c0008 0xff00')
            require('video=1 internal=0' in m.log_path.read_text(),'Aggregate not classified as video')
            check_no_data(m)
            require(state(m,'engine151-capture-pixels-state')['jobs']==0,'Video submitted a finite photo')
            require(not state(m,'output-buffer-ownership-state')['slots'][0]['held'],'Video reserved finite151 output')
        case('native-start-order',native)
        case('native-order-photo-capability',native,extra=('primary-d980-config-model=on','primary-d980-stream-model=on','engine151-capture-pixels-model=on','output-buffer-ownership-model=on'))
        def start(**kwargs):
            def action(m):program(m,**kwargs);m.q('writel 0x15100008 1');check_no_data(m)
            return action
        case('c1-config-only',start(c1_header=(0,0,0)),reject='reason=video:c1_owner')
        case('c1-not-started',start(c1_start=False),reject='reason=video:c1_owner')
        case('a1-no-preview',start(a1={**A1,4:2}),reject='reason=video:a1_enable')
        case('isp-uv-mismatch',start(isp={**ISP0,0x9c0:0xe0000b40}),reject='reason=video:uv_low')
        case('source-static',start(src={**SRC0,0xb0:0x1600}),reject='reason=video:source_y_pitch')
        case('photo-words-not-video',start(isp={**ISP0,0x200:0x10000,0x204:0,0x21c:3,0x220:19}),
             extra=('primary-d980-config-model=on','primary-d980-stream-model=on','engine151-capture-pixels-model=on','output-buffer-ownership-model=on'),reject='reason=capture_unclassified')
        case('history-words-without-parent',start(isp={**ISP0,0x200:0x10000,0x204:0,0x21c:3,0x220:2}),
             extra=('primary-d980-config-model=on','primary-d980-stream-model=on','engine151-capture-pixels-model=on','output-buffer-ownership-model=on'),reject='reason=capture_unclassified')
        def no_owner(m):
            controls=front_program(m);front_start(m,controls,ports=(0x15300000,));check_no_data(m)
            require(state(m)['front_modes']==[0,0],'Unclassified front latched a producer')
        case('front-no-admitted-producer',no_owner,reject='video=0 request=0 history=0')
        def aggregate(m,ports):
            controls=front_program(m);program(m);m.q('writel 0x15100008 1');front_start(m,controls,ports=ports)
            m.q('writel 0x150c0064 0xff00');m.q('writel 0x150c0008 0xff00');check_no_data(m)
        case('aggregate-without-fronts',lambda m:aggregate(m,()),reject='reason=no_profile')
        case('aggregate-only-decoder',lambda m:aggregate(m,(0x15300000,)),reject='reason=no_profile')
        def ambiguous(m):
            controls=front_program(m,wide=True);program(m);m.q('writel 0x15100008 1');front_start(m,controls)
            m.q('writel 0x150c0064 0xff00');m.q('writel 0x150c0008 0xff00');check_no_data(m)
        case('aggregate-video-internal-ambiguity',ambiguous,
             extra=('primary-d980-config-model=on','primary-d980-stream-model=on','z8-d980-internal-start-model=on','z8-d980-internal-wide-start-model=on'),reject='reason=ambiguous')
        def runtime_gate(m):
            from test_z8_video_stream import video_setup
            configure(m);m.q('writel 0x15080010 1');m.q('writel 0x15080028 1');advance(m)
            m.q('writel 0x15300404 0');advance(m)
            require(state(m)['frame_ready'],'No actual decoded input')
            class ClosedGate:
                def q(self,command):
                    if command=='writel 0x15180030 0x1':return None
                    return m.q(command)
                def __getattr__(self,name):return getattr(m,name)
            video_setup(ClosedGate())
            require(state(m)['isp_video_owner'],'Closed gate blocked admission')
            spans=[(0x840000000+ch*8192+off,n) for ch in range(3) for off,n in ((0,2048),(4096,1024))]
            for pa,n in spans:m.q(f'memset {pa:#x} {n:#x} 0x55')
            advance(m)
            require(state(m)['output_frames']==0 and state(m)['frame_ready'],'Closed gate consumed decoded generation')
            for pa,n in spans:require(bytes.fromhex(m.q(f'read {pa:#x} {n:#x}')[2:])==b'\x55'*n,'Closed gate wrote output')
            require(not (int(m.q('readl 0x15100044'),0)&8),'Closed gate posted completion')
            m.q('writel 0x15180030 1');advance(m)
            require(state(m)['output_frames']==1,'Opening gate did not consume exactly one generation')
            for pa,n in spans:require(bytes.fromhex(m.q(f'read {pa:#x} {n:#x}')[2:])!=b'\x55'*n,'No actual output after gate')
        case('runtime-gate-retained',runtime_gate)
    # Keep original M/R accepted source/lease assertions, also with O capabilities.
    from test_z8_integration_frontend import run as cross_modes
    cross_modes(binary,output/'cross-modes')
    result=dict(passed=True,cases=rows,cross_modes='cross-modes/result.json',
                qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                fixture='native control order;1080p ISP0 tuple inferred pending native START log',firmware_execution=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--qemu',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.qemu,args.output)
