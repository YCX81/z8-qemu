# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit C3.11 raw resources and optionally standard containers/NISI parts.

Uses a separately installed nikon-fw-key-tool decoder and private key file.
No decrypted firmware or key material is exported. Signature absence does not
prove absence of compressed, differently encoded, or unrecognized resources.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

from z8_af_lockon_probe import MAIN_SHA, require

PACKAGE_SHA='d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96'


def occurrences(data,needle):
    result=[]
    offset=0
    while True:
        offset=data.find(needle,offset)
        if offset<0:
            return result
        result.append(hex(offset))
        offset+=1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--decoder',type=Path,required=True)
    parser.add_argument('--key',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--scan-compressed',action='store_true',
                        help='Also inspect bounded standard compression containers')
    args=parser.parse_args()
    require(not args.output.exists(),'Refusing to overwrite evidence')
    raw=args.package.read_bytes()
    require(len(raw)==99051117 and hashlib.sha256(raw).hexdigest()==PACKAGE_SHA,'Wrong Z8 package')
    spec=importlib.util.spec_from_file_location('nikon_decoder',args.decoder)
    decoder=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    decoder.check_scheme(raw,str(args.package))
    plain=decoder.decrypt(raw,*decoder.load_key(args.key))
    modules=decoder.validate_firmware(plain[decoder.HDR:])
    patterns={'AF_zero_padded_16':b'AF'+bytes(14),
              'AUDIO_zero_padded_16':b'AUDIO'+bytes(11),
              'HAIMENMONI':b'HAIMENMONI','ENDM':b'ENDM',
              'reference_timing_200_300_500_700_1000':struct.pack('<5H',200,300,500,700,1000)}
    rows=[]
    main_image=None
    for name,offset,length in modules:
        data=plain[decoder.HDR+offset:decoder.HDR+offset+length-2]
        digest=hashlib.sha256(data).hexdigest()
        if name=='eg2070_0311b0.bi':
            require(digest==MAIN_SHA,'Extracted MAIN identity mismatch')
            main_image=data
        row=dict(name=name,bytes=len(data),sha256=digest,
                 raw_signature_matches={key:occurrences(data,needle) for key,needle in patterns.items()})
        if args.scan_compressed:
            from z8_af_compression_scan import scan, inspect_nisi
            row['compression_scan']=scan(data,patterns)
            if name=='li2070_0311b0.bi':
                row['nisi']=inspect_nisi(data,patterns)
            print(f'Checked {name}: {len(row["compression_scan"]["complete_streams"])} complete standard streams',flush=True)
        rows.append(row)
    require(main_image is not None,'MAIN not found')
    item_offset=0x4c27948+0x25*0x30
    group_offset=0x4c28ab8+0x12*0x28
    item_id=struct.unpack_from('<I',main_image,item_offset)[0]
    offset=struct.unpack_from('<Q',main_image,item_offset+8)[0]
    length=struct.unpack_from('<I',main_image,item_offset+0x18)[0]
    group_id=struct.unpack_from('<I',main_image,item_offset+0x24)[0]
    rom=struct.unpack_from('<Q',main_image,group_offset)[0]
    require((item_id,group_id,offset,length,rom)==(0x25,0x12,0x10800,0x2d00,0x462e0000),
            'Original resource descriptors changed')
    require(hashlib.sha256(args.package.read_bytes()).hexdigest()==PACKAGE_SHA,'Package changed during audit')
    result=dict(schema=1,package_sha256=PACKAGE_SHA,all_module_and_package_crcs_valid=True,
                source_unchanged=True,decoder_sha256=hashlib.sha256(args.decoder.read_bytes()).hexdigest(),
                modules=rows,resource=dict(item=hex(item_id),group=hex(group_id),offset=hex(offset),
                                          length=hex(length),rom=hex(rom+offset),
                                          parameters_rom=hex(rom+offset+0xc04)),
                factory_af_body_recovered=False,
                search_limit='Raw signatures across all five decrypted modules; encoded/compressed forms not excluded',
                real_calibration_validation_ready=False)
    if args.scan_compressed:
        result['search_limit']='Raw signatures, bounded standard framed streams, and declared NISI gzip/cpio parts across all five modules; raw, dictionary, other nested and proprietary encodings remain unresolved'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2)
        stream.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
