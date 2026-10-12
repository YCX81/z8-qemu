# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest of an exact internal FIFO start, without Nikon program bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time

from check_z8_machine import QMP, sha256
from test_z8_syscon import transaction

ROOT = Path(__file__).resolve().parents[1]
OPTION = 'z8-d980-internal-start-model'
PROFILE = {0:1,4:0x102c0000,8:0x0ac40002,0x10:0,0x100:7264,0x104:1,
    0x108:1,0x10c:0,0x110:1,0x114:0,0x118:3,0x11c:0,
    **{off:0xe0000000 for off in (0x120,0x128,0x130,0x138)},
    **{off:0 for off in (0x124,0x12c,0x134,0x13c,*range(0x140,0x160,4))}}
BASE = ['bootstrap-fixtures','image-bank-model','engine150c-control-model',
    'engine150c-stop-model','config150d-model','engine150d-control-model',
    'statistics-model','engine1543-control-model','engine151-control-model',
    'raw-pipeline-model','event1508-front-model','event1508-aux-model',
    'event1508-channel-model','event1508-timing-model','primary-d980-config-model',
    'primary-d980-stream-model','primary-d980-packed14-model','sensor-frame-tag-model',
    'primary-d980-single-frame-model','primary-d980-prepared-start-model',
    'primary-d980-source-notify-model','primary-d980-channel-boundary-model',
    'raw-source-boundary-model','primary-empty-end-model',
    'primary19d-config-model','primary19bc-config-model','primary194-config-model',
    'primary19d-submit-model','primary19bc-submit-model','primary194-submit-model',
    'secondary150d-submit-model','primary-parameter-control-model','primary-stopped-source-reset-model']


def require(value, message):
    if not value: raise AssertionError(message)


class Lab:
    def __init__(self, binary, folder, *, enabled=True, machine='nikon-z8-bringup', omit=()):
        self.root=folder; self.proc=self.qmp=self.sock=self.stream=None
        (folder/'zero.bin').write_bytes(bytes(4))
        (folder/'pixels.raw').write_bytes(struct.pack('<8s6I',b'NZ9RAW1\0',64,32,14,0,0,16383)+bytes(4096))
        props=[name+'=on' for name in BASE if name not in omit]
        props += ['statistics-raw='+str(folder/'pixels.raw'),'event1508-timing-period-ns=1000000']
        if enabled: props.append(OPTION+'=on')
        self.command=[str(binary),'-M',machine+','+','.join(props),'-kernel',str(folder/'zero.bin'),
            '-accel','qtest','-smp','4','-S','-display','none','-serial','none','-monitor','none',
            '-qmp',f'unix:{folder}/qmp,server=on,wait=off','-qtest',f'unix:{folder}/qtest,server=on,wait=off',
            '-qtest-log','/dev/null']

    def start(self):
        self.log=(self.root/'stderr').open('wb')
        self.proc=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=self.log)
        deadline=time.monotonic()+10
        while not (self.root/'qtest').exists():
            if self.proc.poll() is not None:raise RuntimeError('Owned QEMU exited: '+self.text())
            if time.monotonic()>deadline:raise TimeoutError('Owned QTest startup')
            time.sleep(.01)
        self.qmp=QMP(self.root/'qmp',time.monotonic()+30)
        self.sock=socket.socket(socket.AF_UNIX);self.sock.settimeout(2);self.sock.connect(str(self.root/'qtest'))
        self.stream=self.sock.makefile('rwb',buffering=0)

    def text(self):return (self.root/'stderr').read_text(errors='replace')
    def tx(self,cmd):return transaction(self.stream,cmd)
    def write(self,address,value,verb='writel'):return self.tx(f'{verb} {address:#x} {value:#x}')
    def state(self,name):return json.loads(self.qmp.command('qom-get',path='/machine',property=name))
    def advance(self):
        clock=self.state('runtime-clock-state')
        require(clock['qtest_accelerator'] is True,'Clock stepping requires CPU-free QTest accelerator')
        before=clock['virtual_ns']
        # VM runstate gates timers even under QTest. This accelerator does not
        # execute guest instructions; stop again before the read-only snapshot.
        self.qmp.command('cont')
        try:self.tx('clock_step 1000000000')
        finally:self.qmp.command('stop')
        require(self.state('runtime-clock-state')['virtual_ns']>=before+1000000000,'Virtual clock did not advance')
    def configure(self,profile=PROFILE,mask=0xff00):
        for off,value in sorted(profile.items()):self.write(0x150d9800+off,value)
        self.write(0x150c0060,0x4000)
        # Existing configuration-only units retain authored zero defaults.
        # Submit them through their public MMIO contract, never host state.
        for bit in (0x100,0x200,0x400,0x800,0x1000,0x2000,0x8000):
            if mask&bit:self.write(0x150c0060,bit)
        self.write(0x150c0064,mask)
    def enable(self,mask=0xff00):self.write(0x150c0008,mask)
    def periodic(self):self.write(0x15080010,1);self.write(0x15080028,1)
    def reset(self):
        self.write(0x150c0014,0xc7ff7f);self.write(0x150c0018,0x4f04f)
        self.write(0x150c0000,0x17ff7f);self.write(0x150c0004,0x17ff7f)
        self.write(0x150c0018,0);self.write(0x150c0014,0x4f04f)
    def no_pixels(self):
        d=self.state('primary-d980-state');single=self.state('primary-d980-single-frame-state')
        tag=self.state('sensor-frame-tag-state');source=self.state('primary-d980-source-notify-state')
        channel=self.state('primary-d980-channel-boundary-state')
        require(all(d[k]==0 for k in ('frames','pixel_dma_bytes','pending','acks','rejects')),'Unexpected D980 pixel event')
        require(single['frames']==single['requests']==single['completed_epoch']==0 and not single['armed'],'Unexpected single frame')
        require(tag['frame_id']==tag['virtual_ns']==0 and not tag['packed_sha256'],'Unexpected tag')
        require(source['notifications']==0 and channel['boundaries']==0,'Unexpected D980 source/channel notification')
        return dict(d980=d,single=single,tag=tag,source=source,channel=channel)
    def close(self):
        if self.qmp:
            try:self.qmp.command('quit')
            except Exception:pass
            self.qmp.close()
        if self.stream:self.stream.close()
        if self.sock:self.sock.close()
        if self.proc:
            try:self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=2)
        if hasattr(self,'log'):self.log.close()


