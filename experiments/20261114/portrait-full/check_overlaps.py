# SPDX-License-Identifier: GPL-2.0-or-later
import sys,itertools
from video_layout_v2 import *
src=open(sys.argv[1],'rb').read()  # original C3.11 MAIN
vid=[];photo=[];miss=[]
for a,f,L,P in rows(src):
    if any(f[8:10]):photo.append((f[14],L,P));continue
    if not any(f[6:8]):continue
    try:vid.append((f[14],L,portrait_rect(f[14],L)))
    except KeyError as e:miss.append(str(e))
print('video',len(vid),'missing',len(miss));print('\n'.join(miss))
def ov(a,b):return a[0]<b[2] and b[0]<a[2] and a[1]<b[3] and b[1]<a[3]
bad=set()
for (i,L1,P1),(j,L2,P2) in itertools.combinations(vid,2):
    if ov(P1,P2) and not ov(L1,L2):bad.add((i,P1,j,P2))
for x in sorted(bad):print('OVERLAP %04x %s  %04x %s'%x)
