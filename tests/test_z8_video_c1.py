# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual receiver RAM and owned 14-bit decode; no camera recording claim."""
import argparse
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from test_z8_video_ring_stop import Lab, require

STATE = 'z8-video-c1-state'


def configure(m):
    def w(a, v):
        m.q(f'writel {a:#x} {v:#x}')
    geometry = {0: 1, 4: 64 << 16, 8: 36 << 16, 0x100: 128,
                0x104: 1, 0x108: 1, 0x110: 1, 0x118: 3}
    for row in range(4):
        geometry[0x120+row*8] = 0xe0000000
        geometry[0x124+row*8] = 0
    for off, value in geometry.items():
        w(0x150d9800+off, value)
    for a in (0x150c0060, 0x150c0064, 0x150c0008):
        w(a, 0x4000)
    fields = {0x14: 1, 0x18: 0x79f, 0x100: 1, 0x104: 1,
              0x10c: 1, 0x114: 3, 0x548: 64, 0x54c: 36}
    for base in (0x152c0000, 0x15300000):
        for off, value in fields.items():
            w(base+off, value)
        for row in range(4):
            address = 0x840000000 + row*0x10000
            if base == 0x152c0000:
                address += 0x400000000
            w(base+0x120+row*8, address & 0xffffffff)
            w(base+0x124+row*8, address >> 32)
    w(0x152c0550, 0x4000)
    for off, value in {0x14:64, 0x1c:36, 0x38:1, 0x44:0x4000, 0x48:14}.items():
        w(0x15ac0000+off, value)
    w(0x15b00038, 1)
    w(0x15300534, 0x4000)
    w(0x152c0008, 1)
    w(0x15080010, 1)
    w(0x15080028, 1)
    return w


