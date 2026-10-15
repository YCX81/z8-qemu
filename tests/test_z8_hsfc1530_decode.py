# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual 152c RAM -> 1530 RAM with an explicit packed12 lab convention.

No Nikon firmware, archived frame injection, or task/queue writes are used.
The 14-to-12 precision conversion is not lossless and is not a silicon claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import numpy as np

from check_z8_machine import sha256
from test_z8_d980_internal import require
from test_z8_hsfc152c_pixels import Lab as PixelLab
from test_z8_hsfc152c_control import P, PHYSICAL

OPTION = 'z8-hsfc1530-decode-model'
STATE = 'z8-hsfc1530-decode-state'
D, CORE, TARGET = 0x15300000, 0x15b00000, 0x890000000
STRIDE, WIDTH, HEIGHT = 6240, 4140, 2756
OUTPUT_BYTES = STRIDE * HEIGHT
FIELDS = {0x14:1,0x18:0x79f,0x104:1,0x504:0xff,0x510:2,0x518:0xff,
          0x524:2,0x52c:1,0x530:2,0x534:0x414800,0x544:STRIDE,0x548:WIDTH,0x54c:HEIGHT}
PARAMS = {0x2c:0x83,0x38:1,0x3c:1,0x4c:1,0x84:0x102c0ac4,
          0xa0:0xfc0,0xa4:0x48f4,0xa8:0x49cd,0xac:0x3f7f}


