# SPDX-License-Identifier: GPL-2.0-or-later
"""C1 reference adapter; an orientation adapter can reuse run_contract unchanged."""
import argparse,hashlib,importlib,json,struct,sys,zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from test_z8_video_c1 import configure
from test_z8_video_ring_stop import Lab
from video_frontend_contract import run_contract


class C1Adapter:
    def __init__(self,binary,directory):
        self.root=Path(directory);self.root.mkdir(parents=True)
        self.frames=[]
        kernel=self.root/'zero.bin';kernel.write_bytes(bytes(4096))
        raw=self.root/'source.raw'
        self.samples=[(i*7)&16383 for i in range(64*36)]
        raw.write_bytes(struct.pack('<8s6I',b'NZ9RAW1\0',64,36,14,0,0,16383)+
                        struct.pack('<'+'H'*len(self.samples),*self.samples))
        props=tuple(n+'=on' for n in ('image-bank-model','engine150c-control-model',
            'engine150c-stop-model','config150d-model','engine150d-control-model',
            'statistics-model','engine1543-control-model','engine151-control-model',
            'raw-pipeline-model','z8-video-interface-model','z8-video-c1-model',
            'event1508-front-model','event1508-timing-model','event1508-channel-model','event1508-aux-model'))
        props+=('statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        self.m=Lab(binary,self.root/'machine',kernel,accelerator='qtest',properties=props)
    def __enter__(self):
        self.m.__enter__();self.m.observe_irq();configure(self.m);self.start(1);return self
    def __exit__(self,*args):return self.m.__exit__(*args)
    def write(self,p,off,value):self.m.q(f'writel {(0x15300000 if p else 0x152c0000)+off:#x} {value:#x}')
    def read(self,p,off):return int(self.m.q(f'readl {(0x15300000 if p else 0x152c0000)+off:#x}'),0)
    def pending(self,p):return self.read(p,0x1c)
    def masked(self,p):return self.read(p,0x10)
    def irq(self,p):self.pending(p);return self.m.irqs.get((0x10c if p else 0x10b)-32,False)
    def ack(self,p,value):self.write(p,0x1c,value)
    def mask(self,p,value):self.write(p,0x18,value)
    def gate(self,p,value):self.write(p,0x14,value)
    def stop(self,p):self.write(p,8,0)
    def start(self,p):self.write(p,8,1)
    def reset(self,p):self.m.q(f'writel 0x0d154340 {0x200000 if p else 2:#x}')
    def rejected(self):return 'Z9_UNKNOWN' in self.m.log_path.read_text()
    def stats(self):
        s=json.loads(self.m.qmp.command('qom-get',path='/machine',property='z8-video-c1-state'))
        return dict(data_acks=[s['receiver_acks'],s['decoder_acks']],stop_ends=s['stop_ends'],
                    stop_acks=s['stop_acks'],stop_cancelled=s['stop_cancelled'],
                    produced=s['packets'],decoded=s['decoded'],epoch=s['epoch'])
    def frame(self):
        self.m.qmp.command('cont');self.m.q('clock_step 50000000');self.m.qmp.command('stop')
        head=bytes.fromhex(self.m.q('read 0x840000000 80')[2:])
        if head[:8]!=b'NZ8BAYR1':raise AssertionError('C1 adapter accepts only its own packet format')
        amount=struct.unpack_from('<I',head,72)[0]
        packet=bytes.fromhex(self.m.q(f'read 0x840000000 {amount:#x}')[2:])
        raw=zlib.decompress(packet[80:80+struct.unpack_from('<Q',head,40)[0]])
        samples=[(int.from_bytes(raw[y*128+x*14//8:y*128+x*14//8+3],'little')>>((x*14)&7))&16383
                 for y in range(36) for x in range(64)]
        if samples!=self.samples or self.stats()['decoded']!=1:raise AssertionError('Real RAM decode differs')
        self.frames.append(dict(epoch=struct.unpack_from('<Q',head,48)[0],
            virtual_ns=struct.unpack_from('<Q',head,56)[0],samples=len(samples),
            packet_sha256=hashlib.sha256(packet).hexdigest(),raw_sha256=hashlib.sha256(raw).hexdigest()))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--adapter',help='optional module:factory implementing the same semantic adapter')
    args=p.parse_args();factory=C1Adapter
    if args.adapter:
        module,name=args.adapter.split(':',1);factory=getattr(importlib.import_module(module),name)
    result=run_contract(factory,args.qemu,args.output)
    print(json.dumps(dict(passed=result['passed'],cases=len(result['cases']))))
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
