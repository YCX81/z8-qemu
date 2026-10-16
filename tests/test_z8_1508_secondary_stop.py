# SPDX-License-Identifier: GPL-2.0-or-later
"""Real source/group MMIO stop transactions; no OS or IRQ injection."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_producer_journal import Lab,require
OPTION='z8-heif-secondary-stop-model'
def run(q,out,case):
 row={'case':case,'passed':False,'firmware_executed':False}
 with tempfile.TemporaryDirectory(prefix='secondary-stop-') as d:
  g=Lab(q,Path(d),False)
  props=['event1508-group-model','event1508-global1-model','event1508-secondary-timing-model']
  g.command[g.command.index('-M')+1]+=','+','.join(p+'=on' for p in props)
  if case!='off':g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
  try:
   g.start();g.write(0x15080068,0x40000)
   if case=='immutable':
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as e:require('immutable' in str(e),'Immutable error')
    else:raise AssertionError('Live change accepted')
   else:
    g.write(0x15080018,1);g.write(0x15080028,2)
    if case!='never-running':g.write(0x15083408,2);g.write(0x1508340c,1)
    if case!='no-stop':g.write(0x15083418,2);g.write(0x1508341c,1)
    require(not g.irq(0x101),'Group stop alone is not source stop')
    g.write(0x1508001c,1)
    expected=case in ('positive','mask')
    require(bool(g.state('event1508-global1-state')['pending']&0x40000)==expected,'Stop notification provenance')
    require(g.irq(0x101)==expected,'Actual IRQ')
    if expected:
     g.write(0x15080068,0);require(not g.irq(0x101),'Mask only')
     g.write(0x1508006c,0);require(g.state('event1508-global1-state')['pending']==0x40000,'Zero W1C')
     g.write(0x15080068,0x40000);require(g.irq(0x101),'Unmask')
     g.write(0x1508006c,0x40000);require(not g.irq(0x101),'Native W1C')
     g.write(0x1508001c,1);g.advance()
     require(not g.state('event1508-global1-state')['pending'],'No repeated completion')
     require(g.state('z8-heif-secondary-stop-state')['notifications']==1,'One transaction')
    g.no_pixels()
   row['passed']=True
  except Exception as e:row['error']=repr(e)
  finally:
   g.close();out.mkdir(parents=True,exist_ok=False);(out/'stderr.txt').write_text(g.text());(out/'transactions.json').write_text(json.dumps(g.transactions,indent=2))
 (out/'result.json').write_text(json.dumps(row,indent=2));print(json.dumps(row));return row['passed']
def main():
 p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 results=[run(a.qemu,a.output/c,c) for c in ('off','positive','mask','never-running','no-stop','immutable')]
 if not all(results):raise SystemExit(1)
if __name__=='__main__':main()
