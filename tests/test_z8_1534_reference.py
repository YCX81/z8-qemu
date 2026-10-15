# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU-free real RAM/IRQ reference witness, not native firmware acceptance."""
import argparse,hashlib,json,struct,tempfile
from pathlib import Path
from test_z8_producer_journal import Lab as BaseLab,A,require
from test_z8_d980_internal import PROFILE
OPTION='z8-heif-1534-inplace-model';STATE='z8-heif-1534-state'
BASES=(0x15340000,0x15380000);PROGRAMS=(0xa02c97500,0xa02c97d00)
STRIDE=96;BYTES=STRIDE*16

class Lab(BaseLab):
    def __init__(self,binary,folder,enabled):
        super().__init__(binary,folder,False)
        self.command[self.command.index('-M')+1]+=(',engine153-config-model=on,engine153-inplace-identity-model=on,'+
            OPTION+'='+('on' if enabled else 'off'))

def source_setup(g):
    c=dict(PROFILE);c.update({0:2,4:64<<16,8:16<<16,0x100:STRIDE,0x118:0})
    for i in range(4):c[0x120+i*8]=A&0xffffffff;c[0x124+i*8]=A>>32
    g.configure(c,0x4000);g.write(0x150c020c,0x4000000);g.enable(0x4000);g.periodic()

def frame(g,n):
    if n>1:g.write(0x150c0210,0x4000000)
    g.advance();d=g.state('primary-d980-state')
    require(d['frames']==n and d['last_physical']==A and d['cache_format']==2,'Real packed12 D980 generation')
    return g.read_ram(A,BYTES)

def program():
    b=bytearray(0x7c0);struct.pack_into('<II',b,0,2,STRIDE);struct.pack_into('<I',b,0x90,64|(16<<16));return bytes(b)

def configure(g,bank,blob=None):
    base=BASES[bank];p=PROGRAMS[bank]
    g.write_ram(p,program() if blob is None else blob)
    for off,value in ((0x20,0x401),(0x40,11),(0x44,1),(0x48,p&0xffffffff),(0x4c,p>>32),
                      (0x74,1),(0x94,1),(0xb0,A>>32),(0xb4,A&0xffffffff)):
        g.write(base+off,value)

def reference(g,ack=True):
    source_setup(g);data=frame(g,1);configure(g,1);g.write(BASES[1]+4,1)
    require(g.irq(0xfc) and g.read_ram(A,BYTES)==data,'Existing bank1 completes actual in-place RAM')
    if ack:g.write(BASES[1]+0x2c,1)
    return hashlib.sha256(data).hexdigest()

def positive(g,change_ram=False):
    first=reference(g);data=frame(g,2)
    if change_ram:data=bytes([0x39])*BYTES;g.write_ram(A,data)
    configure(g,0);g.write(BASES[0]+4,1)
    s=g.state(STATE)
    require(s['jobs']==1 and s['consumed_frame']==2 and s['used_reference_frame']==1,'Bank0 generation witness')
    require(s['source_sha256']==s['readback_sha256']==hashlib.sha256(data).hexdigest(),'Actual RAM readback before success')
    require(s['source_sha256']!=first,'New D980 input is not the first frame cache')
    require(s['accepted_program_sha256']==s['reference_program_sha256'],'Exact program equality')
    require(g.read_ram(A,BYTES)==data and g.irq(0xfb),'Actual data preserved and bank0 IRQ')
    g.write(BASES[0]+0x20,0);require(not g.irq(0xfb),'IRQ mask works')
    g.write(BASES[0]+0x2c,0);require(g.state(STATE)['pending']==1,'Zero ACK preserves pending')
    g.write(BASES[0]+0x20,1);require(g.irq(0xfb),'Unmask retains pending')
    g.write(BASES[0]+0x2c,1);require(not g.irq(0xfb),'W1C retires bank0')
    g.write(BASES[0]+4,0);g.expected_unknown=BASES[0]+4;g.write(BASES[0]+4,1)
    require(g.state(STATE)['jobs']==1 and g.read_ram(A,BYTES)==data,'Duplicate generation rejected')
    return dict(bank1_hash=first,state=s)

def rejected(g,kind):
    if kind=='missing-reference':source_setup(g);frame(g,1);first=None
    else:first=reference(g,kind!='unacked-reference')
    if kind!='same-frame':frame(g,2)
    configure(g,0)
    if kind=='control':g.write(BASES[0]+0x40,10)
    if kind=='program':
        b=bytearray(program());b[0x20]^=1;g.write_ram(PROGRAMS[0],b)
    if kind=='pointer':g.write(BASES[0]+0x48,0x2c97520)
    if kind=='data-acked':g.write(0x150c0210,0x4000000)
    data=g.read_ram(A,BYTES);before=g.state('engine153-frame-pass-state')['banks'][0]
    g.expected_unknown=BASES[0]+4;g.write(BASES[0]+4,1)
    require(g.state('engine153-frame-pass-state')['banks'][0]==before,'Reject did not publish result')
    require(g.read_ram(A,BYTES)==data and not g.irq(0xfb),'Reject preserves source and no IRQ')
    return dict(bank1_hash=first,state=g.state(STATE))

def frozen_receipt(g):
    first=reference(g);frame(g,2);configure(g,0);g.write(BASES[0]+4,1);g.write(BASES[0]+0x2c,1)
    saved=g.state(STATE);frame(g,3);g.write(BASES[1]+4,0);configure(g,1);g.write(BASES[1]+4,1);g.write(BASES[1]+0x2c,1)
    later=g.state(STATE)
    require(later['reference_frame']==3 and later['used_reference_frame']==1,'Current reference and accepted witness separated')
    for k in ('accepted_control','accepted_program_hex','accepted_program_sha256','source_sha256','readback_sha256','bytes'):
        require(later[k]==saved[k],'Accepted receipt overwritten: '+k)
    return dict(bank1_hash=first,state=later)

def immutable(g):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as e:require('immutable' in str(e),'Immutable option error')
    else:raise AssertionError('Live model changed')
    return {}

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    plans=[('positive',True,positive),('ram-not-cache',True,lambda g:positive(g,True)),('default-off',False,lambda g:rejected(g,'off')),
        *[(k,True,lambda g,k=k:rejected(g,k)) for k in ('missing-reference','unacked-reference','same-frame','control','program','pointer','data-acked')],
        ('frozen-receipt',True,frozen_receipt),('immutable',True,immutable)]
    rows=[]
    for name,on,fn in plans:
        row=dict(name=name,passed=False,firmware_executed=False,native_mmio_fixture=False,actual_ram=True)
        with tempfile.TemporaryDirectory(prefix='1534-reference-') as d:
            g=Lab(a.qemu,Path(d),on)
            try:g.start();row['observed']=fn(g);row['passed']=True
            except Exception as e:row['error']=repr(e)
            finally:
                g.close();out=a.output/name;out.mkdir()
                (out/'stderr.txt').write_text(g.text());(out/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
        rows.append(row)
    hashes={r['name']:r.get('observed',{}).get('bank1_hash') for r in rows}
    unchanged=hashes['positive'] is not None and hashes['positive']==hashes['default-off']
    report=dict(cases=rows,bank1_hash_unchanged=unchanged,passed=all(r['passed'] for r in rows) and unchanged)
    (a.output/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)
if __name__=='__main__':main()
