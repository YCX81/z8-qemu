# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded static descriptor recovery for the two pinned E7 MAIN samples.

This is an empirical, sample-scoped parser, NOT a CDNN SDK decoder or an
inference engine. Geometry is an interpretation of serialized fields,
cross-checked by graph consistency and fused-pool dimensions. Unknown fields
are retained as unknown; no firmware, model blob, or weights are written.
"""
from pathlib import Path
import argparse, os
from e7_inputs import new_output_dir, private_firmware_dir
import hashlib
import json
import re
import struct

HERE = Path(__file__).resolve().parent
FIRMWARE = Path(os.environ.get('E7_FIRMWARE_DIR', HERE/'private-inputs/firmware'))
SAMPLES = {
    'z8-c311': '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853',
    'z9-c532': '6380ded18395ce11cb327c766721274c85127d2cf1a1e887ee60e3d156810bdd',
}
KNOWN_BLOBS = {
    '1a14b38f7905898d4de87f477d7ec945f4cbcbdcd28482640f16cffc3e156a76': 'vehicle',
    '418f21fae72c525a7d9b598f0375b520464daeabda8018696e1db69405bb2541': 'animal',
}
MARKER = b'\x09\0\0\0XM6NeuPro\0'
RECORD = re.compile(rb'\x25\x00(.{4})(data\x00|layer[0-9]+-(?:conv|maxpool)\x00)', re.S)

def need(condition, message):
    if not condition:
        raise ValueError(message)

def u32(b, p):
    need(0 <= p <= len(b)-4, 'u32 outside buffer')
    return struct.unpack_from('<I', b, p)[0]

def output_dim(n, k, stride, p0, p1, dilation=1):
    need(stride > 0 and k > 0 and dilation > 0, 'invalid geometry')
    return (n+p0+p1-dilation*(k-1)-1)//stride+1

def parse_blob(blob, *, enforce_identity=True):
    need(blob.startswith(MARKER), 'target marker mismatch')
    ds, ps = u32(blob, 0x40), u32(blob, 0x44)
    need(len(blob) == 0x40+ds+ps, 'container length mismatch')
    sha = hashlib.sha256(blob).hexdigest()
    if enforce_identity:
        need(sha in KNOWN_BLOBS, 'unreviewed model identity')
    need(u32(blob, 0x50) == 14 and blob[0x54:0x62] == b'Darkent2Caffe\0', 'model label mismatch')
    count = u32(blob, 0x62)
    desc_end = 0x40+ds
    matches = list(RECORD.finditer(blob[:desc_end]))
    need(len(matches) == count, 'record count mismatch')
    layers = []
    for idx, m in enumerate(matches):
        name = m[2][:-1].decode('ascii')
        need(u32(m[1],0) == len(m[2]), 'name length mismatch')
        e = m.end()
        input_layer, pool = name == 'data', name.endswith('-maxpool')
        need(blob[e+6] == 1, 'unsupported reference count')
        parent = u32(blob,e+7)
        need((idx==0 and input_layer and parent==0xffffffff) or
             (idx>0 and not input_layer and parent<idx), 'non-topological parent')
        cpos = 36 if input_layer else 32 if pool else 34
        channels = u32(blob,e+cpos)
        need(0 < channels <= 1024, 'invalid channel field')
        layer = dict(index=idx,name=name,descriptor_record_offset=hex(m.start()),
                     fields_after_name_offset=hex(e),
                     serialized_local_id=u32(blob,e+2),
                     parent_index=None if input_layer else parent,
                     boundary_flags=list(blob[e:e+2]),channels=channels,
                     evidence='serialized fields; semantic labels are empirically reconstructed')
        if input_layer:
            need(idx==0, 'input must be first')
            width,height,batch = [u32(blob,e+p) for p in (65,69,73)]
            need(width>0 and height>0 and batch==1, 'unsupported input geometry')
            layer.update(kind='input',shape_whc=[width,height,channels],batch=batch,
                         input_dimension_fields_offset=hex(e+65),
                         precision_field_raw=u32(blob,e+77),
                         channel_order='unresolved',memory_layout='unresolved',normalization='unresolved')
        else:
            iw,ih,ic = layers[parent]['shape_whc']
            scpos, prefix = (29,56) if pool else (31,58)
            scale_flag = blob[e+scpos]
            need(scale_flag in (0,1), 'unsupported scale vector flag')
            k = e+prefix+(5*channels if scale_flag else 0)
            layer.update(parameter_offset=hex(k),per_channel_vector_present=bool(scale_flag))
            if scale_flag:
                scales=struct.unpack_from('<'+'f'*channels,blob,e+prefix)
                need(all(0<v<1 for v in scales), 'invalid positive scale candidates')
                layer['scale_candidate_range']=[min(scales),max(scales)]
                layer['scale_vector_offset']=hex(e+prefix)
                layer['per_channel_byte_vector_offset']=hex(e+prefix+4*channels)
            if pool:
                mode,kw,kh,sx,sy,pl,pr,pt,pb = struct.unpack_from('<9I',blob,k)
                need(mode==0 and (kw,kh,sx,sy)==(2,2,2,2), 'unsupported pool')
                need(channels==ic, 'pool changed channels')
                ow=output_dim(iw,kw,sx,pl,pr);oh=output_dim(ih,kh,sy,pt,pb)
                layer.update(kind='maxpool',kernel=[kw,kh],stride=[sx,sy],padding=[pl,pr,pt,pb])
            else:
                need(u32(blob,k)==channels, 'filters do not match channel field')
                kw,kh=blob[k+7:k+9]
                vals=struct.unpack_from('<20I',blob,k+9)
                dx,dy,sx,sy,unknown,pl,pr,pt,pb=vals[:9]
                need(kw in (1,3) and kh==kw and dx==dy==1 and sx==sy and sx in (1,2), 'unsupported convolution')
                ow=output_dim(iw,kw,sx,pl,pr,dx);oh=output_dim(ih,kh,sy,pt,pb,dy)
                layer.update(kind='convolution',kernel=[kw,kh],stride=[sx,sy],
                             dilation=[dx,dy],padding=[pl,pr,pt,pb],
                             activation_code_raw=list(blob[k+4:k+7]),
                             parameter_words_raw=list(vals),
                             weight_precision_field_raw=u32(blob,k+91))
                # layer8-maxpool is fused into layer7-conv in both samples.
                # Observed pool mode 2 disables the union. In vehicle layer1,
                # inactive union bytes retain printable fragments; do not
                # interpret those bytes as active geometry.
                need(vals[17] in (0,2), 'unsupported fused-pool mode')
                layer['fused_pool_mode_raw']=vals[17]
                if vals[17] == 0:
                    need(vals[13:17]==(2,2,2,2), 'unsupported fused pool')
                    need(vals[11]==ow and vals[12]==ow*oh, 'fused pool extent cross-check failed')
                    layer['fused_pool']=dict(kind='maxpool',kernel=[2,2],stride=[2,2],
                                            serialized_prepool_width=vals[11],
                                            serialized_prepool_elements=vals[12],
                                            prepool_shape_whc=[ow,oh,channels])
                    ow=output_dim(ow,2,2,0,0);oh=output_dim(oh,2,2,0,0)
                need(u32(blob,k+99)==channels, 'second channel count mismatch')
            need(ow>0 and oh>0, 'nonpositive inferred output')
            layer['shape_whc']=[ow,oh,channels]
        layers.append(layer)
    referenced={p['parent_index'] for p in layers if p['parent_index'] is not None}
    terminal=[p['index'] for p in layers if p['index'] not in referenced]
    need(len(terminal)==2 and all(layers[i]['kernel']==[1,1] for i in terminal), 'unexpected terminal branches')
    result=dict(identity=KNOWN_BLOBS.get(sha,'unreviewed'),sha256=sha,total_bytes=len(blob),
                target='XM6NeuPro',embedded_model_label='Darkent2Caffe',
                descriptor_bytes=ds,payload_bytes=ps,payload_offset=hex(desc_end),
                declared_records=count,parsed_records=len(layers),
                input=layers[0],terminal_indices=terminal,
                terminal_shapes_whc=[layers[i]['shape_whc'] for i in terminal],layers=layers,
                limitations=[
                    'Shapes are static reconstructions, not outputs measured by executing the CEVA decoder.',
                    'This restricted parser recovers selected record fields, not all CDNN format semantics.',
                    'Tail flags also mark internal partition boundaries; terminal graph nodes identify the two final outputs.',
                    'Precision fields are raw serialized values; packing, signedness and exact numerical semantics are unresolved.',
                    'The parameter payload may include accelerator commands and padding as well as weights.',
                    'Thirty output channels do not establish thirty animal classes.',
                ])
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware-dir',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    firmware=args.firmware_dir or private_firmware_dir()
    result={'schema':'e7-model-static-recovery-v1','samples':{}}
    blobs={}
    for model,expected in SAMPLES.items():
        data=(firmware/f'{model}-main.bin').read_bytes()
        need(hashlib.sha256(data).hexdigest()==expected,'MAIN identity mismatch')
        items=[]
        for m in re.finditer(re.escape(MARKER),data):
            o=m.start();length=0x40+u32(data,o+0x40)+u32(data,o+0x44)
            blob=data[o:o+length];p=parse_blob(blob)
            p['main_offset']=hex(o);p['arm_load_view_address']=hex(0x40160000+o)
            items.append(p);blobs[(model,p['identity'])]=blob
        need(len(items)==2,'expected exactly two recognized model containers')
        result['samples'][model]=dict(main_sha256=expected,models=items)
    result['cross_model_exact_comparison']={kind:blobs[('z8-c311',kind)]==blobs[('z9-c532',kind)] for kind in ['animal','vehicle']}
    (new_output_dir(args.output)/'models.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    for model,sample in result['samples'].items():
        for p in sample['models']:
            print(model,p['identity'],p['main_offset'],p['parsed_records'],'records',p['input']['shape_whc'],'->',p['terminal_shapes_whc'])
    print('Identical:',result['cross_model_exact_comparison'])

if __name__=='__main__':
    main()
