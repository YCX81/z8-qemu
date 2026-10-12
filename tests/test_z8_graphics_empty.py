# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest for empty inline paths, with private shader identities only.

CPUs remain stopped. Two private 96-byte runtime captures are fingerprint
checked and their source run is bound to the user-supplied original MAIN.
No firmware bytes are kept in this source. This is GPU model acceptance,
not camera/UI acceptance or proof that these bytes are literal MAIN spans.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct

from test_z8_persistent_media import Machine

MAIN_SHA = '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
FINGERPRINTS = (
    'c7a2d85834969d4ed236b979c78d07f4478814ea15614a1f965bd71962b9d394',
    'f7b11e4c7f46b46b927a8ee15476061eb325c12b69ee03c31a2e28da5ee1b9d8',
)
RING, PROGRAM, TARGET, PATH, SEGMENTS, COORDS = (0xa00010000, 0xa00020000,
    0xa00040000, 0xa00050000, 0xa00060000, 0xa00061000)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def local(address):
    return 0x40000000 + address - 0xa00000000


class Graphics(Machine):
    def __init__(self, binary, folder, kernel):
        super().__init__(binary, folder, kernel,
            properties=('bus-window-fixture=on', 'graphics-image-model=on',
                        'graphics-path-model=on'))
        self.slot = 0
        self.q('writeq 0x140d0050 0x200000000')
        self.q(f'writel 0x15540070 {local(RING):#x}')
        self.q('writel 0x15540074 0x1f')

    def write(self, address, data):
        self.q(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def read(self, address, size):
        return bytes.fromhex(self.q(f'read {address:#x} {size:#x}')[2:])

    def word(self, value):
        self.q(f'writel 0x15540000 {value:#x}')

    def packet(self, op, reg, values):
        self.word((op << 24) | (reg << 12) | len(values))
        for value in values:
            self.word(value)

    def selector(self, selector):
        self.word(0xa0000000 | (selector << 7))

    def control(self, selector, values):
        self.selector(selector)
        self.packet(0x80, 0, values)

    def submit(self, command, address):
        self.write(RING + self.slot * 8, struct.pack('<II', command, (local(address) >> 2) | 0x40000000))
        self.q('writel 0x15540078 1')
        self.slot = (self.slot + 1) & 31

    def graphics(self):
        return json.loads(self.qmp.command('qom-get', path='/machine', property='graphics-state'))

    def bind(self, program):
        self.write(PROGRAM, program)
        self.submit(0xe0000000, PROGRAM)
        require(not self.graphics()['fault'], 'Program identity must bind successfully')

    def setup_graphics(self, *, glyph=True, paint=True):
        self.selector(0x123b); self.word(0xa8000000)
        identity = ((0x3f800000, 0, 0), (0, 0x3f800000, 0), (0, 0, 0x3f800000))
        for first in (0xac, 0xc4):
            for row, values in enumerate(identity):
                self.packet(0x43, first + row * 4, values)
        self.packet(0x43, 0xd0, identity[0])
        self.packet(0x43, 0xd4, identity[1])
        self.packet(0x43, 0x12c, [0x40800000])  # target height 4.0
        if glyph:
            self.control(0x826, [0, 0, 0, 0, 0x80000001])
        else:
            self.control(0x97f, [0x80000001])
        if paint:
            self.control(0x7df if glyph else 0x93b, [1])
        for reg, value in ((0x20, local(TARGET)), (0x22, local(TARGET)),
                           (0x21, 0x00040003), (0x23, 0x00040003),
                           (0x610, 8), (0x611, 3), (0x4b1, 0), (0x200, 0)):
            self.packet(0x48, reg, [value])
        self.packet(0x48, 0x780, [0, 0x10000, 0, 0x10000, 0, 0x10000, 0, 0])
        for reg, value in ((0xdf, 1), (0xe0, 0), (0xe1, 0x00030003),
                           (0xc0, 0x10000), (0xc1, 0xff0000ff)):
            self.packet(0x47, reg, [value])
        self.packet(0x46, 0x60, [0]); self.packet(0x46, 0x70, [1])
        require(not self.graphics()['fault'], 'Authored graphics state must be accepted')

    def rectangle(self):
        self.write(SEGMENTS, bytes((2, 4, 4, 4, 0)))
        self.write(COORDS, struct.pack('<8f', 0, 0, 4, 0, 4, 4, 0, 4))
        self.write(PATH, struct.pack('<4I', 0xb3000005, local(SEGMENTS), local(COORDS), 32))
        self.submit(0xc0000000, PATH)
        self.packet(0x46, 0x4a, [1])


def run(binary, firmware, output, fill_program, stroke_program, capture_result):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    source = Path(firmware).read_bytes()
    require(hashlib.sha256(source).hexdigest() == MAIN_SHA, 'Expected original Z8 C3.11 MAIN')
    origin = json.loads(Path(capture_result).read_text())
    require(origin.get('firmware_sha256') == MAIN_SHA, 'Runtime capture must use the pinned original MAIN')
    programs = []
    for path, digest in zip((fill_program, stroke_program), FINGERPRINTS):
        data = Path(path).read_bytes()
        require(len(data) == 96 and hashlib.sha256(data).hexdigest() == digest,
                'Supplied program capture does not match the reviewed private prefix')
        programs.append(data)
    result = dict(classification='real_qtest_empty_path_model_not_keyboard_or_camera_acceptance',
                  test_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  main_sha256=MAIN_SHA, program_capture_source=str(capture_result),
                  capture_result_sha256=hashlib.sha256(Path(capture_result).read_bytes()).hexdigest(),
                  program_capture_derived_main_sha256=origin.get('c10_image',{}).get('derived_sha256'),
                  program_sha256=list(FINGERPRINTS), cpu_execution=False,
                  qemu_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(), cases=[])
    kernel = output/'authored-zero-kernel.bin'; kernel.write_bytes(bytes(4096))
    scenarios = [('fill_empty', 0, True, None), ('stroke_glyph_empty', 1, True, None),
                 ('stroke_ordinary_empty', 1, False, None),
                 ('missing_end', 1, True, 'missing_end'),
                 ('nonempty_inline', 1, True, 'nonempty_inline'),
                 ('end_without_begin', 1, True, 'end_without_begin'),
                 ('unknown_selector', 1, True, 'unknown_selector'),
                 ('missing_program', 1, True, 'missing_program'),
                 ('missing_paint', 1, True, 'missing_paint')]
    for name, mode, glyph, invalid in scenarios:
        record = dict(name=name, passed=False)
        with Graphics(binary, output/name, kernel) as g:
            try:
                guard = bytes(range(16)); initial = guard + bytes((17, 29, 43, 61))*16 + guard
                g.write(TARGET - 16, initial)
                g.setup_graphics(glyph=glyph, paint=invalid != 'missing_paint')
                if invalid not in ('missing_program', 'missing_paint'):
                    # First prove real pixel work and leave nonzero stale edges.
                    g.bind(programs[0]); g.rectangle()
                    require(g.read(TARGET, 64) == bytes((255, 0, 0, 255))*16,
                            'Baseline rectangle must produce independently expected red pixels')
                    require(g.graphics()['path']['edges'] > 0, 'Baseline must leave nonzero old edges')
                if invalid != 'missing_program':
                    g.bind(programs[mode])
                before = g.graphics(); pixels_before = g.read(TARGET - 16, 96)
                if invalid == 'end_without_begin':
                    g.selector(0x5ec); g.word(0xa8000000)
                elif invalid == 'unknown_selector':
                    g.selector(0x5e6)
                else:
                    g.selector(0x5e5); g.word(0xa8000000)
                    if invalid in (None, 'missing_end', 'nonempty_inline'):
                        state = g.graphics()
                        require(not state['fault'], 'Empty begin must accept the bound fill/stroke program')
                        require(state['path']['inline_phase'] == 1 and state['path']['edges'] == 0,
                                'Begin must discard old geometry without completing a draw')
                        require(state['path']['jobs'] == before['path']['jobs'], 'Begin cannot complete')
                    if invalid == 'missing_end':
                        g.packet(0x46, 0x4a, [1])
                    elif invalid == 'nonempty_inline':
                        g.packet(0x80, 0, [1])
                    elif invalid not in ('missing_program', 'missing_paint'):
                        g.selector(0x5ec); g.word(0xa8000000)
                        state = g.graphics()
                        require(state['path']['pending'] and state['path']['jobs'] == before['path']['jobs'],
                                'Matching end must queue work, not complete it')
                        g.packet(0x46, 0x4a, [1])
                        g.packet(0x48, 0, [1])
                after = g.graphics(); pixels_after = g.read(TARGET - 16, 96)
                require(pixels_after == pixels_before, 'Empty or rejected work must not modify pixels or guards')
                if invalid:
                    require(after['fault'], 'Unsupported/incomplete transaction must be rejected')
                    require(after['path']['jobs'] == before['path']['jobs'], 'Rejected work cannot complete')
                else:
                    require(not after['fault'] and not after['path']['pending'], 'Valid empty path must retire')
                    require(after['path']['jobs'] == before['path']['jobs'] + 1, 'Exactly one empty job completes')
                    require(after['path']['pixels'] == before['path']['pixels'] and
                            after['path']['covered_samples'] == before['path']['covered_samples'],
                            'Empty work must contribute zero pixels and zero samples')
                    require(after['pending'] & 0x01000000, 'Only the explicit barrier may report completion')
                require(not g.qmp.command('query-status')['running'], 'QTest must not execute guest CPUs')
                (output/name/'target.ppm').write_bytes(b'P6\n4 4\n255\n' + b''.join(
                    pixels_after[16 + i*4:19 + i*4] for i in range(16)))
                record.update(passed=True, before=before['path'], after=after['path'],
                              target_sha256=hashlib.sha256(pixels_after).hexdigest())
            except Exception as error:
                record.update(error=repr(error), final_state=g.graphics())
        record['owned_qemu_stopped'] = g.proc.poll() is not None
        result['cases'].append(record)
    result['passed'] = all(r['passed'] for r in result['cases'])
    result['source_unchanged'] = hashlib.sha256(Path(firmware).read_bytes()).hexdigest() == MAIN_SHA
    (output/'qtest-result.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fill-program', type=Path, required=True)
    parser.add_argument('--stroke-program', type=Path, required=True)
    parser.add_argument('--capture-result', type=Path, required=True)
    args = parser.parse_args()
    if os.name == 'nt': parser.error('Use the selected Linux/WSL environment')
    result = run(args.qemu, args.firmware, args.output, args.fill_program, args.stroke_program, args.capture_result)
    print(json.dumps({'passed':result['passed'],'cases':[{k:v for k,v in row.items() if k in ('name','passed','error')}
                                                     for row in result['cases']]},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
