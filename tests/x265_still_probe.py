# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile/run the standalone 10-bit x265 experiment in Linux; no QEMU or firmware.

Exit 0: same configuration produces type19 or an explicitly reported candidate
for the authorized single-picture 20-to-19 transform at both tile test sizes.
The probe itself does not transform any bytes. Exit 2: no candidate. Exit 1: setup
failure. The report is evidence for review, never automatic device enablement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

CASES = [('main10',1,0,1),('main10',16,0,1),('main10',16,2,1),
         ('main10-intra',1,0,1),('main10-intra',16,2,1),('main10',16,0,0)]
SIZES = [(320,224),(768,2752),(8256,224)]
PTL_KEYS=('profile_space','tier','profile_idc','compatibility','constraint_flags_hex','level_idc')

def normalize_ptl(row):
    sps=row.get('sps')
    ptl={k:sps[k] for k in PTL_KEYS} if sps is not None else None
    if 'ptl' in row and row['ptl']!=ptl:raise ValueError('PTL disagrees with parsed SPS')
    row['ptl']=ptl


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def compatible_configs(rows):
    required={(320,224),(768,2752)}
    good={}
    for row in rows:
        if row.get('returncode')==0 and row.get('status')=='encoded' and row.get('type19_compatible') is True:
            good.setdefault(row['case'],set()).add((row['width'],row['height']))
    return sorted(key for key,sizes in good.items() if required<=sizes)


def run(args):
    if os.name=='nt':raise RuntimeError('Compile/run with installed WSL libx265; no package installation')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).with_suffix('.c').resolve()
    report=dict(schema=1,firmware_executed=False,qemu_executed=False,
        source_sha256=sha(source),parser_sha256=sha(source.parents[1]/'qemu/nikon-z8-hevc-sps.h'),
        wrapper_sha256=sha(__file__),cases=[],compatible_configs=[],review_required=True)
    def command(argv,timeout=30):
        result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,check=True)
        return result.stdout.strip()
    try:
        report['pkg_config_version']=command(['pkg-config','--modversion','x265'])
        flags=shlex.split(command(['pkg-config','--cflags','--libs','x265']))
        includedir=Path(command(['pkg-config','--variable=includedir','x265']))
        report['headers']={str(p):sha(p) for p in (includedir/'x265.h',includedir/'x265_config.h') if p.is_file()}
        binary=out/'x265-still-probe'
        argv=[args.cc,'-std=c11','-O2','-Wall','-Wextra','-Werror',str(source),*flags,'-o',str(binary)]
        report['compile_command']=argv
        built=subprocess.run(argv,capture_output=True,text=True,timeout=60)
        (out/'compile.log').write_text(built.stdout+built.stderr)
        if built.returncode:raise RuntimeError('Compile failed; see compile.log')
        report['binary_sha256']=sha(binary)
        report['ldd']=command(['ldd',str(binary)])
        report['libraries']={p:sha(p) for p in re.findall(r'libx265[^\s]*\s+=>\s+(\S+)',report['ldd']) if Path(p).is_file()}
        deadline=time.monotonic()+900
        for width,height in SIZES:
            for profile,keyint,bframes,total_frames in CASES:
                if args.ptl_only and total_frames!=0:continue
                name=f'{profile}-k{keyint}-b{bframes}'+('-tf0' if total_frames==0 else '')
                folder=out/f'{width}x{height}-{name}';folder.mkdir()
                argv=[str(binary),str(width),str(height),str(keyint),str(bframes),profile,str(folder/'sample.h265'),str(total_frames)]
                row=dict(case=name,width=width,height=height,command=argv)
                remaining=deadline-time.monotonic()
                try:
                    if remaining<=0:raise subprocess.TimeoutExpired(argv,0)
                    r=subprocess.run(argv,capture_output=True,text=True,timeout=min(args.timeout,remaining))
                    (folder/'stdout.json').write_text(r.stdout)
                    (folder/'stderr.log').write_text(r.stderr)
                    row['returncode']=r.returncode
                    parsed=json.loads(r.stdout)
                    normalize_ptl(parsed)
                    if parsed.get('width')!=width or parsed.get('height')!=height:raise ValueError('Probe identity mismatch')
                    row.update(parsed)
                    stream=folder/'sample.h265'
                    if stream.exists():row['stream_sha256']=sha(stream);row['stream_bytes']=stream.stat().st_size
                except subprocess.TimeoutExpired as error:
                    row.update(status='timeout',type19_compatible=False,returncode=None)
                    for filename,value in (('stdout.json',error.stdout),('stderr.log',error.stderr)):
                        (folder/filename).write_bytes(value if isinstance(value,bytes) else (value or '').encode())
                except (ValueError,OSError) as error:
                    row.update(status='invalid_probe_receipt',error=repr(error),type19_compatible=False)
                report['cases'].append(row)
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
                print(f'{width}x{height} {name}: {row.get("status")} type19={row.get("type19_compatible",False)}',flush=True)
        report['compatible_configs']=compatible_configs(report['cases'])
        report['ptl']=[dict(case=r['case'],width=r['width'],height=r['height'],**r['ptl'])
                       for r in report['cases'] if r.get('ptl') is not None]
        transformed=[dict(r,type19_compatible=r.get('transform20_candidate',False)) for r in report['cases']]
        report['approved_transform_configs']=compatible_configs(transformed)
        report['decision']='candidate_requires_review' if report['compatible_configs'] else 'stop_no_type19_candidate'
        if report['approved_transform_configs']:report['decision']='candidate_with_declared_transform'
    except Exception as error:
        report.update(decision='setup_failed',error=repr(error))
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Receipt:',out/'summary.json',flush=True)
    return 1 if report['decision']=='setup_failed' else 0 if (report['compatible_configs'] or report.get('approved_transform_configs')) else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cc',default='cc')
    p.add_argument('--ptl-only',action='store_true',help='Only main10 keyint16 bframes0 totalFrames0; raw NAL types unchanged')
    p.add_argument('--timeout',type=int,choices=range(10,301),default=120)
    raise SystemExit(run(p.parse_args()))


if __name__=='__main__':main()