def case(binary,name,body,*,reject=None,startup_error=None,**kwargs):
    result=dict(name=name,passed=False,cpu_execution=False,firmware_used=False)
    with tempfile.TemporaryDirectory(prefix='z8-internal-') as tmp:
        g=Lab(binary,Path(tmp),**kwargs)
        try:
            try:g.start()
            except (RuntimeError, OSError):
                if not startup_error or startup_error not in g.text():raise
                result['expected_startup_rejection']=startup_error
            else:
                if startup_error:raise AssertionError('Expected option/dependency rejection')
                result['observed']=body(g)
                if reject:
                    require(f'addr=0x{reject:x}' in g.text() and 'Z9_UNKNOWN' in g.text(),'Missing exact strict rejection')
                else:require('Z9_UNKNOWN' not in g.text(),'Unexpected strict stop')
                require(g.qmp.command('query-status')['running'] is False,'CPU unexpectedly running')
            result['passed']=True
        except Exception as e:result['error']=repr(e)
        finally:
            g.close();result['stderr']=g.text();result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
    return result


def start_and_wait(g):
    g.configure();g.enable();g.periodic();g.advance()
    require(g.state('engine150c-state')['active']&0x4000,'Exact internal profile was not enabled')
    if OPTION+'=on' in ','.join(g.command):
        s=g.state('z8-d980-internal-state');require(s['waiting'] and s['starts']==1 and s['blocked_inputs']>0,'Internal waiting/tick guard not exercised')
    return g.no_pixels()


