# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU-free actual D980/HLG RAM producer -> exact400e held reset transaction."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from test_z8_hlg_capture import Lab as HlgLab,setup
from test_z8_producer_journal import configure_d980,next_d980,configure151,A,require

OPTION='z8-heif-menu-stop-model';STATE='z8-heif-menu-stop-state'
MASK=0x400e;KICK=0x8045;CAUSE=0x200d000

class Lab(HlgLab):
    def __init__(self,binary,folder,enabled=True):
        super().__init__(binary,folder,True)
        self.command[self.command.index('-M')+1]+=','+OPTION+'='+('on' if enabled else 'off')

def units(g):
    for bit in (2,4,8):g.write(0x150c0060,bit)
    g.write(0x150c0064,0xe);g.write(0x150c0008,0xe)
    for a in (0x150c0200,0x150c020c,0x150c0218):g.write(a,0)

def ready(g,sdr=False):
    if sdr:
        configure_d980(g);next_d980(g,1);y,uv,_=configure151(g,A,0,True)
        g.write(0x1510021c,1)
    else:y,uv,_,_=setup(g,2)
    g.write(0x15100008,1);g.write(0x15100044,2)
    units(g)
    require(g.state('engine150c-state')['active']&MASK==MASK,'Active source prerequisite')
    return [hashlib.sha256(g.read_ram(p,1024)).hexdigest() for p in (y,uv)]

def protected(g):
    e=g.state('engine150c-state')
    return dict(control={k:e[k] for k in ('active','latched','stop_requested','stop_done','stop_pending',
        'stop_reset_asserted','stop_reset_released','direct_stop_done','direct_stop_pending','direct_stop_pending1')},
        data=g.state('primary-d980-state'),source=hashlib.sha256(g.read_ram(A,2048)).hexdigest())

def request(g):g.write(0x150c000c,MASK)
def kick(g):g.write(0x150c0018,MASK);g.write(0x150c0034,KICK)
def next_generation(g):
    g.write(0x150c020c,0x4000000);next_d980(g,2);g.write(0x150c020c,0)

def complete(g,split=False):
    ready(g);before=protected(g);request(g)
    require(g.state('engine150c-state')['stop_requested']==MASK,'Request owns all400e')
    require(g.state('engine150c-state')['stop_pending']==0 and not g.irq(0x106),'Request must not complete')
    g.advance()
    require(g.state('engine150c-state')['stop_requested']==MASK,'Empty-end timer stole2/4 before kick')
    g.write(0x150c0218,CAUSE);kick(g)
    e=g.state('engine150c-state')
    require(e['stop_pending']==CAUSE and e['stop_done']==MASK and not e['active']&MASK,'Group2 stop only after kick')
    require(not(e['direct_stop_pending']|e['direct_stop_pending1']),'Not direct drain')
    require(g.irq(0x106),'Enabled group2 stop IRQ')
    require(g.tx('readl 0x150c014c')==0x40,'Native group8 stop-idle bit')
    g.write(0x150c021c,0);g.write(0x150c021c,0x400)
    require(g.irq(0x106),'Zero/unrelated W1C cannot consume stop')
    parts=((2,0x1000),(4,0x4000),(8,0x8000),(0x4000,0x2000000)) if split else ((MASK,CAUSE),)
    for mask,cause in parts:
        g.write(0x150c021c,cause);g.write(0x150c0000,mask);g.write(0x150c0004,mask)
        g.write(0x150c0018,g.tx('readl 0x150c0018')&~mask);g.write(0x150c0014,mask)
    after=protected(g)
    require(after['data']==before['data'] and after['source']==before['source'],'Held stop changed completed DMA or data pending')
    require(not after['control']['latched']&MASK and g.state(STATE)['owned_mask']==0,'Original reset/ACK retirement')
    require(g.tx('readl 0x150c014c')==0,'Stop-idle retired with original ACK')
    require(not g.irq(0x106),'Stop IRQ retired while data remains masked')
    g.write(0x150c020c,0x4000000)
    require(g.irq(0x106),'Preserved old data pending visible when enabled')
    g.write(0x150c0210,0x4000000)
    require(not g.irq(0x106) and g.state('primary-d980-state')['acks']==1,'Only original data W1C ACKs data')
    return g.state(STATE)

def reject(g,pre=lambda g:None,op=request,address=0x150c000c,sdr=False):
    hashes=ready(g,sdr);pre(g);before=protected(g)
    g.expected_unknown=address;op(g)
    require(protected(g)==before,'Rejected control transaction changed state/RAM/data pending')
    return dict(state=g.state(STATE),hashes=hashes)

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    plans=[('complete',True,complete),('split',True,lambda g:complete(g,True)),
        ('default-off',False,reject),('repeat',True,lambda g:reject(g,request)),
        ('partial-request',True,lambda g:reject(g,op=lambda g:g.write(0x150c000c,0x4006))),
        ('kick-before-request',True,lambda g:reject(g,op=lambda g:g.write(0x150c0034,KICK),address=0x150c0034)),
        ('kick-before-hold',True,lambda g:reject(g,request,lambda g:g.write(0x150c0034,KICK),0x150c0034)),
        ('partial-kick',True,lambda g:reject(g,lambda g:(request(g),g.write(0x150c0018,MASK)),lambda g:g.write(0x150c0034,0x45),0x150c0034)),
        ('overlapping-direct',True,lambda g:reject(g,request,lambda g:g.write(0x150c000c,0x4000))),
        ('ack-before-w1c',True,lambda g:reject(g,lambda g:(request(g),kick(g),g.write(0x150c0000,MASK),g.write(0x150c0004,MASK),g.write(0x150c0018,0)),lambda g:g.write(0x150c0014,MASK),0x150c0014)),
        ('wrong-width',True,lambda g:reject(g,op=lambda g:g.tx('writew 0x150c000c 0x400e'))),
        ('changed-generation',True,lambda g:reject(g,next_generation)),
        ('data-already-acked',True,lambda g:reject(g,lambda g:g.write(0x150c0210,0x4000000))),
        ('sdr-off',False,lambda g:reject(g,sdr=True)),('sdr-on',True,lambda g:reject(g,sdr=True))]
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as e:require('immutable' in str(e),'Immutable failure')
        else:raise AssertionError('Live model change accepted')
    plans.append(('immutable',True,immutable));rows=[]
    for name,on,fn in plans:
        row=dict(name=name,passed=False,firmware_executed=False,actual_ram=True)
        with tempfile.TemporaryDirectory(prefix='heif-menu-stop-') as tmp:
            g=Lab(a.qemu,Path(tmp),on)
            try:g.start();row['observed']=fn(g);row['passed']=True
            except Exception as e:row['error']=repr(e)
            finally:
                g.close();folder=a.output/name;folder.mkdir()
                (folder/'stderr.txt').write_text(g.text())
                (folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
        rows.append(row)
    hashes={r['name']:r.get('observed',{}).get('hashes') for r in rows if r['name'].startswith('sdr-')}
    same=hashes.get('sdr-off') is not None and hashes.get('sdr-off')==hashes.get('sdr-on')
    report=dict(cases=rows,sdr_hashes_unchanged=same,passed=all(r['passed'] for r in rows) and same)
    (a.output/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)
if __name__=='__main__':main()
