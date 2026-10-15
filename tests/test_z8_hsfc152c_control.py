# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed Z8 four-buffer receiver start; no firmware and no pixel producer."""
import argparse
import json
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_c0_stop import Lab as PreviewLab
from test_z8_d980_internal import require

OPTION = 'z8-hsfc152c-control-model'
STATE = 'z8-hsfc152c-control-state'
P, D = 0x152c0000, 0x15ac0000
CAPACITY = 0x414800
ENCODED = [0xc84000000 + i * 0x500000 for i in range(4)]
PHYSICAL = [address - 0x400000000 for address in ENCODED]
# Original RAW36 parameters, not executable firmware or a recovered codec.
FIELDS = {0x14: 1, 0x18: 0x79f, 0x100: 1, 0x104: 1, 0x108: 0,
    0x10c: 1, 0x110: 0, 0x114: 3, 0x118: 0,
    0x504: 0xff, 0x510: 2, 0x518: 0xff, 0x524: 2,
    0x528: 0, 0x52c: 0, 0x530: 0, 0x534: 0, 0x538: 0, 0x53c: 0,
    0x540: 0, 0x544: 0, 0x548: 4140, 0x54c: 2756, 0x550: CAPACITY}
PARAMS = {0x14: 4140, 0x1c: 2756, 0x2c: 0x83, 0x38: 1, 0x3c: 1,
    0x44: CAPACITY, 0x48: 14, 0x4c: 1, 0xa0: 0x3f01, 0xa4: 0xc739,
    0xa8: 0x148f4, 0xac: 0xc6b6, 0xb0: 0x149cd, 0xb4: 0x3f7f}
FIFO = [136364800, 69222660, 264, 68190220, 67108880, 20, 24,
        1486144064, 206916164, 879776072, 1078037324, 1356988496, 829674068, 6232]


class Lab(PreviewLab):
    def __init__(self, binary, folder, *, enabled=True, machine='nikon-z8-bringup', omit=()):
        super().__init__(binary, folder, enabled=False, machine=machine)
        additions = ['z8-d980-internal-start-model', 'engine152c-config-model', 'engine152c-request-model']
        if enabled:
            additions.append(OPTION)
        self.command[self.command.index('-M') + 1] += ',' + ','.join(
            value + '=on' for value in additions if value not in omit)

    def setup(self, *, source=True, targets=ENCODED, fifo=FIFO):
        if source:
            self.configure()
            self.enable()
        for off, value in ((0x404, 1), (0x14, 0), (4, 1), (0, 1), (0, 0),
                           (4, 0), (0x14, 1), (0x404, 0)):
            self.write(P + off, value)
        for off, value in FIELDS.items():
            if off != 0x14:
                self.write(P + off, value)
        for i, address in enumerate(targets):
            self.write(P + 0x120 + 8*i, address & 0xffffffff)
            self.write(P + 0x124 + 8*i, address >> 32)
        for off, value in PARAMS.items():
            self.write(D + off, value)
        for word in fifo:
            self.write(D + 0x50, word)
        for address in PHYSICAL:
            self.tx(f'write {address:#x} 0x20 0x' + '5a' * 32)
            self.tx(f'write {address + CAPACITY - 32:#x} 0x20 0x' + 'a5' * 32)

    def unchanged_outputs(self):
        for address in PHYSICAL:
            require(self.read_ram(address, 32) == b'\x5a' * 32, 'Output start canary changed')
            require(self.read_ram(address + CAPACITY - 32, 32) == b'\xa5' * 32, 'Output end canary changed')
        require(self.tx(f'readl {P + 0x1c:#x}') == 0, 'Control admission fabricated hardware status')
        require(not self.irqs.get(0x10b - 32, False), 'Control admission fabricated hardware IRQ')
        self.no_pixels()


