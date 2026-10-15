# SPDX-License-Identifier: GPL-2.0-or-later
"""Real CPU-free DMA/IRQ transactions; not native allocation or photography."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from test_z8_producer_journal import (Lab as BaseLab, full_chain, configure_d980,
    next_d980, run151, configure151, configure1540, require, A, B)

OPTION='output-buffer-ownership-model'
STATE='output-buffer-ownership-state'

class Lab(BaseLab):
    def __init__(self,binary,folder,enabled):
        super().__init__(binary,folder,True)
        self.command[self.command.index('-M')+1]+=','+OPTION+'='+('on' if enabled else 'off')

def pending1540(g):
    configure_d980(g);next_d980(g,1);_,_,raw=run151(g,A,0,True)
    target=configure1540(g,raw);g.write(0x15400008,1)
    o=g.state(STATE)
    require(not o['slots'][0]['held'] and o['slots'][1]['held'],'151 ACK releases only151,1540 retained')
    require(o['slots'][1]['complete'] and g.irq(0x131),'Actual1540 DMA before IRQ')
    before=g.read_ram(target,2048)
    g.write(0x15400020,0);g.write(0x1540002c,1)
    require(g.state(STATE)==o,'Mask and unrelated W1C must not retire ownership')
    require(not g.irq(0x131),'Mask gates IRQ delivery only')
    g.write(0x15400020,0x100)
    require(g.irq(0x131),'Live IRQ mask restores pending delivery')
    return target,before

def retirement(g):
    target,data=pending1540(g)
    g.write(0x1540002c,0x100)
    require(not g.state(STATE)['slots'][1]['held'] and not g.irq(0x131),'Native W1C retires1540')
    require(g.read_ram(target,2048)==data,'ACK does not change pixels')
    next_d980(g,2);_,_,raw=run151(g,B,1)
    configure1540(g,raw,0);g.write(0x15400008,1)
    require(g.state(STATE)['slots'][1]['job']==2,'Same address reuse has new generation')
    g.write(0x1540002c,0x100)
    return g.state(STATE)

def overwrite(g):
    target,data=pending1540(g);next_d980(g,2)
    configure151(g,B,1);g.pair(0x151004c0,target)
    before=g.state('engine151-raw-output-state');g.expected_unknown=0x15100008
    g.write(0x15100008,1)
    require(g.state('engine151-raw-output-state')==before,'Rejected cross-engine overwrite cannot complete')
    require(g.read_ram(target,2048)==data,'Pending1540 bytes protected from151 overwrite')
    require(g.state(STATE)['slots'][1]['held'],'Rejected write retains owner')
    return g.state(STATE)

def immutable(g,enabled):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=not enabled)
    except RuntimeError as error:require('immutable' in str(error),'Option immutable error')
    else:raise AssertionError('Live ownership option changed')
    return g.state(STATE)

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cases=[]
    plans=[('chain-off',False,lambda g:full_chain(g,True)),
           ('chain-on',True,lambda g:full_chain(g,True)),
           ('retire-reuse',True,retirement),('cross-engine-overwrite',True,overwrite),
           ('immutable-off',False,lambda g:immutable(g,False)),
           ('immutable-on',True,lambda g:immutable(g,True))]
    for name,enabled,body in plans:
        row=dict(name=name,passed=False);folder=a.output/name;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='z8-owner-') as tmp:
            g=Lab(a.qemu,Path(tmp),enabled)
            try:
                g.start();row['observed']=body(g)
                require(g.qmp.command('query-status')['running'] is False,'Stopped QTest required')
                require(('Z9_UNKNOWN' in g.text())==(name=='cross-engine-overwrite'),'Strict fault state')
                row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:
                g.close();row['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
                (folder/'stderr.txt').write_text(g.text())
                (folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        cases.append(row);print(json.dumps({k:row[k] for k in row if k!='observed'}),flush=True)
    equivalent=all(c['passed'] for c in cases[:2]) and cases[0]['observed']==cases[1]['observed']
    result=dict(passed=equivalent and all(c['passed'] and c['owned_qemu_stopped'] for c in cases),
        cases=cases,defaultoff_equivalence=equivalent,firmware_used=False,native_allocator_verified=False,
        binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest())
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
