# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual QTest checks for four bounded Z8 indexed display-parameter banks.

No firmware runs. The authored zero kernel stays paused; owned QEMUs exit.
Retention checks do not establish rectangle rendering or camera acceptance.
"""
import argparse
import json
from pathlib import Path

from check_z8_machine import sha256
from test_z8_persistent_media import Machine

BASES = (0x15480000, 0x15484000)
BANKS = (0x1800, 0x1880, 0x1900, 0x1980)
MASKS = (0x3fffffff, 0x3fffffff, 0x003f003f, 0x00ffffff)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(binary, output, *, first_write_only=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    kernel = output / 'authored-zero.bin'
    kernel.write_bytes(bytes(4))
    checks, counter = [], 0

    def machine(properties=('engine1548-config-model=on',), model='nikon-z8-bringup'):
        nonlocal counter
        counter += 1
        return Machine(binary, output / f'q{counter}', kernel, properties, machine=model)

    def checked(name, fn):
        try:
            fn()
            checks.append(dict(name=name, passed=True))
        except Exception as error:
            checks.append(dict(name=name, passed=False, error=repr(error)))

    def first():
        with machine() as m:
            m.q('writel 0x15485800 0x000a016a')
            log = m.log_path.read_text(errors='replace')
            require('Z9_UNKNOWN' not in log, 'Observed capture-03 write remains unknown: ' + log[-600:])
            require(int(m.q('readl 0x15485800'), 0) == 0x000a016a, 'Capture-03 parameter did not round-trip')
    checked('observed capture-03 first indexed parameter write', first)

    def retained():
        with machine() as m:
            before = m.qmp.command('qom-get', path='/machine', property='engine1548-scanout-state')
            expected = {}
            for unit, base in enumerate(BASES):
                for bank, (offset, mask) in enumerate(zip(BANKS, MASKS)):
                    for index in range(32):
                        address = base + offset + index * 4
                        require(int(m.q(f'readl {address:#x}'), 0) == 0, 'Fresh indexed parameter is not zero')
                        value = ((unit + 1) * 0x112233 + bank * 0x12345 + index * 0x101) & mask
                        expected[address] = value
                        m.q(f'writel {address:#x} {value:#x}')
                    m.q(f'writel {base + offset:#x} {mask:#x}')
                    expected[base + offset] = mask
            for address, value in expected.items():
                require(int(m.q(f'readl {address:#x}'), 0) == value, 'Independent unit/bank/entry changed')
            for base in BASES:
                require(int(m.q(f'readl {base + 0x1b10:#x}'), 0) == 0, 'Parameters changed enable bitmap')
                m.q(f'writel {base + 0x1a00:#x} 0x00100020')
                require(int(m.q(f'readl {base + 0x1a00:#x}'), 0) == 0x00100020, 'Existing adjacent point register changed')
                require(int(m.q(f'readl {base + 0x19fc:#x}'), 0) == expected[base + 0x19fc], 'Adjacent point aliased last record')
            after = m.qmp.command('qom-get', path='/machine', property='engine1548-scanout-state')
            require(before == after, 'Parameter retention produced a display job or completion')
            require('Z9_UNKNOWN' not in m.log_path.read_text(errors='replace'), 'Valid indexed parameters hit unknown MMIO')
    if not first_write_only:
        checked('all 256 indexed words remain independent and cause no scanout', retained)

        def rejected(command, properties=('engine1548-config-model=on',), model='nikon-z8-bringup'):
            with machine(properties, model) as m:
                m.q(command)
                require('Z9_UNKNOWN' in m.log_path.read_text(errors='replace'), 'Expected strict MMIO rejection: ' + command)
        for bank, mask in zip(BANKS, MASKS):
            invalid = (~mask & 0xffffffff) & -~mask
            checked(f'reserved bits rejected in bank {bank:#x}',
                    lambda bank=bank, invalid=invalid: rejected(f'writel {0x15484000 + bank:#x} {invalid:#x}'))
        for command in ('writeb 0x15485800 1', 'writew 0x15485800 1',
                        'writeq 0x15485800 1', 'writel 0x15485802 1',
                        'readl 0x154857fc', 'readl 0x15485b0c',
                        'readl 0x15489800', 'writel 0x15485b14 0', 'readl 0x15485b14'):
            checked('strict width/alignment/neighbor: ' + command, lambda command=command: rejected(command))
        checked('config flag remains required', lambda: rejected('writel 0x15485800 1', properties=()))
        checked('Z9 does not silently gain Z8 indexed records',
                lambda: rejected('writel 0x15485800 1', model='nikon-z9-lab'))
        def bootstrap_required():
            try:
                with machine(('bootstrap-fixtures=off', 'nand-fixture=off', 'engine1548-config-model=on')):
                    raise AssertionError('Config without bootstrap unexpectedly started')
            except RuntimeError as error:
                require('requires bootstrap-fixtures=on' in str(error), 'Unexpected startup rejection: ' + str(error))
        checked('bootstrap dependency remains required', bootstrap_required)
    report = dict(passed=all(row['passed'] for row in checks), checks=checks,
                  qemu_sha256=sha256(binary), kernel_sha256=sha256(kernel),
                  firmware_used=False, cpu_execution=False, camera_acceptance=False,
                  scope='Bounded display register retention and strict MMIO boundary only')
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--first-write-only', action='store_true')
    args = parser.parse_args()
    result = run(args.qemu.resolve(), args.output, first_write_only=args.first_write_only)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
