# SPDX-License-Identifier: GPL-2.0-or-later
"""WLVR frontends: C1-reference DATA, REFILL, STOP END and bank contracts.

Adapted semantically from z8-video-c1-20261014 tests captured at the handoff.
One MMIO owner and one packet format; no legacy C1 backend is instantiated.
"""
import argparse,json,tempfile,time
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from test_z8_video_data import configure,PACKET,CAPACITY
from test_z8_video_stream import video_setup

BASES=(0x152c0000,0x15300000)

def setup(m):
    configure(m)
    for base in BASES:
        m.q(f'writel {base+0x114:#x} 3')
        m.q(f'writel {base+0x18:#x} 0x79f')
    for off,value in ((0x548,64),(0x54c,32),(0x534,CAPACITY)):
        m.q(f'writel {0x15300000+off:#x} {value:#x}')
    m.q('writel 0x15080010 1');m.q('writel 0x15080028 1')

def run(binary,output,case='all'):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096));raw=root/'input.raw';raw.write_bytes(generate(64,32))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model','config150d-model','engine150d-control-model','statistics-model','engine1543-control-model','engine151-control-model','raw-pipeline-model','z8-video-interface-model','z8-video-data-model','event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model','event1508-global1-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        for name in (('data','stop','start-before-end-ack','local-flush-end','banks') if case=='all' else (case,)):
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props) as m:
                m.qmp.deadline=time.monotonic()+45;m.observe_irq()
                def w(a,v):m.q(f'writel {a:#x} {v:#x}')
                def read(a):return int(m.q(f'readl {a:#x}'),0)
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-data-state'))
                def advance():m.qmp.command('cont');m.q('clock_step 50000000');m.qmp.command('stop')
                if name!='data':
                    w(BASES[0]+8,0);w(BASES[1]+8,0)
                    require(state()['stop_ends']==[0,0],'Inactive STOP earns no END')
                setup(m);advance()
                require(state()['packets']==1 and read(BASES[0]+0x1c)==3,'Original DATA client receives bits0/1, never END4')
                if name=='data':
                    w(BASES[0]+0x18,0)
                    require(read(BASES[0]+0x10)==0 and read(BASES[0]+0x1c)==3 and not m.irqs.get(0x10b-32),'Mask cannot discard raw causes')
                    w(BASES[0]+0x18,3);w(BASES[0]+0x14,0)
                    require(not m.irqs.get(0x10b-32) and read(BASES[0]+0x10)==3,'Gate controls IRQ, not raw or masked status storage')
                    w(BASES[0]+0x14,1);require(m.irqs.get(0x10b-32),'Gate reopening delivers retained causes')
                    w(BASES[0]+0x1c,0x14);w(BASES[0]+0x1c,1)
                    require(state()['receiver_acks']==0 and read(BASES[0]+0x1c)==2,'Wrong cause and partial DATA ACK do not retire a frame')
                    w(BASES[0]+0x1c,2);require(state()['receiver_acks']==1,'Last DATA bit retires once')
                    w(BASES[1]+0x404,0);advance()
                    require(read(BASES[1]+0x1c)==3,'Decoder has independent DATA causes')
                    w(BASES[1]+0x1c,2);w(BASES[1]+0x1c,0x14)
                    require(state()['decoder_acks']==0 and read(BASES[1]+0x1c)==1,'Reverse ACK order remains pending')
                    w(BASES[1]+0x1c,1);advance()
                    require(state()['decoder_acks']==1 and state()['packets']==1,'ACK cannot stand in for image consumption')
                elif name=='local-flush-end':
                    w(BASES[1]+0x404,0);advance()
                    w(0x150c000c,0x4000);w(0x150c0018,0x4000);w(0x150c0034,0x8000)
                    w(BASES[1]+0x404,1)
                    for off,value in ((4,1),(0,1),(4,0),(0,0),(0x1c,0x7ff),(0x404,0),(8,0)):
                        w(BASES[1]+off,value)
                    require(state()['stop_ends']==[0,1] and state()['decoder_acks']==1,'Native local flush retains stream proof until the explicit STOP')
                    w(0xd154340,0x200000)
                    require(state()['stop_cancelled']==[0,1] and state()['stop_acks']==[0,0] and read(BASES[1]+0x1c)==0,'SoC reset cancels unacknowledged END without successful completion')
                    require(read(BASES[0]+0x1c)==3,'Decoder reset cannot clear receiver DATA')
                elif name in ('stop','start-before-end-ack'):
                    w(BASES[1]+0x404,0);advance()
                    w(BASES[0]+8,0)
                    require(state()['stop_ends']==[0,0] and read(BASES[1]+0x1c)==3,'STOP preserves old and other-port causes')
                    w(BASES[0]+0x1c,1);require(not state()['stop_ends'][0],'Partial data ACK cannot produce END')
                    w(BASES[0]+0x1c,2)
                    require(state()['stop_ends']==[1,0] and read(BASES[0]+0x1c)==4,'Proven stopped stream gets fresh END4')
                    if name=='start-before-end-ack':
                        w(BASES[0]+8,1);require('Z9_UNKNOWN' in m.log_path.read_text(),'Unacknowledged END prevents restart')
                        cases.append(dict(name=name,passed=True,state=state()));continue
                    w(BASES[0]+0x14,0);w(BASES[0]+0x18,0)
                    require(read(BASES[0]+0x1c)==4 and not m.irqs.get(0x10b-32),'Masked/gated END remains pending')
                    w(BASES[0]+0x18,4);w(BASES[0]+0x14,1);require(m.irqs.get(0x10b-32),'Reenable delivers the same END')
                    w(BASES[0]+0x1c,4);w(BASES[0]+8,0)
                    require(state()['stop_acks']==[1,0] and state()['stop_ends']==[1,0],'Duplicate STOP has no fresh END')
                    w(BASES[1]+0x1c,3);w(BASES[1]+8,0)
                    require(state()['stop_ends']==[1,1] and state()['stop_acks']==[1,0],'Decoder keeps its independent proven lifetime')
                    w(BASES[1]+0x1c,4);advance()
                    require(state()['stop_acks']==[1,1] and state()['packets']==1 and not state()['output_frames'],'Stop/END cannot produce pixels')
                else:
                    w(BASES[1]+0x404,0);video_setup(m)
                    def retire(refill=True):
                        for addr,value in ((0x15080048,0x2000),(0x1508006c,0x10),(0x1518000c,0x08100000),(0x15100044,8),(0x1542111c,0x11),(0x15431014,9),(0x15432014,9),(0x150c0204,24)):
                            w(addr,value)
                        for b in BASES:w(b+0x1c,0x13 if refill else 3)
                    def reach(n):
                        for _ in range(16):
                            if state()['output_frames']>=n:return
                            retire();advance()
                        raise AssertionError('Stopped before output '+str(n)+': '+json.dumps(state()))
                    reach(3);retire(False);advance()
                    require(state()['refills']==[1,1] and [read(b+0x1c) for b in BASES]==[16,16],'Third DATA retirement requests independent REFILL16')
                    w(BASES[0]+0x1c,16);advance();require(state()['packets']==3,'Other-port REFILL still owns its gate')
                    w(BASES[1]+0x1c,16);advance();reach(4);retire();advance()
                    require(state()['packets']==4,'Old complete front table has no new bank authorization')
                    for b in BASES:w(b+0x108,1)
                    advance();require(state()['packets']==4,'Selector alone is not a submitted front bank')
                    for port,b in enumerate(BASES):
                        for row in range(4):
                            physical=PACKET+0x200000+row*0x20000;address=physical+(0x400000000 if port==0 else 0)
                            w(b+0x140+row*8,address&0xffffffff)
                            if port:w(b+0x144+row*8,address>>32)
                    advance();require(state()['packets']==4,'Partial receiver low/high generation is not reused')
                    for row in range(4):w(BASES[0]+0x144+row*8,12)
                    advance();require(state()['decoded']==5 and state()['output_frames']==4,'Front bank can advance; output still needs C1 submit')
                    m.write(8,0x303,1);advance();require(state()['output_frames']==4,'C1 selector alone cannot authorize retained C0')
                    for row in range(4):
                        for off,value in ((0,0xe0000000+(row%2)*1920),(4,0),(8,0xe0000080+(row%2)*1920),(12,0)):
                            if (row,off)!=(3,12):m.write(0x400+row*0x80+off,value,1)
                    advance();require(state()['output_frames']==4,'Partial C1 submit cannot retire a decoded frame')
                    m.write(0x58c,0,1);advance()
                    require(state()['output_frames']==5 and state()['input_bank_refills']==1,'Complete fresh C1 submit authorizes retained complete C0')
                    reach(8);retire()
                    for b in BASES:w(b+0x108,0)
                    advance();require(state()['packets']==8,'Bank0 old validity is not fresh bank0 submission')
                    for port,b in enumerate(BASES):
                        for row in range(4):
                            address=PACKET+row*0x20000+(0x400000000 if port==0 else 0)
                            w(b+0x120+row*8,address&0xffffffff);w(b+0x124+row*8,address>>32)
                    advance();require(state()['decoded']==9 and state()['output_frames']==8,'Returned front bank needs its own fresh C1 transaction')
                    for row in range(4):
                        for off,value in ((0,0xe0000000+(row%2)*1920),(4,0),(8,0xe0000080+(row%2)*1920),(12,0)):
                            m.write(0x400+row*0x80+off,value,1)
                    advance();require(state()['output_frames']==8,'Same-selector C1 rewrites cannot supply a new bank')
                    for row in range(4):
                        for off,value in ((0,0xe0000000+(row%2)*1920),(4,0),(8,0xe0000080+(row%2)*1920),(12,0)):
                            m.write(0x200+row*0x80+off,value,1)
                    m.write(8,0x302,1);advance()
                    require(state()['output_frames']==9 and state()['input_bank_refills']==2 and state()['front_banks']==[0,0],'Independent frontend and C1 banks complete 0 to 1 to 0')
                require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected model boundary: '+m.log_path.read_text()[-1000:])
                cases.append(dict(name=name,passed=True,state=state()))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),native_firmware=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=True,cases=len(cases))))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--case',choices=('all','data','stop','start-before-end-ack','local-flush-end','banks'),default='all')
    a=p.parse_args();run(a.qemu,a.output,a.case)