class Lab(PixelLab):
    def __init__(self, binary, folder, *, decode=True, **kwargs):
        super().__init__(binary, folder, **kwargs)
        self.command[self.command.index('-M')+1] += ',engine153-config-model=on,z8-decoder-reset-control-model=on'
        if decode:
            self.command[self.command.index('-M')+1] += ','+OPTION+'=on'
        # Authored source exercises low bits, range endpoints and Bayer resampling.
        values = ((np.arange(2048,dtype=np.uint32)*131) % 16384).astype('<u2')
        (folder/'pixels.raw').write_bytes(struct.pack('<8s6I',b'NZ9RAW1\0',64,32,14,0,0,16383)+values.tobytes())
        self.dumps = 0

    def read_ram(self, address, size):
        if size <= 65536:
            return super().read_ram(address,size)
        require(0x840000000 <= address < address+size <= 0x900000000 and size <= 24<<20,
                'Large test read is outside bounded image RAM')
        require(self.qmp.command('query-status')['running'] is False,'Large snapshot requires paused QTest')
        self.dumps += 1
        path = self.root/f'actual-ram-{self.dumps}.bin'
        require(not path.exists(),'Refuse to replace prior actual RAM evidence')
        self.qmp.command('pmemsave',val=address,size=size,filename=str(path))
        data=path.read_bytes()
        require(len(data)==size,'Incomplete actual RAM export')
        return data

    def source(self, count=1):
        self.setup_pixels()
        for i in range(count):
            if i: self.ack_image()
            self.next_frame(i+1)
        require(self.read_ram(PHYSICAL[0],8)==b'NZ8BAYR1','Missing actual producer RAM')

    def configure_decoder(self, source=PHYSICAL[0], target=TARGET):
        for off,value in ((0x404,1),(0x14,0),(4,1),(0,1),(4,0),(0,0),(0x1c,0x7ff),(0x404,0),(8,0)):
            self.write(D+off,value)
        for off,value in FIELDS.items(): self.write(D+off,value)
        for off,value in PARAMS.items(): self.write(CORE+off,value)
        for off,address in ((0x120,source),(0x220,target)):
            self.write(D+off,address&0xffffffff);self.write(D+off+4,address>>32)
        for address,value in ((TARGET-32,b'\x5a'*32),(TARGET,b'\xa5'*32),(TARGET+OUTPUT_BYTES,b'\x6b'*32)):
            self.tx(f'write {address:#x} 0x20 0x'+value.hex())

    def decode(self):
        self.write(D+8,1)

    def irq_decoder(self):
        self.tx(f'readl {D+0x1c:#x}')
        return self.irqs.get(0x10c-32,False)

    def output(self, frame_id=1):
        raw=self.read_ram(TARGET,OUTPUT_BYTES)
        ys=(np.arange(HEIGHT)//2*16//(HEIGHT//2))*2+(np.arange(HEIGHT)&1)
        xs=(np.arange(WIDTH)//2*32//(WIDTH//2))*2+(np.arange(WIDTH)&1)
        source=((np.arange(2048,dtype=np.uint32)*131)%16384).reshape(32,64)
        samples=source[ys[:,None],xs]
        for y in range(16):
            for x in range(64):
                bit=((frame_id>>(x//2))&1)^(y>=8)
                samples[y,x]=16383*7//8 if bit else 16383//8
        values=(samples*4095+8191)//16383
        a,b=values[:,::2],values[:,1::2]
        expected=np.zeros((HEIGHT,STRIDE),dtype=np.uint8)
        expected[:,0:WIDTH*3//2:3]=a&255
        expected[:,1:WIDTH*3//2:3]=(a>>8)|((b&15)<<4)
        expected[:,2:WIDTH*3//2:3]=b>>4
        require(raw==expected.tobytes(),'Complete actual output samples or row padding differ')
        require(self.read_ram(TARGET-32,32)==b'\x5a'*32 and self.read_ram(TARGET+OUTPUT_BYTES,32)==b'\x6b'*32,
                'Decoder wrote beyond exact target span')
        state=self.state(STATE)
        records=[r for r in state['records'] if r['valid'] and r['target']==TARGET and r['frame_id']==frame_id]
        require(len(records)==1 and records[0]['output_sha256']==hashlib.sha256(raw).hexdigest(),
                'Requested output receipt differs from actual RAM')
        return state


def case(binary,name,body,*,reject=None,**kwargs):
    row=dict(name=name,passed=False,firmware_used=False,cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-hsfc-decode-') as tmp:
        g=Lab(binary,Path(tmp),**kwargs)
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==bool(reject),'Unexpected strict boundary')
            if reject: require(f'addr=0x{reject:x}' in g.text(),'Wrong strict boundary')
            row['passed']=True
        except Exception as error: row['error']=repr(error)
        finally:
            g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row


def complete(g):
    g.source();g.configure_decoder();g.decode()
    require(g.tx(f'readl {D+0x1c:#x}')==6 and g.irq_decoder(),'Actual complete single-buffer job did not precede data and job IRQs')
    return g.output()


def run(binary,red=False):
    if red: return [case(binary,'red original decoder has no pixel consumer',complete,decode=False)]
    rows=[case(binary,'every sample and canary verified after real producer decode',complete)]
    def historical(g):
        g.source(2);g.configure_decoder();g.decode()
        state=g.output(1)
        require(state['records'][-1]['frame_id']==1 and g.state('z8-hsfc152c-pixels-state')['frames']==2,
                'Decoder silently used newest source rather than requested retained RAM')
        return state
    rows.append(case(binary,'older actual packet decoded while newer frame exists',historical))
    def masked(g):
        g.source();g.configure_decoder();g.write(D+0x18,0);g.decode();g.output()
        require(not g.irq_decoder() and g.tx(f'readl {D+0x1c:#x}')==6,'Masked completion lost')
        g.write(D+0x18,0x79f);require(g.irq_decoder(),'Unmask missing IRQ')
        g.write(D+0x1c,1);require(g.irq_decoder(),'Unrelated W1C cleared completion')
        g.write(D+0x1c,2);require(g.tx(f'readl {D+0x1c:#x}')==4 and g.irq_decoder(),'Data ACK lost independent job completion')
        g.write(D+0x1c,4);require(not g.irq_decoder(),'Job W1C failed')
        g.write(D+8,0);g.write(D+8,0)
        require(g.tx(f'readl {D+0x1c:#x}')==0,'Repeated idle stop fabricated event')
        return g.state(STATE)
    rows.append(case(binary,'mask and partial W1C retain independent one-shot completion',masked))
    def duplicate(g):
        complete(g);g.decode();return g.output()
    rows.append(case(binary,'active start rejected without replacing RAM',duplicate,reject=D+8))
    def corrupt(g):
        g.source();g.configure_decoder();g.tx(f'write {PHYSICAL[0]+81:#x} 1 0xff');g.decode()
        require(g.read_ram(TARGET,32)==b'\xa5'*32 and not g.irq_decoder(),'Corrupt input wrote output or completion')
        return g.state(STATE)
    rows.append(case(binary,'actual source corruption cannot be restored from host',corrupt,reject=D+8))
    def wrong_stride(g):
        g.source();g.configure_decoder();g.write(D+0x544,7264);g.decode()
        require(g.read_ram(TARGET,32)==b'\xa5'*32 and not g.irq_decoder(),'Unsupported stride wrote output')
        return g.state(STATE)
    rows.append(case(binary,'wrong output stride rejected before DMA',wrong_stride,reject=D+8))
    def overlap(g):
        g.source();before=g.read_ram(PHYSICAL[0],128);g.configure_decoder(target=PHYSICAL[0]);g.decode()
        require(g.read_ram(PHYSICAL[0],128)==before,'Input overlap overwritten')
        return g.state(STATE)
    rows.append(case(binary,'source target overlap rejected',overlap,reject=D+8))
    def missing(g):
        g.configure_decoder();g.decode()
        require(g.read_ram(TARGET,32)==b'\xa5'*32 and not g.irq_decoder(),'Missing producer manufactured pixels')
        return g.state(STATE)
    rows.append(case(binary,'no completed producer cannot decode',missing,reject=D+8))
    def hole(g):
        g.source();g.configure_decoder(target=0x8adf8d9e0);g.decode()
        require(not g.irq_decoder() and g.state(STATE)['jobs']==0,'Cross-hole output accepted')
        return g.state(STATE)
    rows.append(case(binary,'actual unmapped RAM gap rejected before write',hole,reject=D+8))
    def off(g):
        g.source();g.configure_decoder();g.decode()
        require(g.read_ram(TARGET,32)==b'\xa5'*32 and not g.irq_decoder(),'Default off decoded pixels')
        return g.state(STATE)
    rows.append(case(binary,'default off remains strict configuration only',off,decode=False,reject=D+8))
    def reset(g):
        complete(g);g.write(D+0x404,1);g.write(D,1)
        s=g.state(STATE)
        require(s['cancelled']==1 and s['acks']==0 and s['pending']==0 and not s['active'],
                'Reset conflated cancellation with acknowledgement')
        return g.output()
    rows.append(case(binary,'reset cancels notification and retains completed actual RAM',reset))
    for verb in ('writeb','writew','writeq'):
        def width(g,verb=verb):
            g.source();g.configure_decoder();g.write(D+8,1,verb)
            require(g.read_ram(TARGET,32)==b'\xa5'*32,'Invalid command width wrote pixels')
            return g.state(STATE)
        rows.append(case(binary,'strict start width '+verb,width,reject=D+8))
    def repeated(g):
        complete(g);g.write(D+0x1c,2);g.write(D+8,0);g.write(D+0x1c,4);g.decode()
        state=g.output()
        require(state['jobs']==2 and [r['valid'] for r in state['records']]==[False,True],
                'Explicit target reuse failed to invalidate prior receipt')
        return state
    rows.append(case(binary,'explicit repeat request retires prior target receipt',repeated))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline)
    result=dict(passed=all(x['passed'] for x in rows),cases=rows,qemu_sha256=sha256(a.qemu),source_sha256=sha256(Path(__file__)))
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not result['passed'])


if __name__=='__main__': main()
