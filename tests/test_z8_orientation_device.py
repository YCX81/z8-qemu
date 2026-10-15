# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit QTest acceptance of orientation input; CPUs stay stopped.

Uses authored zero input, never Nikon firmware. Does not establish UI rotation.
"""
import argparse
import json
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_persistent_media import Machine

BASE = 0x0E005C00


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(binary, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    checks = []

    def check(name, value):
        require(value, name)
        checks.append(name)

    with tempfile.TemporaryDirectory(prefix='orientation-', dir=output) as tmp:
        root = Path(tmp)
        kernel = root / 'authored-zero.bin'
        kernel.write_bytes(bytes(4096))
        with Machine(binary, root / 'on', kernel,
                     properties=('i2c-fixture=on', 'orientation-fixture=on')) as m:
            def get():
                return m.qmp.command('qom-get', path='/machine', property='orientation-input')

            def put(sample):
                return m.qmp.command('qom-set', path='/machine', property='orientation-input', value=sample)

            def wl(offset, value):
                m.q(f'writel {BASE + offset:#x} {value:#x}')

            def begin_read():
                wl(0, 0x66)  # clear FIFO, master TX
                wl(0xC, 1)  # sample register pointer
                wl(8, 0x1D)
                wl(0, 0x27)  # master RX, original bit5 path
                wl(0x14, 6)
                wl(8, 0x1D)

            def drain():
                return bytes(int(m.q(f'readl {BASE + 0xC:#x}'), 0) for _ in range(6)).hex()

            check('default preserves original stationary sample', get() == '000000004000')
            for sample in ('400000000000', 'c00000000000', '000040000000',
                           '0000c0000000', '000000004000', '00000000c000',
                           '7ff080000000', '000000000000'):
                put(sample)
                check('QOM readback ' + sample, get() == sample)
                begin_read()
                check('I2C sample ' + sample, drain() == sample)
                state = json.loads(m.qmp.command('qom-get', path='/machine', property='orientation-state'))
                check('state reports actual sample ' + sample, state['sample_hex'] == sample)
            put('400000000000')
            begin_read()
            put('C00000000000')
            check('pending RX remains an atomic old sample', drain() == '400000000000')
            begin_read()
            check('next RX sees new sample', drain() == 'c00000000000')
            for bad in ('', '0000', '0' * 14, '00000000400z', ' 00000004000',
                        '000000004001', '0x0000004000', '000000004000\n'):
                before = get()
                try:
                    put(bad)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Accepted invalid input: ' + repr(bad))
                check('invalid input leaves sample unchanged ' + repr(bad), get() == before)
            check('no unknown access', 'Z9_UNKNOWN' not in m.log_path.read_text())
            check('CPUs remain stopped', not m.qmp.command('query-status')['running'])
        with Machine(binary, root / 'off', kernel, properties=('i2c-fixture=on',)) as m:
            try:
                m.qmp.command('qom-set', path='/machine', property='orientation-input', value='400000000000')
            except RuntimeError:
                checks.append('disabled fixture rejects input')
            else:
                raise AssertionError('Disabled fixture accepted input')
    result = dict(classification='synthetic_orientation_device_acceptance',
                  qemu_sha256=sha256(Path(binary)), checks=checks, firmware_used=False,
                  ui_rotation_verified=False, physical_hardware_verified=False)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'PASS {len(checks)} orientation device checks', flush=True)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    run(a.qemu, a.output)
