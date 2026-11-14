# SPDX-License-Identifier: GPL-2.0-or-later
"""Apply the v2 portrait video layout and the histogram fix to identity-checked MAINs.

python3 build_overlay.py --stock stock.main --base derived=derived.main [--base v37=v37.main] --out DIR
Every replaced byte is checked against the expected original/prior value first.
Outputs are local derived MAINs for emulation; they are not update containers.
"""
import argparse,hashlib,json,struct,sys,os
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path[:0]=[str(HERE),str(ROOT/'experiments/20261111/v36/from-stock'),str(ROOT/'tools')]
ap=argparse.ArgumentParser();ap.add_argument('--stock',required=True,type=Path)
ap.add_argument('--base',action='append',default=[]);ap.add_argument('--out',required=True,type=Path)
args=ap.parse_args();OUT=args.out
import video_layout_v2 as layout2, z8_video_layout as old, histogram_portrait_fix as histfix
from z8_orientation_unlock import build_plan
sha=lambda b:hashlib.sha256(b).hexdigest()
stock=args.stock.read_bytes()
B=layout2.IMAGE_BASE
def apply(base,name):
    img=bytearray(base);rows=[]
    for a,f,L,P in layout2.rows(stock):
        if any(f[8:10]) or not any(f[6:8]):continue
        off=a-B+32
        before=struct.pack('<4h',*old.portrait_rect(f[14],L))
        assert bytes(img[off:off+8])==before,f'{name}: unexpected bytes at {a+32:#x}'
        new=layout2.portrait_rect(f[14],L);after=struct.pack('<4h',*new)
        img[off:off+8]=after
        # second, identical screen set (+0x1b420) is stock in every base: mirror the whole record
        r1=a-B;r2=r1+0x1b420
        assert bytes(img[r2:r2+68])==stock[r1:r1+68],f'{name}: second table not stock at {a+0x1b420:#x}'
        img[r2:r2+68]=img[r1:r1+68]
        rows.append(dict(address=hex(a+32),file_offset=off,widget_id=hex(f[14]),landscape=list(L),
                         expected_hex=before.hex(),replacement_hex=after.hex(),portrait=list(new)))
    if not os.environ.get('NOHIST'):histfix.apply(img,stock)
    diff=sum(x!=y for x,y in zip(base,img))
    return bytes(img),rows,diff
OUT.mkdir(parents=True,exist_ok=True)
derived=build_plan(stock,video=True,video_portrait=True,video_file_rotation=True)['image']
report={}
bases=[('derived',derived)]+[(s.split('=',1)[0],Path(s.split('=',1)[1]).read_bytes()) for s in args.base if not s.startswith('derived=')]
for name,base in bases:
    img,rows,diff=apply(base,name)
    (OUT/f'{name}-portrait-full{os.environ.get("NOHIST","")}.main').write_bytes(img)
    report[name]=dict(base_sha256=sha(base),sha256=sha(img),changed_bytes=diff,records=len(rows))
    (OUT/f'{name}-overlay{os.environ.get("NOHIST","")}.json').write_text(json.dumps(dict(report[name],coordinate_records=rows),indent=1))
print(json.dumps(report,indent=1))
