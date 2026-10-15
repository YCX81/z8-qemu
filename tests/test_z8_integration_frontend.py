# SPDX-License-Identifier: GPL-2.0-or-later
"""Integration-only cross-mode QTest; actual RAM/IRQ assertions, no MAIN."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from make_cmos import generate
from test_z8_buffer_ownership import Lab as PhotoLab
from test_z8_producer_journal import full_chain
from test_z8_d980_internal import BASE
from test_z8_video_ring_stop import Lab, require
from test_z8_video_data import configure
from test_z8_video_stream import video_setup


def run(binary, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    # The M producer/lease/ACK chain must be byte/state equivalent with O on.
    observed = []
    for enabled in (False, True):
        with tempfile.TemporaryDirectory(prefix='z8-i1-photo-') as tmp:
            g = PhotoLab(binary, Path(tmp), True)
            if enabled:
                g.command[g.command.index('-M') + 1] += (
                    ',z8-video-interface-model=on,z8-video-data-model=on,'
                    'raw-pipeline-input-pair-model=on,z8-video-ring-stop-model=on,'
                    'z8-d980-internal-start-model=on,engine152c-config-model=on,engine152c-request-model=on,'
                    'z8-hsfc152c-control-model=on,z8-hsfc152c-pixels-model=on,engine153-config-model=on,'
                    'z8-hsfc1530-decode-model=on,z8-hsfc1530-u16-model=on')
            try:
                g.start()
                observed.append(full_chain(g, True))
                require('Z9_UNKNOWN' not in g.text(), 'M chain rejected with O enabled')
                if enabled:
                    v = g.state('z8-video-data-state')
                    require(v['packets'] == v['decoded'] == v['output_frames'] == 0,
                            'M finite photos must not become video work')
            finally:
                g.close()
                (output / f'photo-{enabled}.log').write_text(g.text())
    require(observed[0] == observed[1], 'M output/state changed when O enabled')
    cases.append('M producer and ownership chain equivalent with O enabled')

    with tempfile.TemporaryDirectory(prefix='z8-i1-video-') as tmp:
        root = Path(tmp)
        kernel = root / 'zero.bin'; kernel.write_bytes(bytes(4096))
        raw = root / 'pixels.raw'; raw.write_bytes(generate(64, 32))
        names = list(dict.fromkeys([n for n in BASE if n != 'bootstrap-fixtures'] + [
            'z8-video-interface-model', 'z8-video-data-model',
            'engine152c-config-model', 'engine152c-request-model',
            'engine153-config-model', 'engine151-capture-pixels-model',
            'output-buffer-ownership-model','z8-d980-internal-start-model',
            'z8-hsfc152c-control-model','z8-hsfc152c-pixels-model',
            'z8-hsfc1530-decode-model','z8-hsfc1530-u16-model']))
        props = tuple(n + '=on' for n in names) + (
            'statistics-raw=' + str(raw), 'event1508-timing-period-ns=1000000')
        for reject_history in (False, True):
            with Lab(binary, root / str(reject_history), kernel,
                     accelerator='qtest', properties=props) as g:
                g.observe_irq()
                configure(g)
                def state():
                    return json.loads(g.qmp.command('qom-get', path='/machine',
                                                  property='z8-video-data-state'))
                def step():
                    g.qmp.command('cont'); g.q('clock_step 70000000'); g.qmp.command('stop')
                if reject_history:
                    # R's single U16 decoder markers must not be accepted as WLVR.
                    # A partial R signature cannot replace its complete native request/parent.
                    for off, value in ((8, 0), (0x548, 4140), (0x54c, 2756),
                                       (0x544, 8288), (0x114, 0), (0x404, 0), (8, 1)):
                        g.q(f'writel {0x15300000 + off:#x} {value:#x}')
                    require('Z9_UNKNOWN' in g.log_path.read_text(), 'History markers accepted as video')
                    require(state()['packets'] == state()['decoded'] == 0,
                            'Rejected history profile produced data')
                    cases.append('R U16 decoder markers rejected without data')
                else:
                    require(state()['front_modes'] == [2, 2], 'Video modes not latched at START')
                    g.q('writel 0x15300404 0')
                    video_setup(g)
                    g.q('writel 0x15080010 1'); g.q('writel 0x15080028 1'); step()
                    v = state()
                    require(v['packets'] == v['decoded'] == v['output_frames'] == 1,
                            'M options intercepted the video stream')
                    require(v['isp_video_owner'], '151 lost the video owner')
                    photo = json.loads(g.qmp.command('qom-get', path='/machine',
                                                    property='engine151-capture-pixels-state'))
                    require(photo['jobs'] == 0, 'Video generated a finite-photo job')
                    require('Z9_UNKNOWN' not in g.log_path.read_text(), 'Cross-mode video boundary')
                    cases.append('Video packet/decode/ISP with M frontend and photo options')
                (output / f'video-{reject_history}.log').write_text(g.log_path.read_text())
    # R's real retained packet/U16/151 path with the O backend also enabled.
    from test_z8_hsfc151_pixels import Lab as HistoryLab
    for name, mutate in (('history-with-video', 0), ('video-header-on-history', 1), ('video-decoder-on-history', 2), ('history-pending-restart', 3)):
        with tempfile.TemporaryDirectory(prefix='z8-i3-history-') as tmp:
            g = HistoryLab(binary, Path(tmp))
            g.command[g.command.index('-M')+1] += ',z8-video-interface-model=on,z8-video-data-model=on,output-buffer-ownership-model=on'
            try:
                g.start(); g.prepare_source(); g.prepare_isp()
                require(g.state('z8-video-data-state')['front_modes'] == [3,3], 'History not selected from native programs')
                if mutate == 3:
                    before = g.state('z8-hsfc1530-decode-state')
                    require(g.tx('readl 0x1530001c') == 6, 'Missing retained decoder completion')
                    g.write(0x15300008,0);g.write(0x15300008,1)
                    require('Z9_UNKNOWN' in g.text(), 'Restart replaced unacknowledged decode')
                    require(g.state('z8-hsfc1530-decode-state')['jobs']==before['jobs'], 'Restart published data over pending')
                    require(g.tx('readl 0x1530001c') == 6, 'Rejected restart consumed pending')
                elif mutate == 2:
                    before = g.state('z8-hsfc1530-decode-state')
                    g.write(0x1530001c,6);g.write(0x15300008,0);g.write(0x15300114,3);g.write(0x15300008,1)
                    require('Z9_UNKNOWN' in g.text(), 'Video bank program accepted as historical decoder')
                    require(g.state('z8-hsfc1530-decode-state')['jobs']==before['jobs'],'Rejected decoder published another job')
                elif mutate == 1:
                    g.write(0x15100200,1);g.write(0x1510021c,5);g.write(0x15100220,2)
                    g.start_isp()
                    require('Z9_UNKNOWN' in g.text(), 'Video parameters reinterpreted as history')
                    require(g.state('engine151-capture-pixels-state')['jobs']==0, 'Rejected cross-profile generated pixels')
                else:
                    g.start_isp();g.verify_outputs()
                    require(g.state('engine151-capture-pixels-state')['source_kind']==1,'Lost U16 historical source kind')
                    require(g.state('z8-video-data-state')['packets']==0,'History also emitted WLVR')
                    g.write(0x15100044,2)
                    require(not g.state('output-buffer-ownership-state')['slots'][0]['held'],'Historical151 ACK did not retire its lease')
                    require('Z9_UNKNOWN' not in g.text(), 'History with O capability rejected')
                cases.append(name)
            finally:
                g.close();(output/(name+'.log')).write_text(g.text())
    result = dict(passed=True, cases=cases, qemu_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  native_firmware=False, physical_verified=False)
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); run(a.qemu, a.output)