def run(binary,red=False):
    if red:return [case(binary,'red exact FIFO cannot start on baseline',start_and_wait,enabled=False)]
    cases=[case(binary,'exact FIFO start waits through periodic ticks',start_and_wait)]
    def start(g):g.configure();g.enable()
    cases.append(case(binary,'default off rejects exact FIFO',start,enabled=False,reject=0x150c0008))
    for mask in (0x4000,0x7f00,0x4010):
        def wrongmask(g,mask=mask):g.configure();g.enable(mask)
        cases.append(case(binary,f'reject unobserved start mask {mask:x}',wrongmask,reject=0x150c0008))
    for off,value in ((0,2),(4,0x102a0000),(8,0x0ac40000),(0x100,7296),(0x10,1),
                      (0x118,0),(0x10c,1),(0x120,0xe0000020),(0x124,1),(0x140,0xe0000000)):
        def invalid(g,off=off,value=value):g.configure(dict(PROFILE,**{})|{off:value});g.enable()
        cases.append(case(binary,f'reject profile offset {off:x}',invalid,reject=0x150c0008))
    for verb in ('writeb','writew','writeq'):
        def width(g,verb=verb):g.configure();g.write(0x150c0008,0xff00,verb)
        cases.append(case(binary,f'reject start width {verb}',width,reject=0x150c0008))
    def repeat(g):start(g);g.enable();return g.no_pixels()
    cases.append(case(binary,'repeat start rejected',repeat,reject=0x150c0008))
    def relatch(g):start(g);g.write(0x150c0064,0x4000);return g.no_pixels()
    cases.append(case(binary,'identical active latch does not restart',relatch))
    def changed(g):
        start(g);g.write(0x150d9808,0x0ac40000);g.write(0x150c0060,0x4000);g.write(0x150c0064,0x4000)
        return g.no_pixels()
    cases.append(case(binary,'active profile change rejected before latch',changed,reject=0x150c0064))
    def arm(g):
        start(g);g.write(0x1508008c,2);g.write(0x1508008c,3);g.write(0x15080028,0x201);g.advance()
        return g.no_pixels()
    cases.append(case(binary,'single arm cannot schedule internal pixels',arm))
    def stop(g):
        start(g);g.write(0x150c000c,0x4000);g.advance()
        require(not g.state('z8-d980-internal-state')['waiting'],'Control stop did not cancel')
        require(g.state('engine150c-state')['active']&0x4000==0,'Control remains active')
        return g.no_pixels()
    cases.append(case(binary,'existing control stop cancels without frame',stop))
    def reset(g):
        start(g);g.write(0x150c0014,0xc7ff7f);g.write(0x150c0018,0x4f04f);g.write(0x150c0000,0x17ff7f)
        require(not g.state('z8-d980-internal-state')['waiting'],'Reset assert did not cancel');g.advance()
        return g.no_pixels()
    cases.append(case(binary,'existing bounded reset cancels',reset))
    def stopped_prepare(g):
        # Give the original preparation path its required prior reset, then
        # stop internal without reset: a future RAM setup must not lose2->3.
        start(g);g.reset();start(g);g.write(0x150c000c,0x4000)
        g.write(0x1508008c,2);g.write(0x1508008c,3)
        s=g.state('primary-d980-prepared-start-state')
        require(s['prepared'] and s['requests']==1,'Stopped internal latch suppressed later RAM preparation')
        return s
    cases.append(case(binary,'stop preserves later RAM preparation',stopped_prepare))
    def prepared_reject(g):
        start(g);g.reset();g.write(0x1508008c,2);g.write(0x1508008c,3)
        require(g.state('primary-d980-prepared-start-state')['prepared'],'Missing genuine prior preparation')
        g.configure();g.enable()
        require(not g.state('z8-d980-internal-state')['waiting'],'Internal consumed a previous preparation')
    cases.append(case(binary,'previous prepared state rejects internal admission',prepared_reject,reject=0x150c0008))
    cases.append(case(binary,'Z9 option rejected',lambda g:None,machine='nikon-z9-lab',startup_error='requires Z8'))
    cases.append(case(binary,'stream prerequisite required',lambda g:None,omit=('primary-d980-stream-model',),startup_error='requires'))
    def ram(g):
        c=dict(PROFILE);c.update({4:64<<16,8:16<<16,0x100:128,0x118:0,0x120:0x40000000,0x124:8})
        for off in (0x128,0x130,0x138):c[off]=0
        g.configure(c,0x4000);g.enable(0x4000);g.periodic();g.advance()
        d=g.state('primary-d980-state');require(d['frames']==1 and d['pixel_dma_bytes']==2048,'Legacy RAM path changed')
        return {k:d[k] for k in ('frames','pixel_dma_bytes','pending','last_encoded','last_physical')}
    for enabled in (False,True):cases.append(case(binary,f'ordinary RAM unchanged flag={enabled}',ram,enabled=enabled))
    def past_frame(g):
        ram(g);g.write(0x15080014,1);g.reset();g.configure();g.enable()
        require(g.state('primary-d980-state')['frames']==1,'Real old frame counter was erased')
        require(not g.state('z8-d980-internal-state')['waiting'],'Old RAM frame admitted as internal input')
    cases.append(case(binary,'old RAM frame prevents internal admission',past_frame,reject=0x150c0008))
    def armed_reconfigure(g):
        c=dict(PROFILE);c.update({4:64<<16,8:16<<16,0x100:128,0x118:0,0x120:0x40000000,0x124:8})
        for off in (0x128,0x130,0x138):c[off]=0
        g.configure(c,0x4000);g.enable(0x4000);g.write(0x1508008c,2);g.write(0x1508008c,3)
        require(g.state('primary-d980-single-frame-state')['armed'],'Original RAM timer was not armed')
        old=g.state('primary-d980-state')['latched'];g.configure()
        require(g.state('primary-d980-state')['latched']==old,'Rejected active commit partially changed latch')
    cases.append(case(binary,'armed RAM cannot relatch internal',armed_reconfigure,reject=0x150c0064))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    cases=run(a.qemu,a.red_baseline);r=dict(cases=cases,passed=all(c['passed'] for c in cases),
        qemu_sha256=sha256(a.qemu),source_sha256=sha256(Path(__file__)),firmware_used=False,cpu_execution=False)
    (a.output/'qtest-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({'passed':r['passed'],'cases':len(cases),'failures':[{k:c.get(k) for k in ('name','error','stderr')} for c in cases if not c['passed']]}))
    raise SystemExit(0 if r['passed'] else 1)

if __name__=='__main__':main()
