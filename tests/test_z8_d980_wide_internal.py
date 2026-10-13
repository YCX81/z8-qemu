# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest candidates for exact HALF19 control admission; no Nikon bytes.

Authored but NOT executed until a separately authorized build/QTest window.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from check_z8_machine import sha256
from test_z8_d980_internal import Lab as BaseLab, PROFILE as OLD, require

OPTION='z8-d980-internal-wide-start-model'
STATE='z8-d980-internal-state'
WIDE={**OLD,4:0x20580000,8:0x12500000,0x100:0x38a0}
RAM={**OLD,4:64<<16,8:16<<16,0x100:128,0x118:0,0x120:0x40000000,0x124:8,
     0x128:0,0x130:0,0x138:0}


class Lab(BaseLab):
    def __init__(self,binary,folder,*,wide=True,enabled=True,journal=False,**kwargs):
        super().__init__(binary,folder,enabled=enabled,**kwargs)
        if wide:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
        if journal:self.command[self.command.index('-M')+1]+=',producer-generation-journal=on'

    def read_ram(self,address,size):
        self.stream.write(f'read {address:#x} {size:#x}\n'.encode())
        reply=self.stream.readline().decode().strip()
        require(reply.startswith('OK 0x'),'Bounded QTest RAM read failed')
        data=bytes.fromhex(reply[5:]);require(len(data)==size,'Short actual RAM read')
        return data


def case(binary,name,body,*,reject=None,startup_error=None,**kwargs):
    result=dict(name=name,passed=False,cpu_execution=False,firmware_used=False)
    with tempfile.TemporaryDirectory(prefix='z8-wide-') as tmp:
        g=Lab(binary,Path(tmp),**kwargs)
        try:
            try:g.start()
            except (RuntimeError,OSError):
                if not startup_error or startup_error not in g.text():raise
                result['expected_startup_rejection']=startup_error
            else:
                if startup_error:raise AssertionError('Expected creation/dependency rejection')
                result['observed']=body(g)
                unknown=[line for line in g.text().splitlines() if 'Z9_UNKNOWN' in line]
                if reject:
                    require(unknown and f'addr=0x{reject:x} ' in unknown[0], 'Wrong/missing FIRST strict rejection')
                else:require(not unknown,'Unexpected strict rejection')
                require(g.qmp.command('query-status')['running'] is False,'QTest not paused')
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();result['stderr']=g.text()
            result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
    return result


def fingerprint(g):
    engine=g.state('engine150c-state');d=g.state('primary-d980-state');s=g.state(STATE)
    return dict(latched_d980=d['latched'],engine={k:engine[k] for k in ('latched','active','commits','starts')},
        internal={k:s[k] for k in ('waiting','starts','wide_starts','last_accepted_profile','control_cancels','blocked_inputs')})


def submit_without_latch(g,profile):
    for off,value in sorted(profile.items()):g.write(0x150d9800+off,value)
    g.write(0x150c0060,0x4000)


def start(g,profile=WIDE):g.configure(profile);g.enable()


def no_d980_work(g):
    result=g.no_pixels()
    require(result['single']['epoch']==0,'Internal admission advanced input epoch')
    require(g.state('primary-d980-prepared-start-state')['requests']==0,'Internal admission prepared a RAW frame')
    return result


def positive(g):
    start(g);state=g.state(STATE)
    require(state['wide_enabled'] and state['waiting'] and state['wide_starts']==1 and state['last_accepted_profile']==2,
        'Exact wide admission not distinguished from earlier profile')
    before=no_d980_work(g)
    g.write(0x1508008c,2);g.write(0x1508008c,3);g.write(0x15080028,0x201)
    g.advance();g.periodic();g.advance()
    after=no_d980_work(g)
    require(g.state(STATE)['blocked_inputs']>0,'No actual periodic receive attempt exercised')
    require(before['d980']['latched']==after['d980']['latched'],'Input attempt changed exact latch')
    return dict(state=g.state(STATE),before=before,after=after)


