# SPDX-License-Identifier: GPL-2.0-or-later
"""Extract all bounded PCNNP resources from the pinned MAIN images.

The ID mapping is recovered from the Z8 resource tables, not assigned by shape.
No camera mode names are inferred here.
"""
from pathlib import Path
import hashlib
import argparse
from e7_inputs import new_output_dir, private_firmware_dir
import json
import re
import struct

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parents[1]
PINS = {
    "z8-c311": "2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853",
    "z9-c532": "6380ded18395ce11cb327c766721274c85127d2cf1a1e887ee60e3d156810bdd",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def descriptor(raw, off):
    size, address, width, height, channels, pitch, plane, kind = struct.unpack_from("<8I", raw, off)
    if size != 32 or not (0 < width <= 4096 and 0 < height <= 4096 and 0 < channels <= 4096):
        raise ValueError(f"Invalid descriptor at {off:x}")
    return dict(offset=address, width=width, height=height, channels=channels,
                row_bytes=pitch, plane_bytes=plane, type_raw=kind)


def scan(raw):
    found = []
    for match in re.finditer(b"PCNNP\0\0\0", raw):
        off = match.start()
        header_size, body_size = struct.unpack_from("<I", raw, off+24)[0], struct.unpack_from("<I", raw, off+32)[0]
        inputs, outputs = struct.unpack_from("<2I", raw, off+64)
        if not (inputs == 1 and 1 <= outputs <= 8 and header_size == 72+32*(inputs+outputs)+8):
            raise ValueError(f"Unrecognized header at {off:x}")
        if off+header_size+body_size > len(raw):
            raise ValueError("Truncated resource")
        body = raw[off+header_size:off+header_size+body_size]
        auxiliary_offset=struct.unpack_from("<I", raw, off+40)[0]
        auxiliary_bytes=struct.unpack_from("<I", raw, off+48)[0]
        auxiliary=raw[off+auxiliary_offset:off+auxiliary_offset+auxiliary_bytes]
        if auxiliary_offset<header_size+body_size or len(auxiliary)!=auxiliary_bytes:
            raise ValueError("Invalid auxiliary extent")
        found.append(dict(chunk=len(found), main_offset=off, body_offset=off+header_size,
                          header_bytes=header_size, body_bytes=body_size, body_sha256=sha(body),
                          workspace_bytes=struct.unpack_from("<I", raw, off+20)[0],
                          auxiliary_offset=off+auxiliary_offset, auxiliary_bytes=auxiliary_bytes,
                          auxiliary_sha256=sha(auxiliary),
                          input=descriptor(raw, off+72),
                          outputs=[descriptor(raw, off+104+32*i) for i in range(outputs)]))
    return found


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    firmware=args.firmware_dir or private_firmware_dir()
    images = {}
    result = {"scope": "Pinned MAIN images; recognized PCNNP and XM6NeuPro markers. Not proof of absence of other formats.",
              "firmware": {}, "pcnnp": [], "cdnn": []}
    for name, pin in PINS.items():
        raw = (firmware/f"{name}-main.bin").read_bytes()
        if sha(raw) != pin:
            raise ValueError(f"{name}: MAIN hash mismatch")
        images[name] = raw
        result["firmware"][name] = dict(sha256=pin, bytes=len(raw), pcnnp=scan(raw))
        for m in re.finditer(b"\x09\0\0\0XM6NeuPro\0", raw):
            off=m.start()
            ds, ps = struct.unpack_from("<2I", raw, off+0x40)
            total=0x40+ds+ps
            if off+total > len(raw):
                raise ValueError("CDNN length outside MAIN")
            result["cdnn"].append(dict(firmware=name, main_offset=off, bytes=total,
                                       sha256=sha(raw[off:off+total]), descriptor_bytes=ds, payload_bytes=ps))
    z8 = images["z8-c311"]
    output=new_output_dir(args.output)
    extracted = output/"resources"
    extracted.mkdir(exist_ok=True)
    chunks = result["firmware"]["z8-c311"]["pcnnp"]
    if len(chunks) != 9:
        raise ValueError("Z8 resource table count needs new review")
    for i in range(9):
        ident, zero, kind, index, zero2 = struct.unpack_from("<5I", z8, 0x4B1E330+20*i)
        start, end, third = struct.unpack_from("<3Q", z8, 0x4B1E258+24*index)
        if (zero, kind, zero2) != (0, 3, 0):
            raise ValueError("Unexpected ID record")
        # The packed resource-region offsets share this affine mapping.
        candidate = [c for c in chunks if c["main_offset"] == start-0x40160000]
        if len(candidate) != 1:
            raise ValueError(f"ID {ident}: range did not resolve")
        item = dict(candidate[0], resource_id=ident, range_index=index, range_start=start, range_end=end)
        body=z8[item["body_offset"]:item["body_offset"]+item["body_bytes"]]
        path=extracted/f"resource{ident}.body.bin"
        path.write_bytes(body)
        item["body_file"]=str(path)
        auxiliary=z8[item["auxiliary_offset"]:item["auxiliary_offset"]+item["auxiliary_bytes"]]
        aux_path=extracted/f"resource{ident}.aux.bin"
        aux_path.write_bytes(auxiliary)
        item["auxiliary_file"]=str(aux_path)
        item["identical_z9_offsets"]=[m.start() for m in re.finditer(re.escape(body[:64]), images["z9-c532"])
                                     if images["z9-c532"][m.start():m.start()+len(body)] == body]
        item["identical_z9_auxiliary_offsets"]=[
            z9["auxiliary_offset"] for z9 in result["firmware"]["z9-c532"]["pcnnp"]
            if z9["body_sha256"]==item["body_sha256"] and z9["auxiliary_sha256"]==item["auxiliary_sha256"]]
        result["pcnnp"].append(item)
        print(ident, item["body_bytes"], [(o["width"],o["height"],o["channels"]) for o in item["outputs"]],
              "Z9 matches",len(item["identical_z9_offsets"]))
    (output/"inventory.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
