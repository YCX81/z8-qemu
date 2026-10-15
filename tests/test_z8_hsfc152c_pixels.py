# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual guest-RAM simulation packets; no firmware or host packet injection."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import zlib

from check_z8_machine import sha256
from test_z8_c0_stop import preview, C0
from test_z8_d980_internal import require
from test_z8_hsfc152c_control import Lab as ControlLab, P, PHYSICAL, ENCODED, CAPACITY

OPTION = 'z8-hsfc152c-pixels-model'
STATE = 'z8-hsfc152c-pixels-state'
PRIMARY = 0x150c0000


class Lab(ControlLab):
    def __init__(self, binary, folder, *, enabled=True, **kwargs):
        super().__init__(binary, folder, **kwargs)
        at = self.command.index('-M') + 1
        self.command[at] += ',primary-stopped-source-reset-model=on,z8-primary-frame-boundary-model=on'
        if enabled:
            self.command[at] += ',' + OPTION + '=on'

    def setup_pixels(self):
        self.setup()
        preview(self)
        for off in (0x60, 0x64, 8):
            self.write(PRIMARY + off, 2)
        self.write(PRIMARY + 0x200, 8)
        self.periodic()
        self.write(P + 8, 1)
        require(self.state('engine152c-request-state')['pending'], 'Actual receiver start missing')
        require(self.tx(f'readl {P + 0x1c:#x}') == 0, 'Start manufactured image completion')

    def packet(self, address, frame_id):
        header = self.read_ram(address, 80)
        require(header[:8] == b'NZ8BAYR1', 'No actual Bayer packet in original output RAM')
        head_size, version, w, h, stride, bits = struct.unpack_from('<6I', header, 8)
        raw_bytes, encoded_bytes, identity, when = struct.unpack_from('<4Q', header, 32)
        raw_crc, encoded_crc, total, head_crc = struct.unpack_from('<4I', header, 64)
        require((head_size, version, w, h, stride, bits, identity) == (80, 1, 4140, 2756, 7264, 14, frame_id),
                'Actual packet metadata differs')
        require(raw_bytes == stride*h and 80 < total <= CAPACITY and not total % 512,
                'Packet geometry or span exceeds native request')
        require(zlib.crc32(header[:76]) == head_crc, 'Header CRC differs')
        packet = self.read_ram(address, total)
        encoded = packet[80:80 + encoded_bytes]
        require(zlib.crc32(encoded) == encoded_crc, 'Encoded actual RAM CRC differs')
        raw = zlib.decompress(encoded)
        require(len(raw) == raw_bytes and zlib.crc32(raw) == raw_crc, 'Decoded actual RAM CRC differs')
        expected = bytearray(raw_bytes)
        for y in range(16):
            for x in range(64):
                bit = ((frame_id >> (x // 2)) & 1) ^ (y >= 8)
                sample = 16383 * 7 // 8 if bit else 16383 // 8
                for n in range(14):
                    at = x*14+n
                    expected[y*stride + at//8] |= ((sample >> n) & 1) << (at & 7)
        require(raw == expected, 'Complete decoded pixels/padding differ from independent authored Bayer expectation')
        require(not any(packet[80 + encoded_bytes:]), 'Packet alignment padding differs')
        return dict(frame_id=identity, captured_ns=when, physical=address, packet_bytes=total,
                    packet_sha256=hashlib.sha256(packet).hexdigest(), raw_sha256=hashlib.sha256(raw).hexdigest())

    def irq152c(self):
        self.tx(f'readl {P + 0x1c:#x}')
        return self.irqs.get(0x10b - 32, False)

    def ack_preview(self):
        status = self.pending()
        if status:
            self.write(C0 + 0x14, status)
        primary = self.tx(f'readl {PRIMARY + 0x208:#x}')
        if primary:
            self.write(PRIMARY + 0x204, primary)

    def next_frame(self, number):
        for _ in range(5):
            self.ack_preview()
            self.advance()
            if self.state(STATE)['frames'] == number:
                return
        raise AssertionError('Bounded actual source ticks produced no requested frame')

    def ack_image(self):
        status = self.tx(f'readl {P + 0x1c:#x}')
        if status:
            self.write(P + 0x1c, status)
        # A separately latched refill request may follow the third data ACK.
        status = self.tx(f'readl {P + 0x1c:#x}')
        if status == 0x10:
            self.write(P + 0x1c, status)


def case(binary, name, body, *, reject=None, startup_error=None, **kwargs):
    row = dict(name=name, passed=False, firmware_used=False, cpu_execution=False)
    with tempfile.TemporaryDirectory(prefix='z8-hsfc-pixels-') as tmp:
        g = Lab(binary, Path(tmp), **kwargs)
        try:
            try:
                g.start()
            except (RuntimeError, OSError):
                if not startup_error or startup_error not in g.text():
                    raise
            else:
                require(startup_error is None, 'Expected startup rejection')
                row['observed'] = body(g)
                require(('Z9_UNKNOWN' in g.text()) == bool(reject), 'Unexpected strict boundary')
                if reject:
                    require(f'addr=0x{reject:x}' in g.text(), 'Wrong strict boundary')
            row['passed'] = True
        except Exception as error:
            row['error'] = repr(error)
        finally:
            g.close()
            row.update(stderr=g.text(), owned_qemu_stopped=g.proc is None or g.proc.poll() is not None)
    return row


def first_frame(g):
    g.setup_pixels()
    g.advance()
    require(g.state('raw-pipeline-state')['frames'] == 1, 'No real preview source tick')
    result = g.packet(PHYSICAL[0], 1)
    require(g.tx(f'readl {P + 0x1c:#x}') == 3 and g.irq152c(), 'Actual complete packet did not precede pixel IRQ')
    return result


def run(binary, red=False):
    if red:
        return [case(binary, 'red real receiver has no packet supplier', first_frame, enabled=False)]
    cases = [case(binary, 'actual complete Bayer packet in native target before IRQ', first_frame)]

    def retained(g):
        g.setup_pixels()
        rows = []
        for i in range(4):
            g.next_frame(i + 1)
            rows.append(g.packet(PHYSICAL[i], i + 1))
            for older in range(i):
                require(g.packet(PHYSICAL[older], older + 1)['packet_sha256'] == rows[older]['packet_sha256'],
                        'Later frame overwrote an earlier native target')
            g.ack_image()
        g.ack_preview()
        g.advance()
        require(g.state(STATE)['frames'] == 4 and g.state(STATE)['waiting_for_bank'],
                'Exhausted native address list was implicitly recycled')
        require(len({row['raw_sha256'] for row in rows}) == 4, 'Authored input frames not distinct')
        require(all(rows[i]['captured_ns'] < rows[i+1]['captured_ns'] for i in range(3)), 'Frame clock order differs')
        return rows
    cases.append(case(binary, 'four actual frames retained without implicit reuse', retained))

    def backpressure(g):
        row = first_frame(g)
        g.ack_preview()
        g.advance()
        require(g.state(STATE)['frames'] == 1, 'Unacknowledged encoded output was replaced')
        require(g.packet(PHYSICAL[0], 1)['packet_sha256'] == row['packet_sha256'], 'Pending packet changed')
        g.write(P + 0x1c, 1)
        require(g.tx(f'readl {P + 0x1c:#x}') == 2 and g.irq152c(), 'Partial W1C lost remaining cause')
        g.write(P + 0x1c, 2)
        require(not g.irq152c(), 'Full W1C left IRQ asserted')
        g.next_frame(2)
        return [row, g.packet(PHYSICAL[1], 2)]
    cases.append(case(binary, 'pending packet and partial W1C own reuse', backpressure))

    def refill(g):
        old = retained(g)
        addresses = [0xc90000000 + i * 0x500000 for i in range(4)]
        g.write(P + 0x108, 1)
        for i, encoded in enumerate(addresses):
            g.write(P + 0x140 + 8*i, encoded & 0xffffffff)
            if i != 3:
                g.write(P + 0x144 + 8*i, encoded >> 32)
        g.ack_preview()
        g.advance()
        require(g.state(STATE)['frames'] == 4, 'Incomplete replacement bank produced data')
        g.write(P + 0x15c, addresses[3] >> 32)
        g.next_frame(5)
        newest = g.packet(addresses[0] - 0x400000000, 5)
        require(g.packet(PHYSICAL[0], 1)['packet_sha256'] == old[0]['packet_sha256'],
                'Replacement bank damaged a retained earlier packet')
        return newest
    cases.append(case(binary, 'only a complete new native bank permits frame five', refill))

    def masked(g):
        g.setup_pixels()
        g.write(P + 0x18, 0)
        g.advance()
        row = g.packet(PHYSICAL[0], 1)
        require(g.tx(f'readl {P + 0x1c:#x}') == 3 and not g.irq152c(), 'Masked cause not retained')
        g.write(P + 0x18, 0x79f)
        require(g.irq152c(), 'Unmask did not expose completed data')
        g.ack_image()
        require(not g.irq152c(), 'ACK did not retire masked generation')
        return row
    cases.append(case(binary, 'masked data completes without an unmasked IRQ', masked))

    def stopped(g):
        row = first_frame(g)
        g.write(P + 8, 0)
        g.ack_image()
        g.ack_preview()
        g.advance()
        require(g.state(STATE)['frames'] == 1, 'Cancelled request wrote another packet')
        require(g.packet(PHYSICAL[0], 1)['packet_sha256'] == row['packet_sha256'], 'Cancel discarded actual retained data')
        return g.state(STATE)
    cases.append(case(binary, 'cancel stops future writes and keeps prior RAM', stopped))

    def default_off(g):
        g.setup_pixels()
        g.advance()
        require(g.state('raw-pipeline-state')['frames'] == 1, 'No actual source preview')
        require(g.state(STATE)['frames'] == 0 and not g.irq152c(), 'Default-off receiver fabricated data')
        g.unchanged_outputs()
        return g.state(STATE)
    cases.append(case(binary, 'default off remains control-only', default_off, enabled=False))

    def no_clock(g):
        g.setup_pixels()
        g.write(0x1508002c, 1)
        require(not g.state('event1508-channel-state')['timing_active'], 'Source clock was not actually stopped')
        g.advance()
        require(g.state(STATE)['frames'] == 0 and not g.irq152c(), 'Stopped source produced packet')
        g.unchanged_outputs()
        return g.state(STATE)
    cases.append(case(binary, 'stopped source timing produces no packet', no_clock))

    def active_shadow(g):
        first_frame(g)
        g.write(P + 0x128, 0xdeadbe00)
        g.ack_image()
        g.next_frame(2)
        return g.packet(PHYSICAL[1], 2)
    cases.append(case(binary, 'active bank shadow does not redirect latched DMA', active_shadow))

    def reset(g):
        before = first_frame(g)
        g.write(P + 8, 0)
        for off, value in ((0x404, 1), (0x14, 0), (4, 1), (0, 1), (0, 0),
                           (4, 0), (0x14, 1), (0x404, 0)):
            g.write(P + off, value)
        state = g.state(STATE)
        require(state['pending'] == 0 and state['cancelled'] == 1 and state['acks'] == 0,
                'Reset fabricated a consumed-frame acknowledgement')
        require(not g.irq152c(), 'Reset left old IRQ asserted')
        require(g.packet(PHYSICAL[0], 1)['packet_sha256'] == before['packet_sha256'], 'Reset rewrote completed RAM')
        return state
    cases.append(case(binary, 'reset cancels notification and preserves completed RAM', reset))

    for verb in ('writeb', 'writew'):
        def bad_width(g, verb=verb):
            first_frame(g)
            before = g.state(STATE)
            g.write(P + 0x1c, 3, verb)
            require(g.state(STATE) == before, 'Wrong-width W1C changed completed ownership')
        cases.append(case(binary, 'reject ' + verb + ' completion W1C', bad_width, reject=P + 0x1c))

    def invalid_bank(g):
        previous = retained(g)
        g.write(P + 0x108, 1)
        for i in range(4):
            g.write(P + 0x140 + 8*i, 0x84000000)
            g.write(P + 0x144 + 8*i, 0xc)
        g.ack_preview()
        g.advance()
        require(g.state(STATE)['frames'] == 4 and g.state(STATE)['failed'], 'Overlapping bank produced data')
        require(g.packet(PHYSICAL[0], 1)['packet_sha256'] == previous[0]['packet_sha256'], 'Invalid bank damaged retained RAM')
        return g.state(STATE)
    cases.append(case(binary, 'invalid replacement bank fails before output', invalid_bank, reject=P + 0x108))

    def explicit_reuse(g):
        retained(g)
        g.write(P + 0x108, 1)
        for i, encoded in enumerate(ENCODED):
            g.write(P + 0x140 + 8*i, encoded & 0xffffffff)
            g.write(P + 0x144 + 8*i, encoded >> 32)
        g.next_frame(5)
        latest = g.packet(PHYSICAL[0], 5)
        records = {r['id']: r for r in g.state(STATE)['records']}
        require(not records[1]['valid'] and records[5]['valid'], 'Actual overwrite kept stale producer metadata valid')
        return latest
    cases.append(case(binary, 'explicit native reuse invalidates old generation', explicit_reuse))

    def immutable(g):
        try:
            g.qmp.command('qom-set', path='/machine', property=OPTION, value=False)
        except RuntimeError as error:
            require('machine creation' in str(error), 'Wrong mutation error')
        else:
            raise AssertionError('Live pixel model mutation accepted')
    cases.append(case(binary, 'pixel model is creation-only', immutable))
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
                  source_sha256=sha256(Path(__file__)), physical_codec_verified=False,
                  raw_precapture_verified=False)
    (args.output / 'qtest-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(passed=passed, cases=len(cases), failures=[
        {k: row[k] for k in ('name', 'error') if k in row} for row in cases if not row['passed']])))
    raise SystemExit(not passed)


if __name__ == '__main__':
    main()
