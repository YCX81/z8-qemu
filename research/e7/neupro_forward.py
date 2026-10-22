# SPDX-License-Identifier: GPL-2.0-or-later
"""Animal NeuPro integer-forward candidate using ORIGINAL quantization records.

Weight maps and parameters are recovered from the firmware. NPU accumulator,
shift rounding, activation placement and standalone-pool requantization have
no original accelerator-output oracle. The selected policy is explicit.
No fitted channel scale is read by the execution path.
"""
from pathlib import Path
import argparse
import os
from e7_inputs import new_output_dir
import hashlib
import json
import time
import numpy as np
import torch
import torch.nn.functional as F

HERE=Path(__file__).resolve().parent
FULL=Path(os.environ.get("E7_PCNN_RESULTS",HERE/"private-inputs/pcnn-results"))
BIRD=Path(os.environ.get("E7_BIRD_SOURCE",HERE/"private-inputs/e7-bird-network"))


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def shift(value,bits,mode):
    bits=np.asarray(bits,dtype=np.int64)
    if np.any(bits<0) or np.any(bits>48):
        raise ValueError("Unsupported shift count")
    unit=np.left_shift(np.int64(1),bits)
    if mode=="floor":
        return value>>bits
    if mode=="nearest_up":
        return (value+np.where(bits>0,unit>>1,0))>>bits
    absolute=np.abs(value)
    q=absolute>>bits
    if mode=="rne":
        rem=absolute&(unit-1)
        half=unit>>1
        q+=((bits>0)&((rem>half)|((rem==half)&((q&1)!=0)))).astype(np.int64)
    elif mode!="rtz":
        raise ValueError(mode)
    return np.where(value<0,-q,q)


def round_input(x,mode):
    if mode=="rne":
        return np.rint(x).astype(np.int64)
    if mode=="floor":
        return np.floor(x).astype(np.int64)
    return np.trunc(x).astype(np.int64)


def conv_exact(x,w,stride,padding):
    """Binary64 convolution is exact here: integer products and sums stay <2**53."""
    bound=int(np.abs(x).max())*int(np.abs(w.astype(np.int64)).max())*int(np.prod(w.shape[1:]))
    if bound>=2**53:
        raise ValueError("Exact-integer binary64 accumulator bound exceeded")
    left,right,top,bottom=padding
    tx=torch.from_numpy(x.astype(np.float64))[None]
    tw=torch.from_numpy(w.astype(np.float64))
    if any(padding):
        tx=F.pad(tx,(left,right,top,bottom))
    y=F.conv2d(tx,tw,stride=tuple(stride))[0].numpy()
    if not np.all(y==np.rint(y)):
        raise ValueError("Convolution produced noninteger values")
    return y.astype(np.int64),bound


