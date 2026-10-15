# SPDX-License-Identifier: GPL-2.0-or-later
<<<<<<< current
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
=======
"""Actual D980/151/1540 RAM, F0 decoding and lineage; no Nikon firmware."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_producer_journal import Lab as BaseLab,configure_d980,next_d980,run151,configure1540,A,B
from test_z8_d980_internal import require
from test_lossless14_codec import unpack_f0,packed

OPTION='engine1540-lossless14-model'
STATE='engine1540-frame-pass-state'

class Lab(BaseLab):
    def __init__(self,binary,folder,*,lossless=True):
        super().__init__(binary,folder,enabled=True)
        if lossless:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'

def samples(raw):
    return [[(int.from_bytes(raw[y*128:(y+1)*128],'little')>>(14*x))&16383 for x in range(64)] for y in range(16)]

def encode(g,index=0):
    if index==0:configure_d980(g)
    next_d980(g,index+1)
    _,_,raw=run151(g,A if index==0 else B,index,initial=index==0)
    source=g.read_ram(raw,2048);target=configure1540(g,raw,index)
    g.write_ram(target,bytes([0x69])*2080)
    g.write(0x15400008,1);state=g.state(STATE)
    require(state.get('encoding')=='nikon-f0-lossless14','F0 codec not selected')
    n=state['last_result_bytes'];require(4<n<2048,'Actual compressed output length required')
    result=g.read_ram(target,n)
    require(unpack_f0(result,64,16,(2048,)*4)==samples(source),'Every actual source sample must survive F0')
    require(g.read_ram(target+n,2080-n)==bytes([0x69])*(2080-n),'Codec wrote outside actual output')
    require(g.irq(0x131) and state['pending']==0x100,'Actual DMA completion IRQ missing')
    journal=g.state('producer-generation-journal-state');last=journal['records'][-1]
    require(not journal['gap'] and last['format']=='nikon-f0-lossless14','Compressed lineage mislabeled or missing')
    require(last['stride']==0 and last['spans'][0]['bytes']==n,'Compressed stream is not a packed row array')
    require(last['spans'][0]['sha256']==hashlib.sha256(result).hexdigest(),'Actual output journal SHA differs')
    require(last['parent_record_id']==journal['records'][-2]['record_id'],'F0 parent is not actual151 output')
    require(last['source_observation']['actual_read_sha256']==hashlib.sha256(source).hexdigest(),'Source observation hash differs')
    return dict(state=state,output_sha256=hashlib.sha256(result).hexdigest(),journal=last)

def case(binary,name,body,*,lossless=True,unknown=None):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-f0-') as tmp:
        g=Lab(binary,Path(tmp),lossless=lossless);g.expected_unknown=unknown
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==bool(unknown),'Unexpected MMIO boundary')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row

def run(binary,red):
    if red:return [case(binary,'old transport cannot pass F0 and lineage',encode,lossless=False)]
    rows=[case(binary,'actual RAM F0 and correct compressed lineage',encode)]
    def repeated(g):
        first=encode(g);g.write(0x1540002c,0x100);require(not g.irq(0x131),'ACK did not lower IRQ')
        second=encode(g,1);require(first['output_sha256']!=second['output_sha256'],'Distinct frames collapsed')
        require(second['state']['acks']==1 and second['state']['jobs']==2,'Independent generation accounting')
        return [first,second]
    rows.append(case(binary,'two actual generations remain distinct after native W1C',repeated))
    def mask(g):
        out=encode(g);g.write(0x15400020,0);require(not g.irq(0x131),'Mask did not suppress IRQ')
        g.write(0x1540002c,1);require(g.state(STATE)['pending']==0x100,'Unrelated W1C consumed data')
        g.write(0x15400020,0x100);require(g.irq(0x131),'Unmask did not retain completion')
        g.write(0x1540002c,0x100);require(not g.irq(0x131),'W1C did not clear IRQ')
        return out
    rows.append(case(binary,'mask and partial W1C retain actual compressed output',mask))
    def pending(g):
        encode(g);before=g.state(STATE);g.write(0x15400008,1)
        require(g.state(STATE)==before,'Pending repeat changed completed output')
    rows.append(case(binary,'pending generation cannot be overwritten',pending,unknown=0x15400008))
    def insufficient_capacity(g):
        configure_d980(g);next_d980(g,1);_,_,raw=run151(g,A,initial=True)
        # Explicit adversarial QTest input, not a claimed producer receipt.
        # Alternating values in each CFA predictor lane exceed this reservation.
        data,stride=packed([[16383*((x//2)&1) for x in range(64)] for _ in range(16)])
        require(stride==128 and len(data)==2048,'Capacity fixture differs')
        g.write_ram(raw,data);target=configure1540(g,raw);g.write_ram(target,bytes([0x72])*2080)
        before=g.state(STATE);journal=g.state('producer-generation-journal-state')
        g.write(0x15400008,1)
        require(g.state(STATE)==before and not g.irq(0x131),'Failed encode published completion')
        require(g.read_ram(target,2080)==bytes([0x72])*2080,'Failed encode performed partial target DMA')
        require(g.state('producer-generation-journal-state')==journal,'Failed encode published a new receipt')
    rows.append(case(binary,'overflow does not DMA or publish completion',insufficient_capacity,unknown=0x15400008))
    def legacy(g):
        configure_d980(g);next_d980(g,1);_,_,raw=run151(g,A,initial=True);target=configure1540(g,raw)
        g.write(0x15400008,1);state=g.state(STATE)
        require(state['last_result_bytes']==2048 and g.read_ram(raw,2048)==g.read_ram(target,2048),'Default transport changed')
        require(g.state('producer-generation-journal-state')['records'][-1]['format']=='packed14le','Legacy journal changed')
        return state
    rows.append(case(binary,'default off preserves packed14 transport',legacy,lossless=False))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as e:require('realization' in str(e),'Wrong option rejection')
        else:raise AssertionError('Live codec toggle accepted')
    rows.append(case(binary,'codec choice immutable after realization',immutable))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline)
    result=dict(passed=all(x['passed'] for x in rows),qemu_sha256=sha256(a.qemu),cases=rows,raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not result['passed'])
>>>>>>> incoming
