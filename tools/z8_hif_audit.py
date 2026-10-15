# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent bounded HIF structure/extent audit and full 10-bit grid decode.

Reads the file, never model RAM. No package installation. Windows ffmpeg may
be called from Windows Python or WSL (paths translated with wslpath). No tone
mapping or rotation is applied; output is cropped planar yuv420p10le.
"""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess

class AuditError(ValueError):pass
def need(value,message):
    if not value:raise AuditError(message)
def sha(data):return hashlib.sha256(data).hexdigest()
def file_sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def compare_color(container,stream):
    """Compare the finite HLG tuple with the independent elementary decoder.

    Report differences without rewriting either authority or claiming a full
    HEIF presentation/conformance verdict (container precedence is separate).
    """
    expected=dict(primaries='bt2020',transfer='arib-std-b67',matrix='bt2020nc')
    source=dict(primaries=stream.get('color_primaries'),transfer=stream.get('color_transfer'),
                matrix=stream.get('color_space'),full_range=stream.get('color_range'))
    differences=[k for k,v in dict(primaries=9,transfer=18,matrix=9).items()
                 if container.get(k)!=v or source[k]!=expected[k]]
    if source['full_range']!=('pc' if container.get('full_range') else 'tv'):differences.append('full_range')
    return dict(container_nclx=container,elementary_stream=source,consistent=not differences,differences=differences)

class Reader:
    def __init__(self,data):self.data=data;self.at=0
    def take(self,n):
        need(0<=n<=len(self.data)-self.at,'Truncated field')
        value=self.data[self.at:self.at+n];self.at+=n;return value
    def u(self,n):return int.from_bytes(self.take(n),'big')
    def full(self):return self.u(1),self.u(3)
    def end(self):need(self.at==len(self.data),'Unconsumed structure bytes')

class Bits:
    def __init__(self,data):self.data=data;self.at=0
    def u(self,n):
        need(0<=n<=64 and self.at+n<=len(self.data)*8,'Truncated SPS')
        v=0
        for _ in range(n):v=v*2+((self.data[self.at//8]>>(7-self.at%8))&1);self.at+=1
        return v
    def ue(self):
        n=0
        while not self.u(1):n+=1;need(n<=30,'Unbounded SPS integer')
        return (1<<n)-1+self.u(n)

def nal_type(nal):
    need(len(nal)>=2 and not nal[0]&0x80 and nal[1]&7,'Invalid HEVC NAL header')
    need(not nal[0]&1 and nal[1]==1,'Only layer0 temporal_id_plus1=1 supported')
    return (nal[0]>>1)&63

def sps_info(nal):
    need(nal_type(nal)==33,'Expected SPS')
    rbsp=bytearray();zeros=0
    for i,b in enumerate(nal[2:],2):
        if zeros==2 and b==3:
            need(i+1<len(nal) and nal[i+1]<=3,'Bad emulation prevention');zeros=0;continue
        rbsp.append(b);zeros=zeros+1 if b==0 else 0
    r=Bits(rbsp);r.u(4);layers=r.u(3);nested=r.u(1)
    need(layers<=6,'Unsupported temporal layers')
    info=dict(profile_space=r.u(2),tier=r.u(1),profile_idc=r.u(5),compatibility=r.u(32),constraints=r.u(48),level_idc=r.u(8))
    sub=[(r.u(1),r.u(1)) for _ in range(layers)]
    if layers:
        for _ in range(layers,8):need(r.u(2)==0,'SPS reserved bits')
    for profile,level in sub:
        if profile:r.u(64);r.u(24)
        if level:r.u(8)
    need(r.ue()<=15,'SPS id');chroma=r.ue();need(chroma<=3,'SPS chroma')
    separate=r.u(1) if chroma==3 else 0
    w,h=r.ue(),r.ue();crop=[r.ue() for _ in range(4)] if r.u(1) else [0]*4
    depth_y,depth_c=8+r.ue(),8+r.ue()
    sx=2 if not separate and chroma in (1,2) else 1;sy=2 if not separate and chroma==1 else 1
    vw=w-(crop[0]+crop[1])*sx;vh=h-(crop[2]+crop[3])*sy
    need(0<vw<=w<=16384 and 0<vh<=h<=16384 and w*h<=100000000 and depth_y<=16 and depth_c<=16,'SPS geometry/depth')
    info.update(width=w,height=h,visible_width=vw,visible_height=vh,chroma=chroma,luma_depth=depth_y,
                chroma_depth=depth_c,temporal_layers=layers+1,temporal_nested=nested)
    return info

def parse_hvcc(data):
    need(23<=len(data)<=65536,'hvcC size');r=Reader(data);need(r.u(1)==1,'hvcC version')
    p=r.u(1);compat=r.u(4);constraints=r.u(6);level=r.u(1)
    r.take(3);chroma=r.u(1)&3;dy=8+(r.u(1)&7);dc=8+(r.u(1)&7);r.take(2);length=(r.u(1)&3)+1
    arrays=r.u(1);need(arrays==3 and length==4,'Expected three parameter arrays and four-byte lengths')
    parameters={}
    for _ in range(arrays):
        typ=r.u(1)&63;count=r.u(2)
        need(typ in (32,33,34) and typ not in parameters and count==1,'Parameter array type/count')
        n=r.take(r.u(2));need(nal_type(n)==typ,'hvcC NAL type mismatch');parameters[typ]=n
    r.end();s=sps_info(parameters[33])
    need((p>>6,(p>>5)&1,p&31,compat,constraints,level)==tuple(s[k] for k in ('profile_space','tier','profile_idc','compatibility','constraints','level_idc')),'hvcC PTL disagrees with actual SPS')
    need((chroma,dy,dc)==(s['chroma'],s['luma_depth'],s['chroma_depth'])==(1,10,10),'Expected actual 10-bit 420, not metadata-only conversion')
    return dict(sps=s,parameters=parameters,length_size=length)

def parse_exif(data):
    need(len(data)>=12,'Exif item short');start=4+int.from_bytes(data[:4],'big');t=data[start:]
    need(len(t)>=8 and t[:2] in (b'II',b'MM'),'Exif TIFF offset/order');endian='<' if t[:2]==b'II' else '>'
    def u(at,fmt):
        n=struct.calcsize(fmt);need(0<=at<=len(t)-n,'Exif field range');return struct.unpack_from(endian+fmt,t,at)[0]
    need(u(2,'H')==42,'TIFF magic');queue=[u(4,'I')];seen=set();tags=0;orientation=None
    sizes={1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8,13:4}
    while queue:
        at=queue.pop()
        if not at:continue
        need(at not in seen and len(seen)<32,'Exif IFD cycle/bound');seen.add(at)
        n=u(at,'H');need(n<=4096 and at+2+n*12+4<=len(t),'Exif IFD bounds')
        for i in range(n):
            entry=at+2+i*12;tag=u(entry,'H');typ=u(entry+2,'H');count=u(entry+4,'I')
            need(typ in sizes,'Unknown TIFF type');length=count*sizes[typ];loc=entry+8 if length<=4 else u(entry+8,'I')
            need(loc<=len(t) and length<=len(t)-loc,'Exif value out of bounds');tags+=1
            if tag in (0x8769,0x8825,0xa005):need(typ in (4,13) and count==1,'Exif subIFD pointer');queue.append(u(loc,'I'))
            if tag==0x112:
                need(typ==3 and count==1 and 1<=u(loc,'H')<=8,'Exif orientation');orientation=u(loc,'H')
        queue.append(u(at+2+n*12,'I'))
    return dict(byte_order=t[:2].decode(),ifds=len(seen),entries=tags,orientation=orientation,sha256=sha(data))

class HIF:
    def __init__(self,data):
        need(0<len(data)<=512*1024*1024,'HIF file size bound');self.data=data;self.box_count=0
        self.items={};self.hidden={};self.extents={};self.assoc={};self.refs={};self.props=[];self.codecs={}
        top=self.boxes(0,len(data));meta=self.one(top,b'meta');ftyp=self.one(top,b'ftyp')
        brands=data[ftyp[1]:ftyp[2]];need(len(brands)>=8 and len(brands)%4==0,'ftyp layout')
        self.brands=[brands[:4].decode('ascii')]+[brands[i:i+4].decode('ascii') for i in range(8,len(brands),4)]
        need(any(b in self.brands for b in ('heic','heix')),'Unsupported HEIF brand')
        need(data[meta[1]:meta[1]+4]==bytes(4),'meta fullbox version/flags')
        children=self.boxes(meta[1]+4,meta[2]);self.mdats=[(a,b) for typ,a,b in top if typ==b'mdat']
        idats=[(a,b) for typ,a,b in children if typ==b'idat'];need(len(idats)<=1,'Multiple idat');self.idat=idats[0] if idats else None
        r=Reader(self.body(self.one(children,b'pitm')));v,flags=r.full();need(v in (0,1) and not flags,'pitm version');self.primary=r.u(2 if v==0 else 4);r.end()
        inf=self.one(children,b'iinf');r=Reader(self.body(inf));v,flags=r.full();need(v in (0,1) and not flags,'iinf version');count=r.u(2 if v==0 else 4)
        entries=self.boxes(inf[1]+r.at,inf[2]);need(count==len(entries)<=256,'iinf count')
        for entry in entries:
            need(entry[0]==b'infe','iinf child');r=Reader(self.body(entry));v,flags=r.full();need(v in (2,3),'infe version')
            need(not flags&~1,'infe flags: only hidden bit0 supported')
            ident=r.u(2 if v==2 else 4);need(not r.u(2),'Protected item unsupported');typ=r.take(4).decode('ascii')
            need(ident not in self.items and b'\0' in r.take(len(r.data)-r.at),'Duplicate item or unterminated name');self.items[ident]=typ
            self.hidden[ident]=bool(flags&1)
        loc=self.one(children,b'iloc');self.parse_iloc(self.body(loc))
        for typ,a,b in children:
            if typ==b'iref':
                r=Reader(data[a:b]);v,flags=r.full();need(v in (0,1) and not flags,'iref version');n=2 if v==0 else 4
                for typ,x,y in self.boxes(a+4,b):
                    r=Reader(data[x:y]);source=r.u(n);num=r.u(2);need(num<=256,'Reference count');targets=[r.u(n) for _ in range(num)];r.end()
                    need((typ,source) not in self.refs,'Duplicate reference list');self.refs[typ,source]=targets
        iprp=self.one(children,b'iprp');parts=self.boxes(iprp[1],iprp[2]);ipco=self.one(parts,b'ipco');self.props=self.boxes(ipco[1],ipco[2]);need(len(self.props)<=512,'Property count')
        for typ,a,b in parts:
            if typ!=b'ipma':continue
            r=Reader(data[a:b]);v,flags=r.full();need(v in (0,1) and not flags&~1,'ipma version/flags');count=r.u(4);need(count<=256,'ipma count')
            for _ in range(count):
                ident=r.u(2 if v==0 else 4);need(ident not in self.assoc,'Duplicate associations');self.assoc[ident]=[]
                for _ in range(r.u(1)):
                    value=r.u(2 if flags&1 else 1);mask=0x7fff if flags&1 else 0x7f;index=value&mask
                    if not index:continue
                    need(index<=len(self.props),'Property index range');prop=self.props[index-1]
                    known=prop[0] in (b'ispe',b'pixi',b'hvcC',b'colr',b'irot',b'imir',b'pasp')
                    need(known or not value&~mask,'Unknown essential property');need(prop[0]!=b'clap','Clean aperture needs explicit audit support')
                    self.assoc[ident].append(prop)
            r.end()
        need(self.primary in self.items,'Missing primary item')
        need(not self.hidden[self.primary],'Hidden primary item')
        for (typ,ident),targets in self.refs.items():need(ident in self.items and all(i in self.items for i in targets),'Dangling item reference')
        need(all(i in self.items for i in self.extents) and all(i in self.items for i in self.assoc),'Dangling item metadata')

    def item_roles(self):
        # Hidden describes presentation, not a ban on decoding a grid input.
        # Reference direction/type checks are independent of the hidden flag.
        images={'hvc1','grid','jpeg'};roles={i:set() for i in self.items}
        roles[self.primary].add('primary')
        for (kind,source),targets in self.refs.items():
            need(source not in targets,'Self-referencing item')
            if kind==b'dimg':
                need(self.items[source]=='grid' and targets and all(self.items[t] in images for t in targets),'dimg role/type')
                for t in targets:roles[t].add('derived_input')
            elif kind==b'cdsc':
                need(self.items[source] in ('Exif','mime') and targets and all(self.items[t] in images for t in targets),'cdsc role/type')
                roles[source].add('metadata')
            elif kind in (b'thmb',b'auxl'):
                need(self.items[source] in images and targets and all(self.items[t] in images for t in targets),'Image reference role/type')
                need(source!=self.primary,'Primary cannot be a thumbnail or auxiliary')
                roles[source].add('thumbnail' if kind==b'thmb' else 'auxiliary')
        # Do not invent a requirement that all derived inputs must be hidden,
        # or that a hidden item must have a reference. Report unused roles.
        return {str(i):sorted(r or {'hidden_unreferenced' if self.hidden[i] else 'unclassified'}) for i,r in roles.items()}

    def boxes(self,start,end):
        rows=[];at=start
        while at<end:
            need(end-at>=8,'Truncated box header');size=int.from_bytes(self.data[at:at+4],'big');typ=self.data[at+4:at+8];head=8
            if size==1:need(end-at>=16,'Truncated extended box');size=int.from_bytes(self.data[at+8:at+16],'big');head=16
            elif size==0:size=end-at
            need(head<=size<=end-at,'Box outside parent');rows.append((typ,at+head,at+size));at+=size;self.box_count+=1
            need(self.box_count<=4096,'Box count bound')
        return rows
    @staticmethod
    def one(rows,kind):
        found=[x for x in rows if x[0]==kind];need(len(found)==1,'Expected one '+kind.decode());return found[0]
    def body(self,box):return self.data[box[1]:box[2]]
    def parse_iloc(self,data):
        r=Reader(data);v,flags=r.full();need(v in (0,1,2) and not flags,'iloc version')
        a,b=r.u(1),r.u(1);osize,lsize,bsize,isize=a>>4,a&15,b>>4,(b&15 if v else 0)
        need(all(n<=8 for n in (osize,lsize,bsize,isize)),'iloc field width');count=r.u(4 if v==2 else 2);need(count<=256,'iloc item count')
        total_extents=0
        for _ in range(count):
            ident=r.u(4 if v==2 else 2);method=r.u(2) if v else 0;need(method in (0,1),'Unsupported construction method')
            need(r.u(2)==0,'External data reference');base=r.u(bsize);n=r.u(2);need(0<n<=64 and ident not in self.extents,'Extent count/duplicate')
            total_extents+=n;need(total_extents<=4096,'Total extent bound')
            spans=[]
            for _ in range(n):
                need(r.u(isize)==0,'Indexed extent unsupported');off=base+r.u(osize);length=r.u(lsize);need(length>0,'Implicit extent length unsupported')
                if method==1:need(self.idat is not None,'Missing idat');off+=self.idat[0];ranges=[self.idat]
                else:ranges=self.mdats
                need(any(a<=off and length<=b-off for a,b in ranges),'iloc outside media data')
                spans.append((off,length))
            need(sum(n for _,n in spans)<=64*1024*1024,'Item size bound');self.extents[ident]=spans
        r.end()
        intervals=sorted((a,a+n) for spans in self.extents.values() for a,n in spans)
        previous=None
        for span in intervals:
            if previous is not None and span[0]<previous[1]:need(span==previous,'Partially overlapping media extents unsupported')
            previous=span
    def payload(self,ident):
        need(ident in self.extents,'Item lacks iloc');return b''.join(self.data[a:a+n] for a,n in self.extents[ident])
    def prop(self,ident,kind):return self.body(self.one(self.assoc.get(ident,[]),kind))
    def nclx(self,ident):
        props=[p for p in self.assoc.get(ident,[]) if p[0]==b'colr']
        need(len(props)<=1,'Duplicate colr')
        if not props:return None
        body=self.body(props[0]);need(len(body)>=4,'colr layout')
        if body[:4]!=b'nclx':return None
        need(len(body)==11 and not body[-1]&127,'nclx layout')
        return dict(primaries=int.from_bytes(body[4:6],'big'),transfer=int.from_bytes(body[6:8],'big'),
                    matrix=int.from_bytes(body[8:10],'big'),full_range=bool(body[-1]&128))
    def geometry(self,ident):
        data=self.prop(ident,b'ispe');need(len(data)==12 and data[:4]==bytes(4),'ispe layout');w,h=struct.unpack('>II',data[4:])
        need(0<w<=16384 and 0<h<=16384 and not (w|h)&1 and w*h<=100000000,'Bounded even geometry')
        pixi=[p for p in self.assoc.get(ident,[]) if p[0]==b'pixi']
        if not pixi:
            need(self.hidden[ident] and self.items[ident]=='hvc1','Missing pixi on displayable image')
            # Hidden coded inputs may omit pixi (MIAF pixel-information rule).
            # their actual SPS; neither hidden nor metadata can imply 10 bit.
            parse_hvcc(self.prop(ident,b'hvcC'))
        else:
            need(len(pixi)==1 and self.body(pixi[0])==b'\0\0\0\0\3\x0a\x0a\x0a','pixi must declare three actual 10-bit channels')
        return w,h
    def codec(self,ident):
        if ident not in self.codecs:
            need(self.items.get(ident)=='hvc1','Expected hvc1 leaf');c=parse_hvcc(self.prop(ident,b'hvcC'));w,h=self.geometry(ident)
            need((w,h)==(c['sps']['visible_width'],c['sps']['visible_height']),'ispe disagrees with SPS');self.codecs[ident]=c
        return self.codecs[ident]
    def tile_stream(self,ident):
        c=self.codec(ident);r=Reader(self.payload(ident));nals=[]
        while r.at<len(r.data):
            need(len(nals)<8,'NAL count bound');nal=r.take(r.u(c['length_size']));need(nal_type(nal)==19,'Expected actual IDR_W_RADL type19');nals.append(nal)
        need(len(nals)==1,'Each hvc1 tile must contain one image NAL')
        return b''.join(b'\0\0\0\1'+n for n in [*(c['parameters'][t] for t in (32,33,34)),*nals])
    def plan(self):
        roles=self.item_roles()
        w,h=self.geometry(self.primary)
        presentation={}
        for typ,a,b in self.assoc.get(self.primary,[]):
            body=self.data[a:b]
            if typ in (b'irot',b'imir'):
                mask=3 if typ==b'irot' else 1
                need(len(body)==1 and not body[0]&~mask,'Image transform property')
                need(typ.decode() not in presentation,'Duplicate image transform');presentation[typ.decode()]=body[0]
            elif typ==b'pasp':
                need(len(body)==8,'pasp layout');ratio=struct.unpack('>II',body);need(all(ratio),'pasp zero');presentation['pasp']=ratio
            elif typ==b'colr':
                need(len(body)>=4,'colr layout')
                if body[:4]==b'nclx':
                    need(len(body)==11 and not body[-1]&127,'nclx layout')
                    presentation['nclx']=dict(zip(('primaries','transfer','matrix'),struct.unpack('>3H',body[4:10])))
                    presentation['nclx']['full_range']=bool(body[-1]&128)
                else:need(body[:4] in (b'prof',b'rICC'),'Unsupported colr type');presentation['icc_sha256']=sha(body[4:])
        if self.items[self.primary]=='grid':
            r=Reader(self.payload(self.primary));need(r.u(1)==0,'grid version');flags=r.u(1);need(flags in (0,1),'grid flags');rows,cols=r.u(1)+1,r.u(1)+1
            need((r.u(4 if flags else 2),r.u(4 if flags else 2))==(w,h),'grid dimensions disagree with ispe');r.end()
            tiles=self.refs.get((b'dimg',self.primary),[]);need(len(tiles)==rows*cols<=256,'grid/dimg item count')
        else:need(self.items[self.primary]=='hvc1','Unsupported primary type');rows=cols=1;tiles=[self.primary]
        sizes=[]
        for ident in tiles:
            self.tile_stream(ident);sizes.append(self.geometry(ident))
            if ident!=self.primary:
                for typ,a,b in self.assoc.get(ident,[]):
                    if typ in (b'irot',b'imir'):need(self.data[a:b]==b'\0','Transformed grid tile unsupported')
        need(len(set(sizes))==1,'Unequal tile geometry');tw,th=sizes[0]
        need(sum(self.codec(i)['sps']['width']*self.codec(i)['sps']['height'] for i in tiles)<=100000000,'Total coded pixel budget')
        need((cols-1)*tw<w<=cols*tw and (rows-1)*th<h<=rows*th,'Grid crop outside edge tiles')
        exif=[]
        for ident,typ in self.items.items():
            self.payload(ident) # validate all item extents, not just primary
            if typ=='hvc1':self.tile_stream(ident) # validate auxiliary HEVC metadata too; decode only primary
            if typ=='Exif':
                need(self.primary in self.refs.get((b'cdsc',ident),[]),'Exif must describe primary');exif.append(parse_exif(self.payload(ident)))
        need(exif,'Missing Exif item')
        need(sum(c['sps']['width']*c['sps']['height'] for c in self.codecs.values())<=100000000,
             'Total primary and auxiliary coded pixel budget')
        return dict(primary=self.primary,width=w,height=h,rows=rows,columns=cols,tile_width=tw,tile_height=th,tiles=tiles,exif=exif,brands=self.brands,
                    extents={str(k):v for k,v in self.extents.items()},presentation_metadata=presentation,
                    hidden_items=[i for i in self.items if self.hidden[i]],item_roles=roles,
                    references=[dict(type=k.decode(),source=i,targets=t) for (k,i),t in self.refs.items()])

def audit(path,output,ffmpeg,ffprobe):
    output.mkdir(parents=True,exist_ok=False)
    report=dict(scope='File-only structural and complete coded-pixel audit; no tone mapping/rotation or Nikon ISP claim',
                decoded=False,tiles=[],auxiliary_hevc=[],color_signaling=[],tools={})
    def toolpath(p):
        if os.name!='nt' and ffmpeg.suffix.lower()=='.exe':return subprocess.check_output(['wslpath','-w',str(p.resolve())],text=True,timeout=10).strip()
        return str(p.resolve())
    def run(argv):
        r=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120)
        need(r.returncode==0,'Decoder failed: '+r.stderr[-2000:]);return r.stdout
    try:
        need(path.stat().st_size<=512*1024*1024,'File size bound');data=path.read_bytes();report['input_sha256']=sha(data)
        h=HIF(data);plan=h.plan();report['plan']=plan
        for name,exe in (('ffmpeg',ffmpeg),('ffprobe',ffprobe)):
            report['tools'][name]=dict(path=str(exe.resolve()),sha256=file_sha(exe),version=run([str(exe),'-version']).splitlines()[0])
        w,ht=plan['width'],plan['height'];target=output/'primary.yuv';total=w*ht*3
        with target.open('w+b') as dest:
            dest.truncate(total)
            with mmap.mmap(dest.fileno(),total) as canvas:
                for index,ident in enumerate(plan['tiles']):
                    stream=output/f'tile-{index:03}.h265';stream.write_bytes(h.tile_stream(ident));decoded=output/f'tile-{index:03}.yuv'
                    info=json.loads(run([str(ffprobe),'-v','error','-f','hevc','-i',toolpath(stream),'-show_streams','-of','json']))['streams']
                    tw,th=h.geometry(ident)
                    need(len(info)==1 and info[0]['codec_name']=='hevc' and info[0]['pix_fmt']=='yuv420p10le' and
                         (info[0]['width'],info[0]['height'])==(tw,th),'Independent decoder source format/geometry')
                    run([str(ffmpeg),'-nostdin','-n','-v','error','-xerror','-err_detect','explode','-f','hevc','-i',toolpath(stream),
                         '-frames:v','1','-pix_fmt','yuv420p10le','-f','rawvideo',toolpath(decoded)])
                    need(decoded.stat().st_size==tw*th*3,'Full decoded tile byte count');raw=decoded.read_bytes()
                    col,row=index%plan['columns'],index//plan['columns'];source_base=target_base=0
                    for plane in range(3):
                        scale=1 if plane==0 else 2;pw,ph=w//scale,ht//scale;sw,sh=tw//scale,th//scale
                        x,y=col*sw,row*sh;cw,ch=min(sw,pw-x),min(sh,ph-y)
                        for line in range(ch):
                            a=source_base+line*sw*2;b=target_base+((y+line)*pw+x)*2
                            canvas[b:b+cw*2]=raw[a:a+cw*2]
                        source_base+=sw*sh*2;target_base+=pw*ph*2
                    report['tiles'].append(dict(item=ident,index=index,stream_sha256=file_sha(stream),decoded_sha256=file_sha(decoded),
                        decoded_bytes=len(raw),sps=h.codec(ident)['sps'],ffprobe=info[0]))
                    if h.nclx(ident) is not None:report['color_signaling'].append(dict(item=ident,**compare_color(h.nclx(ident),info[0])))
                canvas.flush()
        report.update(decoded=True,primary_bytes=total,primary_sha256=file_sha(target),pixels=w*ht,output_format='yuv420p10le',
                      transform_provenance='Not inferred from HIF; compare model receipts separately',orientation_applied=False)
        for ident,typ in h.items.items():
            if typ!='hvc1' or ident in plan['tiles']:continue
            stream=output/f'aux-{ident}.h265';stream.write_bytes(h.tile_stream(ident));decoded=output/f'aux-{ident}.yuv'
            info=json.loads(run([str(ffprobe),'-v','error','-f','hevc','-i',toolpath(stream),'-show_streams','-of','json']))['streams']
            aw,ah=h.geometry(ident)
            need(len(info)==1 and info[0]['codec_name']=='hevc' and info[0]['pix_fmt']=='yuv420p10le' and
                 (info[0]['width'],info[0]['height'])==(aw,ah),'Auxiliary decoder source format/geometry')
            run([str(ffmpeg),'-nostdin','-n','-v','error','-xerror','-err_detect','explode','-f','hevc','-i',toolpath(stream),
                 '-frames:v','1','-pix_fmt','yuv420p10le','-f','rawvideo',toolpath(decoded)])
            need(decoded.stat().st_size==aw*ah*3,'Full decoded auxiliary byte count')
            report['auxiliary_hevc'].append(dict(item=ident,width=aw,height=ah,stream_sha256=file_sha(stream),
                decoded_sha256=file_sha(decoded),decoded_bytes=decoded.stat().st_size,sps=h.codec(ident)['sps'],ffprobe=info[0]))
            if h.nclx(ident) is not None:report['color_signaling'].append(dict(item=ident,**compare_color(h.nclx(ident),info[0])))
        report['color_signaling_consistent']=all(r['consistent'] for r in report['color_signaling']) if report['color_signaling'] else None
        report['warnings']=[] if report['color_signaling_consistent'] is not False else [
            'Container nclx and elementary-stream color signaling differ; no color reinterpretation or repair performed']
    except Exception as error:
        report['error']=repr(error);raise
    finally:(output/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('hif',type=Path)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--ffmpeg',type=Path,required=True);p.add_argument('--ffprobe',type=Path)
    a=p.parse_args();fp=a.ffprobe or a.ffmpeg.with_name('ffprobe'+a.ffmpeg.suffix)
    report=audit(a.hif,a.output,a.ffmpeg,fp);print(json.dumps({k:report[k] for k in
        ('decoded','primary_bytes','primary_sha256','pixels','color_signaling_consistent','warnings')},indent=2))
if __name__=='__main__':main()
