# SPDX-License-Identifier: GPL-2.0-or-later
"""Native object1f2 shared-buffer configuration, without video data production."""
import argparse
import json
import tempfile
from pathlib import Path
from check_z8_machine import sha256
from test_z8_persistent_media import Machine


def run(binary, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    fields={0x8000:1,0x9000:3,0x9400:3,0x9004:3,0x9404:3,
            0x9008:0x7fff,0x9408:0x7fff,0x900c:3,0x940c:3}
    for bank in (0x9200,0x9300,0x9600,0x9700):
        half=bank>=0x9600
        for off,mask in {0:0x700f,0x18:0x1fffffff if half else 0x3fffffff,
                         0x1c:0xffff,0x20:0xffffff,
                         0x28:0x1fffffff if half else 0x3fffffff,
                         0x2c:0xffff,0x30:0xffffff,
                         0x44:0x1fff7fff if half else 0x3fff7fff,
                         0x48:0x1ff07ff if half else 0x3ff07ff,
                         0x4c:0x1fff7fff if half else 0x3fff7fff,
                         0x64:0x1f0003 if half else 0x3f0003,
                         0x68:0xfff,0x7c:0x3ff}.items():fields[bank+off]=mask
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        with Machine(binary,root/'configuration',kernel,accelerator='qtest',
                     properties=('z8-video-interface-model=on',)) as m:
            # Original Z8 reset table IDs4a/4b -> bank8 bits23/24.
            # These empty processing targets own no source/ISP work.
            m.q('writel 0x0d154248 0x800000')
            m.q('writel 0x0d154248 0x1000000')
            if int(m.q('readl 0x0d154248'),0)&0x1800000 != 0x1800000:
                raise AssertionError('Independent empty processing reset releases')
            m.q('writel 0x0d154348 0x800000')
            if int(m.q('readl 0x0d154248'),0)&0x1800000 != 0x1000000:
                raise AssertionError('One reset must preserve its peer')
            for bank in (0x9200,0x9600,0x9300,0x9700):
                m.q(f'writel {0x15640000+bank+0x40:#x} 15')
                if int(m.q(f'readl {0x15640000+bank+0x40:#x}'),0)!=15:
                    raise AssertionError('Native buffer selection')
                for index in range(8):
                    address=0x15640000+bank+0x80+index*8
                    value=0x203000000+index*0x3000000+(bank-0x9200)*0x100000
                    m.q(f'writeq {address:#x} {value:#x}')
                    if int(m.q(f'readq {address:#x}'),0)!=value:raise AssertionError('36-bit buffer address')
            for bank in (0x9200,0x9600,0x9300,0x9700):
                for index in range(8):
                    address=0x15640000+bank+0x80+index*8
                    expected=0x203000000+index*0x3000000+(bank-0x9200)*0x100000
                    if int(m.q(f'readq {address:#x}'),0)!=expected:
                        raise AssertionError('Independent submitted buffer banks')
            for off,mask in fields.items():
                m.q(f'writel {0x15640000+off:#x} {mask:#x}')
                if int(m.q(f'readl {0x15640000+off:#x}'),0)!=mask:
                    raise AssertionError(f'Native shared-buffer word {off:x}')
            m.q('writel 0x15648010 1')
            # Native request resets the first interface configuration domain.
            for off in fields:
                if int(m.q(f'readl {0x15640000+off:#x}'),0):raise AssertionError('Reset configuration')
            if int(m.q('readq 0x15649280'),0):raise AssertionError('Reset buffer addresses')
            m.q('writel 0x15660010 1')
            state=json.loads(m.qmp.command('qom-get',path='/machine',property='z8-video-interface-state'))
            if state['shared_reset_requests']!=2:raise AssertionError('Reset request count')
            if state['transferred_bytes'] or state['completion_events']:raise AssertionError('Invented video data')
            if 'Z9_UNKNOWN' in m.log_path.read_text():raise AssertionError('Observed configuration rejected')
            cases.append(dict(name='bounded fields and reset without data',words=len(fields),passed=True))
        for name,cmd,opt in (
            ('opt in','readl 0x15648000',False),
            ('status unsupported','readl 0x15648050',True),
            ('unknown register','readl 0x15649010',True),
            ('reserved bits','writel 0x15649244 0x8000',True),
            ('byte access','readb 0x15649000',True),
            ('reset read unsupported','readl 0x15648010',True)):
            with Machine(binary,root/name.replace(' ','-'),kernel,accelerator='qtest',
                         properties=('z8-video-interface-model=on',) if opt else ()) as m:
                m.q(cmd)
                if 'Z9_UNKNOWN' not in m.log_path.read_text():raise AssertionError(name)
                cases.append(dict(name=name,passed=True))
    result=dict(passed=True,cases=cases,qemu_sha256=sha256(binary),pixel_processing=False,recording=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.qemu,a.output)
