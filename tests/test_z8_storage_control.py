# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free QTest for Z8 bounded storage setup and explicit empty SD input."""
import argparse
import json
from pathlib import Path

from check_z8_machine import sha256
from test_z8_syscon import case

EMPTY = ('z8-sd-empty-fixture=on',)


def run(binary):
    binary = Path(binary).resolve()
    results = [case(binary, 'bounded storage configuration retention', steps=[
        ('writel 0x17820200 0', None), ('readl 0x17820200', 0),
        ('writel 0x17820230 1', None), ('writel 0x17820210 2', None),
        ('writel 0x17820200 1', None), ('readl 0x17820200', 1),
        ('readl 0x17820210', 2), ('readl 0x17820230', 1),
        ('writel 0x17820210 0x30303', None), ('readl 0x17820210', 0x30303),
        ('readl 0x17820200', 1), ('readl 0x17820230', 1),
    ]), case(binary, 'explicit empty-card bit is independent of retained configuration',
             extra_properties=EMPTY, steps=[
                 ('readl 0x17a0000c', 1),
                 ('writel 0x17a0000c 0x01010200', None),
                 ('readl 0x17a0000c', 0x01010201),
                 ('writel 0x17a0000c 0x01010201', None),
                 ('readl 0x17a0000c', 0x01010201),
                 ('writel 0x17a0000c 0', None), ('readl 0x17a0000c', 1),
             ])]
    for address, value in ((0x17820200, 2), (0x17820210, 4),
                           (0x17820230, 16), (0x17a0000c, 0x00100000)):
        # QTest's common invalid-operation helper uses 0x9e; the explicit
        # setup below exercises the exact forbidden bit before the probe.
        results.append(case(binary, f'forbidden configuration bits {address:#x}',
                            extra_properties=EMPTY,
                            steps=[(f'writel {address:#x} {value:#x}', None)],
                            operation=('writel', address, 4)))
    for operation in (('readb', 0x17820200, 1), ('readw', 0x17820210, 2),
                      ('readq', 0x17820230, 8), ('writeb', 0x17820200, 1),
                      ('writew', 0x17820210, 2), ('writeq', 0x17820230, 8),
                      ('readw', 0x17a0000c, 2)):
        results.append(case(binary, f'reject width {operation}', extra_properties=EMPTY, operation=operation))
    for address in (0x17820204, 0x17820214, 0x17820234, 0x17820000, 0x17a00008):
        results.append(case(binary, f'unimplemented neighbor {address:#x}',
                            extra_properties=EMPTY, operation=('readl', address, 4)))
    for operation in (('readl', 0x17a0000c, 4), ('writel', 0x17a0000c, 4)):
        results.append(case(binary, f'card input requires explicit option {operation[0]}', operation=operation))
    results.append(case(binary, 'bootstrap off remains strict', bootstrap=False,
                        operation=('writel', 0x17820200, 4)))
    results.append(case(binary, 'original Z9 remains strict', machine='nikon-z9-lab',
                        operation=('readl', 0x17820200, 4)))
    for name, machine, bootstrap, expected in (
            ('empty option requires bootstrap', 'nikon-z8-bringup', False, 'requires bootstrap-fixtures'),
            ('empty option rejects original Z9', 'nikon-z9-lab', True, 'requires Z8 at machine creation')):
        item = case(binary, name, machine=machine, bootstrap=bootstrap, extra_properties=EMPTY,
                    steps=[])
        item['expected_startup_rejection'] = expected
        item['passed'] = (expected in item.get('stderr', '') and item['returncode'] != 0
                          and item['owned_qemu_stopped'])
        results.append(item)
    return dict(classification='synthetic_qtest_storage_configuration', firmware_used=False,
                cpu_execution=False, menu_verified=False, physical_hardware_verified=False,
                qemu_sha256=sha256(binary), source_sha256=sha256(Path(__file__).resolve()),
                cases=results, passed=all(item['passed'] for item in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.qemu)
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
