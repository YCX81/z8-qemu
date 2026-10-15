# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored diagnostic code marker and actual RAM -> public HEVC/AVC codec."""
import argparse,json,tempfile,time,zlib
from pathlib import Path
from check_z8_machine import sha256
from test_z8_video_ring_stop import Lab,require

BASE=0x16010000

def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for mode,name in ((1,'hevc'),(3,'avc')):
            props=('image-bank-model=on','wave5-vpu-model=on','wave5-host-codec-diagnostic=on','wave5-lossless=on','wave5-irq=120')
            with Lab(binary,root/name,kernel,accelerator='qtest',properties=props) as m:
                m.observe_irq()
                def w(off,value):m.q(f'writel {BASE+off:#x} {value:#x}')
                def read(off):return int(m.q(f'readl {BASE+off:#x}'),0)
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='wave5-state'))
                def memory(bus,data):m.q(f'write {0xa00000000+bus-0x40000000:#x} {len(data):#x} 0x{data.hex()}')
                def command(op,core=False):
                    w(0x100,op);w(0x70,1);w(0x6c if core else 0x38,1)
                    limit=time.monotonic()+10
                    while read(0x70):
                        m.qmp.command('cont');m.q('clock_step 1000000');m.qmp.command('stop')
                        time.sleep(.002)
                        if time.monotonic()>limit:raise TimeoutError('Diagnostic codec worker')
                    require(read(0x108)==1,'Accepted codec command')
                def ack():w(0x34,0xffffffff)
                def info():w(0x110,mode<<16)
                def result_bytes():
                    ack();w(0x104,2);info();command(0x4000)
                    amount=read(0x14c);bus=read(0x114)
                    require(0<amount<0x100000,'Bounded actual output size')
                    return bytes.fromhex(m.q(f'read {0xa00000000+bus-0x40000000:#x} {amount:#x}')[2:])
                require(read(0x1044)==0x5210,'Declared compatible virtual product')
                # This is explicitly not vendor code or vendor instruction execution.
                memory(0x40010000,bytes([0xa5])*4096)
                w(0x110,0x40010000);w(0x114,4096);w(0x11c,0x40020000);w(0x120,4096)
                command(1,core=True);w(0x48,0xffff)
                info();w(0x114,0x40030000);w(0x118,0x10000);w(0x124,0)
                command(8);require(m.irqs.get(120-32),'Owned CREATE completion interrupt');ack()
                info();w(0x104,0);w(0x11c,32<<16|64);w(0x124,8<<14)
                for off in (0x134,0x138,0x170,0x174,0x178):w(off,0)
                w(0x150,30);command(0x200);ack()
                info();w(0x104,2);command(0x4000)
                require(read(0x1dc)==1,'Separate sequence result')
                command(0x80)
                def output_parameters():info();w(0x118,0x40100000);w(0x11c,0x100000);w(0x128,0)
                output_parameters();w(0x164,0x3c);command(0x100)
                stream=bytearray(result_bytes());expected=bytearray()
                for n,y in enumerate((48,176)):
                    yplane=bytes([y])*2048;uv=bytes([90+n*20,160-n*20])*512
                    memory(0x40060000,yplane);memory(0x40061000,uv)
                    output_parameters();w(0x144,n);w(0x148,0x40060000);w(0x14c,0x40061000);w(0x150,0)
                    w(0x154,64<<16|64);w(0x158,2);w(0x164,3)
                    command(0x100);stream+=result_bytes()
                    expected+=yplane+bytes([90+n*20])*512+bytes([160-n*20])*512
                saved=state()
                require(saved['instances'][0]['pictures']==2 and saved['input_bytes']==6144,'Two real DMA snapshots encoded')
                require(not saved['vendor_microcode_executed'] and saved['diagnostic_host_codec'],'Honest backend identity')
                require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected VPU access: '+m.log_path.read_text()[-1200:])
                (output/(name+'.bin')).write_bytes(stream)
                (output/(name+'-expected.yuv')).write_bytes(expected)
                cases.append(dict(name=name,passed=True,bytes=len(stream),state=saved,
                                  coded_crc=zlib.crc32(stream),decoded_expected_sha256=sha256(output/(name+'-expected.yuv'))))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),original_firmware_execution=False,recording_verified=False,
                independent_decode_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
