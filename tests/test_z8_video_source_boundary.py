# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit input timing must not depend on an enabled output ring."""
import argparse,json,tempfile
from pathlib import Path
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from check_z8_machine import sha256

def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        raw=root/'pixels.raw';raw.write_bytes(generate(64,32))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model','event1508-front-model','event1508-timing-model',
            'event1508-channel-model','event1508-aux-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        for enabled in (False,True):
            with Lab(binary,root/str(enabled),kernel,accelerator='qtest',
                     properties=props+(('raw-source-boundary-model=on',) if enabled else ())) as m:
                m.observe_irq()
                def write(at,value):m.q(f'writel {at:#x} {value:#x}')
                def advance():
                    m.qmp.command('cont');m.q('clock_step 5000000');m.qmp.command('stop')
                def pending():return int(m.q('readl 0x150c0214'),0)
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='raw-source-boundary-state'))
                advance();require(pending()==0,'No input before active timing/source')
                write(0x150c0060,2);write(0x150c0064,2);write(0x150c0008,2)
                advance();require(pending()==0,'Source enable is not an input frame')
                write(0x15080010,1);write(0x15080028,1);advance()
                actual=pending()
                require(actual==(0x20000000 if enabled else 0),'Input notification without an output ring: '+str((hex(actual),state() if enabled else None,m.q('readl 0x150c0018'))))
                if enabled:
                    require(int(m.q('readl 0x150c0210'),0)==0,'Masked view excludes retained input')
                    write(0x150c020c,0x20000000)
                    require(int(m.q('readl 0x150c0210'),0)==0x20000000,'Unmask exposes retained input')
                    require(state()['notifications']==1,'Pending notifications coalesce')
                    advance();require(state()['notifications']==1,'No duplicate owned cause')
                    write(0x150c0210,0x20000000);advance()
                    require(state()['notifications']==2 and state()['acks']==1,'Fresh input after original W1C')
                    write(0x1508002c,1);write(0x150c0210,0x20000000);advance()
                    require(pending()==0,'No input while timing stopped')
                    require(state()['pixel_dma_bytes']==0,'Input boundary is not an output transfer')
                pipeline=json.loads(m.qmp.command('qom-get',path='/machine',property='raw-pipeline-state'))
                require(pipeline['frames']==0 and pipeline['bytes']==0,'No fabricated image output')
                require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected access')
                cases.append(dict(enabled=enabled,passed=True,output_frames=0))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),encoding_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
