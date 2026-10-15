# SPDX-License-Identifier: GPL-2.0-or-later
"""Fail-closed virtual barrier realization dependencies; no firmware execution."""
import argparse,json,subprocess,tempfile
from pathlib import Path
from test_z8_still_hevc_backend import Lab
from test_z8_producer_journal import require
OPTION='z8-still-hevc-virtual-completion-model'
def main():
 p=argparse.ArgumentParser()
 for n in ('qemu','c1-snapshot','output'):p.add_argument('--'+n,type=Path,required=True)
 a=p.parse_args();a.output.mkdir(exist_ok=False);rows=[]
 for name in ('backend-required','icount-required'):
  with tempfile.TemporaryDirectory(prefix='z8-vc-realize-') as t:
   g=Lab(a.qemu,Path(t),json.loads(a.c1_snapshot.read_text()),name!='backend-required');cmd=list(g.command)
   cmd[cmd.index('-M')+1]+=','+OPTION+'=on'
   if name=='icount-required':cmd[cmd.index('-accel')+1]='tcg,thread=single'
   proc=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
   expected='requires x265 backend' if name=='backend-required' else 'requires icount or QTest'
   require(proc.returncode!=0 and expected in proc.stderr,'Exact dependency guard: '+name)
   rows.append(dict(case=name,rc=proc.returncode,passed=True,stderr=proc.stderr,command=cmd))
 (a.output/'result.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
