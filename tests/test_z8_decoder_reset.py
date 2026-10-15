# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual QTest for the original bounded decoder reset-control sequence."""
import argparse
import json
from pathlib import Path
from check_z8_machine import sha256
from test_z8_syscon import case

OPTION = ('z8-decoder-reset-control-model=on',)
SELECTOR, COMMAND = 0x0d154150, 0x0d154030


def sequence():
    return [('readl 0xd154030', 0), ('readl 0xd154150', 0),
        ('writel 0xd154150 7', None), ('readl 0xd154150', 7),
        ('writel 0xd154030 1', None), ('readl 0xd154030', 0),
        ('writel 0xd154150 3', None), ('writel 0xd154030 1', None),
        ('readl 0xd154030', 0), ('readl 0xd154150', 3),
        ('writel 0xd154150 7', None), ('writel 0xd154030 1', None),
        ('readl 0xd154030', 0)]


def run(binary, red=False):
    if red:
        return [case(binary, 'red native decoder reset sequence is unsupported', steps=sequence())]
    cases = [case(binary, 'native7/apply/3/apply sequence', steps=sequence(), extra_properties=OPTION)]
    for address in (SELECTOR, COMMAND):
        for verb, width in (('readb', 1), ('readw', 2), ('writeb', 1), ('writew', 2)):
            cases.append(case(binary, f'reject {verb} at {address:x}',
                operation=(verb, address, width), extra_properties=OPTION))
        cases.append(case(binary, f'default off {address:x}', operation=('writel', address, 4)))
    for name, steps, address in (
        ('command without selection', [('writel 0xd154030 1', None), ('readl 0xd154030', 0)], COMMAND),
        ('unsupported selection', [('writel 0xd154150 4', None), ('readl 0xd154150', 0)], SELECTOR),
        ('unsupported command', [('writel 0xd154150 7', None), ('writel 0xd154030 2', None),
                                  ('readl 0xd154030', 0)], COMMAND),
        ('phase3 before7', [('writel 0xd154150 3', None), ('readl 0xd154150', 0)], SELECTOR),
        ('duplicate apply', [('writel 0xd154150 7', None), ('writel 0xd154030 1', None),
                             ('writel 0xd154030 1', None), ('readl 0xd154030', 0)], COMMAND)):
        cases.append(case(binary, name, steps=steps, operation=('writel', address, 4), extra_properties=OPTION))
    for address in (SELECTOR-4, SELECTOR+4, COMMAND-4, COMMAND+4):
        cases.append(case(binary, f'neighbor remains strict {address:x}',
            operation=('writel', address, 4), extra_properties=OPTION))
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--red-baseline', action='store_true')
    a=p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    cases=run(a.qemu, a.red_baseline)
    result=dict(passed=all(x['passed'] for x in cases), cases=cases,
        qemu_sha256=sha256(a.qemu), source_sha256=sha256(Path(__file__)))
    (a.output/'qtest-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'], cases=len(cases), failures=[
        {k:x[k] for k in ('name','error') if k in x} for x in cases if not x['passed']])))
    raise SystemExit(not result['passed'])


if __name__ == '__main__': main()
