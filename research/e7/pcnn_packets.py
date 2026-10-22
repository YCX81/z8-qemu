# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded PCNN instruction parser. Unsupported commands fail closed."""
from pathlib import Path
import collections
import os
import hashlib
import json
import struct

HERE = Path(__file__).resolve().parent
BIRD = Path(os.environ.get("E7_BIRD_SOURCE", HERE/"private-inputs/e7-bird-network"))


class UnsupportedPacket(ValueError):
    pass


def parse(raw):
    p=0
    events=[]
    activation=[(0,0,0)]*16
    batch=0
    active=False
    while p<len(raw):
        op=struct.unpack_from("<H",raw,p)[0]
        major=op>>8
        if op & 0xF800 == 0xA000:
            matrix_bytes=32 if op&0x80 else 512
            size=6+matrix_bytes+(32 if op & 512 else 0)
            if p+size>len(raw):
                raise ValueError("Truncated matrix")
            compact=bool(op&0x80)
            if compact and active:
                raise ValueError(f"Compact packet inside dense accumulator at {p:x}")
            if compact:
                pass
            elif op & 512:
                if active:
                    raise ValueError("Nested accumulator start")
                active=True
            elif not active:
                raise ValueError("Accumulator continuation without start")
            events.append(dict(offset=p, kind="matrix", flags=op,
                               address=struct.unpack_from("<I",raw,p+2)[0],
                               data_offset=p+6, matrix_bytes=matrix_bytes,
                               bias_offset=p+6+matrix_bytes if op&512 else None,
                               batch=None if compact else batch,
                               activation=list(activation) if compact or op&1024 else None))
            if not compact and op & 1024:
                active=False
                batch+=1
        else:
            if major in (0x20,0x22,0x30,0x31,0x32,0x33):
                size=12
            elif major in (0x68,0x69) and op&255 in (0xa0,0x20):
                size=6 if op&0x80 else 8
            elif major in (0x28,0x38):
                size=4
            elif major in (0x7e,0x7f):
                size=8
            elif major==0x4c:
                size=4
            elif op==0x4400:
                size=16
            elif major in (0x70,0x44) or op in (0,0x4080):
                size=2
            else:
                raise UnsupportedPacket(f"Unsupported {op:#06x} at body+{p:#x}")
            if p+size>len(raw):
                raise ValueError("Truncated control")
            words=struct.unpack_from("<"+"H"*(size//2),raw,p)
            if major==0x44 and op&0x80:
                activation=[(op&3,op>>2&1,op>>3&15)]*16
            elif op==0x4400:
                kinds=words[1]|words[2]<<16
                shifts=sum(words[4+i]<<(16*i) for i in range(4))
                activation=[(kinds>>(2*i)&3,words[3]>>i&1,shifts>>(4*i)&15) for i in range(16)]
            event=dict(offset=p,kind="control",words=words,size=size)
            if major in (0x7e,0x7f):
                event["activation"]=list(activation)
            events.append(event)
        p+=size
    if active or not events or events[-1].get("words")!=(0,):
        raise ValueError("Missing termination or unfinished accumulator")
    for i,event in enumerate(events):
        if event["kind"]=="matrix" and event["flags"]&128:
            nxt=events[i+1] if i+1<len(events) else {}
            event["compact_group_end"]=(nxt.get("kind")!="matrix" or not nxt.get("flags",0)&128 or bool(nxt.get("flags",0)&512))
    return events


def verify_bird():
    reference=json.loads((BIRD/"commands.json").read_text())
    raw=(BIRD/"resource744.body.bin").read_bytes()
    events=parse(raw)
    if len(events)!=len(reference["events"]):
        raise ValueError("Reference event count mismatch")
    matrices=activations=0
    for got,(off,kind,want) in zip(events,reference["events"]):
        if (got["offset"],got["kind"])!=(off,kind):
            raise ValueError(f"Boundary mismatch at {off:x}")
        if kind=="control":
            if list(got["words"])!=want["words"] or got["size"]!=want["size"]:
                raise ValueError("Control mismatch")
        else:
            b=want["block"]
            expected=(int(b["flags"],16),int(b["address"],16),int(b["data_body_offset"],16),
                      int(b["bias_body_offset"],16) if b["bias_body_offset"] else None,want["batch"])
            actual=tuple(got[k] for k in ("flags","address","data_offset","bias_offset","batch"))
            if actual!=expected:
                raise ValueError("Matrix mismatch")
            matrices+=1
            if got["activation"] is not None:
                if [list(v) for v in got["activation"]]!=reference["activations"][str(got["batch"])]:
                    raise ValueError("Activation mismatch")
                activations+=1
    return dict(events=len(events), matrices=matrices, activations=activations, all_equal=True)


def main():
    result={"bird_parser_regression":verify_bird(),"resources":[]}
    for item in json.loads((HERE/"inventory.json").read_text())["pcnnp"]:
        row={"resource_id":item["resource_id"],"body_sha256":item["body_sha256"]}
        raw=Path(item["body_file"]).read_bytes()
        try:
            events=parse(raw)
            row.update(parsed=True,events=len(events),matrices=sum(e["kind"]=="matrix" for e in events),
                       batches=sum(e["kind"]=="matrix" and not e["flags"]&128 and e["activation"] is not None for e in events),
                       compact_packets=sum(e["kind"]=="matrix" and bool(e["flags"]&128) for e in events),
                       control_counts=dict(collections.Counter(hex(e["words"][0]) for e in events if e["kind"]=="control")))
        except ValueError as exc:
            row.update(parsed=False,error=str(exc))
        result["resources"].append(row)
        print(row["resource_id"],{k:v for k,v in row.items() if k not in ("control_counts","body_sha256")})
    (HERE/"packet-audit.json").write_text(json.dumps(result,indent=2))
    print(result["bird_parser_regression"])


if __name__=="__main__":
    main()
