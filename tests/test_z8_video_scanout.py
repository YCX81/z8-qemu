# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded Z8 direct viewport fetch with an independently optional OSD layer."""
import argparse,json,tempfile
from pathlib import Path
from PIL import Image
from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root=Path(tmp);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for name,nv16,overlay,enabled,offset,valid in (
            ('nv12 direct only viewport',False,False,True,(2,2),True),
            ('nv16 direct only viewport',True,False,True,(2,2),True),
            ('nv12 viewport with OSD',False,True,True,(2,2),True),
            ('alpha before color',False,True,True,(2,2),True),
            ('invalid active overlay',False,True,True,(2,2),False),
            ('viewport requires explicit model',False,False,True,(2,2),False),
            ('no enabled source',False,False,False,(2,2),False),
            ('out of bounds viewport',False,False,True,(6,2),False)):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',
                         properties=('engine1548-config-model=on','engine1548-control-model=on',
                                     'engine1548-scanout-model=on')+
                                    (() if name=='viewport requires explicit model' else ('z8-video-interface-model=on',))) as m:
                def word(off,v):m.q(f'writel {0x15484000+off:#x} {v:#x}')
                def address(off,v):
                    bus=v+0x400000000;word(off,bus&0xffffffff);word(off+4,bus>>32)
                def write(at,b):m.q(f'write {at:#x} {len(b):#x} 0x{b.hex()}')
                w,h,vw,vh=8,6,4,2;x0,y0=offset
                color,alpha,yp,uvp=0xa00100000,0xa00200000,0xa00300000,0xa00400000
                if name=='alpha before color':alpha=0xa00080000
                yyuv=bytes((200,200,128,128))*(w*h//2);aa=bytes([128])*(w*h)
                luma=bytes((40,60,80,100,120,140,160,180))
                uv=bytes((128,128,128,160,160,128,128,128))[:vw*vh if nv16 else vw]
                for at,b in ((color,yyuv),(alpha,aa),(yp,luma),(uvp,uv)):write(at,b)
                word(0x2400,(h<<16)|w)
                # Disabled OSD must not dereference invalid/stale addresses.
                address(0x2440,color if overlay and name!='invalid active overlay' else 0);address(0x2500,alpha if overlay else 0)
                word(0x2580,w*2);word(0x25c0,w);word(0x2640,int(overlay))
                for off,v in ((0x2864,0x5a0040),(0x2868,0xd2ea40),(0x286c,0x007140)):word(off,v)
                word(0x200,0x200 if nv16 else 0x204);word(0x208,vh<<16|vw)
                word(0x250,vw);word(0x254,vw);word(0x800,y0<<16|x0)
                address(0x210,yp);address(0x230,uvp)
                if enabled:word(0x100,1)
                word(0x2100,1);word(0x1200,1)
                m.qmp.command('cont');m.q('clock_step 100000000');m.qmp.command('stop')
                state=json.loads(m.qmp.command('qom-get',path='/machine',property='engine1548-scanout-state'))
                if bool(state['frames'])!=valid:raise AssertionError((name,state))
                if valid:
                    path=root/name.replace(' ','-')/'frame.ppm';m.qmp.command('screendump',filename=str(path))
                    with Image.open(path) as im:actual=list(im.convert('RGB').getdata())
                    expected=[]
                    for y in range(h):
                        for x in range(w):
                            bg=(0,0,0)
                            if x0<=x<x0+vw and y0<=y<y0+vh:
                                sx,sy=x-x0,y-y0;i=(sy if nv16 else sy//2)*vw+(sx&~1)
                                yy=luma[sy*vw+sx];u,v=uv[i]-128,uv[i+1]-128
                                clamp=lambda n:max(0,min(255,n))
                                bg=(clamp(yy+((359*v+128)>>8)),clamp(yy-((88*u+183*v+128)>>8)),clamp(yy+((454*u+128)>>8)))
                            expected.append(tuple((200*128+p*127+127)//255 for p in bg) if overlay else bg)
                    if actual!=expected:raise AssertionError((name,actual,expected))
                    if state['last_frame_has_video']!=(not nv16):raise AssertionError('Cache/video classification')
                if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Unexpected MMIO')
                for at,b in ((color,yyuv),(alpha,aa),(yp,luma),(uvp,uv)):
                    if bytes.fromhex(m.q(f'read {at:#x} {len(b):#x}')[2:])!=b:raise AssertionError('Source mutated')
                cases.append(dict(name=name,passed=True,produced_frame=valid))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
