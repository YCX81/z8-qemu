# SPDX-License-Identifier: GPL-2.0-or-later
"""Video C1/ISP0 consumes actual decoded RAM, with all output owners separate."""
import argparse,json,struct,tempfile,zlib
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from test_z8_video_data import configure,PACKET,CAPACITY,OPTION,STATE


def video_setup(m):
    def w(a,v):m.q(f'writel {a:#x} {v:#x}')
    base=0x15100000
    for off,v in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff),(0x1c,0x3f),
                  (0x260,0x1fff),(0x264,0x3ff),(0x400,36<<16|64),(0x450,36<<16|64),
                  (0x200,1),(0x204,1),(0x214,1),(0x218,1),(0x21c,5),(0x220,2),
                  (0x430,192),(0x434,192),(0x480,256),(0x484,256),
                  (0x740,0xe0000000),(0x744,0),(0x9c0,0xe0000080),(0x9c4,0)):
        w(base+off,v)
    for off in range(0x268,0x320,8):w(base+off,0x1000);w(base+off+4,0xa)
    w(0x15180014,1);w(0x15180014,0)
    for off,v in ((8,0x08100000),(0xb0,192),(0xb4,192),(0xbc,1),(0xc0,1),(0xc4,0x22),(0xd4,0x22),(0x30,1)):
        w(0x15180000+off,v)
    for off,v in ((4,0x202),(0x10,1),(0x38,256),(0x3c,256),(0x4c,64),(0x50,36)):
        w(0x15421100+off,v)
    for ch in range(3):
        b=0x15422000+ch*0x100;f=0x15423000+ch*0x100
        for off,v in ((0xc,0),(0x10,1),(0x38,64),(0x3c,64)):w(b+off,v)
        w(f+0x2c,64);w(f+0x30,32)
    m.ring(start=False)
    for row in range(4):
        for ch in range(3):
            y=0x840000000+row*0x10000+ch*0x2000;off=0x210+row*0x80+ch*0x10
            for at,p in ((off,y),(off+8,y+0x1000)):
                m.write(at,p&0xffffffff);m.write(at+4,p>>32)
    m.ring(j=1,start=False)
    for row in range(4):
        m.write(0x200+row*128,0xe0000000+(row%2)*1920,1)
        m.write(0x208+row*128,0xe0000080+(row%2)*1920,1)
    m.write(4,0x101,1);w(base+8,1)


