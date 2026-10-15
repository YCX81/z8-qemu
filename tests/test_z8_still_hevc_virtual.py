# SPDX-License-Identifier: GPL-2.0-or-later
"""Real x265 computation behind an opt-in virtual-clock completion barrier.

CPU-free QTest; no pixels, codec results or IRQs are supplied by the test.
Wall delay with the VM paused must not publish virtual-mode completion.
P21 port of integration U-HM5-01; byte-exact legacy JSON and reuse guard added.
"""
import argparse,json,tempfile,time
from pathlib import Path
from unittest.mock import patch
import test_z8_still_hevc_backend as backend
from test_z8_producer_journal import require
OPTION='z8-still-hevc-virtual-completion-model'

def advance(g,ns):
    require(g.state('runtime-clock-state')['qtest_accelerator'],'CPU-free clock test only')
    g.qmp.command('cont')
    try:g.tx('clock_step '+str(ns))
    finally:g.qmp.command('stop')

def case(args,name,binary,enabled):
    dest=args.output/name;dest.mkdir(parents=True,exist_ok=False)
    report=dict(case=name,passed=False,explicit_virtual_completion=enabled)
    c1=json.loads(args.c1_snapshot.read_text());snapshot=json.loads(args.snapshot.read_text())
    words=[(int(k,16),v['value']) for k,v in snapshot['device_states'][backend.STATE]['registers'].items() if v['request_written']]
    regs=dict(words);y=regs[0xf00]+(regs[0xf04]<<32);uv=regs[0xf08]+(regs[0xf0c]<<32)
    targets=((y,uv),*backend.pipeline.TARGETS[1:])
    with tempfile.TemporaryDirectory(prefix='z8-hevc-virtual-') as tmp:
        g=backend.Lab(binary,Path(tmp),c1,True)
        if enabled:g.command[g.command.index('-M')+1]+=','+OPTION+'=on'
        try:
            g.start()
            if enabled:
                try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
                except Exception as e:require('immutable' in str(e),'Realize property guard')
                else:raise AssertionError('Property changed after realization')
            with patch.object(backend.pipeline,'TARGETS',targets):
                backend.pipeline.producer(g);backend.pipeline.configure(g)
                g.write(0x15432004,0x101);g.write(0x15180030,1)
            g.write(0x15432014,0x20);g.write(0x1542111c,0x11);g.write(0x1518000c,1<<20)
            backend.write_sequence(g,backend.RESET+backend.INIT+words)
            for lane in range(4):
                g.write_ram(regs[0xe40+lane*8]<<4,b'\xa5'*64)
                g.write_ram(regs[0xe90+lane*8]<<4,b'\xa5'*64)
            if name=='virtual-profile':g.write(backend.BASE+0x704,13)
            g.write(backend.BASE+0x100,2)
            if name=='virtual-profile':g.expected_unknown=backend.BASE+0x100
            before=g.state('runtime-clock-state')['virtual_ns'];wall=time.monotonic()
            g.write(backend.BASE+0x100,1)
            if name=='virtual-mask':g.write(backend.BASE+0x128,0)
            if name=='virtual-profile':
                require(g.state(backend.CODEC)['jobs']==0 and not g.state(backend.CODEC)['busy'],'Unknown profile cannot start/complete')
                require('Z9_UNKNOWN' in g.text(),'Strict profile reject retained')
            else:
                if name in ('virtual-paused','virtual-abort','virtual-source-change'):
                    time.sleep(.5)
                    require(g.state('runtime-clock-state')['virtual_ns']==before,'Host delay cannot advance QTest virtual clock')
                    state=g.state(backend.CODEC)
                    require(state['busy'] and not state['result_valid'] and state['jobs']==0,'Paused virtual timer cannot publish worker result')
                    require(g.state(backend.STATE)['pending']==0,'No early completion pending')
                    report['paused_worker_did_not_publish']=True
                if name=='virtual-abort':backend.write_sequence(g,[(0x128,0),(0xfe8,0),(8,0),(0xff4,1)])
                if name=='virtual-source-change':
                    g.expected_unknown=backend.BASE+0x100;g.write_ram(y,bytes([g.read_ram(y,1)[0]^1]))
                advance(g,200_000_000)
                immediate=g.state(backend.CODEC);report['at_virtual_budget']=immediate
                if enabled and name not in ('virtual-abort','virtual-source-change'):
                    require(immediate['jobs']==1 and immediate['result_valid'],'Real result must precede virtual budget expiry')
                    timing=immediate['virtual_completion']
                    require(timing['scheduled_ns']==timing['committed_ns']==before,'Completion follows current virtual deadline, not host duration')
                if name in ('virtual-abort','virtual-source-change'):
                    require(not immediate['busy'] and immediate['jobs']==0 and immediate['dma_bytes']==0,'Abort/mutation cannot publish DMA')
                    require(not g.irq(0xfa),'Abort/mutation cannot assert completion')
                    for lane in range(4):require(g.read_ram(regs[0xe40+lane*8]<<4,64)==b'\xa5'*64,'Failure leaves output untouched')
                    if name=='virtual-abort':require(immediate['cancels']==1 and not immediate['failed'],'Cancellation retained')
                    else:require(immediate['failed'] and 'source-hash' in immediate['last_error'] and 'Z9_UNKNOWN' in g.text(),'Commit validates original RAM')
                else:
                    report['codec']=backend.check_result(g,dest)
                    report['codec_json']=g.qmp.command('qom-get',path='/machine',property=backend.CODEC)
                    if name=='virtual-mask':
                        require(not g.irq(0xfa),'Mask suppresses line, not data/pending')
                        g.write(backend.BASE+0x128,regs[0x128])
                    require(g.irq(0xfa),'Actual data completion raises real IRQ')
                    g.write(backend.BASE+0x120,0x40000)
                    require(g.irq(0xfa) and g.state(backend.STATE)['pending']==0x20000,'Stage1 ACK preserves stage2')
                    g.write(backend.BASE+0x120,0x20000)
                    require(not g.irq(0xfa),'Stage2 ACK deasserts IRQ')
                    if name=='virtual-reuse':
                        backend.write_sequence(g,[(0x128,0),(0xfe8,0),(8,0),(0xff4,1)]+backend.RESET+backend.INIT+words)
                        g.write(backend.BASE+0x100,2)
                        g.expected_unknown=backend.BASE+0x100;g.write(backend.BASE+0x100,1)
                        require(g.state(backend.CODEC)['jobs']==1 and not g.state(backend.CODEC)['busy'],'Consumed C1 ownership cannot be reused')
                        require(g.state(backend.STATE)['last_reject']=='backend-source-ownership','Exact source ownership rejection')
                report['host_seconds']=time.monotonic()-wall
            require(not g.qmp.command('query-status')['running'],'No firmware executed')
            report['passed']=True
        except Exception as e:report['error']=repr(e)
        finally:g.close();(dest/'stderr.txt').write_text(g.text())
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser()
    for n in ('qemu','baseline-qemu','snapshot','c1-snapshot','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    rows=[case(a,'baseline-off',a.baseline_qemu,False),case(a,'candidate-off',a.qemu,False)]
    rows += [case(a,n,a.qemu,True) for n in ('virtual-budget','virtual-paused','virtual-mask','virtual-abort','virtual-source-change','virtual-profile','virtual-reuse')]
    old=rows[0].get('codec_json');new=rows[1].get('codec_json');default_equal=bool(old) and old==new
    report=dict(passed=all(r['passed'] for r in rows) and default_equal,default_off_codec_equal=default_equal,cases=rows,
                scope='Real source pixels, x265, DMA, readback and IRQ; QTest virtual clock control only; no physical latency claim')
    (a.output/'qtest-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=report['passed'],default_off_codec_equal=default_equal,cases=[dict(case=r['case'],passed=r['passed'],error=r.get('error')) for r in rows]),indent=2))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
