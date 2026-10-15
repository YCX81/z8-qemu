# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent pixel expectations for bounded Z8 secondary quarter turns."""
import argparse
import json
from pathlib import Path
import tempfile
from check_z8_machine import sha256
from test_z8_persistent_media import Machine

SOURCE, COLOR, ALPHA = 0xa00100000, 0xa00200000, 0xa00300000


def local(address):return 0x40000000 + address - 0xa00000000


def run(binary, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    scenarios=['clockwise','counterclockwise','disabled-readback-clockwise','planar-clockwise','planar-counterclockwise',
               'over-clockwise','over-planar-clockwise','bad_inverse','bad_blend',
               'bad_extent','overlap','partial']
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for name in scenarios:
            with Machine(binary,root/name,kernel,properties=('bus-window-fixture=on',
                         'graphics-image-model=on','z8-secondary-resample-model=on')) as m:
                def read(addr,n):return bytes.fromhex(m.q(f'read {addr:#x} {n:#x}')[2:])
                def write(addr,b):m.q(f'write {addr:#x} {len(b):#x} 0x{b.hex()}')
                def word(port,v):m.q(f'writel {port:#x} {v&0xffffffff:#x}')
                def packet(reg,values):
                    word(0x15540000,0x48000000|(reg<<12)|len(values))
                    for v in values:word(0x15540000,v)
                def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='graphics-state'))
                m.q('writeq 0x140d0050 0x200000000')
                planar='planar-' in name;color_bytes=8 if planar else 16
                over=name.startswith('over-') or name=='bad_inverse'
                shades=[10,40,70,100,130,160,190,220]
                alphas=[0,128,255,0,128,255,0,128] if over else [255]*8
                src=b''.join(bytes((v,v,v,a)) for v,a in zip(shades,alphas))
                initial_color=bytes([80]*8) if planar else bytes((80,80,128,128))*4
                initial_alpha=bytes((128 if planar else 255,))*8
                write(SOURCE,src);write(COLOR-16,b'G'*16+initial_color+b'H'*16)
                write(ALPHA-16,b'I'*16+initial_alpha+b'J'*16)
                for reg,value in ((0x21,4<<16|(0x71 if planar else 0x72)),(0x4b3,4<<16|2),
                    (0x4c3,0),(0x4c4,1<<16|3),(0x4b4,3),(0x4b7,0),
                    (0x4b2,local(SOURCE)),(0x20,local(SOURCE if name=='overlap' else COLOR)),
                    (0x2a,local(ALPHA)),(0x2b,0xffffffff if not over and (planar or name.startswith('disabled-')) else local(ALPHA)),(0x900,0x4e4e0002),(0x200,0)):
                    packet(reg,[value])
                # Q10 Y=R/2+G/2, neutral chroma. Grayscale expected Y is exact.
                packet(0x901,[512,512,0,0,0,0,0,0,0])
                if name!='bad_inverse':packet(0x911,[256,0,0,256,0,0,256,0,0])
                coeff=[0,0x81100,0,0x81100,0,0x81100,1,0x80100] if over else [0,0x10000,0,0x10000,0,0x10000,0,0]
                if name=='bad_blend':coeff[1]=0x12345
                packet(0x780,coeff)
                if name=='bad_blend':
                    if not state()['fault']:raise AssertionError('Invalid blend accepted')
                    if read(COLOR,color_bytes)!=initial_color or read(ALPHA,8)!=initial_alpha:
                        raise AssertionError('Invalid blend changed target')
                    cases.append(dict(name=name,passed=True));continue
                if state()['fault']:raise AssertionError('Synthetic setup failed: '+name)
                extent=1<<16|3
                word(0x15540060,0x4b000);word(0x15540060,extent)
                geometry=[0,extent,0,3<<16|1,2048,0,0,3*2048,0,-2048]
                expected=[190,130,70,10,220,160,100,40]
                if over:
                    order=[6,4,2,0,7,5,3,1]
                    expected=[(shades[i]*alphas[i]+80*(255-alphas[i])+127)//255 for i in order]
                if name.endswith('counterclockwise'):
                    geometry[4]=-2048;geometry[6]=2048;geometry[7]=0;geometry[9]=2048
                    expected=[40,100,160,220,10,70,130,190]
                if name=='bad_extent':geometry[6]=90000
                before=state();word(0x15540060,0x2000)
                for value in geometry[:9 if name=='partial' else 10]:word(0x15540060,value)
                after=state()
                good='clockwise' in name
                if bool(after['secondary']['fault']) != (name in ('bad_extent','overlap','bad_inverse','bad_blend')):raise AssertionError(name)
                if after['pending']!=before['pending']:raise AssertionError('Unexpected completion')
                if good:
                    packed=bytes(expected) if planar else b''.join(bytes((expected[i],expected[i+1],128,128)) for i in range(0,8,2))
                    if read(COLOR-16,color_bytes+32)!=b'G'*16+packed+b'H'*16:raise AssertionError('Rotated pixels/guards')
                    if read(ALPHA-16,40)!=b'I'*16+bytes((128 if planar else 255,))*8+b'J'*16:raise AssertionError('UV/alpha/guards')
                elif read(COLOR-16,48)!=b'G'*16+initial_color+b'H'*16 or read(ALPHA-16,40)!=b'I'*16+initial_alpha+b'J'*16:
                    raise AssertionError('Invalid/incomplete operation changed target')
                if read(SOURCE,len(src))!=src:raise AssertionError('Source changed')
                cases.append(dict(name=name,passed=True))
    result=dict(classification='synthetic_secondary_rotation_pixels',qemu_sha256=sha256(Path(binary)),cases=cases,physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(f'PASS {len(cases)} secondary rotation pixel checks')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
