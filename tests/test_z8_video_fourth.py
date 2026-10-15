# SPDX-License-Identifier: GPL-2.0-or-later
"""Fourth NV12 output uses explicit paired port/RAM plane transport."""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from test_z8_video_c1 import configure, configure_isp, refill_backends
from test_z8_video_ring_stop import Lab, require


def run(binary, output, wave5=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as directory:
        root = Path(directory)
        kernel = root/'zero.bin'; kernel.write_bytes(bytes(4096))
        raw = root/'input.raw'
        samples = [(i*7)&16383 for i in range(64*36)]
        raw.write_bytes(struct.pack('<8s6I', b'NZ9RAW1\0', 64,36,14,0,0,16383)+
                        struct.pack('<'+'H'*len(samples), *samples))
        props = tuple(n+'=on' for n in (
            'image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model',
            'engine1543-control-model','engine151-control-model','raw-pipeline-model',
            'z8-video-interface-model','event1508-front-model','event1508-timing-model',
            'event1508-channel-model','event1508-aux-model','z8-video-c1-model',
            'z8-video-c1-fourth-output-model'))
        props += ('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        if wave5:props+=('z8-video-processing-config-model=on','z8-video-plane-reader-model=on',
                         'wave5-vpu-model=on','wave5-host-codec-diagnostic=on','wave5-lossless=on','wave5-irq=120')
        names=('valid','corrupt-encoder-input') if wave5 else ('valid','bank-pair','receiver-stop','unbound','overlap','capacity')
        for name in names:
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props) as m:
                m.observe_irq()
                def w(a,v):m.q(f'writel {a:#x} {v:#x}')
                def q(a,v):m.q(f'writeq {a:#x} {v:#x}')
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-c1-state'))
                def advance():
                    m.qmp.command('cont');m.q('clock_step 50000000');m.qmp.command('stop')
                configure(m);w(0x15300008,1);advance()
                w(0x152c001c,0x13);w(0x1530001c,0x13)
                configure_isp(m)
                for off,value in {0x10:1,0x38:64,0x3c:64}.items():w(0x15422300+off,value)
                w(0x1542332c,64);w(0x15423330,36)
                for row in range(4):
                    for uv in range(2):
                        alias=0x200000000+uv*0x18000000+row*0x3000000
                        off=0x240+row*0x80+uv*8
                        m.write(off,alias&0xffffffff);m.write(off+4,alias>>32)
                        bank=0x9600 if uv else 0x9200
                        q(0x15640000+bank+0x80+row*8,alias)
                        physical=0x844000000+row*0x10000+uv*0x4000
                        if name=='overlap' and not row and uv:physical=0x842000000
                        q(0x15640000+bank+0xa0+row*8,physical)
                for uv in range(2):
                    bank=0x9600 if uv else 0x9200
                    rows=18 if uv else 36
                    for off,value in {0x18:64*rows,0x1c:64,0x20:64,0x28:4096,
                                      0x2c:64,0x30:64,0x40:15,0x44:64|(rows<<16)}.items():
                        w(0x15640000+bank+off,value)
                    w(0x15649000+uv*0x400,1)
                    w(0x1564900c+uv*0x400,3)
                if name=='unbound':q(0x15649280,0x233000000)
                if name=='capacity':w(0x15649228,64)
                if name=='receiver-stop':
                    w(0x152c0008,0);w(0x152c001c,4);w(0x152c0008,1)
                before=bytes.fromhex(m.q('read 0x842000000 0x240')[2:])
                advance()
                current=state()
                if name in ('unbound','overlap','capacity'):
                    require(current['output_frames']==0 and current['fourth_frames']==0,
                            'incomplete fourth output must not manufacture any frame')
                    require(bytes.fromhex(m.q('read 0x842000000 0x240')[2:])==before,
                            'all output destinations preflight before first write')
                else:
                    require(current['output_frames']==1 and current['fourth_frames']==1,'one owned fourth output')
                    for uv in range(2):
                        physical=0x844000000+uv*0x4000
                        header=bytes.fromhex(m.q(f'read {physical:#x} 0x50')[2:])
                        require(header[:8]==b'NZ8YUV1\0','explicit simulator plane, not Nikon data')
                        size=struct.unpack_from('<I',header,72)[0]
                        packet=bytes.fromhex(m.q(f'read {physical:#x} {size:#x}')[2:])
                        count=struct.unpack_from('<Q',header,40)[0]
                        plane=zlib.decompress(packet[80:80+count])
                        require(len(plane)==64*(18 if uv else 36) and len(set(plane))>1,'actual image samples')
                        require(zlib.crc32(plane)==struct.unpack_from('<I',header,64)[0],'whole independent plane decode')
                        require(struct.unpack_from('<Q',header,48)[0]==current['consumed_epoch'],'same input identity')
                    require(int(m.q('readl 0x15431014'),0)&1,'C0 completion after fourth actual output')
                    if wave5:
                        test_wave5_picture(m,output,name,w)
                    if name=='bank-pair':
                        def ack():
                            for address,value in ((0x152c001c,0x13),(0x1530001c,0x13),(0x15100044,8),
                                                  (0x1518000c,0x08100000),(0x1542111c,0x11),
                                                  (0x15431014,9),(0x15432014,9)):
                                w(address,value)
                        for _ in range(8):
                            ack();advance()
                            if state()['output_frames']==4:break
                        require(state()['output_frames']==4,'first paired bank complete')
                        ack();advance()
                        refill_backends(m,1)
                        for row in range(4):
                            for ch in range(3):
                                for uv in range(2):
                                    address=0x848000000+row*0x10000+ch*0x2000+uv*0x1000
                                    off=0x410+row*0x80+ch*0x10+uv*8
                                    m.write(off,address&0xffffffff);m.write(off+4,address>>32)
                            for off,value in ((0,0xe0000000),(4,0),(8,0xe0000080),(12,0)):
                                m.write(0x400+row*0x80+off,value,1)
                            for uv in range(2):
                                alias=0x20c000000+uv*0x18000000+row*0x3000000
                                off=0x440+row*0x80+uv*8
                                m.write(off,alias&0xffffffff);m.write(off+4,alias>>32)
                                bank=0x9700 if uv else 0x9300
                                q(0x15640000+bank+0x80+row*8,alias)
                                q(0x15640000+bank+0xa0+row*8,0x844080000+row*0x10000+uv*0x4000)
                        for uv in range(2):
                            bank=0x9700 if uv else 0x9300;rows=18 if uv else 36
                            for off,value in {0x18:64*rows,0x1c:64,0x20:64,0x28:4096,
                                              0x2c:64,0x30:64,0x40:15,0x44:64|(rows<<16)}.items():
                                w(0x15640000+bank+off,value)
                        m.write(8,0x303,1)  # C1 selects the paired video bank; C0 remains stopped.
                        advance()
                        require(state()['fourth_frames']==5 and state()['fourth_physical'][0]==0x844080000,
                                'C1 bank selection consumes its paired complete output bank')
                require(('Z9_UNKNOWN' in m.log_path.read_text()) == (wave5 and name=='corrupt-encoder-input'),
                        'unexpected fixture access\n'+m.log_path.read_text()[-1600:])
                cases.append(dict(name=name,passed=True,state=current))
    (output/'result.json').write_text(json.dumps(dict(passed=True,cases=cases),indent=2)+'\n')
    print(json.dumps(dict(passed=True,cases=len(cases))))