def run(binary,output,repeat=False,source_end=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(dir=output) as t:
        root=Path(t);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096));raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model','z8-video-interface-model',
            'event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model',
            'event1508-global1-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000',OPTION+'=on')
        if repeat:props+=('z8-video-ring-repeat-model=on',)
        if source_end:props+=('z8-video-source-end-model=on',)
        with Lab(binary,root/'stream',kernel,accelerator='qtest',properties=props) as m:
            m.observe_irq()
            def w(a,v):m.q(f'writel {a:#x} {v:#x}')
            def advance():m.qmp.command('cont');m.q('clock_step 70000000');m.qmp.command('stop')
            def state():return json.loads(m.qmp.command('qom-get',path='/machine',property=STATE))
            configure(m);w(0x15080010,1);w(0x15080028,1);advance()
            w(0x15080048,0x2000)
            packet=bytearray(bytes.fromhex(m.q(f'read {PACKET:#x} {CAPACITY:#x}')[2:]))
            # Different, valid actual packet: all-black RAW. Global input stays colored.
            packed=bytes(4096);encoded=zlib.compress(packed)
            struct.pack_into('<II',packet,48,len(encoded),zlib.crc32(packed));packet[64:64+len(encoded)]=encoded
            m.q(f'write {PACKET:#x} {CAPACITY:#x} 0x{packet.hex()}')
            w(0x152c001c,3);w(0x15300404,0);advance();w(0x1530001c,3)
            require(state()['frame_ready'],'Actual decoded packet awaits ISP')
            video_setup(m)
            if source_end:w(0x15180008,0x28100000)
            w(0x15080068,0x10)
            for ch in range(3):
                for offset,n in ((0,2048),(4096,1024)):
                    m.q(f'memset {0x840000000+ch*8192+offset:#x} {n:#x} 0x55')
            advance()
            require(state().get('output_frames',0)==1,'ISP0/C1 must consume one decoded frame while C0 inactive')
            global_state=json.loads(m.qmp.command('qom-get',path='/machine',property='event1508-global1-state'))
            require(global_state['pending']&0x10,'Actual video output must advance the original frame-sequence boundary')
            for ch in range(3):
                y=bytes.fromhex(m.q(f'read {0x840000000+ch*8192:#x} 0x800')[2:])
                uv=bytes.fromhex(m.q(f'read {0x840001000+ch*8192:#x} 0x400')[2:])
                require(y==bytes(2048) and uv==bytes([128])*1024,'Output must use decoded packet, never host RAW cache')
            require(int(m.q('readl 0x15180010'),0)&0x08100000==0x08100000,'Actual source completion')
            require(int(m.q('readl 0x15100044'),0)&8,'Actual ISP completion')
            require(m.pending(1)&1 and m.pending(0)&1,'Independent C1 input and C0 output ownership')
            require(int(m.q('readl 0x150c0208'),0)&0x18==0x18,'Primary input/output events after six writes')
            w(0x1518000c,0x08100000);w(0x15100044,8);w(0x1542111c,0x11)
            w(0x1508006c,0x10)
            m.write(20,1,1);m.write(20,1);w(0x150c0204,8);advance()
            require(state()['output_frames']==1,'Partial primary ACK cannot release output generation')
            w(0x150c0204,16);w(0x152c001c,3);w(0x1530001c,3);advance()
            require(state()['output_frames']==2,'All owners retired permits next real decoded frame')
            def retire(end=True):
                w(0x15080048,0x2000)
                w(0x1508006c,0x10)
                w(0x1518000c,0x28100000 if source_end and end else 0x08100000);w(0x15100044,8);w(0x1542111c,0x11)
                m.write(20,9,1);m.write(20,9);w(0x150c0204,24)
                w(0x152c001c,0x13);w(0x1530001c,0x13)
            def reach(count):
                for _ in range(20):
                    if state()['output_frames']>=count:return
                    retire();advance()
                raise AssertionError('Frame progression stopped: '+json.dumps(state()))
            def refill(bank,partial=False):
                for port,base in enumerate((0x152c0000,0x15300000)):
                    for row in range(4):
                        address=PACKET+bank*0x200000+row*0x20000+(0x400000000 if port==0 else 0)
                        w(base+0x120+bank*0x20+row*8,address&0xffffffff);w(base+0x124+bank*0x20+row*8,address>>32)
                    w(base+0x108,bank)
                for row in range(4):
                    for off,value in ((0,0xe0000000+(row%2)*1920),(4,0),(8,0xe0000080+(row%2)*1920),(12,0)):
                        m.write(0x200+bank*0x200+row*0x80+off,value,1)
                m.write(8,0x302|bank,1)
                for row in range(4):
                    for ch in range(3):
                        y=0x840080000+row*0x10000+ch*0x2000;off=0x210+bank*0x200+row*0x80+ch*0x10
                        for at,p in ((off,y),(off+8,y+0x1000)):
                            m.write(at,p&0xffffffff)
                            if not (partial and row==3 and ch==2 and at==off+8):m.write(at+4,p>>32)
            if source_end:
                require(state()['source_ends']==0,'No source END before three actual outputs')
                reach(3)
                require(state()['source_ends']==1 and int(m.q('readl 0x15180010'),0)&0x20000000,'Third real output owns separate source END')
                retire(False);w(0x152c001c,16);w(0x1530001c,16);advance()
                require(state()['decoded']==4 and state()['output_frames']==3 and state()['source_end_acks']==0,'Data and frontend REFILL ACKs cannot retire source END')
                w(0x1518000c,0x20000000);advance()
            reach(4);refill(1);m.write(8,0x303);reach(8)
            refill(0,partial=True);retire();advance()
            require(state()['output_frames']==8,'Partial alternate bank cannot replace an output generation')
            m.write(0x3bc,8);retire();advance()
            require(state()['output_frames']==9 and state()['output_bank']==0,
                    'Fresh alternate bank advances even if the programming selector is retained')
            require(int(m.q('readl 0x15431008'),0)==0x303,'Completion does not rewrite the programming selector')
            if repeat:
                reach(14)
                require(state()['ring_repeats']>=1,'Complete current bank can stream while no replacement is being written')
            if source_end:
                reach(11);before=state()['source_end_acks'];w(0x15180014,1)
                require(state()['source_end_cancelled']==1 and state()['source_end_acks']==before,'Source reset cancels pending END without a fabricated ACK')
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected MMIO')
            result=dict(passed=True,qemu_sha256=sha256(binary),cases=[dict(name='actual-decoder-to-six-planes',passed=True)],state=state(),recording_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--repeat-test',action='store_true')
    p.add_argument('--source-end-test',action='store_true')
    a=p.parse_args();run(a.qemu,a.output,a.repeat_test,a.source_end_test)
