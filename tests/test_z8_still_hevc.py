# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU-free QTest protocol checks; authored request, no codec or firmware."""
import argparse
import json
from pathlib import Path
import tempfile
from test_z8_producer_journal import Lab as BaseLab, require
from z8_still_hevc_fixture import RESET, INIT, CONFIG

BASE=0x16020000
STATE='z8-still-hevc-state'


class Lab(BaseLab):
    def __init__(self,binary,folder,enabled):
        super().__init__(binary,folder,False)
        if enabled:
            self.command[self.command.index('-M')+1] += ',z8-still-hevc-model=on'


def sequence(g, pairs):
    for off, value in pairs: g.write(BASE+off,value)


def setup(g):
    sequence(g,[(0x7f8,0x544d3234),(0x7fc,0x36332020)]+RESET+INIT)
    require(g.state(STATE)['initialized'],'Exact ordered initialization')


def reject(g, off, value, verb='writel'):
    g.expected_unknown=BASE+off; g.write(BASE+off,value,verb)
    require('Z9_UNKNOWN' in g.text(),'Required strict UNKNOWN')
    require(not g.irq(0xfa),'No fabricated completion IRQ')


def run_case(binary, name, out, snapshot=None):
    result=dict(name=name, passed=False, firmware_executed=False, native_mmio_fixture=False)
    with tempfile.TemporaryDirectory(prefix='z8-hevc-') as tmp:
        g=Lab(binary,Path(tmp),name!='default-off')
        try:
            g.start()
            if name=='default-off':
                sequence(g,[(0x7f8,0x544d3234),(0x7fc,0x36332020)])
                reject(g,0x128,0)
            elif name=='bad-width': reject(g,0x128,0,'writew')
            elif name=='unaligned': reject(g,0x129,0)
            else:
                setup(g)
                if name in ('run06-profile','run06-changed-qp'):
                    native=json.loads(snapshot.read_text())['device_states'][STATE]
                    words=[(int(k,16),v['value']) for k,v in native['registers'].items() if v['request_written']]
                    require(len(words)==47,'Complete native request fixture')
                    sequence(g,words)
                    require(not g.state(STATE)['native_profile_observed'],'Before command2 not a prepared native match')
                    if name=='run06-changed-qp':g.write(BASE+0x704,13)
                    g.write(BASE+0x100,2)
                    require(g.state(STATE)['native_profile_observed']==(name=='run06-profile'),'Exact tuple, not geometry-only provenance')
                    reject(g,0x100,1)
                    result['native_mmio_fixture']=True
                elif name=='incomplete': reject(g,0x100,2)
                elif name=='bad-input': reject(g,0xf00,1)
                elif name=='bad-profile': reject(g,0x310,0x15802041)
                elif name=='result':
                    g.expected_unknown=BASE+0x900;g.tx(f'readl {BASE+0x900:#x}')
                    require('Z9_UNKNOWN' in g.text(),'Result read stays strict')
                elif name=='abort':
                    sequence(g,[(0x128,0),(0xfe8,0),(8,0),(0xff4,1)])
                    require(g.tx(f'readl {BASE+0xfe4:#x}')==1,'Abort control ACK only')
                    require(not g.state(STATE)['initialized'],'Abort invalidates request')
                    sequence(g,RESET+INIT)
                else:
                    sequence(g,CONFIG)
                    # Sentinels are actual guest RAM; no result/NAL DMA is allowed.
                    spans=[(0x880000000,64),(0xa15591b00,64)]
                    for pa,n in spans:g.write_ram(pa,b'\xa5'*n)
                    if name=='bad-span':
                        # A legal 36-bit address in unbacked space. Config writes
                        # are inert, but command2 must verify actual RAM.
                        g.write(BASE+0xf04,9)
                        reject(g,0x100,2)
                    elif name=='overlap':
                        for i in range(4):g.write(BASE+0xe40+i*8,(0x860000000+i*0x1000000)>>4)
                        reject(g,0x100,2)
                    else:
                        g.write(BASE+0x100,2)
                        require(g.tx(f'readl {BASE+0x100:#x}')==0,'Command2 self clears')
                        require(g.state(STATE)['prepared'],'Complete request configured')
                        if name=='immutable':
                            try:g.qmp.command('qom-set',path='/machine',property='z8-still-hevc-model',value=False)
                            except RuntimeError as error:require('immutable' in str(error),'Exact immutable rejection')
                            else:raise AssertionError('Realized property was mutable')
                            require(g.state(STATE)['enabled'],'Option remains on')
                        elif name=='duplicate-prepare': reject(g,0x100,2)
                        else:
                            reject(g,0x100,1);reject(g,0x100,1)
                            require(g.state(STATE)['start_attempts']==2,'Repeated START remains strict')
                    for pa,n in spans:require(g.read_ram(pa,n)==b'\xa5'*n,'RAM/NAL sentinel immutable')
                require(not g.irq(0xfa),'No IRQ from protocol-only transactions')
            result['state']=g.state(STATE)
            require(result['state']['jobs']==result['state']['dma_bytes']==0,'No codec job/DMA')
            require(not g.qmp.command('query-status')['running'],'No guest execution')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close(); result['stderr']=g.text()
            result['transactions']=g.transactions
    out.mkdir(parents=True,exist_ok=True)
    (out/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,help='Optional native run06 first-fault-state.json; no firmware execution')
    args=p.parse_args()
    rows=[run_case(args.qemu,name,args.output) for name in (
        'default-off','bad-width','unaligned','incomplete','bad-input','bad-profile',
        'bad-span','overlap','result','abort','immutable','duplicate-prepare','prepare-start')]
    if args.snapshot:
        import hashlib
        require(hashlib.sha256(args.snapshot.read_bytes()).hexdigest()=='274db49ac0a56223bc5fb2fd9ce1ebe6da7dd3e507f92bbc49b785cd99a854ac',
            'Pinned run06 evidence hash')
        rows += [run_case(args.qemu,name,args.output,args.snapshot) for name in ('run06-profile','run06-changed-qp')]
    print(json.dumps([{k:v for k,v in r.items() if k in ('name','passed','error')} for r in rows],indent=2))
    if not all(r['passed'] for r in rows):raise SystemExit(1)


if __name__=='__main__':main()
