# SPDX-License-Identifier: GPL-2.0-or-later
"""Real paused-CPU QTest for bounded Z8 serial5 configuration fields.

No Nikon firmware, FIFO, DMA, transfer completion or camera acceptance.
Run explicitly with --qemu BINARY --output NEW_DIRECTORY. --red-baseline
demonstrates the native configuration sequence failing without the model.
"""
import argparse
import json
from pathlib import Path

from check_z8_machine import sha256
from test_z8_syscon import case

OPTION = ('z8-serial5-config-model=on',)
OFFSETS = (0x10, 0x40, 0x48, 0x44)


def configuration_steps():
    steps = []
    expected = {}
    for channel in range(5):
        base = 0x0e200100 + channel * 0x100
        values = ((channel % 4) << 30, ((channel % 2) << 8) | ((channel + 1) % 2),
                  0x100 + channel, 0x200 + channel)
        for offset, value in zip(OFFSETS, values):
            address = base + offset
            steps += [(f'readl {address:#x}', 0), (f'writel {address:#x} {value:#x}', None)]
            expected[address] = value
    steps += [(f'readl {address:#x}', value) for address, value in expected.items()]
    # Original 40c17824 fields: mode3, cleared bit8 and a1200 low halfword.
    for address, value in ((0x0e200110, 0xc0000000), (0x0e200140, 0x100),
                           (0x0e200140, 0), (0x0e200148, 0xbeef), (0x0e200148, 1200)):
        steps += [(f'writel {address:#x} {value:#x}', None), (f'readl {address:#x}', value)]
        expected[address] = value
    steps += [(f'readl {address:#x}', value) for address, value in expected.items()]
    return steps


def alternate_configuration_steps():
    # Original40c178bc: read+10/+40/+44, replace mode30..31,
    # replace onlybit0 at+40 and onlylow16 at+44; keep+48 independent.
    return [('readl 0x0e200110', 0), ('readl 0x0e200140', 0),
            ('readl 0x0e200144', 0), ('writel 0x0e200140 0x100', None),
            ('writel 0x0e200148 1200', None),
            ('writel 0x0e200110 0xc0000000', None),
            ('writel 0x0e200140 0x101', None), ('writel 0x0e200144 0x2345', None),
            ('readl 0x0e200110', 0xc0000000), ('readl 0x0e200140', 0x101),
            ('readl 0x0e200144', 0x2345), ('readl 0x0e200148', 1200),
            ('writel 0x0e200140 1', None), ('readl 0x0e200140', 1),
            ('writel 0x0e200140 0', None), ('readl 0x0e200140', 0)]


def flow_configuration_steps():
    # Original40c18368/40c183e4 update paired18/19,26/27,28/29
    # fields at+10. They remain independent from mode30..31.
    steps = []
    expected = {}
    for channel in range(5):
        address = 0x0e200110 + channel * 0x100
        value = (channel % 4) << 30
        steps.append((f'writel {address:#x} {value:#x}', None))
        for bit in (18, 19, 26, 27, 28, 29):
            value |= 1 << bit
            steps += [(f'writel {address:#x} {value:#x}', None), (f'readl {address:#x}', value)]
        for bit in (29, 28, 27, 26, 19, 18):
            value &= ~(1 << bit)
            steps += [(f'writel {address:#x} {value:#x}', None), (f'readl {address:#x}', value)]
        expected[address] = value
    steps += [(f'readl {address:#x}', value) for address, value in expected.items()]
    return steps


