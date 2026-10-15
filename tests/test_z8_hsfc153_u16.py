# SPDX-License-Identifier: GPL-2.0-or-later
"""Neutral NR consumes actual retained decoder RAM without inventing D980 frames."""
import argparse,json,struct,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_d980_internal import require
from test_z8_hsfc1530_u16 import Lab as DecodeLab,complete,TARGET,OUTPUT_BYTES,D,PHYSICAL
from test_z8_producer_journal import configure_d980,A

OPTION='z8-hsfc1538-identity-model'
NR=0x15380000
NR0=0x15340000
PROGRAM=0xa00100000
UNTOUCHED=(0x8a0000000,0x8a0200000)


class Lab(DecodeLab):
    def __init__(self,binary,folder,*,nr_enabled=True,nr0_enabled=False,**kwargs):
        super().__init__(binary,folder,**kwargs)
        self.command[self.command.index('-M')+1]+=',engine153-inplace-identity-model=on'
        if nr_enabled:self.command[self.command.index('-M')+1]+=','+OPTION+'=on'
        if nr0_enabled:self.command[self.command.index('-M')+1]+=',z8-hsfc1534-identity-model=on'

    def prepare_nr(self,*,source=TARGET,mode=0,pitch=8288,width=4140,height=2756,bank=1):
        base=NR if bank else NR0
        program=PROGRAM if bank else PROGRAM+0x1000
        descriptor=bytearray(0x7c0)
        struct.pack_into('<II',descriptor,0,mode,pitch)
        struct.pack_into('<I',descriptor,0x90,width|(height<<16))
        self.tx(f'write {program:#x} {len(descriptor):#x} 0x'+descriptor.hex())
        fields={0x20:0x3c8d,0x40:0xb,0x44:1,0x48:program&0xffffffff,0x4c:program>>32,
                0x74:1,0x94:1,0xb0:source>>32,0xb4:source&0xffffffff,
                0xd8:UNTOUCHED[0]>>32,0xdc:UNTOUCHED[0]&0xffffffff,
                0x100:UNTOUCHED[1]>>32,0x104:UNTOUCHED[1]&0xffffffff,
                0x12c:0xff,0x138:2,0x140:0xff,0x14c:2}
        for off,value in fields.items():self.write(base+off,value)
        for pa in UNTOUCHED:self.tx(f'write {pa:#x} 0x20 0x'+'96'*32)

    def nr_state(self,bank=1):
        return self.state('engine153-inplace-identity-state') if bank else self.state('z8-hsfc-nr-state')['banks'][0]
    def nr_irq(self,bank=1):
        self.tx(f'readl {(NR if bank else NR0)+0x2c:#x}');return self.irqs.get(251+bank-32,False)
    def unchanged_aux(self):
        for pa in UNTOUCHED:require(self.read_ram(pa,32)==b'\x96'*32,'Unproved scratch/plane modified')


