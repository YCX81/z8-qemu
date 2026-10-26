# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest for the default-off board-write observer; authored inputs, no firmware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_d980_internal import require
import test_z8_c0_stop as c0

ENV='WL_DMA_WRITE_OBSERVE'
# Inside the C30 history 0x39 block pool (0x8_77ba7200..0x8_ebd00600), which
# lies in the allocated image banks and was outside the upstream work-RAM limit.
POOL=0x877bb0000
SPAN=0x40000
LIMIT=4096  # WL_DMA_OBSERVER_LIMIT


class Lab(c0.Lab):
    def __init__(self,binary,folder,*,observe=None,omit=()):
        super().__init__(binary,folder,enabled=False,omit=omit)
        self.observe=observe
    def start(self):
        old=os.environ.pop(ENV,None)
        if self.observe is not None:os.environ[ENV]=self.observe
        try:super().start()
        finally:
            os.environ.pop(ENV,None)
            if old is not None:os.environ[ENV]=old
    def records(self):
        lines=[l.split(' ',1)[1] for l in self.text().splitlines() if l.startswith('WL_DMA_OBSERVE ')]
        return [json.loads(l) for l in lines]


def ring_at(g,ybase):
    base=c0.C0
    for off,value in ((4,0x100),(8,0),(0xc,0),(0x10,0x29)):g.write(base+off,value)
    for row in range(4):
        for off in range(0,0x40,4):g.write(base+0x200+row*0x80+off,0)
        for channel in range(3):
            y=ybase+row*0x10000+channel*0x2000
            off=base+0x210+row*0x80+channel*0x10
            for at,p in ((off,y),(off+8,y+0x1000)):
                g.write(at,p&0xffffffff);g.write(at+4,p>>32)
    require(g.block()['ready'],'Authored complete ring did not become ready')
    g.write(base+4,0x101)


def preview_at(g,ybase):
    """Same authored 151/1543 preview protocol as test_z8_c0_stop, other RAM."""
    base=0x15140000
    for off,value in ((0x14,0x3807f),(0,1),(4,1),(0x18,0x3807f),(0x18,0x7ffff),
        (0x1c,0x3f),(0x260,0x1fff),(0x264,0x3ff),(0x400,32<<16|64),(0x450,32<<16|64)):
        g.write(base+off,value)
    for off in range(0x268,0x320,8):g.write(base+off,0x1000);g.write(base+off+4,0xa)
    g.write(base+8,1)
    g.write(0x151c0014,1);g.write(0x151c0014,0);g.write(0x151c0030,1)
    for channel in range(3):
        b=0x15422000+channel*0x100;f=0x15423000+channel*0x100
        for off,value in ((0xc,0),(0x10,1),(0x38,64),(0x3c,64)):g.write(b+off,value)
        g.write(f+0x2c,64);g.write(f+0x30,32)
    ring_at(g,ybase)


def frames(g,ybase=POOL):
    preview_at(g,ybase);before=g.read_ram(ybase,SPAN);g.advance()
    p=g.state('raw-pipeline-state');require(p['frames']>0,'No real preview frame')
    data=g.read_ram(ybase,SPAN);require(data!=before,'No actual preview RAM change')
    return dict(frames=p['frames'],ram_sha256=hashlib.sha256(data).hexdigest())


def case(binary,name,body,*,observe=None,startup_error=None,omit=()):
    result=dict(name=name,passed=False,firmware_used=False,cpu_execution=False,observe=observe)
    with tempfile.TemporaryDirectory(prefix='z8-dma-observe-') as tmp:
        g=Lab(binary,Path(tmp),observe=observe,omit=omit)
        try:
            try:g.start()
            except (RuntimeError,OSError):
                if not startup_error or startup_error not in g.text():raise
                require(g.proc.wait(timeout=5)==1,'Rejected interval did not exit 1')
            else:
                require(startup_error is None,'Expected creation-time interval rejection')
                result['observed']=body(g)
            result['passed']=True
        except Exception as error:result['error']=repr(error)
        finally:
            g.close();result['stderr_tail']=g.text()[-2000:]
            result['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
    return result


def run(binary):
    cases=[]
    def off(g):
        r=frames(g);require(not g.records(),'Default-off observer printed records');return r
    cases.append(case(binary,'default off prints nothing',off))
    low,high=POOL,POOL+SPAN
    def pool(g):
        r=frames(g);rec=g.records()
        require(rec,'No record for actual preview writes inside 0x39 pool interval')
        require(len(rec)<LIMIT,'Record limit reached unexpectedly')
        for x in rec:
            a=int(x['address'],16);s=int(x['sample_address'],16)
            require(a<high and a+x['bytes']>low,'Record outside interval')
            require(low<=s<high and s>=a,'Sample outside interval or write')
            require(x['sample_ram_backed'] and x['sample_matches_RAM'],'Sample not RAM-backed or differs')
            require(x['producer'] and x['line']>0,'Missing producer provenance')
        last=rec[-1];s=int(last['sample_address'],16);n=len(last['sample_hex'])//2
        require(g.read_ram(s,n).hex()==last['sample_hex'],'Last sample differs from QTest RAM read')
        r['records']=len(rec);r['producers']=sorted({x['producer'] for x in rec});return r
    cases.append(case(binary,'image-bank 0x39 pool interval records real writes',pool,observe=f'{low:x}:{high:x}'))
    def untouched(g):
        r=frames(g);require(not g.records(),'Writes outside the interval were recorded');return r
    cases.append(case(binary,'work-RAM interval ignores image-bank writes',untouched,observe='a00000000:a00100000'))
    for name,value,omit in (
        ('malformed interval','pool',()),
        ('empty interval',f'{POOL:x}:{POOL:x}',()),
        ('trailing text',f'{POOL:x}:{POOL+SPAN:x}x',()),
        ('past 512 MiB work RAM','a1fff0000:a20010000',()),
        ('between banks and work RAM','900000000:900001000',()),
        ('image banks not allocated',f'{POOL:x}:{POOL+SPAN:x}',('image-bank-model',))):
        cases.append(case(binary,'rejects '+name,lambda g:None,observe=value,omit=omit,
                          startup_error='WL_DMA_WRITE_OBSERVE requires'))
    on,ref=cases[1],cases[0]
    if on['passed'] and ref['passed']:
        same=on['observed']['ram_sha256']==ref['observed']['ram_sha256'] and on['observed']['frames']==ref['observed']['frames']
        cases.append(dict(name='observer leaves frames and RAM unchanged',passed=same,
                          on=on['observed']['ram_sha256'],off=ref['observed']['ram_sha256']))
    return cases



def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);cases=run(a.qemu)
    result=dict(cases=cases,passed=all(c['passed'] for c in cases),qemu_sha256=sha256(a.qemu),source_sha256=sha256(Path(__file__)))
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],cases=len(cases),failures=[{k:c[k] for k in ('name','error') if k in c} for c in cases if not c['passed']])))
    raise SystemExit(not result['passed'])


if __name__=='__main__':main()
