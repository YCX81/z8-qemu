# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only native video configuration evidence, without image completion."""
import argparse
import json
from pathlib import Path
import tempfile

from test_z8_persistent_media import Machine


def run(binary, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(dir=output) as directory:
        root = Path(directory)
        kernel = root / 'zero.bin'
        kernel.write_bytes(bytes(4096))
        with Machine(binary, root/'observer', kernel, accelerator='qtest',
                     properties=('z8-video-interface-model=on',)) as m:
            def snapshot():
                return json.loads(m.qmp.command('qom-get', path='/machine',
                                               property='z8-video-data-state'))
            empty = snapshot()
            if empty['receiver'] or empty['decoder']:
                raise AssertionError('unsubmitted configuration')
            m.q('writel 0x152c0548 1920')
            m.q('writel 0x15300548 1280')
            state = snapshot()
            if state['receiver']['0x548'] != 1920 or state['decoder']['0x548'] != 1280:
                raise AssertionError('receiver and decoder domains aliased')
            if snapshot() != state:
                raise AssertionError('observer changed state')
            interface = json.loads(m.qmp.command('qom-get', path='/machine',
                                                property='z8-video-interface-state'))
            if interface['transferred_bytes'] or interface['completion_events']:
                raise AssertionError('observation manufactured data')
    result = dict(passed=True, observation_only=True)
    (output/'result.json').write_text(json.dumps(result)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run(a.qemu, a.output)
