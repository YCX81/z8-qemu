# SPDX-License-Identifier: GPL-2.0-or-later
"""Real preview RAM writes, primary bit3 IRQ and W1C; no Nikon firmware."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from check_z8_machine import sha256
from test_z8_c0_stop import Lab as PreviewLab, preview, C0
from test_z8_d980_internal import require

OPTION = 'z8-primary-frame-boundary-model'
STATE = 'z8-primary-frame-boundary-state'
PRIMARY = 0x150c0000
IRQ = 0x106 - 32


class Lab(PreviewLab):
    def __init__(self, binary, folder, *, enabled=True, machine='nikon-z8-bringup', omit=(), no_raw=False):
        super().__init__(binary, folder, enabled=False, machine=machine, omit=omit)
        index = self.command.index('-M') + 1
        if 'primary-stopped-source-reset-model' not in omit:
            self.command[index] += ',primary-stopped-source-reset-model=on'
        if enabled:
            self.command[index] += ',' + OPTION + '=on'
        if no_raw:
            self.command[index] = ','.join(x for x in self.command[index].split(',')
                                             if not x.startswith('statistics-raw='))

    def primary_pending(self):
        return self.tx(f'readl {PRIMARY + 0x208:#x}')

    def primary_irq(self):
        self.primary_pending()
        return self.irqs.get(IRQ, False)

    def setup(self, *, active=True, timing=True, mask=8):
        preview(self)
        for row in range(4):
            for channel in range(3):
                y = 0x840000000 + row * 0x10000 + channel * 0x2000
                self.tx(f'write {y:#x} 0x800 0x' + '5a' * 0x800)
                self.tx(f'write {y + 0x1000:#x} 0x400 0x' + '5a' * 0x400)
        if active:
            for offset in (0x60, 0x64, 8):
                self.write(PRIMARY + offset, 2)
            require(self.state('engine150c-state')['active'] == 2, 'Actual source enable missing')
        self.write(PRIMARY + 0x200, mask)
        if timing:
            self.periodic()
        require(self.primary_pending() == 0, 'Configuration fabricated a frame cause')

    def check_planes(self, row=0, *, written=True):
        hashes = []
        for channel in range(3):
            y = 0x840000000 + row * 0x10000 + channel * 0x2000
            actual_y = self.read_ram(y, 0x800)
            actual_uv = self.read_ram(y + 0x1000, 0x400)
            # Authored zero Bayer input means black Y and neutral chroma.
            require(actual_y == bytes(0x800) if written else actual_y == b'\x5a' * 0x800,
                    'Complete Y plane differs')
            require(actual_uv == b'\x80' * 0x400 if written else actual_uv == b'\x5a' * 0x400,
                    'Complete UV plane differs')
            hashes.append(hashlib.sha256(actual_y + actual_uv).hexdigest())
        return hashes


def case(binary, name, body, *, reject=None, startup_error=None, **kwargs):
    result = dict(name=name, passed=False, firmware_used=False, cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-primary-frame-') as tmp:
        g = Lab(binary, Path(tmp), **kwargs)
        try:
            try:
                g.start()
            except (RuntimeError, OSError):
                if not startup_error or startup_error not in g.text():
                    raise
            else:
                require(startup_error is None, 'Expected creation rejection')
                result['observed'] = body(g)
                require(('Z9_UNKNOWN' in g.text()) == bool(reject), 'Unexpected strict MMIO boundary')
                if reject:
                    require(f'addr=0x{reject:x}' in g.text(), 'Wrong strict MMIO boundary')
            result['passed'] = True
        except Exception as error:
            result['error'] = repr(error)
        finally:
            g.close()
            result.update(stderr=g.text(), irq_events=g.irq_events,
                          owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return result


def pixels_then_bit3(g):
    g.setup()
    g.advance()
    hashes = g.check_planes()
    require(g.state('raw-pipeline-state')['frames'] == 1, 'Expected one actual preview transfer')
    require(g.primary_pending() == 8 and g.primary_irq(), 'Real preview lacks original primary bit3 IRQ')
    return dict(full_plane_sha256=hashes, pending=g.primary_pending())


def default_off(g):
    g.setup()
    g.advance()
    hashes = g.check_planes()
    require(g.primary_pending() == 0 and not g.primary_irq(), 'Default-off primary behavior changed')
    g.no_pixels()
    return dict(full_plane_sha256=hashes, preview_frames=g.state('raw-pipeline-state')['frames'])


def run(binary, red=False):
    if red:
        return [case(binary, 'baseline writes all six preview planes', default_off, enabled=False),
                case(binary, 'red real preview lacks primary bit3', pixels_then_bit3, enabled=False)]
    cases = [case(binary, 'actual six planes precede bit3 IRQ', pixels_then_bit3),
             case(binary, 'default off keeps real preview behavior', default_off, enabled=False)]

    def ack_and_reuse(g):
        pixels_then_bit3(g)
        g.write(C0 + 0x14, 1)
        g.advance()
        require(g.state('raw-pipeline-state')['frames'] == 1, 'Unacknowledged primary cause reused output')
        g.check_planes(1, written=False)
        g.write(PRIMARY + 0x204, 0x100000)
        require(g.primary_pending() == 8, 'Unrelated W1C consumed frame cause')
        g.write(PRIMARY + 0x204, 8)
        require(g.primary_pending() == 0 and not g.primary_irq(), 'W1C did not lower primary IRQ')
        g.advance()
        g.check_planes(1)
        state = g.state(STATE)
        require(state['completions'] == 2 and state['acks'] == 1 and state['pending'] == 8,
                'Fresh actual transfer did not get its own pending cause')
        g.no_pixels()
        return state
    cases.append(case(binary, 'W1C owns reuse and new generation', ack_and_reuse))

    def masked(g):
        g.setup(mask=0)
        g.advance()
        g.check_planes()
        require(g.primary_pending() == 8 and not g.primary_irq(), 'Masked pending lost or IRQ raised')
        require(g.tx(f'readl {PRIMARY + 0x204:#x}') == 0, 'Masked status view differs')
        g.write(PRIMARY + 0x200, 8)
        require(g.primary_irq(), 'Unmask did not deliver retained cause')
        g.write(PRIMARY + 0x204, 8)
        require(not g.primary_irq(), 'ACK did not retire pending cause')
        return g.state(STATE)
    cases.append(case(binary, 'masked cause retained until unmask and W1C', masked))

    def shared(g):
        g.setup()
        g.write(PRIMARY + 0x20c, 0x20000000)
        g.advance()
        require(g.primary_pending() == 8 and g.tx(f'readl {PRIMARY + 0x210:#x}') == 0x20000000,
                'Actual preview and source causes missing')
        g.write(PRIMARY + 0x204, 8)
        require(g.primary_irq(), 'Primary ACK consumed another group cause')
        g.write(PRIMARY + 0x210, 0x20000000)
        require(not g.primary_irq(), 'Shared cause ACK left IRQ high')
        return g.state(STATE)
    cases.append(case(binary, 'shared primary IRQ keeps source cause', shared))

    def no_frame(g, **kwargs):
        g.setup(**kwargs)
        g.advance()
        g.check_planes(written=False)
        require(g.primary_pending() == 0 and g.state(STATE)['completions'] == 0,
                'Missing input/activity manufactured completion')
        return g.state(STATE)
    cases.append(case(binary, 'no RAW input rejected before runtime', lambda g: None, no_raw=True,
                      startup_error='requires an NZ9RAW1 Bayer input file'))
    cases.append(case(binary, 'inactive primary creates no output or cause', lambda g: no_frame(g, active=False)))
    cases.append(case(binary, 'stopped input timing creates no output or cause', lambda g: no_frame(g, timing=False)))

    def invalid_geometry(g):
        g.setup()
        g.write(0x1542322c, 0)
        g.advance()
        g.check_planes(written=False)
        require(g.state('raw-pipeline-state')['frames'] == 0 and g.primary_pending() == 0,
                'Rejected third-plane geometry manufactured completion')
        return g.state(STATE)
    cases.append(case(binary, 'invalid final output rejects before any frame cause', invalid_geometry))

    def held(g):
        pixels_then_bit3(g)
        g.write(C0 + 4, 0x102)
        g.write(C0 + 0x14, 1)
        g.write(PRIMARY + 0x204, 8)
        g.advance()
        require(g.state('raw-pipeline-state')['frames'] == 1 and g.primary_pending() == 0,
                'Held pipeline generated a new frame cause')
        return g.state(STATE)
    cases.append(case(binary, 'held pipeline stops future boundaries', held))

    def reset(g):
        pixels_then_bit3(g)
        g.write(0x15080028, 0)
        g.write(C0 + 4, 0x100)
        g.write(C0 + 0x14, 1)
        g.reset()
        state = g.state(STATE)
        require(state['pending'] == 0 and state['acks'] == 0 and state['cancelled'] == 1,
                'Reset confused cancellation with acknowledgement')
        require(not g.primary_irq(), 'Reset retained primary IRQ')
        return state
    cases.append(case(binary, 'native reset cancels pending without ACK', reset))

    for verb in ('writeb', 'writew'):
        def bad_width(g, verb=verb):
            pixels_then_bit3(g)
            before = g.state(STATE)
            g.write(PRIMARY + 0x204, 8, verb)
            require(g.state(STATE) == before and g.primary_pending() == 8, 'Bad width consumed cause')
        cases.append(case(binary, 'reject ' + verb + ' W1C', bad_width, reject=PRIMARY + 0x204))

    def disabled_w1c(g):
        default_off(g)
        g.write(PRIMARY + 0x204, 8)
        require(g.primary_pending() == 0, 'Disabled option admitted a new cause')
    cases.append(case(binary, 'default off retains strict bit3 W1C boundary', disabled_w1c,
                      enabled=False, reject=PRIMARY + 0x204))

    def immutable(g):
        try:
            g.qmp.command('qom-set', path='/machine', property=OPTION, value=False)
        except RuntimeError as error:
            require('machine creation' in str(error), 'Wrong live-change error')
        else:
            raise AssertionError('Live model toggle accepted')
    cases.append(case(binary, 'option immutable after realization', immutable))
    cases.append(case(binary, 'reject Z9 option', lambda g: None, machine='nikon-z9-lab', startup_error='requires Z8'))
    for dependency in ('raw-pipeline-model', 'engine150c-stop-model', 'primary-stopped-source-reset-model'):
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
