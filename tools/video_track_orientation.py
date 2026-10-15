# SPDX-License-Identifier: GPL-2.0-or-later
"""Read MOV/MP4 movie and track matrices without modifying media or decoding it.

Atom and matrix layout: Apple's QuickTime File Format documentation.
Angles use the canonical unit matrices also used by the cited Z30 design;
raw words and translations remain available to avoid player-convention ambiguity.
"""
import argparse
import json
import struct
from pathlib import Path


def _matrix(raw):
    words=list(struct.unpack('>9i',raw))
    candidates={(65536,0,0,65536):0,(0,-65536,65536,0):90,
                (-65536,0,0,-65536):180,(0,65536,-65536,0):270}
    angle=None
    if words[2]==words[5]==0 and words[8]==1<<30:
        angle=candidates.get((words[0],words[1],words[3],words[4]))
    return dict(words=words,hex=raw.hex(),canonical_rotation_degrees=angle,
                translation_fixed16=words[6:8])


def inspect_stream(stream,length):
    """Inspect a seekable binary stream; skip all encoded sample payloads."""
    if type(length) is not int or length<8:raise ValueError('File is too short')
    budget=100000
    def read(offset,size):
        if offset<0 or size<0 or offset+size>length:raise ValueError('Read outside file')
        stream.seek(offset);data=stream.read(size)
        if len(data)!=size:raise ValueError('Truncated file')
        return data
    def boxes(start,end):
        nonlocal budget
        cursor=start;out=[]
        while cursor<end:
            budget-=1
            if budget<0:raise ValueError('Too many atoms')
            if end-cursor<8:raise ValueError('Truncated atom header')
            size,kind=struct.unpack('>I4s',read(cursor,8));header=8
            if size==1:
                if end-cursor<16:raise ValueError('Truncated extended atom header')
                size=struct.unpack('>Q',read(cursor+8,8))[0];header=16
            elif size==0:size=end-cursor
            if size<header or cursor+size>end:raise ValueError('Atom exceeds parent or has invalid size')
            out.append(dict(kind=kind,start=cursor,data=cursor+header,end=cursor+size))
            cursor+=size
        return out
    def unique(items,kind):
        matching=[b for b in items if b['kind']==kind]
        if len(matching)!=1:raise ValueError(f'Requires one {kind.decode("ascii")} atom')
        return matching[0]
    def fullbox(atom,matrix0,matrix1,minimum0,minimum1):
        start=atom['data'];version=read(start,1)[0]
        if version not in (0,1):raise ValueError('Unsupported full-box version')
        minimum=minimum1 if version else minimum0
        if atom['end']-start<minimum:raise ValueError('Truncated full-box payload')
        offset=matrix1 if version else matrix0
        return version,_matrix(read(start+offset,36)),offset

    top=boxes(0,length)
    moov=unique(top,b'moov');children=boxes(moov['data'],moov['end'])
    movie_matrices=[];tracks=[]
    for atom in children:
        if atom['kind']==b'mvhd':
            version,matrix,_=fullbox(atom,36,48,100,112)
            movie_matrices.append(dict(version=version,offset=atom['start'],**matrix))
        elif atom['kind']==b'trak':
            nodes=boxes(atom['data'],atom['end']);tkhd=unique(nodes,b'tkhd')
            version,matrix,offset=fullbox(tkhd,40,52,84,96)
            data=tkhd['data'];track_id=struct.unpack('>I',read(data+(20 if version else 12),4))[0]
            width,height=struct.unpack('>2I',read(data+offset+36,8))
            mdia=unique(nodes,b'mdia');hdlr=unique(boxes(mdia['data'],mdia['end']),b'hdlr')
            if hdlr['end']-hdlr['data']<12:raise ValueError('Truncated handler')
            handler=read(hdlr['data']+8,4).decode('ascii',errors='replace')
            tracks.append(dict(track_id=track_id,handler=handler,tkhd_offset=tkhd['start'],
                               tkhd_version=version,matrix=matrix,width_fixed16=width,height_fixed16=height))
    if not tracks:raise ValueError('No track headers found')
    return dict(schema=1,bytes=length,movie_matrices=movie_matrices,tracks=tracks,
                video_track_count=sum(row['handler']=='vide' for row in tracks),
                scope='Container headers only; no codec, playback or camera verification')


def inspect_file(path):
    path=Path(path)
    with path.open('rb') as stream:
        stream.seek(0,2);result=inspect_stream(stream,stream.tell())
    return dict(path=str(path.resolve()),**result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media',type=Path)
    args=parser.parse_args()
    try:result=inspect_file(args.media)
    except (OSError,ValueError) as exc:parser.exit(1,str(exc)+'\n')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
