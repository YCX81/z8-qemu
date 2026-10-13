# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual QTest producer transactions; authored input, no firmware or journal injection."""
from pathlib import Path
import argparse
import hashlib
import json
import socket
import tempfile
import time

import check_z8_machine
import test_z8_syscon
import test_z8_d980_internal
from test_z8_d980_internal import Lab as BaseLab, PROFILE, require

OPTION = 'producer-generation-journal'
STATE = OPTION + '-state'
A, B = 0x840000000, 0x840010000
EXTRA = ('engine151-capture-pixels-model', 'engine151-raw-output-model',
         'engine1540-descriptors-model', 'engine1540-parameters-model',
         'engine1540-control-model', 'engine1540-frame-pass-model')
SOURCE = (A, B, A)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Lab(BaseLab):
    def __init__(self, binary, folder, enabled):
        super().__init__(binary, folder, enabled=False)
        index = self.command.index('-M') + 1
        self.command[index] += ',' + ','.join(x+'=on' for x in EXTRA)
        if enabled: self.command[index] += ',' + OPTION + '=on'
        self.irqs = {}; self.transactions = []
        self.expected_unknown = None

    def start(self):
        super().start()
        self.qmp.deadline = time.monotonic() + 60
        gics = [x for x in self.qmp.command('qom-list', path='/machine/unattached')
                if x['type'] == 'child<arm-gicv3>']
        require(len(gics) == 1, 'Unique GIC required')
        self.tx('irq_intercept_in /machine/unattached/' + gics[0]['name'])

    def raw_tx(self, command):
        self.stream.write(command.encode() + b'\n')
        lines = []
        while True:
            reply = self.stream.readline().decode().strip(); lines.append(reply)
            if not reply.startswith('IRQ '): break
            _, action, number = reply.split(); self.irqs[int(number)] = action == 'raise'
        self.transactions.append(dict(command=command, replies=lines))
        require(reply.startswith('OK'), f'QTest failed: {command}: {reply}')
        unknowns = [x for x in self.text().splitlines() if 'Z9_UNKNOWN' in x]
        if unknowns:
            require(self.expected_unknown is not None and
                    all(f'addr=0x{self.expected_unknown:x} ' in x for x in unknowns),
                    'First unexpected strict stop: '+unknowns[0])
        return reply[2:].strip()

    def tx(self, command):
        value = self.raw_tx(command)
        return int(value, 0) if value else None

    def read_ram(self, pa, size):
        value = self.raw_tx(f'read {pa:#x} {size:#x}')
        require(value.startswith('0x'), 'QTest byte result')
        data = bytes.fromhex(value[2:]); require(len(data) == size, 'Short RAM read')
        return data

    def write_ram(self, pa, data):
        self.tx(f'write {pa:#x} {len(data):#x} 0x{data.hex()}')

    def pair(self, addr, value):
        self.write(addr, value & 0xffffffff); self.write(addr+4, value >> 32)

    def irq(self, number):
        self.tx('readl 0xa00010000')
        return self.irqs.get(number - 32, False)


def configure_d980(g):
    c = dict(PROFILE)
    c.update({4: 64 << 16, 8: 16 << 16, 0x100: 128, 0x118: 0})
    for i, pa in enumerate((*SOURCE, 0)):
        c[0x120 + i*8] = pa & 0xffffffff
        c[0x124 + i*8] = pa >> 32
    g.configure(c, 0x4000)
    g.write(0x150c020c, 0x4000000)
    g.enable(0x4000); g.periodic()


def next_d980(g, number):
    if number > 1: g.write(0x150c0210, 0x4000000)
    g.advance()
    d = g.state('primary-d980-state')
    require(d['frames'] == number and d['last_physical'] == SOURCE[number-1], 'Actual D980 generation/address')
    require(d['pending'] == 0x4000000 and g.irq(0x106), 'D980 IRQ before ACK')
    return d


def configure151(g, source, index=0, initial=False):
    base = 0x15100000
    if initial:
        for off, value in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff)):
            g.write(base+off, value)
        for off in range(0x268, 0x320, 8): g.pair(base+off, 0xa00010000+(off-0x268)*2)
    values = {0x1c:0x3f,0x260:0x1fff,0x264:0x3ff,0x40:2,
        0x200:0x10000,0x204:0,0x60:0,0x64:0,0x218:1,0x21c:3,0x220:19,
        0x400:(16<<16)|64,0x450:(16<<16)|64,0x4a0:(16<<16)|64,
        0x430:128,0x4d0:128,0x454:0,0x480:64,0x484:64,0x4a4:0,0x4ac:1}
    for off, value in values.items(): g.write(base+off, value)
    y = 0x840100000 + index*0x10000; uv = y+0x2000; raw = y+0x4000
    for off, value in ((0x900,source),(0x980,y),(0x9c0,uv),(0x4c0,raw)): g.pair(base+off,value)
    require(g.state('engine151-control-state')['instances'][0]['ready'], 'Real 151 readiness')
    return y, uv, raw


