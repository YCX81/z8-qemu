# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent encoder banks and IRQs with actual historical U16/F0 RAM."""
import argparse,hashlib,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_hsfc1540_lossless import Lab as F0Lab,RAW,OUTPUT_BYTES,ENCODED,CAPACITY,decode_f0
from test_z8_hsfc153_u16 import decode_second,NR0
from test_z8_d980_internal import require

BANK=0x153c0000
OPTION='z8-hsfc153c-lossless-model'
STATE='z8-hsfc153c-state'

class Lab(F0Lab):
    def __init__(self,binary,folder,*,enabled=True):
        super().__init__(binary,folder)
        self.program_base=0x15400000
        if enabled:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
    def write(self,address,value,verb='writel'):
        if 0x15400000<=address<0x15400500:address+=self.program_base-0x15400000
        return super().write(address,value,verb)
    def encoder_bank(self,base= BANK,**kw):
        self.program_base=base
        try:self.encoder(**kw)
        finally:self.program_base=0x15400000
    def irq_bank(self,base):
        self.tx(f'readl {base+0x2c:#x}')
        return self.irqs.get((0x130 if base==BANK else 0x131)-32,False)

def case(binary,name,body,*,enabled=True,reject=False,reject_bank=BANK):
    result=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-dual-f0-') as tmp:
        g=Lab(binary,Path(tmp),enabled=enabled)
        try:
            g.start();result['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==reject,'Unexpected strict boundary')
            if reject:require('addr='+hex(reject_bank)[:6] in g.text(),'Rejection belongs to another bank')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:g.close();result.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return result

def first(g):
    g.source_isp();g.encoder_bank();source=g.read_ram(RAW,OUTPUT_BYTES)
    failures=[line for line in g.text().splitlines() if 'Z9_UNKNOWN' in line]
    require(not failures,'Second-bank configuration rejected: '+(failures[0] if failures else ''))
    g.write(BANK+8,1);state=g.state(STATE)
    require(state['jobs']==1 and state['pending']==0x100 and g.irq_bank(BANK),'Missing independent153c completion')
    require(g.state('engine1540-frame-pass-state')['jobs']==0 and not g.irq_bank(0x15400000),'153c changed1540 completion')
    n=state['last_result_bytes'];stream=g.read_ram(ENCODED,n)
    expected=b''.join(source[y*8288:y*8288+8280] for y in range(2756))
    require(decode_f0(stream,4140,2756)==expected,'All second-bank F0 samples must match actual source')
    record=g.state('z8-hsfc1540-state')['records'][-1]
    require(record['encoder_base']==BANK and record['output_sha256']==hashlib.sha256(stream).hexdigest(),'Wrong bank/output provenance')
    require(g.tx(f'readl {BANK+0x198:#x}')==n,'Wrong native result register')
    return record

def cross_bank_overlap(g,*,reverse=False,ack=False,offset=0):
    g.prepare_source(count=2);g.prepare_isp();g.start_isp();g.write(0x15100044,2)
    raw_b=RAW+0x4000000
    second=decode_second(g);g.prepare_nr(source=second,bank=0);g.write(NR0+4,1);g.write(NR0+0x2c,1)
    g.prepare_isp(source=second,raw_target=raw_b);g.start_isp();g.write(0x15100044,2)
    first_base=BANK if reverse else 0x15400000
    second_base=0x15400000 if reverse else BANK
    first_state=STATE if reverse else 'engine1540-frame-pass-state'
    second_state='engine1540-frame-pass-state' if reverse else STATE
    g.encoder_bank(first_base);g.write(first_base+8,1);first=g.state(first_state)
    preserved=g.read_ram(ENCODED,first['last_result_bytes'])
    if ack:g.write(first_base+0x2c,0x100);first=g.state(first_state)
    g.encoder_bank(second_base,source=raw_b,target=ENCODED+offset);g.write(second_base+8,1)
    if ack:
        require(g.state(second_state)['jobs']==1,'Acknowledged other-bank target could not be reused')
        records=g.state('z8-hsfc1540-state')['records']
        require([r['valid'] for r in records]==[False,True],'Reused cross-bank output retained stale record')
        return
    require(g.state(second_state)['jobs']==0 and g.state(first_state)==first,
            'Other-bank pending allocation overwritten')
    require(g.read_ram(ENCODED,len(preserved))==preserved,'Pending F0 bytes overwritten across banks')

def run(binary,red=False,overlap_only=False):
    if overlap_only:return [case(binary,'other-bank pending allocation protected',cross_bank_overlap,reject=True)]
    if red:return [case(binary,'old153c lacks actual historical F0',first,enabled=False)]
    rows=[case(binary,'full actual153c F0 and IRQ130',first)]
    def independent(g):
        g.prepare_source(count=2);g.prepare_isp();g.start_isp();g.write(0x15100044,2)
        raw_b=RAW+0x4000000;encoded_b=ENCODED+0x8000000
        second=decode_second(g);g.prepare_nr(source=second,bank=0);g.write(NR0+4,1);g.write(NR0+0x2c,1)
        g.prepare_isp(source=second,raw_target=raw_b);g.start_isp();g.write(0x15100044,2)
        g.encoder_bank(0x15400000);g.write(0x15400008,1)
        a=g.state('engine1540-frame-pass-state')
        g.encoder_bank(source=raw_b,target=encoded_b);g.write(BANK+8,1)
        b=g.state(STATE)
        records=g.state('z8-hsfc1540-state')['records']
        require([(r['encoder_base'],r['encoder_job'],r['acknowledged']) for r in records]==
                [(0x15400000,1,False),(BANK,1,False)],'Ownership keys must use independent bank-local jobs')
        require(g.irq_bank(BANK) and g.irq_bank(0x15400000),'Both independently pending IRQs required')
        require(g.state('engine1540-frame-pass-state')==a,'Second bank changed first results or pending state')
        record=g.state('z8-hsfc1540-state')['records'][-1]
        source=g.read_ram(raw_b,OUTPUT_BYTES);stream=g.read_ram(encoded_b,b['last_result_bytes'])
        require(decode_f0(stream,4140,2756)==b''.join(source[y*8288:y*8288+8280] for y in range(2756)),
                'Second source generation did not survive other bank encoding')
        require(record['frame_id']==2 and record['isp_job']==2 and record['encoder_base']==BANK,'Cross-bank job binding differs')
        g.write(0x1540002c,0x100)
        require(not g.irq_bank(0x15400000) and g.irq_bank(BANK) and g.state(STATE)==b,'First bank ACK retired second')
        require([r['acknowledged'] for r in g.state('z8-hsfc1540-state')['records']]==[True,False],
                'Native1540 W1C marked another history record')
        g.write(BANK+0x20,0);require(not g.irq_bank(BANK) and g.state(STATE)['pending']==0x100,'Mask consumed second completion')
        g.write(BANK+0x2c,1);require(g.state(STATE)['pending']==0x100,'Unrelated W1C consumed second completion')
        g.write(BANK+0x20,0x100);require(g.irq_bank(BANK),'Unmask lost second completion')
        g.write(BANK+0x2c,0x100);require(not g.irq_bank(BANK) and g.state(STATE)['acks']==1,'Second bank ACK failed')
        require(all(r['acknowledged'] for r in g.state('z8-hsfc1540-state')['records']),
                'Native153c W1C did not mark its own history record')
        return dict(first=a,second=b)
    rows.append(case(binary,'two sources, independent pending masks ACKs and results',independent))
    rows.append(case(binary,'other-bank pending allocation protected',cross_bank_overlap,reject=True))
    rows.append(case(binary,'reverse pending allocation protected',lambda g:cross_bank_overlap(g,reverse=True),reject=True,reject_bank=0x15400000))
    rows.append(case(binary,'whole pending reservation protected',lambda g:cross_bank_overlap(g,offset=0x2000000),reject=True))
    rows.append(case(binary,'other-bank ACK permits target reuse',lambda g:cross_bank_overlap(g,reverse=True,ack=True)))
    def twice(g):
        g.source_isp();g.encoder();g.write(0x15400008,1);a=g.state('engine1540-frame-pass-state')
        g.encoder_bank(target=ENCODED+0x8000000);g.write(BANK+8,1)
        require(g.state(STATE)['jobs']==0 and g.state('engine1540-frame-pass-state')==a,'Same source consumed by two banks')
    rows.append(case(binary,'same parent cannot encode in both banks',twice,reject=True))
    def rejected(g,change=None,source=True):
        if source:g.source_isp()
        g.encoder_bank();g.write_ram(ENCODED,b'\x75'*32)
        if change:change(g)
        before=g.state(STATE);g.write(BANK+8,1)
        require(g.state(STATE)==before and not g.irq_bank(BANK),'Rejected request published second-bank completion')
        require(g.read_ram(ENCODED,32)==b'\x75'*32,'Rejected request wrote output')
    rows.append(case(binary,'native transfer never becomes F0',lambda g:rejected(g,lambda q:q.write(BANK+0x44,2)),reject=True))
    rows.append(case(binary,'missing source rejected',lambda g:rejected(g,source=False),reject=True))
    rows.append(case(binary,'corrupt actual source rejected',lambda g:rejected(g,lambda q:q.write_ram(RAW+10000,b'\xff')),reject=True))
    rows.append(case(binary,'unknown Huffman profile rejected',lambda g:rejected(g,lambda q:q.write(BANK+0x5c,0)),reject=True))
    def pending(g):
        first(g);before=g.state(STATE);g.write(BANK+0x4c,12)
        require(g.state(STATE)==before,'Pending bank reprogrammed')
    rows.append(case(binary,'pending protects parameters',pending,reject=True))
    def shadow(g):
        g.write(BANK+0x44,2)
        require(g.tx(f'readl {BANK+0x44:#x}')==2 and g.state(STATE)['fields'][17]==2,'Legacy153c shadow forked')
        require(g.state('engine1540-parameters-state')['fields'][17]==0,'153c mode changed1540 shadow')
    rows.append(case(binary,'independent shared legacy44 shadow',shadow))
    def default(g):g.write(BANK+0x1b4,0)
    rows.append(case(binary,'default off preserves native62 stop',default,enabled=False,reject=True))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as error:require('realization' in str(error),'Wrong option rejection')
        else:raise AssertionError('Live encoder-bank toggle accepted')
    rows.append(case(binary,'bank immutable after realization',immutable))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true')
    p.add_argument('--overlap-only',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline,a.overlap_only)
    report=dict(passed=all(r['passed'] for r in rows),qemu_sha256=sha256(a.qemu),cases=rows,
                native_bank_verified=False,raw_precapture_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=report['passed'],cases=len(rows),failures=[dict(name=r['name'],error=r.get('error')) for r in rows if not r['passed']])))
    raise SystemExit(not report['passed'])
