# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic command tests for the observed Z8 portrait image setup.

Configuration alone never establishes a rendered frame or completion.
"""
import argparse
import json
from pathlib import Path
import tempfile

from test_z8_persistent_media import Machine
from check_z8_machine import sha256


def run(binary, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    observed = [(0x351, 0), (0x7f0, 0), (0x4c2, 0), (0x790, 0),
                (0x710, 0x40000201), (0x71a, 1)]
    scenarios = [('observed_setup', observed, True)]
    scenarios += [(f'reject_{reg:x}', [(reg, 2 if reg != 0x710 else 0x40000401)], False)
                  for reg, _ in observed]
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root = Path(tmp); kernel = root / 'authored-zero.bin'
        kernel.write_bytes(bytes(4096))
        for name, packets, accepted in scenarios:
            with Machine(binary, root / name, kernel,
                         properties=('graphics-image-model=on',)) as m:
                before = json.loads(m.qmp.command('qom-get', path='/machine', property='graphics-state'))
                for reg, value in packets:
                    m.q(f'writel 0x15540000 {((0x48 << 24) | (reg << 12) | 1):#x}')
                    m.q(f'writel 0x15540000 {value:#x}')
                after = json.loads(m.qmp.command('qom-get', path='/machine', property='graphics-state'))
                if after['fault'] != (not accepted):raise AssertionError(name)
                if after['pending'] != before['pending']:raise AssertionError('Config generated completion')
                if m.qmp.command('query-status')['running']:raise AssertionError('CPU execution')
                cases.append(dict(name=name,passed=True,configuration_only=True))
    result = dict(classification='synthetic_portrait_graphics_setup', cases=cases,
                  qemu_sha256=sha256(Path(binary)), ui_rotation_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS {len(cases)} portrait graphics configuration checks')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run(a.qemu,a.output)
