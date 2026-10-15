# SPDX-License-Identifier: GPL-2.0-or-later
"""Native receiver end response, distinct from actual image completion."""
import argparse,json
from pathlib import Path
from check_z8_machine import sha256
from test_z8_d980_internal import require
from test_z8_hsfc152c_pixels import case,first_frame,STATE,P,PHYSICAL


def ended(g):
    row=first_frame(g);g.ack_image()
    before=g.state(STATE);g.write(P+8,0)
    require(g.tx(f'readl {P+0x1c:#x}')==4 and g.irq152c(),'Owned receiver stop did not acknowledge stream end')
    state=g.state(STATE)
    require(state['frames']==before['frames'] and state['pixel_bytes']==before['pixel_bytes'],
            'Control end manufactured another image')
    require(not g.state('engine152c-request-state')['pending'] and state['ends']==1,'Receiver remained active')
    require(g.packet(PHYSICAL[0],1)['packet_sha256']==row['packet_sha256'],'End changed completed packet RAM')
    return state


def run(binary,red=False):
    if red:return [case(binary,'red original end wait cannot complete',ended)]
    rows=[case(binary,'actual completed packet precedes independent end response',ended)]
    def pending(g):
        first_frame(g);g.write(P+8,0)
        require(g.tx(f'readl {P+0x1c:#x}')==7,'End discarded pending image cause')
        g.write(P+0x1c,3)
        require(g.tx(f'readl {P+0x1c:#x}')==4 and g.irq152c(),'Image ACK lost independent end')
        require(g.state(STATE)['end_acks']==0,'Image ACK counted as end ACK')
        g.write(P+0x1c,4);require(not g.irq152c() and g.state(STATE)['end_acks']==1,'End W1C failed')
        g.ack_preview();g.advance();require(g.state(STATE)['frames']==1,'Stopped stream produced another packet')
        return g.state(STATE)
    rows.append(case(binary,'pending data and end have independent W1C ownership',pending))
    def masked(g):
        first_frame(g);g.ack_image();g.write(P+0x18,0);g.write(P+8,0)
        require(g.tx(f'readl {P+0x1c:#x}')==4 and not g.irq152c(),'Masked end not retained')
        g.write(P+0x18,0x79f);require(g.irq152c(),'Unmask did not deliver actual end')
        g.write(P+0x1c,4);g.write(P+8,0)
        require(not g.irq152c() and g.state(STATE)['ends']==1,'Repeated idle stop fabricated another end')
        return g.state(STATE)
    rows.append(case(binary,'masked end survives until acknowledged once',masked))
    def empty(g):
        g.setup_pixels();g.write(P+8,0)
        require(g.tx(f'readl {P+0x1c:#x}')==4 and g.state(STATE)['frames']==0,'Empty owned stop manufactured pixels')
        g.write(P+0x1c,4);g.unchanged_outputs();return g.state(STATE)
    rows.append(case(binary,'owned empty stream can stop without an image event',empty))
    def idle(g):
        g.write(P+8,0)
        require(g.tx(f'readl {P+0x1c:#x}')==0 and g.state(STATE)['ends']==0,'Unowned idle stop fabricated end')
        return g.state(STATE)
    rows.append(case(binary,'idle without an owned request produces no end',idle))
    def unacked(g):
        ended(g);g.write(P+8,1)
        require(g.state(STATE)['frames']==1 and g.state(STATE)['ends']==1,'Unacknowledged end allowed restart')
        return g.state(STATE)
    rows.append(case(binary,'new request blocked until end acknowledgement',unacked,reject=P+8))
    def reset(g):
        ended(g);g.write(P+0x404,1);g.write(P+4,1);g.write(P,1)
        state=g.state(STATE)
        require(state['pending']==0 and state['cancelled']==1 and state['end_acks']==0,'Reset conflated end cancellation with ACK')
        require(not g.irq152c(),'Reset left end IRQ active');g.packet(PHYSICAL[0],1);return state
    rows.append(case(binary,'reset cancels pending end and preserves completed RAM',reset))
    def off(g):
        g.setup_pixels();g.write(P+8,0)
        require(g.tx(f'readl {P+0x1c:#x}')==0 and g.state(STATE)['ends']==0,'Control-only mode emitted pixel-supplier end')
        return g.state(STATE)
    rows.append(case(binary,'disabled supplier retains old control-only behavior',off,enabled=False))
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