def run(binary,red=False):
    if red:
        # Existing immutable a3b knows the old flag but not the new option;
        # demand wide admission to obtain a meaningful pre-change failure.
        def old_admission(g):
            start(g);state=g.state(STATE);engine=g.state('engine150c-state')
            unknown=[line for line in g.text().splitlines() if 'Z9_UNKNOWN' in line]
            require(unknown and 'addr=0x150c0008 ' in unknown[0], 'Baseline did not reach expected wide readiness rejection')
            require(state['waiting'] and state['starts']>0 and engine['active']&0x4000,
                f"RED: exact wide admission unavailable; waiting={state['waiting']} starts={state['starts']} "
                f"active={engine['active']:#x}; first UNKNOWN150c0008")
            return dict(state=state,engine=engine)
        return [case(binary,'red wide profile lacks admission on original baseline',old_admission,wide=False)]
    cases=[case(binary,'wide exact control waits with no D980 data or epoch',positive)]
    def disabled(g):
        g.configure(WIDE);before=fingerprint(g);g.enable();after=fingerprint(g)
        require(before==after,'Rejected wide start changed control/latch/counts')
        return dict(before=before,after=after)
    cases.append(case(binary,'old flag alone rejects wide',disabled,wide=False,reject=0x150c0008))
    cases.append(case(binary,'wide requires existing internal flag',lambda g:None,enabled=False,
        startup_error='wide-start-model requires z8-d980-internal-start-model'))
    cases.append(case(binary,'wide option is Z8-only',lambda g:None,enabled=False,machine='nikon-z9-lab',
        startup_error='wide-start-model requires Z8 at machine creation'))
    def late(g):
        before=g.state(STATE)
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=True)
        except RuntimeError as error:require('machine creation' in str(error),'Wrong late-option rejection')
        else:raise AssertionError('Late wide opt-in accepted')
        require(g.state(STATE)==before,'Late option changed state')
        return before
    cases.append(case(binary,'wide option cannot change after realization',late,wide=False))
    for off,value in WIDE.items():
        def changed(g,off=off,value=value):
            g.configure({**WIDE,off:value^1});before=fingerprint(g);g.enable();after=fingerprint(g)
            require(before==after,'Rejected word change mutated enable state')
            return dict(offset=off,before=before,after=after)
        cases.append(case(binary,f'exact tuple rejects altered word {off:x}',changed,reject=0x150c0008))
    for profile,name in ((OLD,'old'),(WIDE,'wide')):
        def same(g,profile=profile):
            start(g,profile);before=fingerprint(g);g.write(0x150c0064,0x4000);after=fingerprint(g)
            require(before['latched_d980']==after['latched_d980'] and before['internal']==after['internal'],
                'Identical active latch changed waiting/profile/counters')
            require(after['engine']['commits']==before['engine']['commits']+1 and
                after['engine']['starts']==before['engine']['starts'],'Identical commit restarted input')
            return dict(before=before,after=after)
        cases.append(case(binary,f'active {name} identical commit retains supplier',same))
    for first,second,name in ((OLD,WIDE,'old-to-wide'),(WIDE,OLD,'wide-to-old'),(WIDE,RAM,'wide-to-RAM'),(RAM,WIDE,'RAM-to-wide')):
        def transition(g,first=first,second=second):
            if first is RAM:g.configure(first,0x4000);g.enable(0x4000)
            else:start(g,first)
            submit_without_latch(g,second);before=fingerprint(g);g.write(0x150c0064,0x4000);after=fingerprint(g)
            require(before==after,'Rejected transition changed ANY protected latch/control/count')
            return dict(before=before,after=after)
        cases.append(case(binary,f'active {name} rejected before latches',transition,reject=0x150c0064))
    for first,second,name in ((OLD,WIDE,'old-to-wide'),(WIDE,OLD,'wide-to-old')):
        def after_reset(g,first=first,second=second):
            start(g,first);g.reset();require(not g.state(STATE)['waiting'],'Original reset did not cancel')
            start(g,second);s=g.state(STATE)
            require(s['starts']==2 and s['wide_starts']==1 and s['last_accepted_profile']==(2 if second is WIDE else 1),
                'Fresh post-reset profile admission differs')
            no_d980_work(g);return s
        cases.append(case(binary,f'original reset permits fresh {name}',after_reset))
    def stop(g):
        start(g);g.write(0x150c000c,0x4000);g.advance();s=g.state(STATE)
        require(not s['waiting'] and s['control_cancels']==1 and s['wide_starts']==1,'Original stop did not cancel wide')
        no_d980_work(g);return s
    cases.append(case(binary,'original control stop cancels wide without frame',stop))
    def prepare(g):
        start(g);g.reset();start(g);g.write(0x150c000c,0x4000)
        g.write(0x1508008c,2);g.write(0x1508008c,3)
        s=g.state('primary-d980-prepared-start-state')
        require(s['prepared'] and s['requests']==1,'Stopped wide latch blocked future RAM preparation')
        return s
    cases.append(case(binary,'stopped wide retains later RAW preparation',prepare))
    def prepared_reject(g):
        start(g);g.reset();g.write(0x1508008c,2);g.write(0x1508008c,3)
        require(g.state('primary-d980-prepared-start-state')['prepared'],'Authored native preparation missing')
        g.configure(WIDE);before=fingerprint(g);g.enable();after=fingerprint(g)
        require(before==after,'Wide consumed a prior RAW preparation');return dict(before=before,after=after)
    cases.append(case(binary,'prepared RAW state cannot be adopted by wide',prepared_reject,reject=0x150c0008))
    def missing_valid(g):
        for off,value in WIDE.items():
            if off!=0x100:g.write(0x150d9800+off,value)
        before=fingerprint(g);g.write(0x150c0060,0x4000);after=fingerprint(g)
        require(before==after,'Incomplete submission changed protected control');return dict(before=before,after=after)
    cases.append(case(binary,'incomplete required words cannot submit',missing_valid,reject=0x150c0060))
    def no_submit(g):
        before=fingerprint(g);g.write(0x150c0064,0x4000);after=fingerprint(g)
        require(before==after,'Absent submission partially latched');return dict(before=before,after=after)
    cases.append(case(binary,'absent submission cannot latch',no_submit,reject=0x150c0064))
    for mask in (0x4000,0x7f00):
        def mask_case(g,mask=mask):
            g.configure(WIDE);before=fingerprint(g);g.enable(mask);after=fingerprint(g)
            require(before==after,'Rejected enable mask mutated state');return dict(before=before,after=after)
        cases.append(case(binary,f'wide requires exact enable mask {mask:x}',mask_case,reject=0x150c0008))
    def duplicate(g):
        start(g);before=fingerprint(g);g.enable();after=fingerprint(g)
        require(before==after,'Overlapping enable changed active profile');return dict(before=before,after=after)
    cases.append(case(binary,'overlapping active wide enable rejected',duplicate,reject=0x150c0008))
    def reset_pending(g):
        g.configure(WIDE);g.write(0x150c0014,0xc7ff7f);before=fingerprint(g);g.enable();after=fingerprint(g)
        require(before==after,'Unresolved reset admitted wide');return dict(before=before,after=after)
    cases.append(case(binary,'reset stage blocks wide start',reset_pending,reject=0x150c0008))
    def wide_journal(g):
        observed=positive(g);j=g.state('producer-generation-journal-state')
        require(j['enabled'] and not j['gap'] and not j['records'] and j['completed_operations']==0 and
            j['operation_id']==0 and j['model_write_epoch']==0,'Internal control created a journal pixel transaction')
        return dict(observed=observed,journal=j)
    cases.append(case(binary,'journal enabled wide has no pixels or records',wide_journal,journal=True))
    def ram_after_wide_journal(g):
        wide_journal(g);g.write(0x150c000c,0x4000);g.write(0x15080014,1);g.reset()
        require(not g.state(STATE)['waiting'],'Original cancellation/reset did not release supplier')
        require(not g.state('producer-generation-journal-state')['records'],'Cancellation fabricated an output record')
        address=0x840000000;sentinel=bytes([0xa5])*2048
        g.tx(f'write {address:#x} 0x800 0x{sentinel.hex()}')
        g.configure(RAM,0x4000);g.enable(0x4000);g.periodic();g.advance()
        d=g.state('primary-d980-state');j=g.state('producer-generation-journal-state');pixels=g.read_ram(address,2048)
        require(d['frames']==1 and d['pixel_dma_bytes']==2048 and pixels!=sentinel,'Post-cancel ordinary RAM producer failed')
        require(not j['gap'] and len(j['records'])==1 and j['completed_operations']==1,'Ordinary RAM journal transaction missing')
        record=j['records'][0]
        require(record['producer']=='d980_ram' and record['spans'][0]['pa']==address and
            record['spans'][0]['bytes']==2048 and record['spans'][0]['sha256']==hashlib.sha256(pixels).hexdigest(),
            'Journal output does not match actual post-cancel RAM bytes')
        return dict(d980=d,journal=j,actual_ram_sha256=hashlib.sha256(pixels).hexdigest())
    cases.append(case(binary,'journal and ordinary RAM work after wide cancellation',ram_after_wide_journal,journal=True))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),*[Path(__file__).with_name(name) for name in
        ('test_z8_d980_internal.py','check_z8_machine.py','test_z8_syscon.py')]]
    before={str(p):sha256(p) for p in sources};binary_before=sha256(args.qemu)
    cases=run(args.qemu,args.red_baseline)
    after={str(p):sha256(p) for p in sources};binary_after=sha256(args.qemu)
    unchanged=before==after and binary_before==binary_after
    owned_stopped=all(c['owned_qemu_stopped'] for c in cases)
    receipt=dict(cases=cases,passed=all(c['passed'] for c in cases) and unchanged and owned_stopped,
        qemu_sha256=binary_after,qemu_sha256_before=binary_before,qemu_sha256_after=binary_after,
        sources_sha256_before=before,sources_sha256_after=after,sources_and_binary_unchanged=unchanged,
        all_owned_qemu_stopped=owned_stopped,source_sha256=sha256(Path(__file__)),firmware_used=False,cpu_execution=False)
    (args.output/'qtest-result.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(passed=receipt['passed'],cases=len(cases),failures=[
        {k:c.get(k) for k in ('name','error','stderr')} for c in cases if not c['passed']])))
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__=='__main__':main()