def test_wave5_picture(m,output,name,w):
    def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='wave5-vpu-state'))
    def command(cmd,values=None):
        for off,value in (values or {}).items():w(0x16010000+off,value)
        w(0x16010100,cmd);w(0x16010070,1);w(0x1601006c if cmd==1 else 0x16010038,1)
    def retire():w(0x16010034,0xffffffff)
    m.q('write 0xa00010000 4096 0x'+(b'\x01'+bytes(4095)).hex())
    command(1,{0x110:0x40010000,0x114:4096,0x11c:0x40020000,0x120:4096})
    w(0x16010024,8);w(0x16010020,0x1fec0)
    w(0x16018004,1);w(0x16010048,0x100)
    command(8,{0x110:0x10000,0x114:0x45010000,0x118:4096,0x124:0});retire()
    command(0x200,{0x104:0,0x11c:64|(36<<16),0x124:8<<14,0x138:0,0x134:0,
                   0x170:0,0x178:30,0x174:1});retire()
    command(0x80);retire()
    command(0x100,{0x118:0x45200000,0x11c:0x100000,0x164:0x3c,0x128:0})
    header_bytes=state()['instances'][0]['bytes']
    stream=bytes.fromhex(m.q(f'read 0x845200000 {header_bytes:#x}')[2:])
    retire();command(0x4000,{0x104:2});retire()
    planes=[]
    for address in (0x844000000,0x844004000):
        head=bytes.fromhex(m.q(f'read {address:#x} 80')[2:])
        amount=struct.unpack_from('<I',head,72)[0]
        packet=bytes.fromhex(m.q(f'read {address:#x} {amount:#x}')[2:])
        planes.append(zlib.decompress(packet[80:80+struct.unpack_from('<Q',head,40)[0]]))
    if name=='corrupt-encoder-input':m.q('writeb 0x844000050 255')
    command(0x100,{0x118:0x45210000,0x11c:0x100000,0x164:2,0x144:0,
                   0x148:0x44000000,0x14c:0x44004000,0x150:0,0x154:64|(64<<16),0x158:2})
    if name=='corrupt-encoder-input':
        require(state()['instances'][0]['pictures']==0,'corrupt plane cannot reach encoder')
        require('Z9_UNKNOWN' in m.log_path.read_text(),'strict rejected input')
        return
    for _ in range(200):
        m.qmp.command('cont');m.q('clock_step 1000000');m.qmp.command('stop')
        if state()['instances'][0]['pictures']:break
        time.sleep(.01)
    result=state()
    require(result['instances'][0]['pictures']==1,'actual C1 plane must reach encoder')
    i420=planes[0]+planes[1][0::2]+planes[1][1::2]
    require(result['input_crc']==zlib.crc32(i420),'independent exact input sample CRC')
    encoded=bytes.fromhex(m.q(f"read 0x845210000 {result['instances'][0]['bytes']:#x}")[2:])
    (output/'c1-frame.hevc').write_bytes(stream+encoded)
    (output/'reference.i420').write_bytes(i420)
    (output/'encoder-state.json').write_text(json.dumps(result,indent=2))
    require(m.irqs.get(120-32),'only actual completed picture asserts enabled encoder IRQ')
    retire()
    require(not m.irqs.get(120-32),'original W1C retires codec IRQ')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--wave5',action='store_true')
    a=p.parse_args();run(a.qemu,a.output,a.wave5)
