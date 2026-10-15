# SPDX-License-Identifier: GPL-2.0-or-later
"""Z8 native reset table IDs0x4a/4b: empty latches, no DMA/IRQ readiness."""
import argparse
import json
from pathlib import Path
import tempfile
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'latch',kernel,accelerator='qtest',properties=('z8-video-interface-model=on',)) as m:
            def state():return json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            before=int(m.q('readl 0xd154248'),0)
            m.q('writel 0xd154248 0x800000')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('native ID74 release unsupported')
            if not state()['unit74_released']:raise AssertionError('release request not retained')
            if int(m.q('readl 0xd154248'),0)!=(before|0x800000):raise AssertionError('shared latch corruption')
            m.q('writel 0xd154248 0x800000')
            m.q('writel 0xd154248 0x1000000')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('native ID75 release unsupported')
            if not state()['unit75_released']:raise AssertionError('second release request not retained')
            m.q('writel 0xd154348 0x800000')
            s=state()
            if s['unit74_released'] or s['unit74_releases']!=2 or s['unit74_asserts']!=1:
                raise AssertionError('release/hold request accounting')
            if not s['unit75_released']:raise AssertionError('unit74 reset changed unit75')
            if int(m.q('readl 0xd154248'),0)!=((before&~0x800000)|0x1000000):raise AssertionError('unrelated latch bits changed')
            m.q('writel 0xd154348 0x1000000')
            s=state()
            if s['unit75_released'] or s['unit75_releases']!=1 or s['unit75_asserts']!=1:
                raise AssertionError('second unit request accounting')
            if s['transferred_bytes'] or s['completion_events']:raise AssertionError('invented completion')
            m.q('writel 0xd154248 0x2000')
            if int(m.q('readl 0xd154248'),0)!=(before|0x2000):raise AssertionError('existing reset path changed')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('unexpected supported access')
            cases.append({'name':'latch, isolation, repeated requests, no completion','passed':True})
        for name,command,properties,machine in (
            ('default-off','writel 0xd154248 0x800000',(),'nikon-z8-bringup'),
            ('mixed-bits','writel 0xd154248 0x802000',('z8-video-interface-model=on',),'nikon-z8-bringup'),
            ('combined-new-bits','writel 0xd154248 0x1800000',('z8-video-interface-model=on',),'nikon-z8-bringup'),
            ('wrong-width','writeq 0xd154248 0x800000',('z8-video-interface-model=on',),'nikon-z8-bringup'),
            ('other-machine','writel 0xd154248 0x800000',('z8-video-interface-model=on',),'nikon-z9-lab')):
            try:
                with Machine(binary,root/name,kernel,accelerator='qtest',properties=properties,machine=machine) as m:
                    if name=='other-machine':raise AssertionError('Z8-only model unexpectedly instantiated')
                    m.q(command)
                    if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError('unexpected admission: '+name)
            except RuntimeError as exc:
                if name!='other-machine' or 'requires Z8 at machine creation' not in str(exc):raise
            cases.append({'name':name,'passed':True})
    report={'passed':True,'cases':cases,'scope':'reset request only; no physical unit or recording verification'}
    (output/'result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'passed':True,'cases':len(cases)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.qemu,args.output)
