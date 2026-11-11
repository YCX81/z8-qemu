# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored coordinates for absent video portrait fields in pinned C3.11.

The original renderer, font sizes and bitmap resources remain in use. These
coordinates are experimental UI design, not recovered Nikon portrait assets.
"""
import hashlib
import math
import struct
from z8_resources import MAIN_SHA

TABLE_START=0x43a21a70
TABLE_END=0x43a2c53c
IMAGE_BASE=0x40160000


def video_picture_rect():
    """Nominal 16:9 envelope, rotated into the native 480x720 canvas.

    Firmware's 1024x576 direct video gives a 405-pixel logical extent.
    Keep the half-pixel edges when choosing a common integer inset. The
    actual 1024x682 panel uses a (0,54) direct origin: the stock 40332820
    converter gives x=38.005865..443.401760 (left) and
    36.598240..441.994135 (right). SAFE_RECT fits BOTH, with >=11.358
    panel pixels to the video edge (native-geometry.json records all rows).
    """
    extent=720*9/16
    return ((480-extent)/2,0,(480+extent)/2,720)


PICTURE_MARGIN=8
_picture=video_picture_rect()
SAFE_RECT=(math.ceil(_picture[0])+PICTURE_MARGIN,PICTURE_MARGIN,
           math.floor(_picture[2])-PICTURE_MARGIN,720-PICTURE_MARGIN)


def _draft_rect(ident,rect):
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


def portrait_rect(ident,rect):
    """Translate compound controls together; never shrink fonts or hit boxes.

    The side gutters are narrower than the touch controls. Place controls
    wholly inside the picture instead. Alternative states of a control use
    the same translation, including its frame, labels and individual bars.
    """
    old=_draft_rect(ident,rect)
    l,t,r,b=rect
    dx=dy=None
    # Bottom exposure row: shutter and aperture. ISO has its own row above.
    if 0x1f14<=ident<=0x1f1e or ident in (0x1f42,0x1f43):dx,dy=-132,236
    elif 0x1f1f<=ident<=0x1f22 or ident in (0x1f44,0x1f45):dx,dy=-136,236
    elif 0x1f23<=ident<=0x1f2b:dx,dy=-188,184
    elif ident==0x1f2c:dx,dy=-282,118  # battery, above the ISO row
    # Audio: preserve both channels, all 14 bars and microphone alternatives.
    elif 0x1dc5<=ident<=0x1de6 or 0x1da4<=ident<=0x1da6 or ident in (0x1ee7,0x1ee8):dx,dy=42,167
    elif 0x1de7<=ident<=0x1e08:dx,dy=44,300
    elif 0x1e2a<=ident<=0x1e2c:dx,dy=202,380
    elif ident in (0x1ee5,0x1ee6):dx,dy=234,380
    # Mode and its small suffix; focus group retains its native spacing.
    elif 0x1f08<=ident<=0x1f0b:dx,dy=33,-36
    elif 0x1ef4<=ident<=0x1ef9:dx,dy=-14,-36
    # Recording indicator, elapsed timer and remaining time occupy one row.
    elif ident in (0x1d9e,0x1d9f) or 0x1dad<=ident<=0x1db1:dx,dy=42,51
    elif ident in (0x1ed9,0x1eda,0x1f7f):dx,dy=-165,51
    elif 0x1dbc<=ident<=0x1dc2 or ident in (0x1f7d,0x1f7e):dx,dy=-282,50
    # Keep the entire WB/detection/metering row and the frame-rate/FX pair
    # together. Independently clamping their rightmost members overlaps them.
    elif 38<=t<90 and l>=400:dx,dy=-282,44
    elif ident in (0x1dc3,0x1dc4,0x1f3f,0x1f40):dx,dy=-282,48
    elif ident in (0x1f31,0x1f32,0x1f35,0x1f36):dx,dy=-40,0
    # Format/status rows. All variants share the same anchor.
    elif ident in (0x1e95,0x1e96,0x1e97,0x1e9c,0x1e9d):dx,dy=-42,95
    elif ident in (0x2772,0x2773):dx,dy=-42,95
    elif ident in (0x1f33,0x1f34):dx,dy=-276,95
    elif ident in (0x2177,0x2178):dx,dy=-260,95
    # Touch AF, i, and the adjacent framed status control.
    elif ident in (0x1d1f,0x1d20):dx,dy=42,166
    elif ident in (0x1d23,0x1d24):dx,dy=-282,180
    elif 0x1f64<=ident<=0x1f69:dx,dy=-282,200
    # Compound secondary indicators whose old independent centering changed
    # internal spacing. These may be hidden in the default live-view state.
    elif 0x1f0e<=ident<=0x1f11:dx,dy=42,182
    elif 0x1f5e<=ident<=0x1f63:dx,dy=42,164
    elif 0x1f56<=ident<=0x1f5b:dx,dy=42,142
    if dx is None:
        # Standalone controls retain their authored row, inset as a whole.
        nl,nt,nr,nb=old;w,h=nr-nl,nb-nt
        sl,st,sr,sb=SAFE_RECT
        if w>sr-sl or h>sb-st:
            raise ValueError('Control cannot fit inside the video picture')
        nl=max(sl,min(sr-w,nl));nt=max(st,min(sb-h,nt))
        return (nl,nt,nl+w,nt+h)
    result=(l+dx,t+dy,r+dx,b+dy)
    sl,st,sr,sb=SAFE_RECT
    if not(sl<=result[0]<result[2]<=sr and st<=result[1]<result[3]<=sb):
        raise ValueError('Compound control falls outside the safe picture')
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
