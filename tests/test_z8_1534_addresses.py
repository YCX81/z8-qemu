# SPDX-License-Identifier: GPL-2.0-or-later
"""H format2 native relocated descriptor replay with real synthetic D980 frames."""
import argparse,json,struct,tempfile
from pathlib import Path
import test_z8_1534_reference as old
from test_z8_producer_journal import require
OPTION='z8-heif-1534-address-model'
def run(q,out,fixture,case):
 data=fixture.read_bytes();r=list(struct.unpack_from('<84I',data));c=list(struct.unpack_from('<84I',data,336));rp=data[672:2656];cp=bytearray(data[2656:]);source=(r[0xb0//4]<<32)|r[0xb4//4]
 row=dict(case=case,passed=False,native_mmio_fixture=True,firmware_executed=False,actual_ram=True)
 with tempfile.TemporaryDirectory(prefix='1534-address-') as d:
  g=old.Lab(q,Path(d),True)
  if case!='off':g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
  try:
   g.start()
   if case=='immutable':
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as e:require('immutable' in str(e),'Immutable error')
    else:raise AssertionError('Live property change')
   else:
    profile=dict(old.PROFILE);profile.update({0:2,4:8280<<16,8:5520<<16,0x100:12448,0x118:0})
    for i in range(4):profile[0x120+i*8]=source&0xffffffff;profile[0x124+i*8]=source>>32
    g.configure(profile,0x4000);g.write(0x150c020c,0x4000000);g.enable(0x4000);g.periodic();g.advance()
    require(g.state('primary-d980-state')['frames']==1,'First real input')
    def config(bank,controls,blob):
     g.write_ram(old.PROGRAMS[bank],blob)
     # Replay writable retained fields; +4 is command and +2c is W1C.
     for i,v in enumerate(controls):
      if i*4 in (4,0x2c) or v==0:continue
      g.write(old.BASES[bank]+i*4,v)
    config(1,r,rp);g.write(old.BASES[1]+4,1);g.write(old.BASES[1]+0x2c,1)
    require(g.state(old.STATE)['reference_valid'],'Accepted bank1 reference')
    g.write(0x150c0210,0x4000000);g.advance();require(g.state('primary-d980-state')['frames']==2,'Second real input')
    if case=='non-address':cp[0x94]^=1
    if case=='unaligned':cp[0xb0]^=1
    if case=='out-of-ram':struct.pack_into('<II',cp,0xb0,0xffffff00,15)
    if case=='control':c[0x40//4]=10
    if case=='tail':cp[0x790]^=1
    config(0,c,cp);before=g.read_ram(source,4096)
    good=case in ('positive','tail','repeat')
    if not good:g.expected_unknown=old.BASES[0]+4
    g.write(old.BASES[0]+4,1);s=g.state(old.STATE)
    require(s['jobs']==int(good),'Admission result')
    require(g.read_ram(source,4096)==before,'Actual source unchanged')
    require(g.irq(0xfb)==good,'Completion only after actual RAM work')
    if good:
     require(s['source_sha256']==s['readback_sha256'] and s['bytes']==12448*5520,'Full-plane readback')
     require(s['accepted_program_sha256']!=s['reference_program_sha256'],'Real relocation')
     g.write(old.BASES[0]+0x2c,1)
     if case=='repeat':
      g.write(old.BASES[0]+4,0);g.expected_unknown=old.BASES[0]+4;g.write(old.BASES[0]+4,1)
      require(g.state(old.STATE)['jobs']==1 and not g.irq(0xfb),'No cross-frame repeated consume')
    row['state']={k:v for k,v in s.items() if not k.endswith('_hex') and 'control' not in k}
   row['passed']=True
  except Exception as e:row['error']=repr(e)
  finally:
   g.close();out.mkdir(parents=True,exist_ok=False);(out/'stderr.txt').write_text(g.text())
   (out/'transactions.json').write_text(json.dumps(g.transactions,indent=2))
 (out/'result.json').write_text(json.dumps(row,indent=2));print(json.dumps(row),flush=True);return row['passed']
def main():
 p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--fixture',type=Path,required=True);a=p.parse_args()
 rows=[run(a.qemu,a.output/c,a.fixture,c) for c in ('off','positive','tail','repeat','non-address','unaligned','out-of-ram','control','immutable')]
 if not all(rows):raise SystemExit(1)
if __name__=='__main__':main()