def pool2(x):
    c,h,w=x.shape
    if h%2 or w%2:
        raise ValueError("Pool extent not divisible by two")
    return x.reshape(c,h//2,2,w//2,2).max(axis=(2,4))


def infer(raw,policy="floor",parameters=None):
    parameters=parameters or os.environ.get("E7_NEUPRO_PARAMETERS")
    if not parameters:raise ValueError("Set E7_NEUPRO_PARAMETERS or pass --parameters")
    spec=json.loads(Path(parameters).read_text())
    if len(raw)!=3*256*384:
        raise ValueError("Expected uint8 CHW[3,256,384]")
    pixels=np.frombuffer(raw,np.uint8).reshape(3,256,384).astype(np.int64)
    input_scale=np.asarray(spec["input_scale"])[:,None,None]
    # Observed original XM6 input conversion, including all 256 byte values.
    # The u8 normalization is /256, and the conversion uses a Q8 multiplier.
    input_multiplier=np.rint((1/256)/input_scale*256).astype(np.int64)
    tensors={0:np.clip((pixels*input_multiplier+128)>>8,-32768,32767)}
    trace=[]
    for layer in spec["layers"][1:]:
        ix=layer["index"]
        x=tensors[layer["parent"]]
        started=time.monotonic()
        if layer["kind"]=="maxpool":
            y=pool2(x)
            src=np.asarray(spec["layers"][layer["parent"]]["output_float_scale"])
            dst=np.asarray(layer["output_float_scale"])
            # Explicit candidate Q30 rescale. This is not yet a recovered
            # original XM6 max-pool kernel's exact coefficient/rounding path.
            ratio=src/dst
            multiplier=np.rint(ratio*(1<<30)).astype(np.int64)[:,None,None]
            y=shift(y*multiplier,30,policy)
            acc_bound=None
        else:
            p=np.load(layer["parameter_file"])
            if hashlib.sha256(Path(layer["parameter_file"]).read_bytes()).hexdigest()!=layer["parameter_file_sha256"]:
                raise ValueError("Parameters changed")
            dot,acc_bound=conv_exact(x,p["weights"],layer["stride"],layer["padding"])
            channels=len(p["bias"])
            # Independently checked against the recovered convolution/bias
            # correspondence. These three parameter arrays interleave halves.
            order=(np.r_[np.arange(0,channels,2),np.arange(1,channels,2)]
                   if ix in (2,4,6) else np.arange(channels))
            acc=dot+p["bias"][order].astype(np.int64)[:,None,None]
            pre=p["post_clip_shift"][order].astype(np.int64)[:,None,None]
            y=shift(acc,pre,policy)
            lo=p["clip"][order,0].astype(np.int64)[:,None,None]
            hi=p["clip"][order,1].astype(np.int64)[:,None,None]
            y=np.clip(y,lo,hi)
            activation=layer["activation_raw"]
            if activation==[0,2,0]:
                y=np.where(y<0,shift(y,3,policy),y)
            elif activation!=[0,0,0]:
                raise ValueError(f"Unknown activation {activation}")
            multiplier=p["scale"][order].astype(np.int64)[:,None,None]
            post=p["post_scale_shift"][order].astype(np.int64)[:,None,None]
            # Inferred normalization, constrained by original clip/multiplier
            # records and by all exposed subnet scales. Not an observed NPU
            # datapath operation. Linear heads require no extra shift.
            bound=np.maximum(-lo.astype(float)/8,hi)*multiplier/2.**post
            normalize=(np.maximum(0,np.floor(np.log2(32768/bound))).astype(np.int64)
                       if activation==[0,2,0] else np.zeros_like(post))
            post=post-normalize
            y=shift(y*multiplier,post,policy)
            if layer["fused_pool"]:
                y=pool2(y)
        saturated=int(np.count_nonzero((y<-32768)|(y>32767)))
        y=np.clip(y,-32768,32767)
        if list(y.shape)!=layer["shape_chw"]:
            raise ValueError(f"{layer['name']}: output shape mismatch")
        tensors[ix]=y
        trace.append(dict(index=ix,name=layer["name"],shape=list(y.shape),
                          min=int(y.min()),max=int(y.max()),saturated_elements=saturated,
                          integer_dot_bound=acc_bound,sha256=digest(y.astype("<i2")),
                          seconds=time.monotonic()-started))
    result={}
    for label,ix in (("fine",12),("coarse",17)):
        result[label+"_i16"]=tensors[ix].astype("<i2")
        scale=np.asarray(spec["layers"][ix]["output_float_scale"],dtype=np.float32)[:,None,None]
        result[label+"_f32"]=tensors[ix].astype(np.float32)*scale
    return result,trace,tensors


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy",choices=["floor","rtz","rne","nearest_up"],default="floor")
    ap.add_argument("--case",choices=["zero","stripe","random"],default="stripe")
    ap.add_argument("--input-chw",type=Path)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument('--parameters',type=Path)
    ap.add_argument('--pcnn-reference',type=Path)
    args=ap.parse_args()
    args.output=new_output_dir(args.output)
    torch.set_num_threads(4)
    raw=(args.input_chw.read_bytes() if args.input_chw else bytes(3*256*384)
         if args.case=="zero" else (BIRD/"fixtures"/args.case/"input-chw-u8.bin").read_bytes())
    started=time.monotonic()
    with torch.inference_mode():
        heads,trace,tensors=infer(raw,args.policy,args.parameters)
    np.savez_compressed(args.output/"heads.npz",**heads)
    np.savez_compressed(args.output/"layers.npz",**{f"layer{i}":v.astype("<i2") for i,v in tensors.items()})
    report=dict(
        completed=True,original_accelerator_equivalence=False,
        original_native_network_execution_completed=False,
        parameter_source="Original fixed-point integer weights, bias, clip, scale, shifts and exposed float scales; no fitted execution scale.",
        policy=args.policy,
        input_quantizer="Original XM6 observed: (u8 * 32444 + 128) >> 8; independent of the candidate NPU shift policy.",
        unresolved_policy=["Accumulator width/saturation is modeled as exact signed int64.",
                           "Shift rounding is explicitly selected, not measured on original NeuPro.",
                           "Candidate sequence: shift, clip, negative /8, multiply, normalized shift, signed16 saturation.",
                           "Additional LeakyReLU normalization is inferred from clip bounds and multiplier; channel-order fix applies to layers 2,4,6.",
                           "Standalone pooling uses candidate Q30 rescaling from original source/destination float scales."],
        input_sha256=hashlib.sha256(raw).hexdigest(),trace=trace,
        seconds=time.monotonic()-started,
        outputs={k:dict(shape=list(v.shape),sha256=digest(v),min=float(v.min()),max=float(v.max()))
                 for k,v in heads.items()},
    )
    if args.pcnn_reference:
        reference=np.load(args.pcnn_reference)
        report["pcnn_comparison_is_not_a_neupro_oracle"]={}
        for i,label in enumerate(("fine","coarse")):
            a=heads[label+"_f32"].astype(float)
            b=reference[f"head{i}"].view("<f2").astype(float)
            report["pcnn_comparison_is_not_a_neupro_oracle"][label]=dict(
                rms=float(np.sqrt(np.mean((a-b)**2))),max_abs=float(np.max(np.abs(a-b))),
                correlation=float(np.corrcoef(a.ravel(),b.ravel())[0,1]))
    (args.output/"verification.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ("trace","unresolved_policy")}),flush=True)


if __name__=="__main__":
    main()
