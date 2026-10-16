# SPDX-License-Identifier: GPL-2.0-or-later
"""Two actual HLG pixel generations; native held-C1 reconfiguration."""
import argparse,json,tempfile
from pathlib import Path
import test_z8_hlg_pipeline as p
from test_z8_producer_journal import require
OPTION='z8-heif-c1-rearm-model'
def run(q,out,snapshot,case):
 row=dict(case=case,passed=False,firmware_executed=False,native_mmio_fixture=True)
 with tempfile.TemporaryDirectory(prefix='hlg-rearm-') as d:
  g=p.Lab(q,Path(d),True,snapshot)
  if case!='off':g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
  try:
   g.start()
   if case=='immutable':
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as e:require('immutable' in str(e),'Immutable error')
    else:raise AssertionError('Live toggle')
   else:
    p.producer(g);p.configure(g);g.write(0x15432004,0x101);g.write(0x15180030,1)
    require(g.state(p.STATE)['jobs']==1,'Actual first C1 output')
    if case!='pending':
     g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
    g.write(0x15180030,0);g.write(0x1518000c,1<<21)
    if case!='not-held':g.write(0x15432004,0x102)
    if case not in ('same-frame','pending'):
     g.write(0x150c0210,0x4000000);g.advance();require(g.state('primary-d980-state')['frames']==2,'Actual second D980')
     g.write(0x15100008,1);g.write(0x15100044,2)
     require(g.state('engine151-capture-pixels-state')['jobs']==2,'Actual second 151')
    before=g.state(p.STATE);good=case=='positive'
    if not good:g.expected_unknown=0x15432004
    g.write(0x15432004,0x103)
    c=g.state('engine1543-control-state')['blocks'][1]
    require(c['active']!=good,'Only retired held photo receiver reconfigures')
    require(g.state(p.STATE)==before,'Reconfiguration neither clears pending nor changes output/job receipt')
    if good:
     require(not c['held'] and g.state('z8-heif-c1-rearm-state')['rearms']==1,'One rearm')
     p.configure(g);g.write(0x15432004,0x101);g.write(0x15180030,1)
     require(g.state(p.STATE)['jobs']==2 and g.state(p.STATE)['source_frame']==2,'Actual second C1 DMA')
    row['state']=g.state(p.STATE)
   row['passed']=True
  except Exception as e:row['error']=repr(e)
  finally:
   g.close();out.mkdir(parents=True,exist_ok=False);(out/'stderr.txt').write_text(g.text());(out/'transactions.json').write_text(json.dumps(g.transactions,indent=2))
 (out/'result.json').write_text(json.dumps(row,indent=2));print(json.dumps({k:v for k,v in row.items() if k!='state'}),flush=True);return row['passed']
def main():
 a=argparse.ArgumentParser();a.add_argument('--qemu',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--snapshot',type=Path,required=True);args=a.parse_args();snap=json.loads(args.snapshot.read_text())
 rows=[run(args.qemu,args.output/c,snap,c) for c in ('off','positive','pending','not-held','same-frame','immutable')]
 if not all(rows):raise SystemExit(1)
if __name__=='__main__':main()
