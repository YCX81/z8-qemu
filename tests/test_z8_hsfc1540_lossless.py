# SPDX-License-Identifier: GPL-2.0-or-later
"""D0=1 compression candidate, with native transfer explicitly rejected."""
import argparse,array,hashlib,json,sys,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_hsfc151_pixels import Lab as ISPLab,RAW,OUTPUT_BYTES
from test_z8_hsfc153_u16 import decode_second,NR0
from test_z8_d980_internal import require

OPTION='z8-hsfc1540-lossless-model'
STATE='z8-hsfc1540-state'
ENCODED=0x8c0000000
CAPACITY=68712960

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from z8_f0_reader import decode_f0

class Lab(ISPLab):
    def write_ram(self,address,data):
        self.tx(f'write {address:#x} {len(data):#x} 0x'+data.hex())
    def __init__(self,binary,folder,*,hsfc_f0=True):
        super().__init__(binary,folder)
        options=['engine1540-descriptors-model','engine1540-parameters-model',
            'engine1540-control-model','engine1540-frame-pass-model','engine1540-lossless14-model']
        if hsfc_f0:options.append(OPTION)
        self.command[self.command.index('-M')+1]+=','+','.join(x+'=on' for x in options)
    def source_isp(self):
        self.prepare_source();self.prepare_isp();self.start_isp();self.write(0x15100044,2)
    def encoder(self,target=ENCODED,source=RAW):
        for i,value in enumerate([2,0xff,0,0,0]*2):self.write(0x154001a4+i*4,value)
        for off,value in {0x40:0x10000,0x44:0,0x48:0,0x4c:4140,0x50:2756,
                0x54:0x11,0x58:2048,0x408:8288,0x428:8288,0xd4:OUTPUT_BYTES}.items():
            self.write(0x15400000+off,value)
        codes=(0x3e00,0x3f00,0x3d00,0x3800,0x3000,0x2000,0x1000,0,0x1800,0x2800,0x3400,0x3a00,0x3c00,0x3f80,0x3fc0)
        lengths=(6,7,6,5,4,3,3,2,3,3,4,5,6,8,8)
        for i,(code,length) in enumerate(zip(codes,lengths)):
            self.write(0x1540005c+4*i,code);self.write(0x15400098+4*i,length)
        self.pair(0x15400400,source);self.pair(0x15400420,target)
        self.write(0x15400020,0x706011f)
    def irq1540(self):
        self.tx('readl 0x1540002c');return self.irqs.get(0x131-32,False)

def case(binary,name,body,*,reject=False,**kw):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='hsfc-f0-') as tmp:
        g=Lab(binary,Path(tmp),**kw)
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==reject,'Unexpected strict stop')
            if reject:require('addr=0x15400008' in g.text(),'Wrong strict stop address')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row

def complete(g):
    g.source_isp();g.encoder();g.write_ram(ENCODED+CAPACITY,b'\x69'*32)
    source=g.read_ram(RAW,OUTPUT_BYTES);g.write(0x15400008,1)
    state=g.state('engine1540-frame-pass-state')
    require(state['jobs']==1 and state['pending']==0x100 and g.irq1540(),'No actual historical F0 completion')
    n=state['last_result_bytes'];require(4<n<CAPACITY,'Invalid actual entropy length')
    stream=g.read_ram(ENCODED,n)
    expected=b''.join(source[y*8288:y*8288+4140*2] for y in range(2756))
    require(decode_f0(stream,4140,2756)==expected,'Every F0 decoded sample must equal actual U16 source')
    require(g.read_ram(ENCODED+CAPACITY,32)==b'\x69'*32,'Exceeded target capacity')
    r=g.state(STATE)['records'][-1]
    require(r['source_sha256']==hashlib.sha256(source).hexdigest() and r['output_sha256']==hashlib.sha256(stream).hexdigest(),
            'Actual source/encoded hashes differ')
    require(r['isp_job']==1 and r['frame_id']==1 and g.state('z8-hsfc151-state')['records'][-1]['encoded'],
            'Lost or unconsumed historical generation')
    require(g.state('primary-d980-state')['frames']==0,'Invented ordinary D980 generation')
    return r

def reject_transfer(g):
    g.source_isp();g.encoder();g.write(0x15400044,2);g.write(0x154000d4,0)
    before=g.state(STATE);g.write_ram(ENCODED,b'\x75'*32);g.write(0x15400008,1)
    require(g.state(STATE)==before and not g.irq1540(),'Native transfer must never publish F0 completion')
    require(g.read_ram(ENCODED,32)==b'\x75'*32,'Native transfer request was overwritten with F0')

