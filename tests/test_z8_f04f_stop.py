# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact held/reset transaction; real QTest, authored input and no firmware."""
import argparse
import json
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_d980_internal import Lab, require

OPTION='z8-primary-f04f-stop-model'
MASK=0xf04f
KICK=0x1f87d
CAUSE=0x07e8dc00


def setup(g):
    # Discover the existing configuration-only MMIO schema, then supply
    # authored zeros. No original table bytes or host state assignments.
    for name,base,bit in (('primary19d-config-state',0x150c0400,1),
                          ('primary194-config-state',0x150d4000,0x40)):
        for off in g.state(name)['fields']:g.write(base+int(off,16),0)
        g.write(0x150c0060,bit)
    bank=g.state('primary19bc-config-state')['banks'][0]
    for off in bank['fields']:g.write(bank['base']+int(off,16),0)
    for bit in (0x10,2,4,8):g.write(0x150c0060,bit)
    g.write(0x150c0064,0x5f);g.enable(0x5f)
    g.configure();g.enable()
    require(g.state('engine150c-state')['active']==0xff5f,'Synthetic prerequisites did not produce activeff5f')


def state(g):
    s=g.state('engine150c-state')
    return {k:s[k] for k in ('active','latched','stop_requested','stop_done','stop_pending',
        'stop_reset_asserted','stop_reset_released','direct_stop_done','direct_stop_pending','direct_stop_pending1')}


def request(g):g.write(0x150c000c,MASK)
def kick(g):g.write(0x150c0018,MASK);g.write(0x150c0034,KICK)
def retire(g):
    g.write(0x150c021c,CAUSE);g.write(0x150c0000,MASK);g.write(0x150c0004,MASK)
    g.write(0x150c0018,0);g.write(0x150c0014,MASK)


def atomic_state(g):
    return dict(control=state(g),held=g.state('z8-primary-f04f-stop-state'),
        internal=g.state('z8-d980-internal-state'),
        empty_end=g.state('primary-empty-end-state'),hold=g.tx('readl 0x150c0018'))


def complete(g,when='before'):
    setup(g)
    if when=='before':g.write(0x150c000c,0x10)
    request(g)
    s=state(g)
    require(s['stop_requested']==MASK and s['stop_pending']==0,'Request produced completion or wrong ownership')
    require(s['active']==(0xff4f if when=='before' else 0xff5f),'Request changed active')
    g.advance()
    require(state(g)==s,'Legacy empty-end timer stole requested2/4 before kick')
    g.no_pixels()
    if when=='between':g.write(0x150c000c,0x10)
    kick(g);s=state(g)
    require(s['stop_done']==MASK and s['stop_pending']==CAUSE and s['stop_requested']==0,'Incorrect group2 stop')
    require(s['active']==(0xf10 if when=='after' else 0xf00),'Wrong retired active units')
    require(s['direct_stop_pending1']==0,'Incorrect direct D980/D9A completion')
    require(not g.state('z8-d980-internal-state')['waiting'],'Internal supplier wait not cancelled')
    if when=='after':g.write(0x150c000c,0x10)
    require(state(g)['direct_stop_pending']==0x4000000,'Independent stop10 lost')
    retire(g);s=state(g)
    require(s['stop_done']==s['stop_pending']==0 and s['latched']&MASK==0,'Held reset/ack incomplete')
    require(s['direct_stop_pending']==0x4000000 and s['direct_stop_done']==0x10,'Held ACK consumed unrelated group0')
    g.write(0x150c0204,0x4000000)
    require(state(g)['direct_stop_done']==state(g)['direct_stop_pending']==0,'Independent ACK did not retire unit10')
    g.no_pixels()
    return state(g)


def case(binary,name,body,*,enabled=True,reject=None,startup_error=None,internal_enabled=True,machine='nikon-z8-bringup'):
    result={'name':name,'passed':False,'firmware_used':False,'cpu_execution':False}
    with tempfile.TemporaryDirectory(prefix='z8-f04f-') as tmp:
        g=Lab(binary,Path(tmp),enabled=internal_enabled,machine=machine)
        if enabled:g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
        try:
            try:g.start()
            except (RuntimeError,OSError):
                if not startup_error or startup_error not in g.text():raise
            else:
                require(startup_error is None,'Startup precondition was bypassed')
                result['observed']=body(g)
                text=g.text()
                require(('Z9_UNKNOWN' in text)==bool(reject),'Unexpected strict boundary result')
                if reject:require(f'addr=0x{reject:x}' in text,'Wrong strict access')
            result['passed']=True
        except Exception as e:result['error']=repr(e)
        finally:g.close();result['stderr']=g.text();result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
    return result


