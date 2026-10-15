# SPDX-License-Identifier: GPL-2.0-or-later
"""CPU-free actual RAM/IRQ tests; profiles derived statically, not native HLG acceptance."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from test_z8_producer_journal import Lab as BaseLab, configure_d980, next_d980, configure151, A, require

OPTION='z8-capture-hlg-pixels-model'
STATE='z8-capture-hlg-pixels-state'

class Lab(BaseLab):
    def __init__(self,binary,folder,enabled):
        super().__init__(binary,folder,False)
        self.command[self.command.index('-M')+1]+=','+OPTION+'='+('on' if enabled else 'off')

def setup(g,mode):
    configure_d980(g); next_d980(g,1)
    y,uv,_=configure151(g,A,0,True)
    g.write(0x1510021c,1)
    g.write(0x1510045c,mode)
    stride=96 if mode==2 else 128
    for off in (0x480,0x484):g.write(0x15100000+off,stride)
    return y,uv,stride*16,stride*(16 if mode==2 else 8)

def positive(g,mode):
    y,uv,ny,nuv=setup(g,mode)
    g.write_ram(y,bytes([0xa5])*ny); g.write_ram(uv,bytes([0x5a])*nuv)
    g.write(0x15100008,1)
    row=g.state(STATE)
    require(row['jobs']==1 and row['source_frame']==1,'Owned frame/job receipt')
    require(row['output_bits']==(12 if mode==2 else 10),'Native output format distinguished')
    require(row['bit_order_verified'] is False,'Do not promote packing hypothesis')
    require(row['pending'] and g.irq(0x10d),'Actual writes precede completion IRQ')
    for pa,n,key in ((y,ny,'y_sha256'),(uv,nuv,'uv_sha256')):
        data=g.read_ram(pa,n)
        require(hashlib.sha256(data).hexdigest()==row[key],'Hash of actual full destination RAM')
        require(data!=bytes([0xa5 if pa==y else 0x5a])*n,'Destination actually written')
    g.write(0x15100044,0); require(g.irq(0x10d),'W1C zero retains completion')
    g.write(0x15100044,1); require(g.irq(0x10d),'Unrelated W1C retains completion')
    g.write(0x15100044,2); require(not g.irq(0x10d),'Native bit1 W1C lowers completion')
    g.expected_unknown=0x15100008; g.write(0x15100008,1)
    require(g.state(STATE)['jobs']==1,'A completed producer frame cannot be reused')
    return row

def rejected(g,off=None,value=None):
    y,uv,ny,nuv=setup(g,5)
    if off is not None:g.write(0x15100000+off,value)
    g.write_ram(y,bytes([0xa5])*ny);g.write_ram(uv,bytes([0x5a])*nuv)
    g.expected_unknown=0x15100008;g.write(0x15100008,1)
    require(g.state(STATE)['jobs']==0 and not g.irq(0x10d),'Rejected request has no completion')
    require(g.read_ram(y,ny)==bytes([0xa5])*ny and g.read_ram(uv,nuv)==bytes([0x5a])*nuv,'Rejection preserves both spans')
    return g.state(STATE)

def sdr(g):
    configure_d980(g);next_d980(g,1);y,uv,_=configure151(g,A,0,True)
    g.write(0x1510021c,1);g.write(0x15100008,1)
    require(g.state(STATE)['jobs']==0,'SDR cannot enter HLG implementation')
    require(g.state('engine151-capture-pixels-state')['jobs']==1,'SDR still completes')
    return [hashlib.sha256(g.read_ram(p,1024)).hexdigest() for p in (y,uv)]

def changed_ram(g):
    y,uv,ny,nuv=setup(g,5)
    # Actual D980 still supplies identity. Change only RAM after its DMA, not
    # the host source/cache: all-white RGGB versus the original zero fixture.
    g.write_ram(A,bytes([255])*2048)
    g.write(0x15100008,1)
    row=g.state(STATE)
    require(row['jobs']==1,'Modified actual RAM is consumed')
    require(row['source_sha256']==hashlib.sha256(bytes([255])*2048).hexdigest(),'Source hash is guest RAM, not cache')
    data=g.read_ram(y,ny)
    # Independent LE32 extraction. 10-bit white must be 1023, not 8-bit 255<<2.
    word=int.from_bytes(data[:4],'little')
    require((word&1023)==1023 and ((word>>10)&1023)==1023,'Full 10-bit values, including low bits')
    return row

def reset_after_completion(g):
    setup(g,2);g.write(0x15100008,1)
    require(g.irq(0x10d),'Pending before reset')
    g.write(0x15100014,0x3807f)
    require(not g.irq(0x10d) and not g.state(STATE)['pending'],'Reset cancels pending completion')
    return g.state(STATE)

def immutable(g):
    try:g.qmp.command('qom-set',path='/machine',property=OPTION,value=False)
    except RuntimeError as error:require('immutable' in str(error),'Immutable option error')
    else:raise AssertionError('Live HLG option changed')
    return g.state(STATE)

def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);cases=[]
    plans=[('packed12',True,lambda g:positive(g,2)),('packed10',True,lambda g:positive(g,5)),
           ('default-off',False,rejected),('bad-stride',True,lambda g:rejected(g,0x480,96)),
           ('bad-mode',True,lambda g:rejected(g,0x45c,1)),
           ('raw-side-rejected',True,lambda g:rejected(g,0x21c,3)),
           ('overlap',True,lambda g:rejected(g,0x9c0,0x40100000)),
           ('ram-causality',True,changed_ram),('reset-pending',True,reset_after_completion),
           ('immutable',True,immutable),
           ('sdr-off',False,sdr),('sdr-on',True,sdr)]
    for name,on,body in plans:
        row=dict(name=name,passed=False);folder=a.output/name;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='z8-hlg-') as tmp:
            g=Lab(a.qemu,Path(tmp),on)
            try:
                g.start();row['observed']=body(g)
                require(not g.qmp.command('query-status')['running'],'QTest stays stopped')
                row['passed']=True
            except Exception as error:row['error']=repr(error)
            finally:
                g.close();row['owned_qemu_stopped']=g.proc is None or g.proc.poll() is not None
                (folder/'stderr.txt').write_text(g.text())
                (folder/'transactions.json').write_text(json.dumps(g.transactions,indent=2)+'\n')
                (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        cases.append(row);print(json.dumps({k:v for k,v in row.items() if k!='observed'}),flush=True)
    equivalent=all(x['passed'] for x in cases[-2:]) and cases[-2].get('observed')==cases[-1].get('observed')
    result=dict(passed=equivalent and all(x['passed'] and x['owned_qemu_stopped'] for x in cases),
        cases=cases,sdr_equivalent=equivalent,firmware_used=False,bit_order_verified=False,
        binary_sha256=hashlib.sha256(a.qemu.read_bytes()).hexdigest())
    (a.output/'qtest-result.json').write_text(json.dumps(result,indent=2)+'\n')
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