def queued_generations(g,*,ack_second=True,repeat_first=False,reuse_output=False):
    raw_b=RAW+0x4000000;encoded_b=ENCODED if reuse_output else ENCODED+0x8000000
    g.prepare_source(count=2);g.prepare_isp();g.start_isp()
    first=g.read_ram(RAW,OUTPUT_BYTES);g.write(0x15100044,2)
    second=decode_second(g);g.prepare_nr(source=second,bank=0);g.write(NR0+4,1);g.write(NR0+0x2c,1)
    # Reuse only preview planes, retaining two independent RAW allocations.
    g.prepare_isp(source=second,raw_target=raw_b);g.start_isp()
    if ack_second:g.write(0x15100044,2)
    isp=g.state('z8-hsfc151-state')['records']
    require([r['valid'] for r in isp]==[True,True],'Preview reuse retired a still-owned RAW output')
    require(isp[0]['acknowledged'] and not isp[0]['preview_valid'],'First RAW ACK/preview lifetime not independent')
    require(isp[1]['acknowledged']==ack_second,'ACK attached to wrong generation')
    g.encoder(source=RAW);g.write(0x15400008,1)
    state=g.state(STATE);require(state['jobs']==1 and state['records'][-1]['frame_id']==1,'Newest151 globals blocked older owned RAW')
    n=state['records'][-1]['output_bytes'];stream=g.read_ram(ENCODED,n)
    expected=b''.join(first[y*8288:y*8288+8280] for y in range(2756))
    require(decode_f0(stream,4140,2756)==expected,'Older generation F0 samples differ')
    g.write(0x1540002c,0x100);g.encoder(target=encoded_b,source=raw_b);before=g.state(STATE);g.write(0x15400008,1)
    if not ack_second:
        require(g.state(STATE)==before,'Unacknowledged newer generation encoded');return state
    state=g.state(STATE);require(state['jobs']==2 and [r['frame_id'] for r in state['records']]==[1,2],'Second generation lost')
    require(state['records'][0]['valid']==(not reuse_output),'Encoded-output reuse retained stale identity')
    require(state['records'][0]['output_sha256']!=state['records'][1]['output_sha256'],'Distinct frames collapsed')
    if repeat_first:
        g.write(0x1540002c,0x100);g.encoder(source=RAW);before=g.state(STATE);g.write(0x15400008,1)
        require(g.state(STATE)==before,'A B A source was encoded again')
    return state

def run(binary,red=False,transfer_negative_only=False,queued_only=False):
    if queued_only:return [case(binary,'two151 completions may precede first1540',queued_generations)]
    if transfer_negative_only:return [case(binary,'native transfer cannot be substituted with F0',reject_transfer,reject=True)]
    if red:return [case(binary,'old1540 cannot encode actual historical U16',complete,hsfc_f0=False)]
    rows=[case(binary,'all11409840 samples survive actual U16 to F0',complete)]
    def reject(g,change=None,owned=True,ack=True):
        if owned:
            g.prepare_source();g.prepare_isp();g.start_isp()
            if ack:g.write(0x15100044,2)
        g.encoder();g.write_ram(ENCODED,b'\x75'*32)
        if change:change(g)
        before=g.state(STATE);g.write(0x15400008,1)
        require(g.state(STATE)==before and not g.irq1540(),'Rejected source published completion')
        require(g.read_ram(ENCODED,32)==b'\x75'*32,'Rejected encode wrote target')
    rows.append(case(binary,'no owned ISP generation rejected',lambda g:reject(g,owned=False),reject=True))
    rows.append(case(binary,'unacknowledged ISP rejected',lambda g:reject(g,ack=False),reject=True))
    rows.append(case(binary,'corrupt actual U16 source rejected',lambda g:reject(g,
        lambda q:q.write_ram(RAW+32,bytes([q.read_ram(RAW+32,1)[0]^1]))),reject=True))
    rows.append(case(binary,'invalid input stride rejected',lambda g:reject(g,lambda q:q.write(0x15400408,7264)),reject=True))
    rows.append(case(binary,'wrong compression D4 rejected',lambda g:reject(g,lambda q:q.write(0x154000d4,1)),reject=True))
    rows.append(case(binary,'target crossing real RAM gap rejected',lambda g:reject(g,lambda q:q.pair(0x15400420,0x8adf8d9e0)),reject=True))
    rows.append(case(binary,'target cannot overwrite owned RAW',lambda g:reject(g,lambda q:q.pair(0x15400420,RAW)),reject=True))
    def again(g,pending):
        g.source_isp();g.encoder();g.write(0x15400008,1)
        require(g.state(STATE)['jobs']==1,'Initial F0 failed')
        if not pending:g.write(0x1540002c,0x100)
        before=g.state(STATE);g.write(0x15400008,1)
        require(g.state(STATE)==before,'One historical generation encoded twice')
    rows.append(case(binary,'pending encode cannot repeat',lambda g:again(g,True),reject=True))
    rows.append(case(binary,'consumed ISP cannot repeat after ACK',lambda g:again(g,False),reject=True))
    rows.append(case(binary,'default off leaves historical source strict',reject,reject=True,hsfc_f0=False))
    rows.append(case(binary,'noncompression mode rejected',lambda g:reject(g,lambda q:q.write(0x15400044,2)),reject=True))
    rows.append(case(binary,'wrong target stride rejected',lambda g:reject(g,lambda q:q.write(0x15400428,7264)),reject=True))
    rows.append(case(binary,'native transfer cannot be substituted with F0',reject_transfer,reject=True))
    rows.append(case(binary,'wrong native lossless code table rejected',lambda g:reject(g,lambda q:q.write(0x1540005c,0)),reject=True))
    rows.append(case(binary,'two151 completions may precede first1540',queued_generations))
    rows.append(case(binary,'older ACK permits encoding while newer151 awaits ACK',lambda g:queued_generations(g,ack_second=False),reject=True))
    rows.append(case(binary,'nonconsecutive A B A encode rejected',lambda g:queued_generations(g,repeat_first=True),reject=True))
    rows.append(case(binary,'new generation reuses retired encoded output',lambda g:queued_generations(g,reuse_output=True)))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true')
    p.add_argument('--transfer-negative-only',action='store_true');p.add_argument('--queued-only',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline,a.transfer_negative_only,a.queued_only)
    r=dict(passed=all(x['passed'] for x in rows),cases=rows,qemu_sha256=sha256(a.qemu),raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(passed=r['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not r['passed'])
