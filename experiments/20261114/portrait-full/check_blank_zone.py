# SPDX-License-Identifier: GPL-2.0-or-later
import sys
from video_layout_v2 import *
src=open(sys.argv[1],'rb').read()  # original C3.11 MAIN
for a,f,L,P in rows(src):
    if any(f[8:10]) or not any(f[6:8]):continue
    r=portrait_rect(f[14],L)
    if r[0]<46 and r[1]<602 and r[3]>570:print('KILLZONE',hex(f[14]),r)
print('done')