def run151(g, source, index=0, initial=False):
    y, uv, raw = configure151(g, source, index, initial)
    g.write(0x15100008, 1)
    state = g.state('engine151-capture-pixels-state')
    require(state['jobs'] == index+1 and state['pending'], '151 actual output completion')
    require(g.irq(0x10d), '151 actual IRQ raised')
    require(g.read_ram(source,2048) == g.read_ram(raw,2048), '151 neutral full RAW bytes')
    g.write(0x15100044,2)
    require(not g.irq(0x10d), '151 W1C lowers IRQ')
    return y, uv, raw


def configure1540(g, raw, index=0):
    target = 0x840200000 + index*0x10000
    for i, value in enumerate([2,0xff,0,0,0]*2): g.write(0x154001a4+i*4,value)
    for off, value in {0x40:0x10001,0x44:0,0x48:0,0x4c:64,0x50:16,
        0x408:128,0x428:128,0xd4:2048}.items(): g.write(0x15400000+off,value)
    g.pair(0x15400400,raw); g.pair(0x15400420,target)
    g.write(0x15400020,0x100)
    return target


def run1540(g, raw, index=0):
    target = configure1540(g,raw,index)
    g.write(0x15400008,1)
    state = g.state('engine1540-frame-pass-state')
    require(state['jobs'] == index+1 and state['pending'] == 0x100, 'Actual 1540 copy completion')
    require(g.irq(0x131), '1540 actual IRQ raised')
    require(g.read_ram(raw,2048) == g.read_ram(target,2048), '1540 whole actual RAM copy')
    g.write(0x1540002c,0x100)
    require(not g.irq(0x131), '1540 W1C lowers IRQ')
    return target


def verify_records(g, expected):
    j = g.state(STATE)
    require(len(j['records']) == expected, 'Completed output record count')
    for r in j['records']:
        if r['state'] != 'no_monitored_overlap_since_completion': continue
        for span in r['spans']:
            actual = hashlib.sha256(g.read_ram(span['pa'],span['bytes'])).hexdigest()
            require(actual == span['sha256'], 'Journal full actual RAM SHA')
    return j


def full_chain(g, enabled):
    configure_d980(g); snapshots=[]; hashes=[]
    for i in range(2):
        next_d980(g,i+1)
        y,uv,raw = run151(g,SOURCE[i],i,initial=i==0)
        target = run1540(g,raw,i)
        hashes.append([hashlib.sha256(g.read_ram(pa,n)).hexdigest()
            for pa,n in ((SOURCE[i],2048),(y,1024),(uv,1024),(raw,2048),(target,2048))])
        if enabled:
            j=verify_records(g,4*(i+1));records=j['records'][-4:]
            require([r['producer'] for r in records] == ['d980_ram','151_nv16','151_raw','1540_raw_copy'],'Producer order')
            require(records[1]['parent_record_id']==records[0]['record_id'] and
                records[2]['parent_record_id']==records[0]['record_id'] and
                records[3]['parent_record_id']==records[2]['record_id'],'Actual lineage IDs')
            snapshots.append(j)
    require(hashes[0][0] != hashes[1][0], 'A/B actual tagged pixel content differs')
    if enabled:
        require(not snapshots[-1]['gap'], 'Unexpected lineage or journal gap')
        require(all(r['state']=='no_monitored_overlap_since_completion' for r in snapshots[-1]['records']),
                'Separate-RAM A/B should remain distinct monitored records')
    else: require(g.state(STATE)['records']==[], 'Default-off creates no journal')
    functional={k:g.state(k) for k in ('primary-d980-state','engine151-capture-pixels-state',
        'engine151-raw-output-state','engine1540-frame-pass-state')}
    return dict(hashes=hashes,functional=functional,journal=snapshots)


def overlap(g):
    configure_d980(g);next_d980(g,1);a=g.read_ram(A,2048)
    next_d980(g,2);before=verify_records(g,2)
    next_d980(g,3);after=verify_records(g,3)
    require(after['records'][0]['state']=='overwritten_by_monitored_write','Actual D980 reuse invalidates A')
    require(after['records'][1]['state']=='no_monitored_overlap_since_completion','Separate B preserved')
    require(after['records'][2]['model_write_epoch']>after['records'][0]['model_write_epoch'],'Write epoch monotonic')
    require(g.read_ram(A,2048)!=a,'Actual reuse changed bytes')
    return dict(before=before,after=after)


