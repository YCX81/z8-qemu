# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed A1/A2 post-recording NV12/NV16 RAM scaling, no firmware."""
import argparse
import json
import tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_video_ring_stop import Lab, require


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root = Path(tmp); kernel = root / 'zero.bin'; kernel.write_bytes(bytes(4096))
        for case in ('off', 'pixels', 'input-format', 'output-overlap', 'input-overlap', 'shape', 'a2-pixels'):
            props = ('image-bank-model=on', 'z8-yuv-data-model=on')
            if case != 'off': props += ('z8-yuv-single-model=on',)
            with Lab(binary, root / case, kernel, accelerator='qtest', properties=props) as m:
                def w(a, v): m.q(f'writel {a:#x} {v:#x}')
                def pair(a, v): w(a, v & 0xffffffff); w(a + 4, v >> 32)
                def state(): return json.loads(m.qmp.command('qom-get', path='/machine', property='z8-yuv-state'))
                second=case.startswith('a2-')
                width, height = 48, 24
                source = 0x840100000
                y = bytes((r * 13 + x * 7) & 255 for r in range(height) for x in range(width))
                uv = bytes((r * 17 + x * 3 + 29) & 255 for r in range(height // (1 if second else 2)) for x in range(width))
                m.q(f'write {source:#x} {len(y+uv):#x} 0x{(y+uv).hex()}')
                a = 0x15421200 if second else 0x15421100
                request=0x1001 if second else 0x3801
                for off, value in ((4,request+1),(8,0x10000000 if second else 0x78000000),(0xc,0x71000010 if second else 0x31000000),(0x10,1),
                                   (0x18,0x11111),(0x38,width),(0x3c,width),(0x4c,width),(0x50,height)):
                    w(a+off,value)
                pair(a+0x20,source); pair(a+0x28,source+len(y))
                expected = []
                for lane, factor in (((4,4),) if second else ((2,2),(3,1),(4,6))):
                    b, f = 0x15422000+lane*0x100, 0x15423000+lane*0x100
                    ow, oh = width//factor, height//factor
                    pitch=ow+16 if second else ow
                    for off, value in ((0xc,0x41000010),(0x10,1),(0x38,pitch),(0x3c,pitch)):
                        w(b+off,value)
                    for off, value in ((0x18,width),(0x1c,height),(0x2c,ow),(0x30,oh)):
                        w(f+off,value)
                    dst = 0x840200000+lane*0x10000
                    pair(b+0x20,dst); pair(b+0x28,dst+0x1000)
                    for plane in range(2):
                        actual_expected = bytes(
                            (y if plane==0 else uv)[
                                (r*factor if plane==0 or second else r*factor//2)*width
                                + (x*factor if plane==0 else (x//2)*factor*2+x%2)]
                            for r in range(oh) for x in range(ow))
                        if second:
                            actual_expected=b''.join(actual_expected[r*ow:(r+1)*ow]+bytes([85])*(pitch-ow) for r in range(oh))
                        m.q(f'memset {dst+plane*0x1000:#x} {pitch*oh:#x} 0x55')
                        expected.append((dst+plane*0x1000,actual_expected))
                if case=='input-format': w(a+0xc,0x31000010)
                if case=='output-overlap': pair(0x15422320,0x840220000)
                if case=='input-overlap': pair(0x15422320,source)
                if case=='shape': w(0x15423430,6)
                require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Supported configuration writes')
                before = [m.q(f'read {addr:#x} {len(data):#x}') for addr,data in expected]
                w(a+4,request)
                if case.endswith('pixels'):
                    require(state()['single_jobs']==1,'Actual A1 job completed')
                    for addr,data in expected:
                        require(m.q(f'read {addr:#x} {len(data):#x}')=='0x'+data.hex(), 'Exact scaled pixels from RAM')
                    require(int(m.q(f'readl {a+0x1c:#x}'),0)==0x10,'A1 DATA bit4 only after writes')
                    w(a+4,request)
                    require(state()['single_jobs']==1 and int(m.q(f'readl {a+0x1c:#x}'),0)==0x10,
                            'Unretired completion cannot be overwritten')
                    w(a+0x1c,0x10);m.qmp.command('cont');m.qmp.command('stop')
                    w(a+4,request);require(state()['single_jobs']==2,'Fresh command after ACK can read RAM again')
                else:
                    require(int(m.q(f'readl {a+0x1c:#x}'),0)==0,'Rejected job cannot publish completion')
                    require(before==[m.q(f'read {addr:#x} {len(data):#x}') for addr,data in expected],
                            'All destinations unchanged on preflight rejection')
                    require(m.q(f'read {source:#x} {len(y+uv):#x}')=='0x'+(y+uv).hex(),'Input remains unchanged')
                    require('Z9_UNKNOWN' in m.log_path.read_text(),'Unsupported command remains visible')
                (output/(case+'.log')).write_text(m.log_path.read_text())
                cases.append(dict(name=case,passed=True,state=state()))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),firmware_execution=False,
                algorithm='diagnostic nearest-neighbour NV12/NV16 scaling, not Nikon filtering')
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
