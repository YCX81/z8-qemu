# SPDX-License-Identifier: GPL-2.0-or-later
"""A proven stream STOP owns a fresh END after old data causes retire."""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_z8_video_c1 import configure
from test_z8_video_ring_stop import Lab,require


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(dir=output) as directory:
        root=Path(directory);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        raw=root/'input.raw';raw.write_bytes(struct.pack('<8s6I',b'NZ9RAW1\0',64,36,14,0,0,16383)+bytes(64*36*2))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model','z8-video-interface-model','z8-video-c1-model',
            'event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        with Lab(binary,root/'stop',kernel,accelerator='qtest',properties=props) as m:
            m.observe_irq()
            def w(a,v):m.q(f'writel {a:#x} {v:#x}')
            def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-c1-state'))
            def advance():m.qmp.command('cont');m.q('clock_step 50000000');m.qmp.command('stop')
            w(0x152c0008,0);w(0x15300008,0)
            require(state()['stop_ends']==[0,0],'inactive STOP cannot fabricate END')
            configure(m);w(0x15300008,1);advance()
            require(state()['packets']==1 and state()['decoded']==1,'actual stream data before STOP')
            w(0x152c0008,0)
            require(state()['stop_ends']==[0,0],'old data cause must retire before a new END')
            w(0x152c001c,3)
            require(state()['receiver_acks']==1 and state()['stop_ends']==[1,0] and
                    state()['stop_acks']==[0,0],'data ACK produces fresh owned END')
            w(0x152c001c,4)
            require(state()['stop_acks']==[1,0] and state()['receiver_pending']==0,'END has independent original ACK')
            w(0x152c0008,0)
            require(state()['stop_ends']==[1,0],'duplicate STOP not another END')
            w(0x1530001c,3);w(0x15300008,0)
            require(state()['stop_ends']==[1,1],'decoder retains its own proven stream lifetime')
            w(0x1530001c,4);advance()
            require(state()['stop_acks']==[1,1] and state()['packets']==1 and state()['output_frames']==0,
                    'stopping never generates pixels or a new input')
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'unmodeled access')
            result=dict(passed=True,state=state(),camera_recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