def old_source(g, device):
    configure_d980(g);next_d980(g,1)
    _,_,raw=run151(g,A,0,True)
    next_d980(g,2);before=verify_records(g,4)
    if device=='151':configure151(g,A,1);address=0x15100008
    else:configure1540(g,raw);address=0x15400008
    g.write(address,1)
    after=g.state(STATE)
    require(after==before,'Latest rejection must not start journal or invalidate output')
    require(g.state('engine151-capture-pixels-state')['jobs']==1,'Old A did not create151 work')
    require(g.state('engine1540-frame-pass-state')['jobs']==0,'Old A did not create1540 work')
    return dict(journal=after,expected_unknown=hex(address))


def immutable_option(g, enabled):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=not enabled)
    except RuntimeError as e:require('immutable' in str(e),'Expected immutable option rejection')
    else:raise AssertionError('Post-init option changed')
    require(g.qmp.command('qom-get',path='/machine',property=OPTION) is enabled,'Option changed')
    return g.state(STATE)


def case(binary, output, name, body, enabled=True, unknown=None):
    result=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    caseout=output/name;caseout.mkdir()
    with tempfile.TemporaryDirectory(prefix='nj-qtest-') as tmp:
        g=Lab(binary,Path(tmp),enabled)
        g.expected_unknown=unknown
        try:
            g.start();result['command']=g.command
            result['observed']=body(g)
            if unknown:require('Z9_UNKNOWN' in g.text() and f'addr=0x{unknown:x}' in g.text(),'Exact expected native model rejection')
            else:require('Z9_UNKNOWN' not in g.text(),'Unexpected strict stop')
            require(g.state('runtime-clock-state')['qtest_accelerator'] is True,'CPU-free accelerator required')
            require(g.qmp.command('query-status')['running'] is False,'Unexpected running VM')
            result['passed']=True
        except Exception as e:result['error']=repr(e)
        finally:
            g.close();result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
            result['exit_code']=None if g.proc is None else g.proc.returncode
            (caseout/'stderr.txt').write_text(g.text())
            (caseout/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
            (caseout/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('name','passed','error','owned_qemu_stopped')}),flush=True)
    return result


def accepted_result(cases, planned, equivalent, binary_unchanged, sources_unchanged):
    return (len(cases) == planned and equivalent is True and binary_unchanged is True
            and sources_unchanged is True and all(c.get('passed') is True
                and c.get('owned_qemu_stopped') is True for c in cases))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--qemu',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),Path(check_z8_machine.__file__),
             Path(test_z8_syscon.__file__),Path(test_z8_d980_internal.__file__)]
    before={str(p):sha(p) for p in sources};binary_before=sha(a.qemu)
    cases=[]
    plans=[('chain-off',lambda g:full_chain(g,False),False,None),
           ('chain-on',lambda g:full_chain(g,True),True,None),
           ('overlap',overlap,True,None),
           ('old-A-151',lambda g:old_source(g,'151'),True,0x15100008),
           ('old-A-1540',lambda g:old_source(g,'1540'),True,0x15400008),
           ('immutable-off',lambda g:immutable_option(g,False),False,None),
           ('immutable-on',lambda g:immutable_option(g,True),True,None)]
    for name,body,enabled,unknown in plans:
        c=case(a.qemu,a.output,name,body,enabled,unknown);cases.append(c)
        if not c['passed']:break
    equivalent=False
    if len(cases)>=2 and cases[0]['passed'] and cases[1]['passed']:
        x,y=cases[0]['observed'],cases[1]['observed']
        equivalent=x['hashes']==y['hashes'] and x['functional']==y['functional']
    after={str(p):sha(p) for p in sources}
    binary_unchanged=sha(a.qemu)==binary_before
    sources_unchanged=before==after
    receipt=dict(cases=cases,passed=accepted_result(cases,len(plans),equivalent,binary_unchanged,sources_unchanged),
        defaultoff_equivalence=equivalent,binary_sha256=binary_before,binary_unchanged=binary_unchanged,
        sources_before=before,sources_after=after,sources_unchanged=before==after,
        firmware_used=False,cpu_execution=False,native_owner_verified=False)
    (a.output/'qtest-result.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(passed=receipt['passed'],cases=len(cases),defaultoff_equivalence=equivalent)),flush=True)
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__=='__main__':main()
