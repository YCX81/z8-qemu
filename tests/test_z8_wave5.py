# SPDX-License-Identifier: GPL-2.0-or-later
"""A virtual Wave5 identity must not imply original microcode execution."""
import argparse,json,tempfile
from pathlib import Path
from test_z8_persistent_media import Machine


def run(binary,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);cases=[]
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        root=Path(temporary);kernel=root/'zero.bin';kernel.write_bytes(bytes(4096))
        for diagnostic,program in ((False,True),(True,False),(True,True)):
            props=['z8-video-processing-config-model=on','wave5-vpu-model=on']
            if diagnostic:props+=['wave5-host-codec-diagnostic=on']
            name=f'host{int(diagnostic)}-code{int(program)}'
            with Machine(binary,root/name,kernel,accelerator='qtest',properties=props) as m:
                if int(m.q('readl 0x16011044'),0)!=0x5210:raise AssertionError('Virtual product')
                data=(b'\x01' if program else b'\0')+bytes(4095)
                m.q('write 0xa00010000 4096 0x'+data.hex())
                for off,value in {0x110:0x40010000,0x114:4096,0x11c:0x40020000,0x120:4096,
                                  0x100:1,0x70:1,0x6c:1}.items():
                    m.q(f'writel {0x16010000+off:#x} {value:#x}')
                state=json.loads(m.qmp.command('qom-get',path='/machine',property='wave5-vpu-state'))
                ok=diagnostic and program
                if state['booted']!=ok:raise AssertionError('Initialization gate')
                if state['vendor_microcode_executed'] or state['input_bytes'] or state['output_bytes']:
                    raise AssertionError('Invented processing')
                if ('Z9_UNKNOWN' in m.log_path.read_text())==ok:raise AssertionError('Strict rejection')
                cases.append(dict(name=name,state=state))
    (output/'result.json').write_text(json.dumps(dict(passed=True,cases=cases),indent=2)+'\n')
    print(json.dumps(dict(passed=True,cases=len(cases))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