def case(binary, name, body, *, reject=None, startup_error=None, **kwargs):
    row = dict(name=name, passed=False, firmware_used=False, cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-hsfc152c-') as folder:
        g = Lab(binary, Path(folder), **kwargs)
        try:
            try:
                g.start()
            except (RuntimeError, OSError):
                if not startup_error or startup_error not in g.text():
                    raise
            else:
                require(startup_error is None, 'Expected startup rejection')
                row['observed'] = body(g)
                require(('Z9_UNKNOWN' in g.text()) == bool(reject), 'Unexpected strict MMIO boundary')
                if reject:
                    require(f'addr=0x{reject:x}' in g.text(), 'Wrong strict boundary')
            row['passed'] = True
        except Exception as error:
            row['error'] = repr(error)
        finally:
            g.close()
            row.update(stderr=g.text(), owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row


def accepted(g):
    g.setup()
    g.write(P + 8, 1)
    request = g.state('engine152c-request-state')
    require(request['pending'] and request['requests'] == 1, 'Original four-target request not admitted')
    g.periodic()
    g.advance()
    g.unchanged_outputs()
    return request


def run(binary, red=False):
    if red:
        return [case(binary, 'red native four-target control start rejected', accepted, enabled=False)]
    cases = [case(binary, 'native four-target request remains data-incomplete', accepted)]

    def cancel(g):
        accepted(g)
        first = g.state(STATE)
        require(first['encoded_targets'] == ENCODED and first['physical_targets'] == PHYSICAL,
                'Original address conversion or slot ordering differs')
        g.write(P + 8, 0)
        last = g.state(STATE)
        require(not last['pending'] and last['cancellations'] == 1, 'Cancellation not recorded')
        g.unchanged_outputs()
        return last
    cases.append(case(binary, 'cancel retains separate no-image accounting', cancel))

    def reset_and_restart(g):
        accepted(g)
        g.write(P + 8, 0)
        g.write(P + 8, 0)
        require(g.state(STATE)['cancellations'] == 1, 'Repeated stop counted twice')
        g.setup(source=False)
        require(g.state('engine152c-config-state')['fifo_words'] == len(FIFO), 'Native reset did not clear old parameter FIFO')
        g.write(P + 8, 1)
        state = g.state(STATE)
        require(state['pending'] and state['starts'] == 2 and state['cancellations'] == 1,
                'Fresh request after reset was not independently admitted')
        g.unchanged_outputs()
        return state
    cases.append(case(binary, 'cancel/reset permits independent new request', reset_and_restart))

    def shadow(g):
        accepted(g)
        before = g.state(STATE)
        g.write(P + 0x120, 0xdeadbe00)
        require(g.state(STATE) == before, 'Shadow write changed admitted target ownership')
        g.unchanged_outputs()
        return before
    cases.append(case(binary, 'active request retains its latched targets', shadow))

    def duplicate(g):
        accepted(g)
        before = g.state(STATE)
        g.write(P + 8, 1)
        require(g.state(STATE) == before, 'Duplicate start mutated ownership')
    cases.append(case(binary, 'duplicate start rejected without mutation', duplicate, reject=P + 8))

    variants = [('wrong mode', P + 0x52c, 1), ('wrong width', P + 0x548, 4142),
                ('wrong height', P + 0x54c, 2758), ('wrong capacity', P + 0x550, CAPACITY + 512),
                ('wrong precision', D + 0x48, 12), ('wrong slot count', P + 0x114, 2),
                ('nonzero explicit input', P + 0x220, 0xe0000000),
                ('nonzero other bank', P + 0x140, 0xe0000000),
                ('wrong receiver control', P + 0x10c, 0)]
    for name, address, value in variants:
        def bad(g, address=address, value=value):
            g.setup()
            g.write(address, value)
            g.write(P + 8, 1)
            require(not g.state('engine152c-request-state')['pending'], 'Bad request admitted')
            g.unchanged_outputs()
        cases.append(case(binary, name, bad, reject=P + 8))
    for name, targets in (
        ('overlapping outputs', [ENCODED[0]] * 4),
        ('unaligned output', [ENCODED[0] + 1, *ENCODED[1:]]),
        ('non-RAM output', [0xc00000000, *ENCODED[1:]]),
        ('output crosses actual image-bank gap', [0xcadf8d800, *ENCODED[1:]]),
        ('wrong address encoding', [0xe84000000, *ENCODED[1:]])):
        def bad_address(g, targets=targets):
            g.setup(targets=targets)
            g.write(P + 8, 1)
            require(not g.state('engine152c-request-state')['pending'], 'Bad target admitted')
            g.unchanged_outputs()
        cases.append(case(binary, name, bad_address, reject=P + 8))

    def missing_source(g):
        g.setup(source=False)
        g.write(P + 8, 1)
        require(not g.state('engine152c-request-state')['pending'], 'Inactive source admitted')
        g.unchanged_outputs()
    cases.append(case(binary, 'missing native internal source rejected', missing_source, reject=P + 8))

    def bad_fifo(g):
        g.setup(fifo=FIFO[:-1])
        g.write(P + 8, 1)
        require(not g.state('engine152c-request-state')['pending'], 'Incomplete parameter FIFO admitted')
        g.unchanged_outputs()
    cases.append(case(binary, 'incomplete parameter FIFO rejected', bad_fifo, reject=P + 8))

    def changed_fifo(g):
        g.setup(fifo=[FIFO[0] ^ 1, *FIFO[1:]])
        g.write(P + 8, 1)
        require(not g.state('engine152c-request-state')['pending'], 'Changed parameter packet admitted')
        g.unchanged_outputs()
    cases.append(case(binary, 'changed parameter FIFO rejected', changed_fifo, reject=P + 8))

    for verb in ('writeb', 'writew'):
        def wrong_width(g, verb=verb):
            g.setup()
            g.write(P + 8, 1, verb)
            require(not g.state('engine152c-request-state')['pending'], 'Wrong-width start acquired request')
            g.unchanged_outputs()
        cases.append(case(binary, 'reject ' + verb + ' start', wrong_width, reject=P + 8))

    def off(g):
        g.setup()
        g.write(P + 8, 1)
        require(not g.state('engine152c-request-state')['pending'], 'Default-off profile broadened')
        g.unchanged_outputs()
    cases.append(case(binary, 'default off preserves old strict profile', off, enabled=False, reject=P + 8))

    def legacy_request(g):
        g.setup()
        for off, value in ((0x52c, 1), (0x548, 64), (0x54c, 16), (0x544, 128),
                           (0x550, 4096), (0x120, PHYSICAL[0] & 0xffffffff),
                           (0x124, PHYSICAL[0] >> 32), (0x220, 0xe0000000)):
            g.write(P + off, value)
        for off, value in ((0x14, 64), (0x1c, 16), (0x44, 4096)):
            g.write(D + off, value)
        g.write(P + 8, 1)
        old = g.state('engine152c-request-state')
        require(old['pending'] and old['input'] == 0xe0000000 and old['width'] == 64,
                'Previously admitted explicit-input request changed')
        require(not g.state(STATE)['pending'] and g.state(STATE)['starts'] == 0,
                'Legacy request falsely acquired HSFC ownership')
        g.unchanged_outputs()
        return old
    for enabled in (False, True):
        cases.append(case(binary, 'legacy request retained with new option ' + str(enabled),
                          legacy_request, enabled=enabled))

    def immutable(g):
        try:
            g.qmp.command('qom-set', path='/machine', property=OPTION, value=False)
        except RuntimeError as error:
            require('machine creation' in str(error), 'Wrong live-change rejection')
        else:
            raise AssertionError('Live model mutation admitted')
    cases.append(case(binary, 'creation-only option', immutable))
    cases.append(case(binary, 'wrong machine rejected', lambda g: None,
                      machine='nikon-z9-lab', startup_error='requires Z8'))
    for dependency in ('engine152c-config-model', 'engine152c-request-model', 'z8-d980-internal-start-model'):
        cases.append(case(binary, 'requires ' + dependency, lambda g: None,
                          omit=(dependency,), startup_error='requires'))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--red-baseline', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = run(args.qemu, args.red_baseline)
    passed = all(row['passed'] for row in cases)
    result = dict(passed=passed, cases=cases, qemu_sha256=sha256(args.qemu),
                  source_sha256=sha256(Path(__file__)), raw_precapture_verified=False)
    (args.output / 'qtest-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(passed=passed, cases=len(cases), failures=[
        {k: row[k] for k in ('name', 'error') if k in row} for row in cases if not row['passed']])))
    raise SystemExit(not passed)


if __name__ == '__main__':
    main()
