# SPDX-License-Identifier: GPL-2.0-or-later
"""Native recording geometry: independently check every transported RAW sample."""
import argparse,json,struct,tempfile,zlib,time
from pathlib import Path
import numpy as np
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab,require
from test_z8_video_data import configure,PACKET,OPTION,STATE


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    width,height,capacity=8280,4688,19407872
    pitch=(((width*14+7)//8+31)//32)*32
    with tempfile.TemporaryDirectory(dir=output) as t:
        root=Path(t);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        payload=generate(64,32);raw=root/'source.raw';raw.write_bytes(payload)
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model','engine150c-stop-model',
            'config150d-model','engine150d-control-model','statistics-model','engine1543-control-model',
            'engine151-control-model','raw-pipeline-model','z8-video-interface-model',
            'event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000',OPTION+'=on')
        with Lab(binary,root/'full-raw',kernel,accelerator='qtest',properties=props) as m:
            m.sock.settimeout(30);m.qmp.deadline=time.monotonic()+60;m.observe_irq()
            def qmp_timeout():
                remaining=m.qmp.deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Full RAW audit exceeded its 60-second deadline')
                m.qmp.socket.settimeout(min(30,remaining))
            m.qmp.timeout=qmp_timeout
            def w(a,v):m.q(f'writel {a:#x} {v:#x}')
            def state():return json.loads(m.qmp.command('qom-get',path='/machine',property=STATE))
            def advance():m.qmp.command('cont');m.q('clock_step 70000000');m.qmp.command('stop')
            configure(m,width,height,capacity)
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'Native 8280x4688 recording request rejected')
            w(0x15080010,1);w(0x15080028,1);advance()
            require(state()['packets']==1,'One bounded full-resolution packet')
            dump=root/'packet.bin'
            # The full packet is about 19 MB; a mounted workspace may need
            # longer than the generic one-second QMP socket receive timeout.
            m.qmp.command('human-monitor-command',**{'command-line':f'pmemsave {PACKET:#x} {capacity:#x} '+json.dumps(str(dump))})
            packet=dump.read_bytes();size,crc=struct.unpack_from('<II',packet,48)
            packed=zlib.decompress(packet[64:64+size])
            require(len(packed)==pitch*height and zlib.crc32(packed)==crc,'Independent whole-packet decode and checksum')
            encoded=np.frombuffer(packed,dtype=np.uint8).reshape(height,pitch)
            require(not np.any(encoded[:,width*14//8:]),'All row padding remains zero')
            groups=encoded[:,:width*14//8].reshape(height,width//4,7).astype(np.uint16)
            actual=np.empty((height,width),dtype=np.uint16)
            actual[:,0::4]=groups[:,:,0]|((groups[:,:,1]&63)<<8)
            actual[:,1::4]=(groups[:,:,1]>>6)|(groups[:,:,2]<<2)|((groups[:,:,3]&15)<<10)
            actual[:,2::4]=(groups[:,:,3]>>4)|(groups[:,:,4]<<4)|((groups[:,:,5]&3)<<12)
            actual[:,3::4]=(groups[:,:,5]>>2)|(groups[:,:,6]<<6)
            source=np.frombuffer(payload,dtype='<u2',offset=32).reshape(32,64)
            xs=(np.arange(width)//2)*32//(width//2)*2+np.arange(width)%2
            ys=(np.arange(height)//2)*16//(height//2)*2+np.arange(height)%2
            expected=((source[np.ix_(ys,xs)].astype(np.uint32)*16383+4095//2)//4095).astype(np.uint16)
            mismatches=int(np.count_nonzero(actual!=expected))
            require(mismatches==0,'Full-resolution sample mismatch')
            del groups,actual,expected,packed,packet
            w(0x15080048,0x2000);w(0x152c001c,3);w(0x15300404,0);advance()
            require(state()['decoded']==1 and state()['frame_ready'],'Full-resolution packet must reach the decoder')
            require('Z9_UNKNOWN' not in m.log_path.read_text(),'Unexpected access')
            result=dict(passed=True,qemu_sha256=sha256(binary),cases=[dict(name='full-recording-raw',passed=True)],
                width=width,height=height,pitch=pitch,samples=width*height,mismatches=mismatches,state=state(),
                source='declared 64x32 synthetic Bayer expanded by CFA cells; not captured sensor data',recording_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
