# SPDX-License-Identifier: GPL-2.0-or-later
"""Generic PCNNP replay of the bounded, understood packet subset.

Arithmetic is imported, unchanged, from the pinned local e7-bird-network source.
The packet parser, resource headers, dimensions, memory checks and dispatch here
are independent of its resource744-specific commands.json and fixed head sizes.
Other resources have no captured hardware oracle and remain candidate replays.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time

from pcnn_packets import parse, BIRD, HERE
from e7_inputs import bird_source, new_output_dir
import importlib.util, os

import numpy as np

def _load_backend():
    global PhysicalStream, POLICY, rounded, apply_pcnn_candidate, torch, F, BIRD
    BIRD=bird_source()
    spec=importlib.util.spec_from_file_location('e7_bird_external_core',BIRD/'core.py')
    peer=importlib.util.module_from_spec(spec);spec.loader.exec_module(peer)
    PhysicalStream, POLICY, rounded=peer.PhysicalStream,peer.POLICY,peer.rounded
    apply_pcnn_candidate, torch, F=peer.apply_pcnn_candidate,peer.torch,peer.F



def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Replay:
    def __init__(self, resource, experimental=False):
        _load_backend()
        self.resource=resource
        self.experimental=experimental
        raw=Path(resource["body_file"]).read_bytes()
        if digest(raw)!=resource["body_sha256"]:
            raise ValueError("Resource body changed")
        self.events=parse(raw)
        self.hypotheses=[]
        if resource["resource_id"]!=744:
            self.hypotheses.append("Resource744 arithmetic and input /256 profile transferred to this resource without a captured per-model oracle.")
        needs_experimental=False
        if any(e["kind"]=="control" and e["words"][0] in (0x6820,0x6920) for e in self.events):
            self.hypotheses.append("Explicit pool mask applies the same 2x2 max operation to only selected lanes.")
        if any(e["kind"]=="control" and e["words"][0]>>8==0x28 for e in self.events):
            self.hypotheses.append("Short input transfer uses the selected CHW descriptor plane; global activation 0x4481 is broadcast ReLU.")
        if any(e["kind"]=="matrix" and e["matrix_bytes"]!=512 for e in self.events):
            self.hypotheses.append("Compact 16-element packets are channelwise products accumulated in groups of four, with no second activation.")
            needs_experimental=True
        if any(e["kind"]=="control" and e["words"][0]>>8 in (0x7e,0x7f) for e in self.events):
            self.hypotheses.append("Auxiliary positive subnormal half patterns encode zero runs of code+1; FC weights are HWC, 16-lane tree followed by raster-order accumulation, bias first.")
            needs_experimental=True
        if needs_experimental and not experimental:
            raise ValueError("Resource requires --experimental: "+" ".join(self.hypotheses))
        auxiliary=Path(resource["auxiliary_file"]).read_bytes()
        if digest(auxiliary)!=resource["auxiliary_sha256"]:
            raise ValueError("Auxiliary weights changed")
        expanded=[]
        for code in np.frombuffer(auxiliary,"<u2"):
            if code<1024:
                expanded.extend([0]*(int(code)+1))
            else:
                expanded.append(int(code))
        self.auxiliary=torch.from_numpy(np.array(expanded,dtype="<u2").view("<f2").astype(np.float32))
        self.math=PhysicalStream.__new__(PhysicalStream)
        self.math.policy=dict(POLICY)
        for e in self.events:
            if e["kind"]=="matrix":
                count=e["matrix_bytes"]//2
                shape=(16,16) if count==256 else (16,)
                e["weight"]=torch.from_numpy(np.frombuffer(raw,"<f2",count,e["data_offset"]).astype(np.float32).reshape(shape))
                if e["bias_offset"] is not None:
                    e["bias"]=torch.from_numpy(np.frombuffer(raw,"<f2",16,e["bias_offset"]).astype(np.float32))
        self.trace=[]

    def infer(self,raw_input):
        desc=self.resource["input"]
        if desc["type_raw"]!=2 or desc["row_bytes"]!=desc["width"] or desc["plane_bytes"]!=desc["width"]*desc["height"]:
            raise ValueError("Unreviewed input descriptor")
        if len(raw_input)!=desc["plane_bytes"]*desc["channels"]:
            raise ValueError("Input byte count mismatch")
        pixels=torch.from_numpy(np.frombuffer(raw_input,np.uint8).astype(np.float32)/256).reshape(1,-1)
        banks=torch.zeros(2,1,16,256,256)
        work=torch.zeros(1,max(self.resource["workspace_bytes"],0x100000)//2)
        work_written=torch.zeros(work.shape[-1],dtype=torch.bool)
        head_bytes=max(d["offset"]+d["channels"]*d["plane_bytes"] for d in self.resource["outputs"])
        heads=torch.zeros(1,head_bytes//2)
        head_written=torch.zeros(heads.shape[-1],dtype=torch.bool)
        hw=None
        edge=0
        acc=None
        aux_cursor=0
        self.trace=[]

        def region(x,y,w,h):
            if min(x,y,w,h)<0 or max(x+w,y+h)>256:
                raise ValueError(f"Bank range outside canvas: {x,y,w,h}")

        for e in self.events:
            if e["kind"]=="matrix":
                flags,address=e["flags"],e["address"]
                src=flags>>8&1
                dst=1-src
                h,w=PhysicalStream.shape(hw,edge)
                stride=hw[2]
                sx=(address>>8&255)-bool(edge&16)
                sy=(address&255)-bool(edge&1)
                ys=sy+torch.arange(h)*stride
                xs=sx+torch.arange(w)*stride
                ry=torch.arange(h)*stride-int(bool(edge&1))
                rx=torch.arange(w)*stride-int(bool(edge&16))
                vy=(ys>=0)&(ys<256)&(ry>=0)&(ry<hw[0])
                vx=(xs>=0)&(xs<256)&(rx>=0)&(rx<hw[1])
                x=banks[src][:,:,ys.clamp(0,255)[:,None],xs.clamp(0,255)[None,:]].clone()
                x*=vy[None,None,:,None]*vx[None,None,None,:]
                compact=bool(flags&128)
                part=(rounded(e["weight"][None,:,None,None]*x,"rtz",True)
                      if compact else self.math.dot(e["weight"],x)).double()
                if flags&512:
                    if acc is not None:
                        raise ValueError("Accumulator already active")
                    acc=self.math.zero_add(e["bias"][None,:,None,None],part)
                else:
                    if acc is None or acc.shape!=part.shape:
                        raise ValueError(f"Accumulator shape mismatch at {e['offset']:x}")
                    acc=self.math.zero_add(acc,part)
                acc=rounded(acc,"rtz")
                if flags&1024 or compact and e["compact_group_end"]:
                    if not compact:
                        acc=apply_pcnn_candidate(acc,e["activation"],"negative_pre_round_ftz")
                    acc=rounded(acc,"rtz")
                    ox,oy=address>>24&255,address>>16&255
                    region(ox,oy,w,h)
                    banks[dst,:,:,oy:oy+h,ox:ox+w]=acc
                    bits=acc.numpy().astype("<f2")
                    if not np.isfinite(bits).all():
                        raise ValueError(f"Nonfinite batch {e['batch']}")
                    self.trace.append(dict(batch=e["batch"],kind="compact" if compact else "dense",body_offset=e["offset"],bank=dst,x=ox,y=oy,
                                           shape=list(acc.shape),sha256=digest(bits.tobytes())))
                    acc=None
                continue
            words=e["words"]
            op=words[0]
            major=op>>8
            if major==0x4c:
                hw=(op&255,words[1]&4095,2 if words[1]&4096 else 1)
            elif major==0x70:
                edge=op&255
            elif major==0x44 or op in (0,0x4080):
                pass
            elif major==0x28:
                # Short input transfer selects a descriptor plane and bank lane.
                # This interpretation is structurally checked, not hardware validated.
                if op&15!=8:
                    raise ValueError("Unreviewed short input mode")
                h,w,_=hw
                lane=op>>4&15
                sx,sy=words[1]>>8,words[1]&255
                region(sx,sy,w,h)
                if (w,h)!=(desc["width"],desc["height"]) or lane>=desc["channels"]:
                    raise ValueError("Short input geometry differs from descriptor")
                base=lane*desc["plane_bytes"]
                ix=base+torch.arange(h)[:,None]*desc["row_bytes"]+torch.arange(w)[None,:]
                banks[0,:,lane,sy:sy+h,sx:sx+w]=pixels[:,ix]
            elif major in (0x20,0x22,0x30,0x31,0x32,0x33):
                h,w,_=hw
                address=words[1]<<16|words[2]
                pitch=words[3]
                sx,sy,mask=words[4]>>8,words[4]&255,words[5]
                if not mask or mask&(mask-1):
                    raise ValueError("Multi-lane transfer not supported")
                lane=mask.bit_length()-1
                region(sx,sy,w,h)
                if major==0x20:
                    ix=address+torch.arange(h)[:,None]*pitch+torch.arange(w)[None,:]
                    if ix.max()>=pixels.shape[-1]:
                        raise ValueError("Input transfer outside buffer")
                    banks[0,:,lane,sy:sy+h,sx:sx+w]=pixels[:,ix]
                else:
                    if address%2 or pitch%2:
                        raise ValueError("Unaligned half transfer")
                    ix=address//2+torch.arange(h)[:,None]*(pitch//2)+torch.arange(w)[None,:]
                    target=work if major in (0x22,0x32,0x33) else heads
                    if ix.max()>=target.shape[-1]:
                        raise ValueError(f"Transfer outside buffer at {e['offset']:x}")
                    if major==0x22:
                        if not work_written[ix].all():
                            raise ValueError(f"Unwritten workspace read at {e['offset']:x}")
                        banks[0,:,lane,sy:sy+h,sx:sx+w]=work[:,ix]
                    else:
                        target[:,ix]=banks[major&1,:,lane,sy:sy+h,sx:sx+w]
                        (work_written if major in (0x32,0x33) else head_written)[ix]=True
            elif major in (0x68,0x69):
                if op&255 not in (0xa0,0x20):
                    raise ValueError("Unreviewed pool variant")
                h,w,_=hw
                sx,sy=words[1]>>8,words[1]&255
                ox,oy=words[2]>>8,words[2]&255
                region(sx,sy,w,h)
                z=F.max_pool2d(banks[major&1,:,:,sy:sy+h,sx:sx+w],2)
                region(ox,oy,z.shape[-1],z.shape[-2])
                mask=0xffff if op&0x80 else words[3]
                for lane in range(16):
                    if mask>>lane&1:
                        banks[1-(major&1),:,lane,oy:oy+z.shape[-2],ox:ox+z.shape[-1]]=z[:,lane]
            elif major in (0x7e,0x7f):
                h,w,_=hw
                src=major&1
                sx,sy=words[1]>>8,words[1]&255
                ox,oy=words[2]>>8,words[2]&255
                lane=op>>4&15
                if op&15:
                    raise ValueError("Unreviewed reduction mode")
                region(sx,sy,w,h)
                count=h*w*16
                if aux_cursor+count>len(self.auxiliary):
                    raise ValueError("FC read exceeds expanded auxiliary weights")
                weights=self.auxiliary[aux_cursor:aux_cursor+count].reshape(h*w,16)
                aux_cursor+=count
                values=banks[src,0,:,sy:sy+h,sx:sx+w].permute(1,2,0).reshape(h*w,16)
                products=rounded(weights*values,"rtz",True)
                while products.shape[1]>1:
                    products=self.math.zero_add(products[:,0::2],products[:,1::2])
                bias=np.array([words[3]],dtype="<u2").view("<f2").astype(np.float32)
                reduced=torch.from_numpy(bias)
                for k in range(h*w):
                    reduced=self.math.zero_add(reduced,products[k])
                reduced=apply_pcnn_candidate(reduced.reshape(1,1),[e["activation"][lane]],"negative_pre_round_ftz")
                reduced=rounded(reduced,"rtz")
                region(ox,oy,1,1)
                banks[1-src,0,lane,oy,ox]=reduced[0,0]
                self.trace.append(dict(kind="fc",body_offset=e["offset"],bank=1-src,lane=lane,x=ox,y=oy,
                                       auxiliary_consumed_halves=aux_cursor,sha256=digest(reduced.numpy().astype("<f2").tobytes())))
            else:
                raise ValueError(f"Unimplemented command {op:x}")
        if acc is not None:
            raise ValueError("Unfinished accumulator")
        if aux_cursor!=len(self.auxiliary):
            raise ValueError("Unused auxiliary weights")
        outputs=[]
        for d in self.resource["outputs"]:
            if d["type_raw"]!=1 or d["row_bytes"]%2 or d["plane_bytes"]%2:
                raise ValueError("Unreviewed output descriptor")
            ix=(d["offset"]//2+torch.arange(d["channels"])[:,None,None]*(d["plane_bytes"]//2)
                +torch.arange(d["height"])[None,:,None]*(d["row_bytes"]//2)
                +torch.arange(d["width"])[None,None,:])
            if not head_written[ix].all():
                raise ValueError("Unwritten logical output")
            outputs.append(heads[0,ix].numpy().astype("<f2").view("<u2").copy())
        if not all(np.isfinite(v.view("<f2")).all() for v in outputs):
            raise ValueError("Nonfinite output")
        return dict(outputs=outputs,workspace=work[0].numpy().astype("<f2").view("<u2").copy(),
                    workspace_written=work_written.numpy().copy())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ids",default="740,744,750,754")
    ap.add_argument("--cases",default="stripe,random")
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--experimental",action="store_true")
    ap.add_argument("--input-chw",type=Path,help="One raw uint8 CHW input, for one resource ID; channel order is not asserted to match the camera.")
    ap.add_argument('--inventory',type=Path,required=True)
    ap.add_argument('--bird-source',type=Path)
    args=ap.parse_args()
    if args.bird_source:os.environ['E7_BIRD_SOURCE']=str(args.bird_source)
    _load_backend()
    if args.input_chw and "," in args.ids:
        ap.error("--input-chw requires exactly one resource ID")
    args.output=new_output_dir(args.output)
    torch.set_num_threads(4)
    index=json.loads(args.inventory.read_text())
    report={"scope":"Candidate PCNNP replay; passed means completed with structural checks, not hardware equivalence.",
            "hardware_equivalence":False,
            "source_sha256":{p.name:digest(p.read_bytes()) for p in (HERE/"replay.py",HERE/"packets.py",BIRD/"core.py")},
            "python":sys.version,"numpy":np.__version__,"torch":torch.__version__,"argv":sys.argv,
            "runs":[]}
    for ident in map(int,args.ids.split(",")):
        spec=next(s for s in index["pcnnp"] if s["resource_id"]==ident)
        for case in (["custom"] if args.input_chw else args.cases.split(",")):
            row=dict(resource_id=ident,case=case,body_sha256=spec["body_sha256"],passed=False)
            report["runs"].append(row)
            started=time.monotonic()
            try:
                inp=spec["input"]
                if args.input_chw:
                    raw=args.input_chw.read_bytes()
                    row["input_file"]=str(args.input_chw.resolve())
                elif (inp["width"],inp["height"],inp["channels"])==(384,256,3):
                    raw=(BIRD/"fixtures"/case/"input-chw-u8.bin").read_bytes()
                elif (inp["width"],inp["height"],inp["channels"])==(32,32,3):
                    source=np.frombuffer((BIRD/"fixtures"/case/"input-chw-u8.bin").read_bytes(),np.uint8).reshape(3,256,384)
                    raw=source[:,:32,:32].copy().tobytes()
                    row["input_derivation"]="Top-left 32x32 CHW crop of the public 384x256 fixture; no expected output provided."
                else:
                    raise ValueError("Input fixture shape not covered")
                replay=Replay(spec,args.experimental)
                row["unverified_operation_hypotheses"]=replay.hypotheses
                with torch.inference_mode():
                    result=replay.infer(raw)
                row["input_sha256"]=digest(raw)
                row["outputs"]=[dict(shape=list(a.shape),sha256=digest(a.tobytes()),
                                     min=float(a.view("<f2").min()),max=float(a.view("<f2").max())) for a in result["outputs"]]
                row["batches"]=len(replay.trace)
                row["auxiliary_expanded_halves"]=len(replay.auxiliary)
                row["captured_oracle_available"]=ident==744 and not args.input_chw
                if row["captured_oracle_available"]:
                    want=np.load(BIRD/"fixtures"/case/"expected.npz")
                    mask=np.load(BIRD/"fixtures/workspace-mask.npy")
                    diffs={name:int((got!=want[name]).sum()) for name,got in zip(("fine","coarse"),result["outputs"])}
                    diffs["workspace"]=int((result["workspace"][:len(mask)][mask]!=want["defined_workspace"]).sum())
                    row["oracle_differences"]=diffs
                    if any(diffs.values()):
                        raise ValueError("Captured bird fixture differs")
                stem=f"{ident}-{case}"
                np.savez_compressed(args.output/f"{stem}.npz",**{f"head{i}":a for i,a in enumerate(result["outputs"])})
                (args.output/f"{stem}-trace.json").write_text(json.dumps(replay.trace,indent=2))
                row["passed"]=True
            except Exception as exc:
                row["error"]=repr(exc)
            row["seconds"]=time.monotonic()-started
            (args.output/"verification.json").write_text(json.dumps(report,indent=2))
            print(json.dumps(row),flush=True)
    if not all(r["passed"] for r in report["runs"]):
        raise SystemExit(1)


if __name__=="__main__":
    main()