def run(binary, output, *, boundaries=False, source_end=False, full_input=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    with tempfile.TemporaryDirectory(dir=output) as directory:
        root = Path(directory)
        kernel = root/'zero.bin'
        kernel.write_bytes(bytes(4096))
        raw = root/'input.raw'
        samples = [(i * 7) & 16383 for i in range(64*36)]
        raw.write_bytes(struct.pack('<8s6I', b'NZ9RAW1\0', 64, 36, 14, 0, 0, 16383) +
                        struct.pack('<'+'H'*len(samples), *samples))
        props = tuple(n+'=on' for n in (
            'image-bank-model', 'engine150c-control-model', 'engine150c-stop-model',
            'config150d-model', 'engine150d-control-model', 'statistics-model',
            'engine1543-control-model', 'engine151-control-model', 'raw-pipeline-model',
            'z8-video-interface-model', 'event1508-front-model', 'event1508-timing-model',
            'event1508-channel-model', 'event1508-aux-model'))
        props += ('statistics-raw='+str(raw), 'event1508-timing-period-ns=1000000')
        if boundaries:
            props += ('z8-video-c1-primary-boundaries-model=on',)
        if source_end:
            props += ('z8-video-c1-source-end-model=on',)
        names=('full-input',) if full_input else (('isp', 'overlap') if boundaries or source_end else ('valid', 'disabled', 'corrupt', 'closed-source', 'stop', 'isp', 'overlap'))
        for name in names:
            with Lab(binary, root/name, kernel, accelerator='qtest',
                     properties=props+(() if name == 'disabled' else ('z8-video-c1-model=on',))) as m:
                m.observe_irq()
                def state():
                    return json.loads(m.qmp.command('qom-get', path='/machine', property=STATE))
                def advance():
                    m.qmp.command('cont')
                    m.q('clock_step 50000000')
                    m.qmp.command('stop')
                state()
                w = configure(m)
                if full_input:
                    # Actual C3.11 REC switches4140x2344 to8280x4688.
                    width,height,capacity=8280,4688,19407872
                    for off,value in ((4,width<<16),(8,height<<16),(0x100,14496)):
                        w(0x150d9800+off,value)
                    w(0x150c0060,0x4000);w(0x150c0064,0x4000)
                    for base in (0x152c0000,0x15300000):
                        w(base+0x548,width);w(base+0x54c,height)
                        for row in range(4):
                            address=0x840000000+row*0x2000000+(0x400000000 if base==0x152c0000 else 0)
                            w(base+0x120+row*8,address&0xffffffff);w(base+0x124+row*8,address>>32)
                    for a,v in ((0x152c0550,capacity),(0x15300534,capacity),
                                (0x15ac0014,width),(0x15ac001c,height),(0x15ac0044,capacity)):
                        w(a,v)
                    w(0x15300008,1);advance();advance()
                    full=state()
                    require(full['packets']==1 and full['decoded']==1 and
                            full['decoded_samples']==width*height,'owned full sensor readout reaches decoder')
                    require(not full['failed'] and not full['rejects'],'full input is admitted without fallback')
                    require('Z9_UNKNOWN' not in m.log_path.read_text(),'full input register contract\n'+m.log_path.read_text()[-3000:])
                    cases.append(dict(name=name,passed=True,state=full))
                    continue
                if name == 'closed-source':
                    w(0x1508002c, 1)
                advance()
                if name in ('disabled', 'closed-source'):
                    require(state()['packets'] == 0, 'no input while disabled or stopped')
                    require(not any(bytes.fromhex(m.q('read 0x840000000 0x100')[2:])), 'no RAM write')
                else:
                    require(state()['packets'] == 1 and state()['decoded'] == 0, 'one owned packet before decoder')
                    require(int(m.q('readl 0x152c001c'),0)==3,
                            'Z8 data subscriber mask13 receives DATA0/1, not STOP END4')
                    header = bytes.fromhex(m.q('read 0x840000000 0x50')[2:])
                    require(header[:8] == b'NZ8BAYR1', 'actual packet header')
                    length = struct.unpack_from('<I', header, 72)[0]
                    packet = bytes.fromhex(m.q(f'read 0x840000000 {length:#x}')[2:])
                    payload_bytes = struct.unpack_from('<Q', header, 40)[0]
                    data = zlib.decompress(packet[80:80+payload_bytes])
                    require(len(data) == 128*36 and zlib.crc32(data) == struct.unpack_from('<I', header, 64)[0], 'independent packet decode')
                    unpacked = [((int.from_bytes(data[y*128+(x*14)//8:y*128+(x*14)//8+3], 'little') >> ((x*14)&7)) & 16383)
                                for y in range(36) for x in range(64)]
                    require(unpacked == samples, 'every original 14-bit sample retained')
                    advance()
                    require(state()['packets'] == 1, 'unconsumed RAM cannot be overwritten')
                    if name == 'corrupt':
                        m.q('write 0x840000000 1 0x00')
                    if name == 'stop':
                        w(0x152c0008, 0)
                    w(0x15300008, 1)
                    advance()
                    if name in ('valid', 'isp', 'overlap'):
                        require(state()['decoded'] == 1 and state()['input_bits'] == 14, 'owned 14-bit decoder input')
                        require(state()['decoded_samples'] == 64*36, 'all samples decoded')
                        require(int(m.q('readl 0x1530001c'),0)==3,'decoder delivers DATA0/1 to its native client')
                        require(state()['output_frames'] == 0, 'no ISP descriptor means no output')
                        w(0x152c001c, 0x13)
                        w(0x1530001c, 0x13)
                        advance()
                        require(state()['packets'] == 1, 'ISR ACK cannot replace image consumer')
                        if name != 'valid':
                            configure_isp(m, overlap=name == 'overlap')
                            def planes():
                                return [bytes.fromhex(m.q(f'read {0x842000000+ch*0x2000+uv*0x1000:#x} {288 if uv else 576:#x}')[2:])
                                        for ch in range(3) for uv in range(2)]
                            before = planes()
                            advance()
                            after = planes()
                            if name == 'overlap':
                                require(before == after and state()['output_frames'] == 0,
                                        'cross-plane overlap rejected before any output write')
                            else:
                                require(all(a != b and any(b) for a, b in zip(before, after)),
                                        'all six ISP destination planes contain actual pixels: ' +
                                        json.dumps(state()) + '\n' + m.log_path.read_text()[-2400:])
                                require(state()['output_frames'] == 1 and state()['consumed_epoch'] == 1,
                                        'one decoded input consumed once')
                                if boundaries:
                                    require(int(m.q('readl 0x150c0208'),0) & 0x18 == 0x18,
                                            'primary causes follow all six real writes')
                                    require(int(m.q('readl 0x150c0204'),0) & 0x18 == 0,
                                            'masked causes retained')
                                    w(0x150c0200,0x18)
                                    require(int(m.q('readl 0x150c0204'),0) & 0x18 == 0x18,
                                            'unmask exposes pending causes')
                                    w(0x150c0204,8)
                                    advance()
                                    require(state()['primary_pending'] == 16 and state()['output_frames'] == 1,
                                            'partial primary ACK cannot release output ownership')
                                    w(0x150c0204,16)
                                advance()
                                require(state()['output_frames'] == 1 and planes() == after,
                                        'unacknowledged ISP outputs prevent reuse')
                                def ack_outputs():
                                    for address, bits in ((0x152c001c,0x13), (0x1530001c,0x13),
                                                          (0x15100044,8), (0x1518000c,0x08100000),
                                                          (0x1542111c,0x11), (0x15432014,9)):
                                        w(address,bits)
                                    if boundaries:
                                        w(0x150c0204,0x18)
                                if source_end:
                                    require(not (int(m.q('readl 0x15180010'),0)&0x20000000),
                                            'first input cannot finish a three-input group')
                                    for _ in range(4):
                                        ack_outputs(); advance()
                                        if state()['output_frames'] == 3: break
                                    require(state()['output_frames'] == 3 and state()['source_ends'] == 1,
                                            'END after third actual complete frame')
                                    ack_outputs(); advance()
                                    require(state()['output_frames'] == 3 and state()['source_end_acks'] == 0,
                                            'data ACK does not release END ownership')
                                    w(0x1518000c,0x20000000)
                                    require(state()['source_end_acks'] == 1, 'distinct native END ACK')
                                for _ in range(8):
                                    ack_outputs(); advance()
                                    if state()['output_frames'] == 4: break
                                require(state()['output_frames'] == 4, 'four owned input slots')
                                ack_outputs(); advance()
                                require(state()['output_frames'] == 4, 'next bank needs native descriptors')
                                refill_backends(m,1)
                                m.write(8, 0x303, 1)
                                advance()
                                require(state()['output_frames'] == 4, 'bank selector alone is not refill')
                                for row in range(4):
                                    for off,value in ((0,0xe0000000),(4,0),(8,0xe0000080),(12,0)):
                                        if (row,off) != (3,12):
                                            m.write(0x400+row*0x80+off,value,1)
                                advance()
                                require(state()['output_frames'] == 4, 'partial descriptor bank rejected')
                                m.write(0x58c,0,1)
                                advance()
                                require(state()['output_frames'] == 5 and state()['input_bank_refills'] == 1,
                                        'fresh complete C1 bank supplies next frame with retained output table')
                    else:
                        require(state()['decoded'] == 0 and state()['output_frames'] == 0, 'bad or cancelled packet cannot produce pixels')
                cases.append(dict(name=name, passed=True, state=state()))
    result = dict(passed=True, cases=cases, native_firmware=False, recording=False)
    (output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(passed=True, cases=len(cases))))


def configure_isp(m, *, overlap=False):
    def w(a, v):
        m.q(f'writel {a:#x} {v:#x}')
    values = {0x200:1, 0x204:1, 0x214:1, 0x218:1, 0x21c:5, 0x220:2,
              0x1c:0x3f, 0x260:0x1fff, 0x264:0x3ff,
              0x400:64 | 36 << 16, 0x450:64 | 36 << 16,
              0x430:192, 0x434:192, 0x480:256, 0x484:256,
              0x740:0xe0000000, 0x9c0:0xe0000080}
    for off, value in ((0x14,0x3807f), (0,1), (4,1), (0x18,0x3807f), (0x18,0x7ffff)):
        w(0x15100000+off, value)
    for off, value in values.items():
        w(0x15100000+off, value)
    for off in range(0x268, 0x320, 8):
        w(0x15100000+off, 0x1000)
        w(0x15100004+off, 0xa)
    w(0x15100008, 1)
    w(0x15180014, 1)
    w(0x15180014, 0)
    for off, value in {0x30:1, 0xb0:192, 0xb4:192, 0xbc:1, 0xc0:1, 0xc4:0x22, 0xd4:0x22}.items():
        w(0x15180000+off, value)
    for off, value in {4:0x202, 0x10:1, 0x38:256, 0x3c:256, 0x4c:64, 0x50:36}.items():
        w(0x15421100+off, value)
    m.ring(0, start=False)
    for ch in range(3):
        for off, value in {0x10:1, 0x38:32, 0x3c:32}.items():
            w(0x15422000+ch*0x100+off, value)
        w(0x1542302c+ch*0x100, 32)
        w(0x15423030+ch*0x100, 18)
    for row in range(4):
        for ch in range(3):
            for uv in range(2):
                address = 0x842000000+row*0x10000+ch*0x2000+uv*0x1000
                if overlap and ch == 2 and uv:
                    address = 0x842000000+row*0x10000
                off = 0x210+row*0x80+ch*0x10+uv*8
                m.write(off, address & 0xffffffff)
                m.write(off+4, address >> 32)
    m.ring(1, start=False)
    for row in range(4):
        m.write(0x200+row*0x80, 0xe0000000, 1)
        m.write(0x208+row*0x80, 0xe0000080, 1)
    m.write(4, 0x101, 1)
    require('Z9_UNKNOWN' not in m.log_path.read_text(), 'invalid fixture\n'+m.log_path.read_text()[-2400:])


def refill_backends(m,bank):
    for port in range(2):
        base=0x15300000 if port else 0x152c0000
        for row in range(4):
            address=0x840000000+bank*0x80000+row*0x10000
            if not port:address+=0x400000000
            off=0x120+bank*0x20+row*8
            m.q(f'writel {base+off:#x} {address&0xffffffff:#x}')
            m.q(f'writel {base+off+4:#x} {address>>32:#x}')
        m.q(f'writel {base+0x108:#x} {bank}')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--primary-boundaries', action='store_true')
    p.add_argument('--source-end', action='store_true')
    p.add_argument('--full-input', action='store_true')
    a = p.parse_args()
    run(a.qemu, a.output, boundaries=a.primary_boundaries, source_end=a.source_end,full_input=a.full_input)
