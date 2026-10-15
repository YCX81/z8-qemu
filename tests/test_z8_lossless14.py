# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual1540 output bytes/results/IRQ plus independent F0 reader."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_z8_buffer_ownership import Lab as OwnerLab
from test_z8_producer_journal import configure_d980,next_d980,run151,configure1540,A,B,require
from test_lossless14 import decode_f0
from z8_capture_pixels import decode_packed_row

class Lab(OwnerLab):
    def __init__(self,binary,folder,enabled):
        super().__init__(binary,folder,True)
        self.command[self.command.index('-M')+1]+=',engine1540-lossless14-model='+('on' if enabled else 'off')

def photos(g,compressed):
    configure_d980(g);previous=0;results=[]
    for i,source in enumerate((A,B)):
        next_d980(g,i+1);_,_,raw=run151(g,source,i,initial=i==0)
        target=configure1540(g,raw,i);original=g.read_ram(raw,2048)
        sentinel=b'\xa5'*2048;g.write_ram(target,sentinel)
        g.write(0x15400008,1);state=g.state('engine1540-frame-pass-state')
        length=state['last_result_bytes'];encoded=g.read_ram(target,length)
        require(state['bytes']-previous==length,'Cumulative bytes differ from actual last result')
        require(g.tx('readl 0x15400198')==length,'Original result register supplies exact output length')
        require(g.irq(0x131) and g.state('output-buffer-ownership-state')['slots'][1]['held'],'Complete DMA owns IRQ output')
        if length<2048:
            require(g.read_ram(target+length,2048-length)==sentinel[length:],'No DMA beyond actual output')
        if compressed:
            import struct
            expected=[list(struct.unpack('<64H',decode_packed_row(original[y*128:(y+1)*128],64,14))) for y in range(16)]
            require(decode_f0(encoded,64,16,(2048,)*4)==expected,'Independent F0 decoder equals every actual RAW sample')
            require(length!=((64*7//4+15)//16*16)*16,'Packed fallback excluded')
            record=g.state('producer-generation-journal-state')['records'][-1]
            require(record['format']=='nikon-f0-lossless14' and record['spans'][0]['bytes']==length,'Journal format/span cannot claim packed copy')
        else:require(encoded==original and length==2048,'Default neutral transport unchanged')
        g.write(0x1540002c,0x100);previous=state['bytes']
        results.append(dict(job=i+1,output_bytes=length,sha256=hashlib.sha256(encoded).hexdigest(),samples=1024))
    require(not g.state('producer-generation-journal-state')['gap'],'Unexpected compressed lineage gap')
    return results

def capacity(g):
    configure_d980(g);next_d980(g,1);_,_,raw=run151(g,A,0,True)
    # Authored QTest source with high-entropy rows, intentionally outside
    # native producer provenance. Test only failure-before-DMA, not acceptance.
    import random
    randomizer=random.Random(918)
    rows=[[randomizer.randrange(16384) for _ in range(64)] for _ in range(16)]
    payload=b''.join(sum(v<<(14*x) for x,v in enumerate(row)).to_bytes(112,'little').ljust(128,b'\0') for row in rows)
    g.write_ram(raw,payload);target=configure1540(g,raw);g.write_ram(target,b'\xa5'*2048)
    g.expected_unknown=0x15400008;g.write(0x15400008,1)
    require(g.read_ram(target,2048)==b'\xa5'*2048,'Failed entropy output must not DMA partial bytes')
    state=g.state('engine1540-frame-pass-state')
    require(state['jobs']==0 and state['pending']==0 and not g.irq(0x131),'Failure has no completed output/IRQ')
    require(not g.state('output-buffer-ownership-state')['slots'][1]['held'],'Failure cannot leave1540 owner')
    return state

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    cases=[]
    for name,enabled,body in [('packed-off',False,lambda g:photos(g,False)),
                              ('lossless-on',True,lambda g:photos(g,True)),('capacity-failure',True,capacity)]:
        row=dict(name=name,passed=False);folder=a.output/name;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='z8-f0-') as tmp:
            g=Lab(a.qemu,Path(tmp),enabled)
            try:
                g.start();row['observed']=body(g)
                require(('Z9_UNKNOWN' in g.text())==(name=='capacity-failure'),'Unexpected fault outcome')
                try:g.qmp.command('qom-set',path='/machine',property='engine1540-lossless14-model',value=not enabled)
                except RuntimeError as error:require('immutable' in str(error),'Codec option immutable')
                else:raise AssertionError('Live codec changed')
                row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:
                g.close();row['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
                (folder/'stderr.txt').write_text(g.text());(folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        cases.append(row);print(json.dumps({k:v for k,v in row.items() if k!='observed'}),flush=True)
    result=dict(passed=all(r['passed'] and r['owned_qemu_stopped'] for r in cases),cases=cases,
        firmware_used=False,native_nef_accepted=False,independent_decoder='authored F0 reader',
        binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest())
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