def run(binary, *, red_baseline=False):
    binary = Path(binary).resolve()
    if red_baseline:
        results = [case(binary, 'missing serial5 RMW fields on original binary',
                        steps=configuration_steps())]
    else:
        results = [case(binary, 'original alternate configuration and independent fields',
                        extra_properties=OPTION, steps=alternate_configuration_steps()),
                   case(binary, 'five independent channels and original software RMW',
                        extra_properties=OPTION, steps=configuration_steps()),
                   case(binary, 'actual RAW29 D0000000 configuration word', extra_properties=OPTION,
                        steps=[('writel 0x0e200110 0xc0000000', None),
                               ('writel 0x0e200110 0xd0000000', None), ('readl 0x0e200110', 0xd0000000)]),
                   case(binary, 'paired source-derived configuration fields and channel isolation',
                        extra_properties=OPTION, steps=flow_configuration_steps())]
        for offset in OFFSETS:
            address = 0x0e200100 + offset
            for verb in ('readl', 'writel'):
                results.append(case(binary, f'default off {verb} {address:#x}',
                                    operation=(verb, address, 4)))
            for verb, width in (('readb', 1), ('readw', 2), ('readq', 8),
                                ('writeb', 1), ('writew', 2), ('writeq', 8)):
                if width == 8 and address & 7:
                    # QEMU splits unaligned64 at+44 into two32-bit callbacks.
                    # It is not an8-byte callback which this device can reject.
                    continue
                results.append(case(binary, f'reject {verb} {address:#x}',
                                    extra_properties=OPTION, operation=(verb, address, width)))
        for address, value in ((0x0e200110, 1), (0x0e200110, 0x02000000),
                               (0x0e200110, 0x01000000), (0x0e200110, 0x00100000),
                               (0x0e200140, 2), (0x0e200140, 0x200),
                               (0x0e200148, 0x10000), (0x0e200144, 0x10000)):
            results.append(case(binary, f'reject unmodeled field bits {address:#x}={value:#x}',
                extra_properties=OPTION, steps=[(f'writel {address:#x} {value:#x}', None)],
                operation=('writel', address, 4)))
        for address in (0x0e200010, 0x0e200100, 0x0e200104, 0x0e20011c,
                        0x0e200120, 0x0e200124, 0x0e200128, 0x0e200154,
                        0x0e20014c, 0x0e200150, 0x0e200610):
            results.append(case(binary, f'FIFO/control/neighbor remains strict {address:#x}',
                                extra_properties=OPTION, operation=('readl', address, 4)))
        # This pinned build registers Z8 and Z9; it has no ZR machine.
        for machine in ('nikon-z9-lab',):
            results.append(case(binary, f'other machine stays strict {machine}', machine=machine,
                                operation=('readl', 0x0e200110, 4)))
            item = case(binary, f'option rejected on {machine}', machine=machine,
                        extra_properties=OPTION, steps=[])
            message = 'z8-serial5-config-model requires Z8 at machine creation'
            item['expected_startup_rejection'] = message
            item['passed'] = (message in item.get('stderr', '') and item['returncode'] != 0
                              and item['owned_qemu_stopped'])
            results.append(item)
        item = case(binary, 'option requires bootstrap fixtures', bootstrap=False,
                    extra_properties=OPTION, steps=[])
        message = 'z8-serial5-config-model requires bootstrap-fixtures=on'
        item['expected_startup_rejection'] = message
        item['passed'] = (message in item.get('stderr', '') and item['returncode'] != 0
                          and item['owned_qemu_stopped'])
        results.append(item)
        results.append(case(binary, 'legacy serial5 fields unchanged', extra_properties=OPTION,
            steps=[('writel 0x0e200108 0x20', None), ('readl 0x0e200108', 0x20),
                   ('writel 0x0e200114 0x1234', None), ('readl 0x0e200114', 0x1234)]))
        results.append(case(binary, 'legacy transfer enable still rejected', extra_properties=OPTION,
            steps=[('writel 0x0e200108 0x1000', None)], operation=('writel', 0x0e200108, 4)))
        results.append(case(binary, 'host unaligned quad splits into two independent fields', extra_properties=OPTION,
            steps=[('writel 0x0e200144 0x1234', None), ('writel 0x0e200148 0x5678', None),
                   ('readq 0x0e200144', 0x0000567800001234),
                   ('writeq 0x0e200144 0x0000123400005678', None),
                   ('readl 0x0e200144', 0x5678), ('readl 0x0e200148', 0x1234)]))
    return dict(classification='real_qtest_bounded_serial5_configuration_fields',
        firmware_used=False, cpu_execution=False, fifo_implemented=False,
        completion_implemented=False, camera_acceptance=False, physical_hardware_verified=False,
        red_baseline=red_baseline, qemu_sha256=sha256(binary),
        source_sha256=sha256(Path(__file__).resolve()), cases=results,
        passed=all(item['passed'] for item in results))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--red-baseline', action='store_true')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = run(args.qemu, red_baseline=args.red_baseline)
    (args.output / 'qtest-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