def run(binary,red=False):
    if red:return [case(binary,'red original f04f held transaction',complete,enabled=False)]
    cases=[case(binary,'complete with independent stop10 '+when,lambda g,when=when:complete(g,when)) for when in ('before','between','after')]
    def disabled(g):setup(g);request(g)
    cases.append(case(binary,'default off remains strict',disabled,enabled=False,reject=0x150c000c))
    noop=lambda g:None
    for name,pre,op,address in (
        ('kick before request',noop,lambda g:g.write(0x150c0034,KICK),0x150c0034),
        ('kick before hold',request,lambda g:g.write(0x150c0034,KICK),0x150c0034),
        ('wrong kick drops upper engines',lambda g:(request(g),g.write(0x150c0018,MASK)),lambda g:g.write(0x150c0034,0x787d),0x150c0034),
        ('repeated request',request,request,0x150c000c),
        ('wrong request subset',noop,lambda g:g.write(0x150c000c,0xe04f),0x150c000c),
        ('ack before reset',lambda g:(request(g),kick(g)),lambda g:g.write(0x150c0014,MASK),0x150c0014),
        ('overlapping direct stop',request,lambda g:g.write(0x150c000c,0x4000),0x150c000c),
        ('repeated kick',lambda g:(request(g),kick(g)),lambda g:g.write(0x150c0034,KICK),0x150c0034),
        ('ack while held',lambda g:(request(g),kick(g),g.write(0x150c021c,CAUSE),g.write(0x150c0000,MASK),g.write(0x150c0004,MASK)),lambda g:g.write(0x150c0014,MASK),0x150c0014),
        ('ack before W1C',lambda g:(request(g),kick(g),g.write(0x150c0000,MASK),g.write(0x150c0004,MASK),g.write(0x150c0018,0)),lambda g:g.write(0x150c0014,MASK),0x150c0014),
        ('release before assert',lambda g:(request(g),kick(g)),lambda g:g.write(0x150c0004,MASK),0x150c0004)):
        def bad(g,pre=pre,op=op):
            setup(g);pre(g);before=atomic_state(g);op(g)
            require(atomic_state(g)==before,'Rejected command partially mutated a control transaction')
        cases.append(case(binary,name,bad,reject=address))
    for phase in ('requested','kicked'):
        def reset(g,phase=phase):
            setup(g);request(g)
            if phase=='kicked':kick(g)
            g.reset();s=g.state('z8-primary-f04f-stop-state')
            require(s['owned_mask']==0 and s['reset_cancels']==1,'Global reset left stale ownership')
            g.no_pixels();return state(g)
        cases.append(case(binary,'global reset cancels '+phase,reset))
    for when in ('before_request','before_kick','after_kick'):
        def separate_ack(g,when=when):
            setup(g);g.write(0x150c000c,0x10)
            if when=='before_request':g.write(0x150c0204,0x4000000)
            request(g)
            if when=='before_kick':g.write(0x150c0204,0x4000000)
            kick(g)
            if when=='after_kick':g.write(0x150c0204,0x4000000)
            require(state(g)['stop_pending']==CAUSE,'Unrelated group0 ACK altered group2 causes')
            retire(g);g.no_pixels();return state(g)
        cases.append(case(binary,'independent stop10 ACK '+when,separate_ack))
    def split_ack(g):
        setup(g);g.write(0x150c000c,0x10);request(g);kick(g)
        for mask,cause in ((0xc000,0x06000000),(0x304f,0x01e8dc00)):
            g.write(0x150c021c,cause);g.write(0x150c0000,mask);g.write(0x150c0004,mask)
            g.write(0x150c0018,g.tx('readl 0x150c0018')&~mask);g.write(0x150c0014,mask)
        require(g.state('z8-primary-f04f-stop-state')['owned_mask']==0,'Partial ACK ownership leaked')
        require(state(g)['direct_stop_pending']==0x4000000,'Partial group2 ACK consumed group0')
        g.no_pixels();return state(g)
    cases.append(case(binary,'partial group2 W1C and reset ACK',split_ack))
    for verb in ('writeb','writew'):
        def width(g,verb=verb):
            setup(g);before=atomic_state(g);g.write(0x150c000c,MASK,verb)
            require(atomic_state(g)==before,'Wrong-width request changed state')
        cases.append(case(binary,'request width '+verb,width,reject=0x150c000c))
    def split64(g):
        setup(g);g.write(0x150c000c,MASK,'writeq')
        s=state(g)
        require(s['stop_requested']==MASK and s['active']==0xff5f,'Unaligned QTest64 did not expose its first32-bit write')
        require(s['stop_pending']==s['stop_done']==0,'Split request manufactured a kick/completion')
        require(g.tx('readl 0x150c0010')==0,'Second32-bit zero write did not retain existing+10 value')
        g.advance();require(state(g)==s,'Unkicked split request advanced');g.no_pixels()
        return dict(control=state(g),host_access='unaligned64 split into two32-bit device writes',second_word_zero=True)
    cases.append(case(binary,'document unaligned64 split bus behavior',split64))
    def reset64(g):
        setup(g);request(g);kick(g);before=atomic_state(g)
        g.write(0x150c0000,MASK,'writeq')
        require(atomic_state(g)==before,'Actual8-byte reset transaction changed state')
    cases.append(case(binary,'actual8-byte device reset rejected',reset64,reject=0x150c0000))
    def no_internal(g):
        setup(g);g.write(0x150c000c,0x4000);before=atomic_state(g);request(g)
        require(atomic_state(g)==before,'Request without internal wait changed state')
    cases.append(case(binary,'internal wait required',no_internal,reject=0x150c000c))
    def immutable(g):
        try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
        except RuntimeError as error:require('machine creation' in str(error),'Wrong QOM rejection')
        else:raise AssertionError('Live property mutation was accepted')
        require(g.qmp.command('qom-get',path='/machine',property=OPTION) is True,'Property changed')
    cases.append(case(binary,'creation-only property',immutable))
    cases.append(case(binary,'Z9 cannot enable option',lambda g:None,machine='nikon-z9-lab',internal_enabled=False,startup_error='z8-primary-f04f-stop-model requires Z8'))
    cases.append(case(binary,'internal model prerequisite',lambda g:None,internal_enabled=False,startup_error='z8-primary-f04f-stop-model requires internal start'))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cases=run(a.qemu,a.red_baseline)
    result={'cases':cases,'passed':all(c['passed'] for c in cases),'qemu_sha256':sha256(a.qemu),'source_sha256':sha256(Path(__file__))}
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':result['passed'],'cases':len(cases),'failures':[
        {k:c[k] for k in ('name','error') if k in c} for c in cases if not c['passed']]}))
    raise SystemExit(not result['passed'])

if __name__=='__main__':main()
