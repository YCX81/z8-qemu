# SPDX-License-Identifier: GPL-2.0-or-later
"""Real-RAM bank0 -> bank1 -> bank0 evidence; no descriptor repeat fallback."""
import argparse,json,struct,zlib
from pathlib import Path
from test_z8_video_frontend_contract import C1Adapter
from test_z8_video_c1 import configure_isp
from video_frontend_contract import check


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with C1Adapter(binary,output/'device') as d:
        m=d.m;frames=[];checks=[]
        def s():return json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-c1-state'))
        def w(a,v):m.q(f'writel {a:#x} {v:#x}')
        def tick():m.qmp.command('cont');m.q('clock_step 50000000');m.qmp.command('stop')
        def downstream():
            for a,v in ((0x15100044,8),(0x1518000c,0x08100000),
                        (0x1542111c,0x11),(0x15431014,9),(0x15432014,9)):
                w(a,v)
        def wait_output(target):
            for _ in range(8):
                tick()
                if s()['output_frames']==target:return
                downstream()
            raise AssertionError('output did not follow complete authorization')
        def packet(expected):
            state=s();address=state['packet_address'];size=state['packet_bytes']
            raw=bytes.fromhex(m.q(f'read {address:#x} {size:#x}')[2:])
            check(raw[:8]==b'NZ8BAYR1','reference adapter packet family')
            epoch,stamp=struct.unpack_from('<QQ',raw,48)
            payload=zlib.decompress(raw[80:80+struct.unpack_from('<Q',raw,40)[0]])
            check(epoch==expected and zlib.crc32(payload)==struct.unpack_from('<I',raw,64)[0],
                  'real RAM generation and CRC')
            frames.append(dict(epoch=epoch,virtual_ns=stamp,address=address,bytes=size))
        def retire(target):
            for port in (0,1):d.ack(port,3)
            if target%4==3:
                for port in (0,1):check(d.pending(port)==16,'third DATA ACK owns separate REFILL')
                before=s()['packets'];tick()
                check(s()['packets']==before,'unretired refill holds next receiver packet')
                checks.append('third-slot refill independently blocks reuse')
            for port in (0,1):d.ack(port,16)
            downstream()
        def full_input_bank(bank):
            for row in range(4):
                for word in range(4):
                    value=int(m.q(f'readl {0x15432200+row*0x80+word*4:#x}'),0)
                    m.write(0x200+bank*0x200+row*0x80+word*4,value,1)
            m.write(8,0x302|bank,1)
        def address(bank,row):return 0x840000000+bank*0x80000+row*0x10000
        configure_isp(m)
        for wanted in range(1,10):
            if wanted in (5,9):
                bank=1 if wanted==5 else 0;before=s()
                for port in (0,1):d.write(port,0x108,bank)
                tick();check(s()['packets']==before['packets'],'selector alone must not authorize old/absent bank')
                for row in range(4):d.write(0,0x120+bank*0x20+row*8,address(bank,row)&0xffffffff)
                tick();check(s()['packets']==before['packets'],'low halves alone cannot publish receiver bank')
                for row in range(3):d.write(0,0x124+bank*0x20+row*8,12)
                tick();check(s()['packets']==before['packets'],'partial receiver bank cannot publish')
                d.write(0,0x13c+bank*0x20,12);tick()
                check(s()['packets']==wanted and s()['decoded']==wanted-1,'receiver DMA precedes independent decoder bank')
                for row in range(4):d.write(1,0x120+bank*0x20+row*8,address(bank,row)&0xffffffff)
                tick();check(s()['decoded']==wanted-1,'decoder low halves cannot consume')
                for row in range(3):d.write(1,0x124+bank*0x20+row*8,8)
                tick();check(s()['decoded']==wanted-1,'partial decoder bank cannot consume')
                d.write(1,0x13c+bank*0x20,8);tick()
                check(s()['decoded']==wanted,'whole decoder bank consumes same physical packet')
                full_input_bank(bank)
                checks.append(f'bank{bank}: selector/half/partial rejected, complete accepted')
            wait_output(wanted);packet(wanted);retire(wanted)
        check(not d.rejected(),'reference fixture must not trigger unknown access')
        check(len({r['epoch'] for r in frames})==9,'unique packet generation')
        report=dict(passed=True,frames=frames,checks=checks,state=s(),
                    scope='QTest reference semantics; not native firmware or hardware acceptance')
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,frames=len(frames),checks=len(checks))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