def case(binary,name,body,*,reject=None,**kwargs):
    row=dict(name=name,passed=False,cpu_execution=False,firmware_used=False)
    with tempfile.TemporaryDirectory(prefix='z8-history-nr-') as tmp:
        g=Lab(binary,Path(tmp),**kwargs)
        try:
            g.start();row['observed']=body(g)
            require(('Z9_UNKNOWN' in g.text())==bool(reject),'Unexpected strict stop')
            if reject:require(f'addr=0x{reject:x}' in g.text(),'Wrong strict boundary')
            row['passed']=True
        except Exception as e:row['error']=repr(e)
        finally:
            g.close();row.update(stderr=g.text(),owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row


def accepted(g):
    complete(g);g.prepare_nr();g.write(NR+4,1)
    require(g.tx(f'readl {NR+0x2c:#x}')==1 and g.nr_irq(),'Retained decoded source did not complete neutral NR')
    g.output();g.unchanged_aux();state=g.nr_state()
    require(state['consumed_decoder_job']==1 and state['hsfc_frame']==1 and state['consumed_dma_frame']==0,
            'Historical decoder input conflated with D980 producer')
    require(g.state('primary-d980-state')['frames']==0,'Fabricated D980 frame')
    return state


def decode_second(g):
    target=TARGET+0x4000000
    g.write(D+0x1c,6);g.write(D+8,0)
    for off,pa in ((0x120,PHYSICAL[1]),(0x220,target)):
        g.write(D+off,pa&0xffffffff);g.write(D+off+4,pa>>32)
    g.write(D+8,1)
    require(g.state('z8-hsfc1530-decode-state')['jobs']==2,'No actual second decode')
    return target


def nonconsecutive(g):
    g.source(2);g.configure_decoder();g.decode();g.output()
    g.prepare_nr();g.write(NR+4,1);g.write(NR+0x2c,1);g.write(NR+4,0)
    second=decode_second(g)
    g.prepare_nr(source=second);g.write(NR+4,1);g.write(NR+0x2c,1);g.write(NR+4,0)
    g.output()  # Older source still contains its complete original pixels.
    g.prepare_nr();g.write(NR+4,1)
    require(g.nr_state()['jobs']==2,'Nonconsecutive historical generation was consumed twice')
    return g.nr_state()


def sequential_start(g,bank=1):
    base=NR if bank else NR0
    g.source(2);g.configure_decoder();g.decode();g.output()
    g.prepare_nr(bank=bank);g.write(base+4,1);g.write(base+0x2c,1)
    require(g.tx(f'readl {base+4:#x}')==1,'Admitted start retention unexpectedly changed')
    second=decode_second(g);before=g.read_ram(second,OUTPUT_BYTES)
    g.prepare_nr(source=second,bank=bank)
    # The actual RAW44 sequence issues the next start without first writing0.
    g.write(base+4,1)
    require(g.nr_state(bank)['jobs']==2 and g.nr_irq(bank),
            'New decoder generation rejected after native ACK and next start pulse')
    require(g.read_ram(second,OUTPUT_BYTES)==before,'Second actual output bytes changed')
    return g.nr_state(bank)


def run(binary,red=False):
    if red:return [case(binary,'red retained decoder not an accepted NR source',accepted,nr_enabled=False)]
    rows=[case(binary,'actual decoder pixels survive complete neutral NR',accepted)]
    def ack(g):
        accepted(g);g.write(NR+0x20,0);require(not g.nr_irq(),'Mask failed')
        g.write(NR+0x20,0x3c8d);require(g.nr_irq(),'Unmask lost pending cause')
        g.write(NR+0x2c,1);require(not g.nr_irq(),'W1C failed')
        g.write(NR+4,0);g.write(NR+4,1)
        require(g.nr_state()['jobs']==1,'One decoder generation consumed twice')
        return g.nr_state()
    rows.append(case(binary,'masked completion ACK and repeated-generation rejection',ack,reject=NR+4))
    def corrupt(g):
        complete(g);g.prepare_nr();g.tx(f'write {TARGET+10000:#x} 1 0xff');g.write(NR+4,1)
        require(g.nr_state()['jobs']==0 and not g.nr_irq(),'Corrupted actual source accepted')
        g.unchanged_aux();return g.nr_state()
    rows.append(case(binary,'changed decoded RAM rejected without host restoration',corrupt,reject=NR+4))
    for name,changes in (('mode',dict(mode=1)),('stride',dict(pitch=7264)),('width',dict(width=4142)),
                         ('height',dict(height=2758)),('source',dict(source=TARGET+32))):
        def mismatch(g,changes=changes):
            complete(g);g.prepare_nr(**changes);g.write(NR+4,1)
            require(g.nr_state()['jobs']==0,'Mismatched descriptor accepted')
            g.output();g.unchanged_aux();return g.nr_state()
        rows.append(case(binary,'reject differing '+name,mismatch,reject=NR+4))
    def off(g):
        complete(g);g.prepare_nr();g.write(NR+4,1)
        require(g.nr_state()['jobs']==0,'Default-off changed existing source policy')
        return g.nr_state()
    rows.append(case(binary,'default off preserves existing D980-only contract',off,nr_enabled=False,reject=NR+4))
    def ordinary(g):
        configure_d980(g);g.advance()
        require(g.state('primary-d980-state')['frames']==1,'No actual ordinary D980 input')
        before=g.read_ram(A,2048)
        g.prepare_nr(source=A,mode=1,pitch=128,width=64,height=16);g.write(NR+4,1)
        require(g.read_ram(A,2048)==before and g.nr_irq(),'Ordinary D980 pixels changed or did not complete')
        s=g.nr_state()
        require(s['consumed_dma_frame']==1 and s['consumed_decoder_job']==0 and s['source_kind']==0,
                'Ordinary source lineage changed')
        g.unchanged_aux();return s
    rows.append(case(binary,'ordinary D980 input still works with new option off',ordinary,nr_enabled=False))
    rows.append(case(binary,'ordinary D980 input still works with new option on',ordinary))
    rows.append(case(binary,'A B A repetition rejected with both older buffers intact',nonconsecutive,reject=NR+4))
    def bank0(g):
        complete(g);g.prepare_nr(bank=0);g.write(NR0+4,1)
        require(g.nr_irq(0),'Second NR engine did not complete actual input')
        g.output();s=g.nr_state(0)
        require(s['consumed_decoder_job']==1 and s['hsfc_frame']==1,'Bank0 lineage differs')
        return s
    rows.append(case(binary,'bank0 accepts actual decoded historical input',bank0,nr0_enabled=True))
    def off0(g):
        complete(g);g.prepare_nr(bank=0);g.write(NR0+4,1)
        require(not g.nr_irq(0),'Default-off bank0 unexpectedly completed')
        return g.nr_state()
    rows.append(case(binary,'bank0 remains strict when not explicitly enabled',off0,reject=NR0+4))
    def parallel(g):
        g.source(2);g.configure_decoder();g.decode();g.prepare_nr();g.write(NR+4,1)
        second=decode_second(g);g.prepare_nr(source=second,bank=0);g.write(NR0+4,1)
        require(g.nr_irq() and g.nr_irq(0),'Independent bank pending ownership lost')
        banks=g.state('z8-hsfc-nr-state')['banks']
        require([x['hsfc_frame'] for x in banks]==[2,1],'Banks consumed wrong historical frames')
        require(banks[0]['last_actual_ram_fnv1a64']!=banks[1]['last_actual_ram_fnv1a64'],'Per-bank hash identity collapsed')
        g.write(NR+0x2c,1);require(not g.nr_irq() and g.nr_irq(0),'Bank1 ACK cleared bank0 IRQ')
        g.write(NR0+0x2c,1);require(not g.nr_irq(0),'Bank0 ACK failed')
        g.output();return banks
    rows.append(case(binary,'both NR engines retain distinct frame and IRQ identity',parallel,nr0_enabled=True))
    def cross(g):
        accepted(g);g.prepare_nr(bank=0);g.write(NR0+4,1)
        require(not g.nr_irq(0),'Same decoded generation accepted by both banks')
        return g.state('z8-hsfc-nr-state')
    rows.append(case(binary,'same frame cannot be consumed again through another bank',cross,nr0_enabled=True,reject=NR0+4))
    rows.append(case(binary,'bank1 next native start after ACK needs no artificial zero write',sequential_start))
    rows.append(case(binary,'bank0 next native start after ACK needs no artificial zero write',
                     lambda g:sequential_start(g,0),nr0_enabled=True))
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--red-baseline',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);rows=run(a.qemu,a.red_baseline)
    r=dict(passed=all(x['passed'] for x in rows),cases=rows,qemu_sha256=sha256(a.qemu),source_sha256=sha256(Path(__file__)))
    (a.output/'qtest-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(passed=r['passed'],cases=len(rows),failures=[{k:x[k] for k in ('name','error') if k in x} for x in rows if not x['passed']])))
    raise SystemExit(not r['passed'])


if __name__=='__main__':main()
