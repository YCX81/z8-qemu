# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit every Z8 NEF against its own paused RAW output snapshot.

Adapted per-photo invariants from z9-qemu c6dbfc4. Directory ordinal is never
used as native job identity. This proves supplied file/pixel consistency;
writer lifetime, JPEG-job binding and recovery remain separate acceptance.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import numpy as np
from z8_capture_bindings import load_profile
from z8_precapture_audit import decode_rawpy, _nikon_tiff, _path, _integer
from z8_export_nefs import _compare_export
from z8_capture_pixels import geometry

def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()

def checked_json(root, ref):
    path=_path(root,ref['path'])
    if path.stat().st_size>32*1024*1024 or sha(path)!=ref['sha256']:
        raise ValueError('Evidence JSON identity/size differs')
    return json.loads(path.read_text(encoding='utf-8'))

def audit_photos(manifest, root, *, decoder=None):
    root=Path(root).resolve()
    report=dict(schema='z8-photo-audit-v1',status='failed',native_accepted=False,
        raw_precapture_verified=False,burst_mode_verified=False,
        native_writer_binding_verified=False,jpeg_job_binding_verified=False,
        photo_recovery_verified=False,physical_camera_verified=False,
        files=[],issues=[],samples_compared=0,injected_decoder=decoder is not None)
    try:
        if manifest['schema']!='z8-photo-audit-v1':raise ValueError('Unknown photo audit schema')
        codec=manifest.get('codec','packed14-transport')
        if codec not in ('packed14-transport','nikon-f0-lossless14'):raise ValueError('Unknown codec contract')
        native=checked_json(root,manifest['run']);export=checked_json(root,manifest['export'])
        main=load_profile()['main_sha256']
        if (native.get('firmware_sha256')!=main or native.get('owned_qemu_stopped') is not True or
            native.get('error') is not None or native.get('cpu_register_redirection') is not False or
            native.get('ram_patches')!=[] or native.get('executed_program_sha256',main)!=main):
            raise ValueError('Stopped unmodified pinned Z8 run required')
        if (export.get('schema')!='z8-nef-export-v1' or export.get('status')!='exported' or
            export.get('source_main_sha256')!=main or export.get('executed_program_sha256')!=main):
            raise ValueError('Original Z8 export identity required')
        storage=export['storage'];files=storage['nef_files'];photos=manifest['photos']
        snapshots=native['raw_output_snapshots']
        if (storage.get('kind')!='guest_nvme_export' or storage.get('catalog_complete') is not True or
            not isinstance(photos,list) or not 1<=len(photos)<=8 or
            len(photos)!=len(files) or len(photos)!=len(snapshots)):
            raise ValueError('Complete bounded mapping of every file and snapshot required')
        by_id={r['file_id']:r for r in files}
        if len(by_id)!=len(files):raise ValueError('Duplicate exported file identity')
        card=_path(root,storage['card_path']);seen=set();paths=set();frame=0;prior_bytes=0;spans=[]
        watched={p:p.stat() for p in (_path(root,manifest['run']['path']),_path(root,manifest['export']['path']),card)}
        decode=decode_rawpy if decoder is None else decoder
        for job,(mapping,snapshot) in enumerate(zip(photos,snapshots),1):
            seq=snapshot['photo_sequence'];producer=snapshot['producer']
            if (type(mapping['job']) is not int or mapping['job']!=job or
                snapshot['device_job_counter']!=job or seq['job']!=job or
                seq['previous_job']!=job-1 or seq['previous_bytes']!=prior_bytes or
                snapshot['device_frame_id']<=frame or seq['frame']!=snapshot['device_frame_id'] or
                snapshot.get('main_sha256')!=main or snapshot.get('kind')!='engine151_raw_output' or
                snapshot.get('paused_during_snapshot') is not True or
                snapshot.get('device_identity_unchanged') is not True or
                snapshot.get('guest_state_modified') is not False):
                raise ValueError('Fresh ordered model job/frame snapshot required')
            amount=_integer(seq['per_job_bytes'],'job bytes',1,128*1024*1024)
            if seq['cumulative_bytes']-prior_bytes!=amount:raise ValueError('Cumulative counter is not per-job length')
            prior_bytes=seq['cumulative_bytes'];frame=snapshot['device_frame_id']
            file_id=mapping['file_id']
            if file_id in seen or file_id not in by_id:raise ValueError('One unique actual NEF per photo required')
            seen.add(file_id);row=by_id[file_id];nef=_path(root,row['path'])
            if nef in paths or row.get('export_status')!='exported':raise ValueError('Exported file alias/incomplete file')
            paths.add(nef)
            watched[nef]=nef.stat()
            _integer(row['bytes'],'NEF bytes',8,256*1024*1024)
            if sha(nef)!=row['sha256'] or nef.stat().st_size!=row['bytes']:raise ValueError('Exported NEF changed')
            for span in row['extents']:
                start=_integer(span['offset'],'card offset');end=start+_integer(span['length'],'extent length',1)
                if end>card.stat().st_size:raise ValueError('NEF extent outside actual card')
                if any(start<b and a<end for a,b in spans):raise ValueError('NEF card extents overlap')
                spans.append((start,end))
            _compare_export(card,nef,row['extents'],row['bytes'],row['sha256'])
            _nikon_tiff(nef)
            codec_receipt=None
            if codec=='nikon-f0-lossless14':
                from z8_nef_codec import require_f0
                codec_receipt=require_f0(nef)
            source=_path(root,producer['path']);width=producer['width'];height=producer['height']
            watched[source]=source.stat()
            stride=producer['stride_samples'];bits=producer['bit_depth']
            geometry(width,height,((width*bits+7)//8+31)&~31,bits)
            _integer(stride,'normalized stride',width,65536)
            if (producer.get('origin')!='guest_producer_dma' or producer.get('encoding')!='u16le' or
                source.stat().st_size!=stride*height*2 or sha(source)!=producer['sha256']):
                raise ValueError('Actual complete normalized producer snapshot required')
            actual=decode(nef)
            pixels=np.asarray(actual.samples)
            if (pixels.shape!=(height,width) or pixels.dtype.kind!='u' or pixels.dtype.itemsize!=2 or
                actual.white_level!=(1<<bits)-1):raise ValueError('Independent full RAW geometry/depth differs')
            mismatches=0
            with source.open('rb') as stream:
                for y in range(height):
                    expected=np.frombuffer(stream.read(stride*2),dtype='<u2')[:width]
                    mismatches+=int(np.count_nonzero(expected!=pixels[y]))
            report['files'].append(dict(job=job,frame=frame,file_id=file_id,
                nef_sha256=row['sha256'],producer_sha256=producer['sha256'],
                samples_compared=width*height,mismatching_samples=mismatches,decoder=actual.decoder,
                codec_profile=codec_receipt))
            report['samples_compared']+=width*height
            if mismatches:raise ValueError('Photo RAW samples differ from its own producer')
        if any(p.stat()!=before for p,before in watched.items()):raise ValueError('Evidence changed during audit')
        report.update(status='synthetic_checks_passed' if decoder is not None else 'pixels_verified',
            all_photo_raw_samples_verified=decoder is None,
            scope='All supplied original card files versus ordered model RAW snapshots; no native writer/JPEG/recovery or history acceptance')
    except (ValueError,OSError,KeyError,TypeError,OverflowError,subprocess.SubprocessError) as error:
        report['issues'].append(str(error))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.manifest.stat().st_size>32*1024*1024:parser.error('Manifest exceeds limit')
    result=audit_photos(json.loads(args.manifest.read_text(encoding='utf-8')),args.manifest.resolve().parent)
    result['manifest_sha256']=sha(args.manifest);result['auditor_sha256']=sha(Path(__file__))
    with args.output.open('x',encoding='utf-8') as out:json.dump(result,out,indent=2);out.write('\n')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='pixels_verified' else 1

if __name__=='__main__':raise SystemExit(main())
