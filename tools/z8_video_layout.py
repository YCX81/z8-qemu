# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored coordinates for absent video portrait fields in pinned C3.11.

The original renderer, font sizes and bitmap resources remain in use. These
coordinates are experimental UI design, not recovered Nikon portrait assets.
"""
import hashlib
import struct
from z8_resources import MAIN_SHA

TABLE_START=0x43a21a70
TABLE_END=0x43a2c53c
IMAGE_BASE=0x40160000


def portrait_rect(ident,rect):
    l,t,r,b=rect
    w,h=r-l,b-t
    if not (0<=l<r<=720 and 0<=t<b<=480 and w<=480):
        raise ValueError('Requires a bounded control that fits without resizing')
    nl=max(0,min(480-w,round((l+r)/3-w/2)))
    nt=max(0,min(720-h,round((t+b)*.75-h/2)))
    if w>240:
        return (nl,nt,nl+w,nt+h)
    dx=dy=None
    if 0x1f14<=ident<=0x1f1e:dx,dy=-184,240
    elif ident in (0x1f42,0x1f43):dx,dy=-178,240
    elif 0x1f1f<=ident<=0x1f22 or ident in (0x1f44,0x1f45):dx,dy=-178,244
    elif 0x1f23<=ident<=0x1f2b:dx,dy=-142,244
    elif ident==0x1f2c:dx,dy=-240,176
    elif 0x1dc5<=ident<=0x1de6 or 0x1da4<=ident<=0x1da6 or ident in (0x1ee7,0x1ee8):dx,dy=0,163
    elif 0x1e2a<=ident<=0x1e2c:dx,dy=152,430
    elif ident in (0x1ee5,0x1ee6):dx,dy=184,430
    elif t<38:dx,dy=(-240 if l>=480 else -100 if l>=300 else 0),44
    elif 38<=t<90:dx,dy=(-240,44) if l>=400 else (-14 if 0x1ef4<=ident<=0x1ef9 else 0,-38)
    elif 90<=t<174:dx,dy=(-240,48) if l>=480 else (0,0)
    if dx is not None:nl,nt=l+dx,t+dy
    result=(nl,nt,nl+w,nt+h)
    if not(0<=nl<nl+w<=480 and 0<=nt<nt+h<=720):
        raise ValueError('Authored control falls outside portrait canvas')
    return result


def patch_coordinates(source,image):
    if hashlib.sha256(source).hexdigest()!=MAIN_SHA or len(image)!=len(source):
        raise ValueError('Requires pinned original MAIN and same-size derived image')
    edits=[];sites=[];omitted=[]
    # Native403a65a0 walks 68-byte records.403a84a8/403a947c select
    # 24+orientation*8 for the rectangle and paired32-bit attributes.
    for address in range(TABLE_START,TABLE_END,68):
        off=address-IMAGE_BASE;row=source[off:off+68]
        fields=struct.unpack('<17I',row)
        if not fields[1]:break
        if any(fields[8:10]) or not any(fields[6:8]):continue
        rect=struct.unpack_from('<4h',row,24)
        try:new=portrait_rect(fields[14],rect)
        except ValueError:
            omitted.append(dict(address=address,widget_id=fields[14]))
            continue
        struct.pack_into('<4h',image,off+32,*new)
        for src,dst in ((8,12),(16,20),(40,44),(48,52)):
            image[off+dst:off+dst+4]=row[src:src+4]
        edits.append(dict(address=address,widget_id=fields[14],landscape=list(rect),portrait=list(new)))
        sites.append(dict(address=address,bytes=68,
                          original_sha256=hashlib.sha256(row).hexdigest(),
                          replacement_sha256=hashlib.sha256(image[off:off+68]).hexdigest()))
    return edits,sites,omitted
