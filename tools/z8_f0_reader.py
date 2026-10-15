# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent bounded F0 reader for the explicit14-bit/four2048 profile."""
import array
import sys

def decode_f0(data,width,height):
    if (not isinstance(data,bytes) or len(data)<4 or len(data)>128<<20 or
        type(width) is not int or type(height) is not int or
        not 0<width<=16384 or not 0<height<=16384 or width%4 or width*height>64<<20):
        raise ValueError('Unsupported bounded F0 input')
    counts=(0,1,4,2,2,3,1,2);symbols=(7,6,8,5,9,4,10,3,11,12,2,0,1,13,14)
    lookup=[None]*256;code=index=0
    for length,count in enumerate(counts,1):
        for _ in range(count):
            for suffix in range(1<<(8-length)):
                lookup[(code<<(8-length))|suffix]=(length,symbols[index])
            code+=1;index+=1
        code<<=1
    pos=0;vertical=[[2048,2048],[2048,2048]];out=array.array('H')
    try:
        for y in range(height):
            horizontal=[0,0]
            for x in range(width):
                at,shift=pos>>3,pos&7
                entry=lookup[((data[at]<<8|data[at+1])>>(8-shift))&255]
                if entry is None:raise ValueError('Invalid F0 code')
                length,bits=entry;pos+=length;difference=0
                if bits:
                    at,shift=pos>>3,pos&7
                    word=data[at]<<16|data[at+1]<<8|data[at+2]
                    difference=(word>>(24-shift-bits))&((1<<bits)-1);pos+=bits
                    if difference<(1<<(bits-1)):difference-=(1<<bits)-1
                previous=vertical[y&1][x] if x<2 else horizontal[x&1]
                value=previous+difference
                if not 0<=value<=16383:raise ValueError('F0 sample exceeds14 bits')
                if x<2:vertical[y&1][x]=value
                horizontal[x&1]=value;out.append(value)
    except IndexError as error:
        raise ValueError('Truncated F0 stream') from error
    if sys.byteorder!='little':out.byteswap()
    return out.tobytes()
