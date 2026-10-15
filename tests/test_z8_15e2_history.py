# SPDX-License-Identifier: GPL-2.0-or-later
"""Historical U16 -> actual151 NV16 -> JPEG bank1, with real RAM SHA parent chain."""
import argparse,hashlib,io,json,tempfile
from pathlib import Path
from PIL import Image
from test_z8_hsfc151_pixels import Lab as HistoryLab,Y,UV
from test_z8_15e2_banks import Lab as BankOperations
from test_z8_d980_internal import require

class Lab(HistoryLab):
    program=BankOperations.program
    begin=BankOperations.begin
    gates=BankOperations.gates
    ack=BankOperations.ack
    banks=BankOperations.banks
    leases=BankOperations.leases
    jpeg_bytes=BankOperations.jpeg_bytes
    def __init__(self,binary,root):
        super().__init__(binary,root)
        self.command[self.command.index('-M')+1]+=',output-buffer-ownership-model=on,engine15e2-config-model=on,engine15e2-jpeg-model=on,engine15e2-bank1-jpeg-model=on'

def run(binary,output):
    output.mkdir(parents=True,exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='jpeg-history-') as tmp:
        g=Lab(binary,Path(tmp))
        try:
            g.start();g.prepare_source();g.prepare_isp();g.start_isp();g.write(0x15100044,2)
            source=(Y,UV,4128,2752,4128);target=0x8c8000000
            g.program(1,source,target,capacity=32768);g.begin(1);g.gates(1)
            b=g.banks()[1];require(b['source_kind']==1 and b['frame']==1 and b['decoder_job']==1 and b['parent_151_job']==1,'JPEG lost HSFC parent identity')
            record=next(r for r in g.state('engine15e2-bank-state')['sources'] if r['generation']==b['generation'])
            parent=g.state('z8-hsfc151-state')['records'][0]
            require(record['isp_record_job']==parent['job'] and record['y_sha256']==parent['y_sha256'] and record['uv_sha256']==parent['uv_sha256'],'HSFC RAM hash not bound')
            data=g.jpeg_bytes(1);require(g.banks()[0]['jobs']==0 and g.leases()[7]['frame']==1,'bank0 or lease identity changed')
            g.ack(1);require(not g.leases()[7]['held'] and 'Z9_UNKNOWN' not in g.text(),'History JPEG retirement failed')
            result=dict(passed=True,bank=b,producer=record,jpeg_sha256=hashlib.sha256(data).hexdigest(),qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),native_firmware=False)
            (output/'bank1.jpg').write_bytes(data);(output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        finally:g.close();(output/'stderr.log').write_text(g.text())
    print(json.dumps(dict(passed=True)))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
