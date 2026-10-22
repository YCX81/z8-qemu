# SPDX-License-Identifier: GPL-2.0-or-later
"""Static-reconstruction reference for the embedded E7 animal postprocessor.

Input: the two dequantized float32 CHW output buffers, not packed NPU weights
or raw quantized activations. Returns normalized candidates after class-wise
center-distance suppression and score sorting. Final integer rounding,
edge clipping and 16-result output packing are deliberately left out.

This is a reviewable reconstruction, not an original-DSP execution oracle.
See docs/status.md for the distinction between reconstruction and execution.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import struct

HERE=Path(__file__).resolve().parent
SHAPES=((24,16,30),(12,8,30))
LABELS=('AnimalE','AnimalH','AnimalR','AnimalL','AnimalR','AnimalL','AnimalE','AnimalH','AnimalR','AnimalL')
COEFFICIENT_BITS=(0x3f800001,0x3f00000a,0x3e2aaa6a,0x3d2aa390,0x3c0897a6,0x3ab91bcb,0x394d4a8b)

def f32(x):return struct.unpack('<f',struct.pack('<f',x))[0]
def from_bits(x):return struct.unpack('<f',struct.pack('<I',x&0xffffffff))[0]
def bits(x):return struct.unpack('<I',struct.pack('<f',x))[0]
def mul(a,b):return f32(a*b)
def add(a,b):return f32(a+b)

def exp_approx(x):
    """Recreate the observed scalar polynomial/range reduction, in f32.

    IEEE mode, subnormal/overflow behavior and cycle-level DSP rounding have
    not been measured. The reference intentionally accepts a bounded range.
    """
    x=f32(x)
    if not math.isfinite(x) or abs(x)>32:raise ValueError('Reference exp range is [-32,32]')
    n=math.trunc(mul(x,from_bits(0x3fb8aa3b)))
    r=f32(x-mul(f32(n),from_bits(0x3f317218)))
    c1,c2,c3,c4,c5,c6,c7=map(from_bits,COEFFICIENT_BITS)
    r2=mul(r,r);r4=mul(r2,r2)
    p0=add(mul(r,c1),1.0);p1=add(mul(r,c3),c2)
    p2=add(mul(r,c5),c4);p3=add(mul(r,c7),c6)
    p=add(add(p0,mul(r2,p1)),mul(r4,add(p2,mul(r2,p3))))
    return from_bits(bits(p)+(n<<23))

def sigmoid(x):return f32(1.0/add(1.0,exp_approx(-f32(x))))

def load_spec(inventory=None):
    inventory=inventory or os.environ.get('E7_ANIMAL_INVENTORY')
    if not inventory:raise ValueError('Set E7_ANIMAL_INVENTORY to a private identity-checked inventory')
    inv=json.loads(Path(inventory).read_text())['samples']['z8-c311']
    data=Path(inv['path']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=inv['sha256']:raise ValueError('MAIN identity changed')
    symbols={s['name']:s for s in inv['elfs'][2]['symbols']}
    def read(name,fmt):
        s=symbols[name];return struct.unpack_from(fmt,data,int(s['file_offset'],16))
    anchors=list(zip(*[iter(read('cAnimalDetectionPostprocess_AnchorBox_Table','<8H'))]*2))
    pairs=list(zip(*[iter(read('cAnimalDetectionPostprocess_CvIndex_Table','<24B'))]*2))
    table=read('cAnimalDetectionPostprocess_Cv_Table','<12000f')
    curves=[table[i*1000:(i+1)*1000] for i in range(12)]
    if not all(all(a<=b for a,b in zip(c,c[1:])) for c in curves):raise ValueError('Non-monotonic calibration table')
    return {'anchors':anchors,'pairs':pairs,'curves':curves,
            'thresholds':read('cAnimalDetectionPostprocess_Threshold_Table','<3f'),
            'reference':read('cAnimalDetectionPostprocess_ClassReferenceTh_Table','<10H'),
            'effective':read('cAnimalDetectionPostprocess_ClassEffectiveTh_Table','<10H')}

def calibrate(spec,row,raw_probability):
    """Inverse monotonic lookup, matching the bounded binary-search structure.

    A literal equality returns the current midpoint. Insertion beyond index
    999 returns 1000. First find the joint-threshold cutoff starting at
    reference[class]-1, then search the candidate from cutoff-1 (uint16).
    """
    cls=spec['pairs'][row][1];curve=spec['curves'][row]
    def search(value,lo):
        hi=999
        while lo<=hi:
            mid=(lo+hi)//2;v=curve[mid]
            if v<value:lo=mid+1
            elif v>value:hi=mid-1
            else:return mid
        return min(1000,hi+1)
    start=search(spec['thresholds'][1],spec['reference'][cls]-1)
    return search(raw_probability,(start-1)&0xffff)

def suppress(candidates,factor):
    """Class-wise center-distance gate, not intersection-over-union NMS."""
    out=[dict(c) for c in candidates]
    for i,a in enumerate(out):
        if a['score']==0:continue
        for b in out[i+1:]:
            if b['score']==0 or a['class']!=b['class']:continue
            dx=f32(abs(f32(a['cx']-b['cx'])));dy=f32(abs(f32(a['cy']-b['cy'])))
            tx=mul(add(mul(factor,a['w']),mul(factor,b['w'])),0.5)
            ty=mul(add(mul(factor,a['h']),mul(factor,b['h'])),0.5)
            if dx<tx and dy<ty:
                if a['score']>=b['score']:b['score']=0
                else:a['score']=0;break
    # Original qsort does not specify order among equal scores.
    return sorted((c for c in out if c['score']),key=lambda c:-c['score'])

def decode_candidates(heads,spec=None):
    spec=spec or load_spec()
    if len(heads)!=2:raise ValueError('Expected two output buffers')
    for data,(w,h,c) in zip(heads,SHAPES):
        if len(data)!=w*h*c:raise ValueError('CHW buffer length mismatch')
    key_to_row={key:row for row,key in enumerate(spec['pairs'])}
    obj_threshold,joint_threshold,nms_factor=spec['thresholds']
    candidates=[];full=False
    for head,(width,height,_) in enumerate(SHAPES):
        area=width*height;data=heads[head]
        for local_anchor in range(2):
            anchor_id=head*2+local_anchor;aw,ah=spec['anchors'][anchor_id]
            key=(head<<4)|anchor_id
            base=local_anchor*15
            for y in range(height):
                for x in range(width):
                    cell=y*width+x
                    def channel(k):return f32(data[(base+k)*area+cell])
                    obj=sigmoid(channel(4))
                    if obj<obj_threshold:continue
                    best_score=0;best_class=None;best_joint=None;best_row=None
                    for cls in range(10):
                        row=key_to_row.get((key,cls))
                        if row is None:continue
                        joint=mul(obj,sigmoid(channel(5+cls)))
                        if joint<joint_threshold:continue
                        score=calibrate(spec,row,joint)
                        if score>best_score:
                            best_score,best_class,best_joint,best_row=score,cls,joint,row
                    if best_score==0:continue
                    candidates.append({'head':head,'anchor':anchor_id,'cell':[x,y],
                        'cx':f32(add(sigmoid(channel(0)),f32(x))/width),
                        'cy':f32(add(sigmoid(channel(1)),f32(y))/height),
                        'w':f32(mul(f32(aw),exp_approx(channel(2)))/384.0),
                        'h':mul(mul(f32(ah),exp_approx(channel(3))),1.0/256.0),
                        'objectness':obj,'joint_probability':best_joint,
                        'calibration_row':best_row,'score':best_score,'class':best_class,
                        'debug_label':LABELS[best_class]})
                    if len(candidates)==32:full=True;break
                if full:break
            if full:break
        if full:break
    return {'before_suppression':candidates,'after_suppression':suppress(candidates,nms_factor),
            'candidate_cap_reached':full,'final_dsp_output_limit':16,
            'not_applied':'final integer rounding, edge clipping, invalid-box rejection and 12-byte output packing'}
