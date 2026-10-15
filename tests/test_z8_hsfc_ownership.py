# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual dual encoder output leases: independent slot/job/ACK identity."""
import argparse,json,tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_hsfc153c_lossless import Lab, BANK
from test_z8_hsfc1540_lossless import RAW, ENCODED
from test_z8_hsfc153_u16 import decode_second, NR0
from test_z8_d980_internal import require


def run(binary, output):
    output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for name in ('independent-ack','other-slot-untouched-on-failure'):
        with tempfile.TemporaryDirectory(prefix='hsfc-owner-') as tmp:
            g=Lab(binary,Path(tmp))
            try:
                g.start();g.prepare_source(count=2);g.prepare_isp();g.start_isp();g.write(0x15100044,2)
                second=decode_second(g);g.prepare_nr(source=second,bank=0);g.write(NR0+4,1);g.write(NR0+0x2c,1)
                g.prepare_isp(source=second,raw_target=RAW+0x4000000);g.start_isp();g.write(0x15100044,2)
                g.encoder_bank(0x15400000);g.write(0x15400008,1)
                def owner():return g.state('output-buffer-ownership-state')
                def slots():return {x['slot']:x for x in owner()['slots']}
                a=slots();require(a[1]['held'] and a[1]['complete'] and not a[5]['held'],'Independent1540 lease')
                before=owner()
                target=ENCODED if name.endswith('failure') else ENCODED+0x8000000
                g.encoder_bank(BANK,source=RAW+0x4000000,target=target);g.write(BANK+8,1)
                if name.endswith('failure'):
                    require(owner()==before,'Failed153c reservation cancelled/changed1540 lease')
                    require('Z9_UNKNOWN' in g.text() and not g.irq_bank(BANK),'Rejected overlap completed')
                else:
                    s=slots();require(s[1]['held'] and s[5]['held'] and s[1]['job']==s[5]['job']==1,'Two distinct bank-local job1 leases')
                    require(s[1]['frame']==1 and s[5]['frame']==2,'Historical frame identity was replaced by D980 globals')
                    require(g.state('z8-hsfc1540-state')['output_ownership_connected'],'Unconnected ownership declaration')
                    g.write(BANK+0x20,0);g.write(BANK+0x2c,1)
                    require(slots()==s,'Mask/unrelated W1C retired output')
                    acks=owner()['acknowledged'];g.write(0x1540002c,0x100)
                    require(not slots()[1]['held'] and slots()[5]['held'],'1540 W1C released153c')
                    require(owner()['acknowledged']==acks+1,'1540 ACK called twice')
                    g.write(0x1540002c,0x100);require(owner()['acknowledged']==acks+1,'Duplicate1540 ACK')
                    g.write(BANK+0x2c,0x100)
                    require(not slots()[5]['held'] and owner()['acknowledged']==acks+2,'153c failed exact lease retirement')
                    require(all(r['acknowledged'] for r in g.state('z8-hsfc1540-state')['records']),'History ACK metadata not committed')
                    require('Z9_UNKNOWN' not in g.text(),'Unexpected lifecycle boundary')
                rows.append(dict(name=name,passed=True,owner=owner()))
            finally:
                g.close();(output/(name+'.log')).write_text(g.text())
    result=dict(passed=True,cases=rows,qemu_sha256=sha256(binary),firmware_execution=False,physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
