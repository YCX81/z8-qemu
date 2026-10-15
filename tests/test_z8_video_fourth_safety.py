# SPDX-License-Identifier: GPL-2.0-or-later
"""Eight-output preflight from the C1 arbitration, using WLYV RAM only."""
import argparse
import json
import tempfile
from pathlib import Path

from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_data import CAPACITY, OPTION, PACKET, configure
from test_z8_video_ring_stop import Lab, require
from test_z8_video_stream import video_setup
from test_z8_yuv_stream import recording_setup


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    mutations = {
        'duplicate-y': [(0x15649288, 0x200000000, 8)],
        'duplicate-uv': [(0x15649688, 0x218000000, 8)],
        'unbound-y': [(0x15649280, 0x23f000000, 8)],
        'masked-y': [(0x15649240, 14, 4)],
        'unarmed-y': [(0x1564900c, 0, 4)],
        'inactive-b3': [(0x15422310, 0, 4)],
        'overlap-planes': [(0x156496a0, 0x840400000, 8)],
        'overlap-preview': [(0x156492a0, 0x840000000, 8)],
        'overlap-raw-fourth': [(0x156492a0, PACKET, 8)],
        'overlap-raw-preview': [(0x15431210, PACKET, 8)],
        'short-uv-capacity': [(0x15649628, 64, 4)],
        'distinct-planes': [],
        'inactive-duplicate': [(0x15649288, 0x200000000, 8), (0x15649240, 13, 4)],
    }
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        root = Path(tmp)
        kernel = root / 'zero.bin'
        kernel.write_bytes(bytes(4096))
        raw = root / 'pixels.raw'
        raw.write_bytes(generate(64, 32))
        props = tuple(n + '=on' for n in (
            'image-bank-model', 'engine150c-control-model', 'engine150c-stop-model',
            'config150d-model', 'engine150d-control-model', 'statistics-model',
            'engine1543-control-model', 'engine151-control-model', 'raw-pipeline-model',
            'z8-video-interface-model', 'event1508-front-model', 'event1508-timing-model',
            'event1508-channel-model', 'event1508-aux-model', 'event1508-global1-model',
            OPTION, 'z8-yuv-data-model'))
        props += ('statistics-raw=' + str(raw), 'event1508-timing-period-ns=1000000')
        for name, writes in mutations.items():
            with Lab(binary, root / name, kernel, accelerator='qtest', properties=props) as m:
                def w(a, v): m.q(f'writel {a:#x} {v:#x}')
                def state(prop): return json.loads(m.qmp.command('qom-get', path='/machine', property=prop))
                def advance():
                    m.qmp.command('cont'); m.q('clock_step 70000000'); m.qmp.command('stop')
                configure(m); w(0x15080010, 1); w(0x15080028, 1); advance()
                w(0x15080048, 0x2000); w(0x152c001c, 3); w(0x15300404, 0); advance()
                w(0x1530001c, 3)
                require(state('z8-video-data-state')['frame_ready'], 'Decoded RAW is required')
                video_setup(m); recording_setup(m)
                spans = [(0x840000000 + c * 8192 + p * 4096, 2048 >> p)
                         for c in range(3) for p in range(2)]
                spans += [(0x840400000, 4096), (0x840402000, 4096)]
                for addr, size in spans: m.q(f'memset {addr:#x} {size:#x} 0x55')
                spans += [(PACKET, CAPACITY)]
                for addr, value, size in writes:
                    if size == 8 and addr >= 0x15640000:
                        m.q(f'writeq {addr:#x} {value:#x}')
                    else:
                        w(addr, value & 0xffffffff)
                        if size == 8: w(addr + 4, value >> 32)
                before = [m.q(f'read {a:#x} {n:#x}') for a, n in spans]
                advance()
                video, yuv = state('z8-video-data-state'), state('z8-yuv-state')
                (output / (name + '.log')).write_text(m.log_path.read_text())
                require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Fixture must use supported MMIO')
                accepted = name in ('distinct-planes', 'inactive-duplicate')
                passed = (video['output_frames'] == 1 and yuv['frames'] == 1) if accepted else (
                    video['output_frames'] == 0 and yuv['frames'] == 0 and yuv['owned'] == 0
                    and video['primary_pending'] == 0 and not (m.pending(0) & 1)
                    and not (m.pending(1) & 1)
                    and before == [m.q(f'read {a:#x} {n:#x}') for a, n in spans])
                cases.append(dict(name=name, passed=passed, expected_output=accepted,
                                  video=video, yuv=yuv))
                (output / (name + '.log')).write_text(m.log_path.read_text())
                print(name, 'PASS' if passed else 'FAIL', flush=True)
    result = dict(passed=all(c['passed'] for c in cases), cases=cases,
                  qemu_sha256=sha256(binary), firmware_execution=False, camera_recording=False)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    require(result['passed'], 'Fourth-output preflight failed; see case receipts')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    run(args.qemu, args.output)
