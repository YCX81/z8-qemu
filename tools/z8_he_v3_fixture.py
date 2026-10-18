# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay one caller-supplied, hash-pinned real HE sample through exact v3 hooks.

The JPEG header is authored from a small subset of the sample metadata. DCF,
writer, allocator and JPEG services are models. This is not native QEMU capture
or a sensor/codec implementation. Files stay in a new caller-selected directory;
decode them externally before making any pixel-preservation claim.
"""
import argparse,hashlib,json,struct,sys
from pathlib import Path,PureWindowsPath
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from z8_he_v3_audit import V3Harness,load_prebuilt,closed,inspect_nef
from z8_he_history_audit import tiff,PAYLOAD,need,digest

P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--sample',type=Path,required=True)
P.add_argument('--stock-main',type=Path,required=True)
P.add_argument('--toolkit',type=Path,required=True)
P.add_argument('--output',type=Path,required=True)
A=P.parse_args()
need(A.sample.stat().st_size==24384512,'Sample size differs from raw.pixls.us record 6616')
need(A.stock_main.stat().st_size==81807709,'Stock size differs')
A.output.mkdir(parents=True,exist_ok=False)
data=A.sample.read_bytes()
need(digest(data)=='82df041542b8d328738443bb5ff3373b396e406ecc8cbe7dc3b35252790c53e7','Unreviewed sample')
def ifd(b,at):
    entries={};n=struct.unpack_from('<H',b,at)[0]
    for i in range(n):
        pos=at+2+i*12;tag,kind,count,off=struct.unpack_from('<HHII',b,pos)
        size={1:1,2:1,3:2,4:4,5:8,7:1,9:4,10:8}[kind]*count
        entries[tag]=(kind,count,b[pos+8:pos+8+size] if size<=4 else b[off:off+size])
    return entries
u32=lambda x:struct.unpack('<I',x)[0]
top=ifd(data,u32(data[4:8]));exif=ifd(data,u32(top[0x8769][2]));mn=exif[0x927c][2][10:]
maker=ifd(mn,u32(mn[4:8]));preview=ifd(mn,u32(maker[0x11][2]))
subs=[ifd(data,o) for o in struct.unpack('<6I',top[0x14a][2])]
def jpeg(b,d):return b[u32(d[0x201][2]):u32(d[0x201][2])+u32(d[0x202][2])]
streams=[jpeg(data,subs[0]),jpeg(mn,preview),jpeg(data,subs[2])]
thumb=jpeg(data,top);payload=data[u32(subs[1][0x111][2]):u32(subs[1][0x111][2])+PAYLOAD]
need(len(payload)==PAYLOAD,'Payload size')
for b in streams+[thumb]:need(b[:2]==b'\xff\xd8' and b[-2:]==b'\xff\xd9','JPEG markers')

def header():
    mn2=b'Nikon\0\x02\x11\0\0'+tiff([(0xc,*maker[0xc]),(0x16,3,4,struct.pack('<4H',0,0,8256,5504)),(0x54,*maker[0x54])])
    out=bytearray(b'II*\0\x08\0\0\0')
    def reserve(n):
        at=len(out);out.extend(bytes(6+n*12));struct.pack_into('<H',out,at,n);return at
    i0=reserve(3);ix=reserve(1);it=reserve(2)
    def write(at,items,next_=0):
        for i,(tag,kind,count,value) in enumerate(items):
            p=at+2+i*12;struct.pack_into('<HHI',out,p,tag,kind,count)
            if len(value)<=4:out[p+8:p+12]=value.ljust(4,b'\0')
            else:
                if len(out)&1:out.append(0)
                struct.pack_into('<I',out,p+8,len(out));out.extend(value)
        struct.pack_into('<I',out,at+2+len(items)*12,next_)
    write(i0,[(0x10f,*top[0x10f]),(0x110,*top[0x110]),(0x8769,4,1,struct.pack('<I',ix))],it)
    write(ix,[(0x927c,7,len(mn2),mn2)])
    if len(out)&1:out.append(0)
    ta=len(out);out.extend(thumb)
    write(it,[(0x201,4,1,struct.pack('<I',ta)),(0x202,4,1,struct.pack('<I',len(thumb)))])
    ex=b'\xff\xd8\xff\xe1'+struct.pack('>H',len(out)+8)+b'Exif\0\0'+out
    mp=bytearray(48);mpf=tiff([(0xb002,7,48,mp)])
    total=len(ex)+8+len(mpf)
    for i,b in enumerate(streams):struct.pack_into('<I',mp,i*16+4,len(b)+(total-2 if i==0 else 0))
    mpf=tiff([(0xb002,7,48,mp)])
    return ex+b'\xff\xe2'+struct.pack('>H',len(mpf)+6)+b'MPF\0'+mpf

class RealHarness(V3Harness):
    def frame(self,**kw):
        f=super().frame(**kw)
        self.uc.mem_write(f['payload'],payload)
        self.uc.mem_write(f['m']+0x2b4,maker[0x3d][2])
        f['ybase']=0xb20000000;self.map(f['ybase'],0x800000)
        hp=0xb30000000;h=header();self.map(hp,len(h));self.uc.mem_write(hp,h)
        lp=self.get64(f['j']+0x18);self.put64(lp,hp);self.put32(lp+8,len(h));self.put32(lp+12,len(h))
        return f
    def allocate_y(self,f,size):
        need(size==0x800000,'Unexpected Y size')
        f['ybytes']=size;y=f['y'];b=f['ybase'];need(y not in self.live_y,'Already live Y')
        self.put64(y+8,f['yback']);self.put64(f['yback'],b);self.put32(f['yback']+8,size)
        self.put64(f['r']+0x6a8,y);da=self.get64(f['j']+0x10);at=b
        for i,((desc,cap),raw) in enumerate(zip(((0x38,0x30),(0xa0,0x98),(0xc0,0xb8),(0xe0,0xd8)),streams+[b'\xff\xd8\xff\xd9'])):
            capacity=(len(raw)+127)&~127
            self.put32(y+cap,capacity);self.put64(y+desc+8,at);self.put32(y+desc+16,len(raw));self.uc.mem_write(at,raw)
            if i<3:self.put64(da+i*8,y+desc)
            at+=capacity
        need(at<=b+size,'Y overflow');self.uc.mem_write(y+0x3c,struct.pack('<HH',8256,5504));self.live_y.add(y)
    def service(self,uc,address,size,name):
        if name=='y_size':
            uc.reg_write(self.a.UC_ARM64_REG_X0,0x800000)
            uc.reg_write(self.a.UC_ARM64_REG_PC,uc.reg_read(self.a.UC_ARM64_REG_LR));return
        super().service(uc,address,size,name)
    def dma(self,addr,size):
        for f in self.frames:
            if f['ybase']<=addr<f['ybase']+0x800000:
                need(f['y'] in self.live_y and addr+size<=f['ybase']+f['ybytes'],'Y DMA lifetime')
        return super().dma(addr,size)
    def output(self,name,spans):
        row=super().output(name,spans)
        with (self.folder/PureWindowsPath(name).name).open('xb') as s:
            for address,size in spans:s.write(self.dma(address,size))
        return row
    def stock_write(self,op,mode,j):
        if mode==0:
            need(op==0,'Writer op');f=self.find_frame(jid=self.get32(j));need(j==f['j'],'JPEG job')
            self.stock_calls.append(dict(mode=mode,jid=f['shot']))
            self.output(f['name'],[(f['ybase'],len(streams[0]))]);self.database[f['shot']]['state']=4;return 0
        return super().stock_write(op,mode,j)

code,symbols,profile=load_prebuilt(A.toolkit)
stock=A.stock_main.read_bytes()
need(digest(stock)=='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853','Stock hash')
rows=[]
for kind in (0,1):
    h=RealHarness(code,symbols,stock);h.folder=A.output/f'type{kind}';h.folder.mkdir()
    h.menu(kind,2,0);f=h.frame();h.mark(f);h.p2(f);h.pipeline(f);status=h.p3(f)
    need(status==0,'Write failure');h.cleanup_contract(f);closed(h)
    name=f['name'][:-3]+'NEF';need(name in h.files,'NEF missing')
    checks=inspect_nef(h,name,h.files[name])
    rows.append(dict(kind=kind,status=status,nef=checks,summary=h.summary(),
                     files=[dict(name=p.name,bytes=p.stat().st_size,sha256=digest(p.read_bytes())) for p in h.folder.iterdir()]))
report=dict(scope='Exact hooks; real public HE payload and JPEGs; authored metadata header; modeled DCF/writer/JPEG services; no camera or full QEMU',
     source_url='https://raw.pixls.us/',source_record=6616,source_license='CC0 as listed by source',
     source_sha256=digest(data),code_sha256=digest(code),stock_sha256=digest(stock),
     tool_sha256=digest(Path(__file__).read_bytes()),payload_sha256=digest(payload),cases=rows)
(A.output/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
