# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared 1564/1566 MMIO, reset isolation and real packet ownership QTests."""
import argparse
import json
import tempfile
from pathlib import Path
from check_z8_machine import sha256
from make_cmos import generate
from test_z8_video_ring_stop import Lab, require
from test_z8_video_data import configure
from test_z8_video_stream import video_setup
from test_z8_yuv_stream import recording_setup

LEGACY = ('yuvcmp-config-model=on', 'yuvcmp-channels-model=on')
VIDEO = ('z8-video-interface-model=on',)


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as temp:
        root = Path(temp)
        kernel = root / 'zero.bin'; kernel.write_bytes(bytes(4096))
        raw = root / 'input.raw'; raw.write_bytes(generate(64, 32))
        for name, props in (('legacy', LEGACY), ('video', VIDEO), ('both', LEGACY + VIDEO)):
            with Lab(binary, root/name, kernel, accelerator='qtest', properties=props) as m:
                def w(a, v, width='l'): m.q(f'write{width} {a:#x} {v:#x}')
                def r(a, width='l'): return int(m.q(f'read{width} {a:#x}'), 0)
                def state(prop): return json.loads(m.qmp.command('qom-get', path='/machine', property=prop))
                w(0x15649008, 64); w(0x15661008, 72)
                w(0x15649280, 0x203456780, 'q')
                if name != 'video':
                    require(r(0x15649280) == 0x03456780 and r(0x15649284) == 2, 'Pair/word alias')
                    w(0x15649280, 0x76543210)
                    require(r(0x15649280, 'q') == 0x276543210, 'Word write visible through pair')
                legacy_words = {v['address']: v['value'] for v in state('yuvcmp-config-state')['registers']}
                require(legacy_words[0x15649008] == 64, 'M view reads actual shared shadow')
                if name != 'legacy':
                    o = state('z8-yuv-state')
                    require(o['registers']['0x15649008'] == legacy_words[0x15649008], 'M/O register identity')
                    require(o['input_addresses'][0][0] == r(0x15649280, 'q'), 'O address view shares pair bytes')
                w(0x15648010, 1)
                require(r(0x15661008) == 72, 'Writer reset preserves reader domain')
                require(r(0x15649008) == (64 if name == 'legacy' else 0), 'Declared reset compatibility')
                w(0x15649008, 81); w(0x15660010, 1)
                require(r(0x15649008) == 81, 'Reader reset preserves writer domain')
                require(r(0x15661008) == (72 if name == 'legacy' else 0), 'Reader reset scope')
                require(state('yuvcmp-config-state')['empty_resets'] == 2, 'One reset counter per command')
                require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Supported transaction rejected')
                cases.append(dict(name=name, passed=True))
        bad_accesses = (
            ('writer-result', 'readl 0x15648050'), ('reader-result', 'readl 0x15660064'),
            ('byte-write', 'writeb 0x15649008 1'), ('address-overflow', 'writeq 0x15649280 0x1000000000'),
            ('uv-reserved-bit', 'writel 0x15649618 0x20000000'),
            ('unaligned-pair', 'writeq 0x15649284 1'),
        )
        for name, command in bad_accesses:
            with Lab(binary, root/name, kernel, accelerator='qtest', properties=LEGACY + VIDEO) as m:
                m.q(command)
                require('Z9_UNKNOWN' in m.log_path.read_text(), name + ' did not reject')
                cases.append(dict(name=name, passed=True))
        for name, address in (('no-model-release', 0xd154244), ('no-model-assert', 0xd154344)):
            with Lab(binary, root/name, kernel, accelerator='qtest') as m:
                m.q(f'writel {address:#x} 0x4000')
                require('Z9_UNKNOWN' in m.log_path.read_text(), 'Z8 shared reset must be explicit')
                cases.append(dict(name=name, passed=True))
        # Use actual RAW packet -> decoded frame -> fourth-output RAM. No
        # host writes to device ownership structures or completion registers.
        names = ('image-bank-model','engine150c-control-model','engine150c-stop-model',
                 'config150d-model','engine150d-control-model','statistics-model',
                 'engine1543-control-model','engine151-control-model','raw-pipeline-model',
                 'event1508-front-model','event1508-timing-model','event1508-channel-model',
                 'event1508-aux-model','event1508-global1-model','z8-video-data-model','z8-yuv-data-model')
        props = LEGACY + VIDEO + tuple(n+'=on' for n in names) + (
            'statistics-raw='+str(raw),'event1508-timing-period-ns=1000000')
        for reset in ('local', 'syscon', 'queue-while-owned'):
            with Lab(binary, root/reset, kernel, accelerator='qtest', properties=props) as m:
                def w(a, v): m.q(f'writel {a:#x} {v:#x}')
                def st(prop='z8-yuv-state'): return json.loads(m.qmp.command('qom-get',path='/machine',property=prop))
                def advance(): m.qmp.command('cont'); m.q('clock_step 70000000'); m.qmp.command('stop')
                configure(m); w(0x15080010,1); w(0x15080028,1); advance()
                w(0x15080048,0x2000); w(0x152c001c,3); w(0x15300404,0); advance(); w(0x1530001c,3)
                video_setup(m); recording_setup(m); advance()
                before = st(); require(before['owned'] == before['frames'] == 1, 'Actual fourth packet owner')
                require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Both models rejected actual producer')
                packet = m.q('read 0x840400000 0x1000')
                upstream = st('z8-video-data-state')['primary_pending']
                if reset == 'queue-while-owned':
                    m.q('readl 0x15649020')
                    require('Z9_UNKNOWN' in m.log_path.read_text(), 'Empty model masked a real owner')
                    require(st()['owned'] == 1 and st()['cancels'] == 0, 'Unknown status cannot retire ownership')
                else:
                    w(0x15660010,1)
                    require(st()['owned'] == 1 and st()['cancels'] == 0, 'Consumer reset consumed producer')
                    if reset == 'syscon': w(0xd154244,0x4000); w(0xd154344,0x4000)
                    else: w(0x15648010,1); w(0x15648010,1)
                    after = st()
                    require(after['owned'] == 0 and after['cancels'] == 1 and after['reads'] == 0,
                            'Producer reset cancels once without successful encoder read')
                    require('Z9_UNKNOWN' not in m.log_path.read_text(), 'Valid reset rejected')
                require(m.q('read 0x840400000 0x1000') == packet, 'Reset changed RAM')
                require(st('z8-video-data-state')['primary_pending'] == upstream, 'Reset acknowledged upstream cause')
                (output/(reset+'.log')).write_text(m.log_path.read_text())
                cases.append(dict(name=reset, passed=True))
    result = dict(passed=True, cases=cases, qemu_sha256=sha256(binary), firmware_execution=False, physical_verified=False)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--qemu',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); run(a.qemu,a.output)
