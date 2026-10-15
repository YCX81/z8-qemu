# SPDX-License-Identifier: GPL-2.0-or-later
"""Functional primary bit31 after six real writes, never an OS flag shortcut."""
import argparse
import json
import tempfile
from pathlib import Path

from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab, require
from test_z8_video_source_completion import setup

OPTION = 'raw-pipeline-input-pair-model'
STATE = 'raw-pipeline-input-pair-state'
CAUSE = 0x80000000


def run(binary, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as temp:
        root = Path(temp)
        kernel = root / 'zero.bin'
        kernel.write_bytes(bytes(4096))
        raw = root / 'pixels.raw'
        raw.write_bytes(generate(64, 32))
        props = tuple(x + '=on' for x in (
            'image-bank-model', 'engine150c-control-model', 'engine150c-stop-model',
            'config150d-model', 'engine150d-control-model', 'statistics-model',
            'engine1543-control-model', 'engine151-control-model', 'raw-pipeline-model',
            'primary-empty-end-model')) + ('statistics-raw=' + str(raw),)
        for name in ('enabled', 'disabled', 'closed-source', 'bad-last-plane', 'overlap', 'reset'):
            enabled = name != 'disabled'
            with Lab(binary, root/name, kernel, accelerator='qtest',
                     properties=props + ((OPTION+'=on',) if enabled else ())) as m:
                m.observe_irq()

                def w(addr, value):
                    m.q(f'writel {addr:#x} {value:#x}')

                def advance():
                    m.qmp.command('cont')
                    m.q('clock_step 100000000')
                    m.qmp.command('stop')

                def state():
                    return json.loads(m.qmp.command('qom-get', path='/machine', property=STATE))

                def pending():
                    return int(m.q('readl 0x150c0214'), 0) & CAUSE

                def planes(row=0):
                    return [bytes.fromhex(m.q(f'read {0x840000000+row*0x10000+ch*0x2000+uv*0x1000:#x} {0x400 if uv else 0x800:#x}')[2:])
                            for ch in range(3) for uv in range(2)]

                advance()
                require(pending() == 0, 'No output setup, no completion')

                def before_start(lab):
                    if name == 'closed-source':
                        w(0x151c0030, 0)
                    if name == 'bad-last-plane':
                        w(0x15423230, 0)
                    if name == 'overlap':
                        # Channel 1 UV aliases channel 0 Y, but not its own Y.
                        lab.write(0x228, 0x40000000)
                        lab.write(0x22c, 8)

                setup(m, before_start=before_start)
                before = planes()
                advance()
                after = planes()
                blocked = name in ('closed-source', 'bad-last-plane', 'overlap')
                require(pending() == (CAUSE if enabled and not blocked else 0), name+' pending')
                if blocked:
                    require(before == after and state()['completions'] == 0,
                            'Rejected frame must not write earlier planes or notify')
                else:
                    require(all(a != b and any(b) for a, b in zip(before, after)),
                            'All six actual planes must change before completion')
                if name == 'enabled':
                    require(state()['completions'] == 1, 'Single owned generation')
                    require(int(m.q('readl 0x150c0210'), 0) == 0, 'Masked raw status retained')
                    w(0x150c020c, CAUSE)
                    require(int(m.q('readl 0x150c0210'), 0) == CAUSE, 'Live unmask exposes cause')
                    require(m.irqs.get(0x106-32), 'Unmask raises the original primary IRQ')
                    m.write(20, 1)
                    row_before = planes(1)
                    advance()
                    require(planes(1) == row_before and state()['completions'] == 1,
                            'Downstream ACK alone cannot reuse primary-owned frame')
                    w(0x150c0210, 0x20)
                    require(pending() == CAUSE and state()['acks'] == 0, 'Unrelated W1C preserves owner')
                    w(0x150c0210, CAUSE)
                    require(pending() == 0 and state()['acks'] == 1, 'Native W1C retires once')
                    require(not m.irqs.get(0x106-32), 'W1C lowers primary IRQ independently')
                    w(0x150c0210, CAUSE)
                    require(state()['acks'] == 1, 'Repeated ACK is not a new retirement')
                    advance()
                    require(state()['completions'] == 2 and planes(1) != row_before,
                            'ACK permits the next actual frame')
                    try:
                        m.qmp.command('qom-set', path='/machine', property=OPTION, value=False)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('Option changed after realization')
                if name == 'reset':
                    # Existing bounded reset sequence with only primary unit 2 active.
                    w(0x150c0060, 2)
                    w(0x150c0064, 2)
                    w(0x150c0008, 2)
                    w(0x151c0030, 0)
                    w(0x150c0014, 0xc7ff7f)
                    w(0x150c0018, 0x4f04f)
                    w(0x150c0000, 0x17ff7f)
                    require(state()['cancelled'] == 1 and state()['acks'] == 0 and pending() == 0,
                            'Reset cancels ownership without pretending the ISR acknowledged')
                    advance()
                    require(state()['completions'] == 1, 'No output while source closed/reset')
                require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Unexpected MMIO: '+name)
                cases.append(dict(name=name, passed=True, state=state()))
    result = dict(passed=True, cases=cases, qemu_sha256=sha256(binary),
                  firmware_execution=False, recording_verified=False, physical_timing_verified=False)
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    run(args.qemu, args.output)
