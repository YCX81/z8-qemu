# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free scanout checks for full-height menu UV and NV12 backgrounds."""
import argparse
import json
from pathlib import Path
import tempfile
from PIL import Image
from check_z8_machine import sha256
from test_z8_persistent_media import Machine

COLOR,ALPHA,LUMA,UV=0xa00100000,0xa00200000,0xa00300000,0xa00400000
BASE=0x15484000


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    names=('nv16-fullheight','nv16-alpha','nv12-row-reuse','nv12-alpha',
           'disabled-cache','unsupported-format','nv16-short-uv','nv12-odd-height','nv16-odd-height')
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for name in names:
            with Machine(binary,root/name,kernel,accelerator='qtest',properties=('engine1548-config-model=on',
                         'engine1548-control-model=on','engine1548-scanout-model=on')) as m:
                def word(off,value):m.q(f'writel {BASE+off:#x} {value:#x}')
                def address(off,value):
                    bus=value+0x400000000;word(off,bus&0xffffffff);word(off+4,bus>>32)
                def write(at,value):m.q(f'write {at:#x} {len(value):#x} 0x{value.hex()}')
                width=4;height=3 if name.endswith('odd-height') else 2;count=width*height
                nv12=name.startswith('nv12');mix=name.endswith('alpha')
                alpha=bytes(([0,64,128,255] if mix else [0]*4)*height)
                colors=bytes((200,200,128,128))*(count//2)
                y=bytes(range(40,40+count*10,10))
                # Each full-height UV row is distinct. Reusing NV12's y/2
                # indexing for a menu cache must fail on the second row.
                uv=bytes((128,128,128,128,128,160,160,128,112,128,128,112))[:count]
                if nv12:uv=uv[:width*((height+1)//2)]
                uv_at=0xa1ffffffc if name=='nv16-short-uv' else UV
                write(COLOR,colors);write(ALPHA,alpha);write(LUMA,y)
                write(uv_at,uv[:4] if name=='nv16-short-uv' else uv)
                word(0x2400,(height<<16)|width);address(0x2440,COLOR);address(0x2500,ALPHA)
                word(0x2580,width*2);word(0x25c0,width);word(0x2640,1)
                for offset,value in ((0x2864,0x5a0040),(0x2868,0xd2ea40),(0x286c,0x007140)):
                    word(offset,value)
                word(0x200,0x201 if name=='unsupported-format' else 0x204 if nv12 else 0x200)
                word(0x208,(height<<16)|width);word(0x250,width);word(0x254,width)
                address(0x210,LUMA);address(0x230,uv_at)
                if name!='disabled-cache':word(0x100,1)
                word(0x2100,1);word(0x1200,1)
                # QTest acceleration executes no CPU instructions; running
                # state lets the virtual timer callbacks retire.
                m.qmp.command('cont')
                m.q('clock_step 100000000')
                m.qmp.command('stop')
                state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine1548-scanout-state'))
                if not state['frames']:
                    raise AssertionError(dict(scanout=state,
                        sources=m.qmp.command('qom-get',path='/machine',property='engine1548-source-state'),
                        engine=m.qmp.command('qom-get',path='/machine',property='engine1548-state'),
                        log=m.log_path.read_text(errors='replace')[-2500:]))
                path=root/name/'frame.ppm';m.qmp.command('screendump',filename=str(path))
                with Image.open(path) as image:raw=image.convert('RGB').tobytes()
                actual=[tuple(raw[i:i+3]) for i in range(0,len(raw),3)]
                enabled=name not in ('disabled-cache','unsupported-format','nv16-short-uv','nv12-odd-height')
                expected=[]
                for row in range(height):
                    for x in range(width):
                        at=row*width+x
                        if enabled:
                            pos=(row//2 if nv12 else row)*width+(x&~1)
                            u,v=uv[pos]-128,uv[pos+1]-128
                            clamp=lambda z:max(0,min(255,z))
                            bg=(clamp(y[at]+((359*v+128)>>8)),
                                clamp(y[at]-((88*u+183*v+128)>>8)),
                                clamp(y[at]+((454*u+128)>>8)))
                        else:bg=(0,0,0)
                        expected.append(tuple((200*alpha[at]+value*(255-alpha[at])+127)//255 for value in bg))
                if actual!=expected:raise AssertionError((name,actual,expected))
                if state['last_frame_has_video']!=(enabled and nv12):raise AssertionError('Menu cache mislabeled as video')
                wanted='NV12' if enabled and nv12 else 'NV16' if enabled else 'none'
                if state['background_format']!=wanted:raise AssertionError('Background identity')
                if bool(state['menu_cache_frames'])!=(enabled and not nv12):raise AssertionError('Cache frame accounting')
                for at,data in ((COLOR,colors),(ALPHA,alpha),(LUMA,y),(uv_at,uv[:4] if name=='nv16-short-uv' else uv)):
                    if bytes.fromhex(m.q(f'read {at:#x} {len(data):#x}')[2:])!=data:raise AssertionError('Scanout changed source')
                if 'Z9_UNKNOWN' in m.log_path.read_text(errors='replace'):raise AssertionError('Unexpected MMIO')
                cases.append(dict(name=name,passed=True,background_format=state.get('background_format')))
    result=dict(classification='synthetic_native_menu_cache_scanout',qemu_sha256=sha256(Path(binary)),cases=cases,physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(f'PASS {len(cases)} menu cache scanout pixel checks')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.qemu,args.output)
