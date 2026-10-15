# SPDX-License-Identifier: GPL-2.0-or-later
"""F0 metadata guard adapted from z9-qemu c6dbfc4 audit/codec profile.

Reject TIFF tags that LibRaw can interpret as uncompressed packed fallback.
This is format evidence, never execution of Nikon's proprietary codec.
"""
import hashlib
from pathlib import Path
import struct
import subprocess

def image_ifds(data):
    if data[:4]!=b'II*\0' or len(data)<8:raise ValueError('Little-endian classic NEF required')
    seen=set();rows=[]
    def visit(offset):
        if not offset:return
        if offset in seen:raise ValueError('Cyclic or aliased IFD')
        if len(seen)>=32 or not 8<=offset<=len(data)-2:raise ValueError('IFD bounds exceeded')
        seen.add(offset);n=struct.unpack_from('<H',data,offset)[0]
        if n>1024 or offset+2+n*12+4>len(data):raise ValueError('Truncated IFD')
        tags={}
        for i in range(n):
            at=offset+2+i*12;tag,kind,count,pointer=struct.unpack_from('<HHII',data,at)
            if tag not in (254,256,257,258,259,262,273,279,330,513,514) or kind not in (3,4):continue
            if tag in tags:raise ValueError('Duplicate image tag')
            unit,fmt=(2,'H') if kind==3 else (4,'I')
            if not 0<count<=1024:raise ValueError('Image tag count exceeded')
            start=at+8 if unit*count<=4 else pointer
            if start+unit*count>len(data):raise ValueError('Image tag outside NEF')
            tags[tag]=list(struct.unpack_from('<'+fmt*count,data,start))
        rows.append(tags)
        for child in tags.get(330,[]):visit(child)
        visit(struct.unpack_from('<I',data,offset+2+n*12)[0])
    visit(struct.unpack_from('<I',data,4)[0]);return rows

def f0_metadata(tags,table,file_size):
    width,height,bits=(tags[key][0] for key in (256,257,258))
    if (not 0<width<=16384 or not 0<height<=16384 or width*height>64*1024*1024 or
        width%4 or bits!=14 or tags.get(259)!=[34713]):raise ValueError('Bounded14-bit Nikon compressed RAW required')
    counts=tags.get(279,[]);offsets=tags.get(273,[])
    fallback=[((width*7*channels//4+15)//16*16)*height for channels in (1,3)]
    if (len(counts)!=1 or len(offsets)!=1 or counts[0]<=4 or counts[0] in fallback or
        offsets[0]+counts[0]>file_size):raise ValueError('Packed fallback or incomplete RAW strip')
    if not table.startswith(b'F0'+struct.pack('<4H',2048,2048,2048,2048)):
        raise ValueError('F0/four2048 predictor metadata required')
    return dict(encoding='nikon-f0-lossless14',predictor_seeds=[2048]*4,
        libraw_packed_fallback_excluded=True,raw_strip_bytes=counts[0],
        uncompressed_packed_bytes=fallback[0],linearization_sha256=hashlib.sha256(table).hexdigest(),
        physical_codec_verified=False)

def require_f0(path):
    path=Path(path)
    if not 8<=path.stat().st_size<=256*1024*1024:raise ValueError('NEF size exceeded')
    data=path.read_bytes()
    raws=[r for r in image_ifds(data) if r.get(254)==[0] and r.get(262)==[32803]]
    if len(raws)!=1:raise ValueError('One full RAW IFD required')
    table=subprocess.check_output(['exiftool','-b','-NEFLinearizationTable',str(path)],timeout=30)
    return f0_metadata(raws[0],table,len(data))
