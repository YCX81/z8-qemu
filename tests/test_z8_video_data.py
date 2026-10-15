# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual internal RAW packet RAM and decoder ownership, no vendor codec claim."""
import argparse
import json
import struct
import tempfile
import zlib
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab, require

OPTION='z8-video-data-model'
STATE='z8-video-data-state'
PACKET=0x840100000
CAPACITY=8192


def configure(m, width=64, height=32, capacity=CAPACITY, pitch=None):
    def w(a,v):m.q(f'writel {a:#x} {v:#x}')
    pitch=pitch or (((width*14+7)//8+31)//32)*32
    for off,v in ((0,1),(4,width<<16),(8,height<<16),(0x100,pitch),(0x104,1),
                  (0x108,1),(0x110,1),(0x118,3)):
        w(0x150d9800+off,v)
    for row in range(4):
        w(0x150d9920+row*8,0xe0000000);w(0x150d9924+row*8,0)
    for a in (0x150c0060,0x150c0064,0x150c0008):w(a,0x4000)
    for off,v in ((0x100,1),(0x104,1),(0x114,3),(0x548,width),(0x54c,height),(0x550,capacity)):
        w(0x152c0000+off,v)
    for off,v in ((0x114,3),(0x548,width),(0x54c,height),(0x534,capacity)):
        w(0x15300000+off,v)
    for row in range(4):
        a=PACKET+row*max(0x20000,((capacity+0xffff)//0x10000)*0x10000)
        w(0x152c0120+row*8,a&0xffffffff);w(0x152c0124+row*8,a>>32)
        w(0x15300120+row*8,a&0xffffffff);w(0x15300124+row*8,a>>32)
    for off,v in ((0x14,width),(0x1c,height),(0x44,capacity),(0x48,14),(0x50,1),(0x38,1)):
        w(0x15ac0000+off,v)
    w(0x15b00038,1)
    for base in (0x152c0000,0x15300000):
        for off,v in ((0x404,1),(0x14,1),(0x404,0),(8,1)):w(base+off,v)
    # Decoder remains held until the test can independently inspect packet RAM.
    w(0x15300404,1)


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as t:
        root=Path(t);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        payload=generate(64,32);raw=root/'pixels.raw';raw.write_bytes(payload)
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model','z8-video-interface-model',
            'event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        for enabled,width in ((False,64),(True,64),(True,110)):
            with Lab(binary,root/f'{enabled}-{width}',kernel,accelerator='qtest',
                     properties=props+((OPTION+'=on',) if enabled else ())) as m:
                m.observe_irq()
                def w(a,v):m.q(f'writel {a:#x} {v:#x}')
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property=STATE))
                def advance():
                    m.qmp.command('cont');m.q('clock_step 70000000');m.qmp.command('stop')
                configure(m,width);advance()
                require(not any(bytes.fromhex(m.q(f'read {PACKET:#x} 0x40')[2:])), 'No source clock means no RAM output')
                w(0x15080010,1);w(0x15080028,1);advance()
                if not enabled:
                    require(not any(bytes.fromhex(m.q(f'read {PACKET:#x} 0x40')[2:])), 'Default has no invented packet')
                    cases.append(dict(name='disabled',passed=True));continue
                s=state();require(s['packets']==1 and s['decoded']==0 and s['packet_owned'], 'One real packet, held decoder')
                require(int(m.q('readl 0x15080048'),0)&0x2000,'Native frame-index channel follows an actual input packet')
                w(0x15080044,0x2000);require(m.irqs.get(0xfe-32),'Channel13 mask exposes retained input boundary')
                w(0x15080048,1)
                require(int(m.q('readl 0x15080048'),0)&0x2000,'Channel0 ACK does not retire channel13')
                w(0x15080048,0x2000)
                require(not m.irqs.get(0xfe-32),'Original channel13 W1C retires its own boundary')
                packet=bytes.fromhex(m.q(f'read {PACKET:#x} {CAPACITY:#x}')[2:])
                require(packet[:8]==b'WLVR0001', 'Declared simulator transport envelope')
                packed_len,crc=struct.unpack_from('<II',packet,48)
                packed=zlib.decompress(packet[64:64+packed_len])
                pitch=(((width*14+7)//8+31)//32)*32
                require(len(packed)==pitch*32 and zlib.crc32(packed)==crc, 'Independent zlib and CRC')
                samples=struct.unpack_from('<2048H',payload,32)
                for y in range(32):
                    bits=int.from_bytes(packed[y*pitch:(y+1)*pitch],'little')
                    actual=[(bits>>(x*14))&0x3fff for x in range(width)]
                    indices=[(x//2)*32//(width//2)*2+(x&1) for x in range(width)]
                    expected=[min(16383,(samples[y*64+x]*16383+4095//2)//4095) for x in indices]
                    require(actual==expected and not any(packed[y*pitch+(width*14+7)//8:(y+1)*pitch]), 'Every packed14 sample and padding')
                require(int(m.q('readl 0x152c001c'),0)==3 and not m.irqs.get(0x10b-32), 'Masked receiver pending retained')
                w(0x152c0018,3);require(m.irqs.get(0x10b-32),'Live receiver IRQ mask')
                w(0x152c001c,3);advance()
                require(state()['packets']==1 and state()['receiver_acks']==1, 'Packet ownership survives receiver ACK')
                # A corrupted actual packet must not be replaced by the host RAW cache.
                m.q(f'write {PACKET+52:#x} 0x4 0x00000000')
                w(0x15300404,0);advance()
                require(state()['decoded']==0 and state()['decode_rejects']>0, 'Actual RAM corruption rejects decode')
                m.q(f'write {PACKET+52:#x} 0x4 0x{packet[52:56].hex()}');advance()
                require(state()['decoded']==1 and state()['frame_ready'], 'Validated decoder output owns one frame')
                require(int(m.q('readl 0x1530001c'),0)==3,'Decoder completion after actual packet decode')
                w(0x15300018,3);require(m.irqs.get(0x10c-32),'Separate decoder IRQ')
                w(0x1530001c,3);advance()
                require(state()['packets']==1 and state()['decoder_acks']==1,'Output consumer still owns decoded frame')
                # Receiver and decoder own different stages. A stopped-source reset
                # may cancel the decoder frame, but receiver reset must not steal it.
                w(0x150c000c,0x4000);w(0x150c0018,0x4000);w(0x150c0034,0x8000)
                w(0x152c0404,1);w(0x152c0004,1)
                require(state()['frame_ready'] and not state().get('cancelled',0),'Receiver reset preserves decoder-owned frame')
                w(0x15300404,1);w(0x15300004,1)
                require(not state()['frame_ready'] and state()['cancelled']==1,'Quiescent decoder reset cancels its own frame')
                for base in (0x152c0000,0x15300000):
                    w(base,1);w(base+4,0);w(base,0);w(base+8,0)
                w(0x150c021c,0x02000000)
                for off in (0,4):w(0x150c0000+off,0x4000)
                w(0x150c0018,0);w(0x150c0014,0x4000)
                configure(m,width);advance()
                first=bytes.fromhex(m.q(f'read {PACKET:#x} 0x40')[2:])
                require(state()['packets']==2 and struct.unpack_from('<Q',first,16)[0]==2,
                        'A new receiver request restarts at slot zero: '+json.dumps(state())+' epoch='+str(struct.unpack_from('<Q',first,16)[0])+' log='+m.log_path.read_text()[-1100:])
                try:m.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
                except RuntimeError:pass
                else:raise AssertionError('Option changed after realization')
                require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected MMIO')
                cases.append(dict(name=f'actual-transport-{width}',passed=True,samples=width*32,state=state()))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),firmware_execution=False,recording_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
