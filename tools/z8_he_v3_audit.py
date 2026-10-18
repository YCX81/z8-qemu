# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute the exact, externally supplied v3 HE patch with bounded services.

The old audit remains frozen as a separate baseline. This module models database,
writer, JPEG-engine and allocator boundaries explicitly; it does not claim a HE
codec, camera filesystem or RTOS implementation. No upstream code is vendored.
"""
import argparse
import hashlib
import json
import io
from pathlib import Path
import struct
import zipfile
import zlib

from z8_he_history_audit import Harness, BlockedIO, PAYLOAD, MODE, DRAINED, WRITER, need, digest

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / 'profiles/z8-he-v3-source.json'
DB_OK = 0x4320080
QTYPE, QGRADE, QSTAR = 0xa03d482c8, 0xa03d482cc, 0xa03d482d0
DB, CREATE_LOCK, PREVIEW_Q = 0xa04822af8, 0xa048229f0, 0xa029e9fd0
EXTRA = {
    'stock_write': 0x406cc134, 'lock': 0x40875834, 'unlock': 0x408758b4,
    'db_add': 0x408602cc, 'db_remain': 0x408648a8, 'db_cancel': 0x4085ac54,
    'rc_params': 0x40eaecb4, 'y_alloc': 0x41048d78, 'y_size': 0x4104886c,
    'capacity': 0x410457cc, 'desc_init': 0x41060780, 'main_encode': 0x40eaea0c,
    'job_word': 0x40eaec6c, 'ch_lock': 0x40eb8854, 'ch_unlock': 0x40eb88a4,
    'engine': 0x40e82090, 'free_y': 0x41049504, 'free_src': 0x410486a8,
    'pool_notify': 0x40d2bc8c,
}


def load_prebuilt(toolkit):
    toolkit = Path(toolkit)
    pin = json.loads(PIN.read_text())
    if toolkit.is_file():
        need(toolkit.stat().st_size <= 4 * 1024 * 1024, 'Archive size bound')
        raw = toolkit.read_bytes()
        need(digest(raw) == pin['archive_sha256'], 'Unreviewed v3 toolkit archive')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            prefix = 'NikonFirmwarePatch-v0.1.0/'
            profile_bytes = archive.read(prefix + 'resources/precapture.json')
            code = archive.read(prefix + 'resources/precapture.bin')
    else:
        profile_bytes = (toolkit / 'resources/precapture.json').read_bytes()
        code = (toolkit / 'resources/precapture.bin').read_bytes()
    need(digest(profile_bytes) == pin['profile_sha256'], 'Unreviewed v3 profile')
    profile = json.loads(profile_bytes)
    need(len(code) == pin['code_bytes'] and digest(code) == pin['code_sha256'], 'Unreviewed v3 machine code')
    need(profile['entries'] == pin['entries'], 'Entry table differs')
    return code, {k: {'va': v} for k, v in pin['entries'].items()}, profile


def v3_config(flags=0x93):
    raw = bytearray(64)
    raw[:16] = b'Z8PRECFGv3'.ljust(16, b'\0')
    struct.pack_into('<4I', raw, 16, flags, 30, 1500000, 0)
    struct.pack_into('<I', raw, 60, zlib.crc32(raw[:60]))
    return bytes(raw)


class V3Harness(Harness):
    def __init__(self, code, symbols, stock_main=None):
        super().__init__(code, symbols, stock_main)
        self.cfg = {'A:\\Z8PRE.CFG': v3_config()}
        self.live_y, self.live_src = set(), set()
        self.database, self.objects = {}, {}
        self.db_locked = False
        self.channel_locks = set()
        self.stock_calls, self.preview_jobs = [], []
        self.writer_busy = None
        for addr in (QTYPE, DB, PREVIEW_Q): self.map(addr, 16)
        self.put64(DB, 0x74000000); self.put64(DB + 8, 0)
        self.put32(PREVIEW_Q, 2); self.put32(PREVIEW_Q + 4, 0)
        for name, address in EXTRA.items():
            self.map(address, 4)
            self.uc.mem_write(address, struct.pack('<I', 0xd65f03c0))
            self.uc.hook_add(self.u.UC_HOOK_CODE, self.service, name, address, address)
        self.menu(0, 2, 0)

    def menu(self, kind=0, grade=2, star=0):
        self.put32(QTYPE, kind); self.put32(QGRADE, grade); self.put32(QSTAR, star)

    def frame(self, **kwargs):
        f = super().frame(**kwargs)
        r, m = f['r'], f['m']
        shot = self.get32(m + 0x2508)
        self.put32(r + 0x14, 1); self.put32(r + 0x18, 1); self.put32(r + 0x1c, shot)
        self.put32(m + 0x294, 3)
        f.update(shot=shot, y=r + 0x5000, yback=r + 0x6200, ybase=f['base'] + 0x1800000,
                 ctx=r + 0xf100, src=r + 0xf300, ybytes=0x4000, requested_y=0x4000)
        self.map(f['ybase'], 0x20000)
        self.put64(f['ctx'] + 0x18, r)
        self.put64(r + 0x690, f['src']); self.live_src.add(f['src'])
        # Stock JPEG reservation exists before the patch adds the NEF member.
        self.database[shot] = {'state': 2, 'bytes': 0, 'frame': f}
        self.objects[r + 0xd100] = shot; self.objects[r + 0xd180] = shot | 2
        hdr = r + 0xe000
        self.put64(r + 0x6b0, hdr); self.live_headers.add(hdr); self.issued_headers.add(hdr)
        return f

    def find_frame(self, *, r=None, m=None, jid=None, y=None, ctx=None):
        for f in self.frames:
            if ((r is not None and f['r'] == r) or (m is not None and f['m'] == m) or
                (jid is not None and f['shot'] == (jid & ~15)) or
                (y is not None and f['y'] == y) or (ctx is not None and f['ctx'] == ctx)):
                return f
        raise ValueError('Unknown frame identity')

    def allocate_y(self, f, bytes_):
        need(f['y'] not in self.live_y, 'Y allocated while live')
        f['ybytes'] = bytes_
        y, b = f['y'], f['ybase']
        self.put64(y + 8, f['yback']); self.put64(f['yback'], b)
        self.put32(f['yback'] + 8, bytes_)
        self.put64(f['r'] + 0x6a8, y)
        da = self.get64(f['j'] + 0x10)
        for i, (desc, cap) in enumerate(((0x38, 0x30), (0xa0, 0x98), (0xc0, 0xb8), (0xe0, 0xd8))):
            p = b + i * 0x400
            self.put32(y + cap, 0x400); self.put64(y + desc + 8, p); self.put32(y + desc + 16, 4)
            self.uc.mem_write(p, b'\xff\xd8\xff\xd9')
            if i < 3: self.put64(da + i * 8, y + desc)
        self.uc.mem_write(y + 0x3c, struct.pack('<HH', 8256, 5504))
        self.live_y.add(y)
        if self.fail.get('overlap'): self.put32(y + 0x30, 0x18000)
        if self.fail.get('capacity'): self.put32(f['yback'] + 8, 0x4000)

    def mark(self, f, current=False):
        return self.call('precapture_current_entry' if current else 'precapture_history_entry', f['r'], 1)

    def pipeline(self, f):
        self.uc.reg_write(self.a.UC_ARM64_REG_X23, f['r'])
        f['requested_y'] = self.call('precapture_y_size_entry', f['m'], f['m'] + 0x26c, 0)
        self.uc.reg_write(self.a.UC_ARM64_REG_X23, f['r'])
        y = self.call('precapture_y_alloc_entry', 1, f['m'])
        if not y: return
        self.call('precapture_main_rc', f['r'], 0, 0, 0, f['r'] + 0xf800)
        self.call('precapture_main_step', f['ctx'])

    def p3(self, f):
        self.put64(WRITER, f['job'])
        return self.call('precapture_write_file', 0, 0, f['j'])

    def cleanup_contract(self, f):
        if self.original_cleanup:
            return super().cleanup_contract(f)
        # Explicit stand-in when proprietary cleanup instructions are not supplied.
        super().cleanup_contract(f)
        for offset, kind in ((0x690, 'free_src'), (0x6a8, 'free_y'), (0x6b0, 'free_hdr')):
            p = self.get64(f['r'] + offset)
            if p:
                self.release_resource(kind, p)
                self.put64(f['r'] + offset, 0)

    def release_resource(self, kind, p):
        owners = {'free_y': self.live_y, 'free_src': self.live_src, 'free_hdr': self.live_headers}[kind]
        need(p in owners, 'Double/unknown ' + kind)
        if kind == 'free_y': need(self.writer_busy is None, 'Y released during blocked writer')
        owners.remove(p)
        self.events.append({'service': kind, 'address': p})

    def dma(self, address, size):
        need(size > 0 and address + size > address, 'Invalid DMA span')
        if self.HEAP <= address < self.HEAP + 0x100000:
            need(not any(address <= p < address + size for p in self.dirty), 'Dirty metadata DMA')
        for f in self.frames:
            if f['payload'] <= address < f['payload'] + PAYLOAD:
                need(f['e'] in self.live_e and address + size <= f['payload'] + PAYLOAD, 'Invalid/released E DMA')
            if f['ybase'] <= address < f['ybase'] + 0x20000:
                need(f['y'] in self.live_y and address + size <= f['ybase'] + f['ybytes'], 'Invalid/released Y DMA')
        return bytes(self.uc.mem_read(address, size))

    def output(self, name, spans):
        row = {'bytes': 0, 'sha': hashlib.sha256(), 'prefix': b'', 'writes': []}
        for address, size in spans:
            raw = self.dma(address, size)
            row['writes'].append({'address': address, 'offset': row['bytes'], 'bytes': size, 'sha256': digest(raw)})
            row['bytes'] += size; row['sha'].update(raw)
            if len(row['prefix']) < 8192: row['prefix'] += raw[:8192 - len(row['prefix'])]
        self.files[name] = row
        return row

    def stock_write(self, op, mode, j):
        need(op == 0 and mode in (0, 3), 'Unsupported original writer contract')
        jid = self.get32(j); f = self.find_frame(jid=jid)
        self.stock_calls.append({'mode': mode, 'jid': jid})
        if self.fail.get('blocked_stock'):
            self.writer_busy = f['r']; raise BlockedIO('Original writer boundary did not return')
        if mode == 0:
            need(j == f['j'], 'Wrong JPEG object')
            if self.fail.get('jpeg_write'):
                self.database.pop(jid, None); return 5
            self.output(f['name'], [(self.get64(f['y'] + 0x40), 4)])
            self.database[jid]['state'] = 4
            return 0
        need(jid == (f['shot'] | 2) and jid in self.database, 'NEF not registered')
        attr = self.get64(j + 0x10); dims = self.get64(attr)
        need(bytes(self.uc.mem_read(dims + 8, 4)) == struct.pack('<HH', 8256, 5504), 'Writer dimension attribute differs')
        n = struct.unpack('<H', self.uc.mem_read(j + 0x218, 2))[0]
        need(1 <= n <= 27, 'Unbounded segment list')
        spans = [(self.get64(j + 0x68 + k * 16), self.get32(j + 0x70 + k * 16)) for k in range(n)]
        need(sum(s for _, s in spans) == self.get64(j + 0x30) == self.database[jid]['bytes'], 'Writer total differs from reservation')
        payload = [(a, n) for a, n in spans if n == PAYLOAD]
        need(payload == [(f['payload'], PAYLOAD)], 'NEF payload crossed frames')
        if self.fail.get('nef_write'):
            self.database.pop(jid, None)
            return 5
        if not self.fail.get('missing_nef'):
            self.output(f['name'][:-3] + 'NEF', spans)
        self.database[jid]['state'] = 4
        if self.fail.get('slow_stock'): self.time += 2000000
        return 0

    def service(self, uc, address, size, name):
        x = [uc.reg_read(self.a.UC_ARM64_REG_X0 + i) for i in range(5)]
        value = 0
        if name == 'stock_write': value = self.stock_write(*x[:3])
        elif name == 'lock':
            need(x[0] == CREATE_LOCK and not self.db_locked, 'Wrong/reentrant database lock')
            if self.fail.get('lock'): value = 1
            else: self.db_locked = True; value = DB_OK
        elif name == 'unlock':
            need(self.db_locked and x[0] == CREATE_LOCK, 'Unowned database unlock')
            self.db_locked = False; value = DB_OK
        elif name == 'db_add':
            need(self.db_locked and x[0] == self.get64(DB), 'Unprotected database registration')
            need(self.get32(x[2]) == 2, 'Unexpected member type')
            jid = x[1] | 2
            if self.fail.get('db_add'): value = 1
            else:
                need(jid not in self.database, 'Duplicate NEF reservation')
                self.database[jid] = {'state': 2, 'bytes': self.get64(x[2] + 8), 'frame': self.find_frame(jid=jid)}
                value = DB_OK
        elif name == 'db_remain': need(self.db_locked and x[0] == 1, 'Incorrect remaining-shot update')
        elif name == 'db_cancel':
            if not (self.fail.get('cancel_jpeg') and (x[0] & 15) == 0): self.database.pop(x[0], None)
        elif name == 'dcf_get':
            if x[0] in self.database:
                f = self.find_frame(jid=x[0]); value = f['r'] + (0xd180 if x[0] & 15 else 0xd100)
                self.put32(value + 0x70, self.database[x[0]]['state'])
        elif name == 'dcf_name':
            need(x[0] in self.objects, 'Unknown file object')
            value = self.find_frame(jid=self.objects[x[0]])['r'] + 0xd000
        elif name == 'dcf_put': need(x[0] in self.objects, 'Unowned DCF reference')
        elif name == 'y_size': value = 0x4000
        elif name == 'capacity': value = 0x10000
        elif name == 'y_alloc':
            f = self.find_frame(m=x[1])
            if not self.fail.get('y_alloc'):
                self.allocate_y(f, f['requested_y']); value = f['y']
        elif name == 'desc_init':
            need(x[:2] == [1, 0], 'Preview descriptor ABI differs')
            if not self.fail.get('desc_init'): self.put32(x[3], 1); value = 1
        elif name == 'rc_params':
            uc.mem_write(x[4], bytes(40)); self.put32(x[4] + 0x14, 0); self.put32(x[4] + 0x18, 0)
        elif name == 'main_encode':
            f = self.find_frame(ctx=x[0]); value = 5 if self.fail.get('main_encode') else 0
            if not value: uc.mem_write(f['y'] + 0x18, b'\x01')
        elif name == 'job_word': value = 1
        elif name == 'ch_lock':
            need(x[0] in (0x23, 0x24) and x[0] not in self.channel_locks, 'Wrong/reentrant JPEG channel')
            self.channel_locks.add(x[0])
        elif name == 'ch_unlock':
            need(x[0] in self.channel_locks, 'Unowned JPEG unlock'); self.channel_locks.remove(x[0])
        elif name == 'engine':
            need(0x23 + x[0] in self.channel_locks, 'Unprotected preview encode')
            target = self.get64(x[1] + 0x10); f = self.find_frame(y=target - 0x100)
            need(self.get64(x[1] + 8) == f['src'] + 0x30, 'Preview source differs')
            cap = self.get32(x[1] + 0x18); p = self.get64(target + 8)
            need(cap == 0x10000 and p >= f['ybase'] + 0x4000 and p + cap <= f['ybase'] + f['ybytes'], 'Preview overwrites stock Y layout')
            n = 0 if self.fail.get('preview_zero') else (cap + 1 if self.fail.get('preview_oversize') else 4)
            if n == 4: uc.mem_write(p, b'\xff\xd8\xff\xd9')
            self.put32(target + 0x10, n)
            self.preview_jobs.append({'engine': x[0], 'y': f['y'], 'bytes': n})
        elif name in ('free_y', 'free_src'): self.release_resource(name, x[0])
        elif name == 'pool_notify': need(x[0] == 0xa03b8549c and x[1] in (1, 0x10, 0x20, 0x40, 0x80), 'Unknown pool notification')
        else: return super().service(uc, address, size, name)
        self.events.append({'service': name, 'arg0': x[0], 'result': value})
        uc.reg_write(self.a.UC_ARM64_REG_X0, value & ((1 << 64) - 1))
        uc.reg_write(self.a.UC_ARM64_REG_PC, uc.reg_read(self.a.UC_ARM64_REG_LR))

    def summary(self):
        row = super().summary()
        row.update(live_y=len(self.live_y), live_source=len(self.live_src), db_locked=self.db_locked,
                   jpeg_locks=len(self.channel_locks), reservations=sum(v['state'] in (2, 3, 8) for v in self.database.values()),
                   stock_writer_calls=self.stock_calls, preview_jobs=self.preview_jobs)
        return row


def closed(h):
    need(not (h.live_e or h.live_y or h.live_src or h.live_work or h.live_headers or h.fds or h.db_locked or h.channel_locks), 'Resource ownership remains open')


def inspect_nef(h, name, row):
    """Independently bind TIFF tags and emitted DMA spans, not a HE decoder."""
    data = row['prefix']
    need(data[:4] == b'II*\0', 'NEF TIFF header differs')
    def ifd(at):
        need(8 <= at <= len(data) - 2, 'IFD outside retained prefix')
        n = struct.unpack_from('<H', data, at)[0]
        need(0 < n <= 160 and at + 6 + n * 12 <= len(data), 'IFD length outside prefix')
        result = {}
        for i in range(n):
            tag, kind, count, value = struct.unpack_from('<HHII', data, at + 2 + i * 12)
            need(tag not in result, 'Duplicate TIFF tag')
            result[tag] = (kind, count, value)
        return result
    top = ifd(struct.unpack_from('<I', data, 4)[0])
    kind, count, subs = top[0x14a]
    need(kind == 4 and count in (2, 3) and subs + 4 * count <= len(data), 'SubIFD list differs')
    raw = ifd(struct.unpack_from('<I', data, subs + 4)[0])
    for tag, expected in ((0x100, 8280), (0x101, 5520), (0x102, 14), (0x103, 34713), (0x117, PAYLOAD)):
        need(raw[tag][2] == expected, 'RAW tag differs: ' + hex(tag))
    start = raw[0x111][2]
    f = next(f for f in h.frames if f['name'][:-3] + 'NEF' == name)
    offset = 0
    for w in row['writes']:
        if start <= w['offset'] < start + PAYLOAD:
            need(w['offset'] == start + offset and w['address'] == f['payload'] + offset, 'Wrong-frame RAW DMA')
            offset += w['bytes']
    need(offset == PAYLOAD and row['bytes'] >= start + PAYLOAD, 'Incomplete RAW payload')
    return dict(raw_dimensions=[8280, 5520], bits=14, subifds=count,
                full_preview_selected=count == 3, payload_bytes=offset, source_binding_verified=True,
                valid_HE_decoded=False)


def run_cases(code, symbols, stock=None):
    rows = []
    def run(name, fn, outcome='expected_behavior'):
        h = V3Harness(code, symbols, stock); fn(h)
        outputs={n:inspect_nef(h,n,r) for n,r in h.files.items() if n.endswith('.NEF')}
        rows.append(dict(case=name, outcome=outcome, nef_output_checks=outputs, **h.summary()))
        print(name + ': ' + outcome, flush=True)

    def shoot(h, kind=0, grade=2, star=0, current=False, mode=3, shot=16):
        h.menu(kind, grade, star); f = h.frame(mode=mode, shot=shot)
        h.mark(f, current=current); h.p2(f); h.pipeline(f)
        st = h.p3(f)
        need(f['e'] not in h.live_e, 'Writer returned while retaining E')
        need(f['y'] in h.live_y, 'Writer prematurely released Y')
        h.cleanup_contract(f); closed(h)
        return f, st

    for mode in (3, 6):
        for phase in ('history', 'current'):
            for kind, grade, star in [(1, 0, 0)] + [(t, g, s) for t in (0, 3) for g in range(3) for s in (0, 1)]:
                def menu_case(h, mode=mode, phase=phase, kind=kind, grade=grade, star=star):
                    f, st = shoot(h, kind, grade, star, phase == 'current', mode)
                    need(st == 0, 'Successful menu path failed')
                    ext = sorted(p.rsplit('.', 1)[1] for p in h.files)
                    need(ext == (['NEF'] if kind == 1 else ['JPG'] if kind == 3 else ['JPG', 'NEF']), 'Menu file set differs')
                    need(h.summary()['reservations'] == 0, 'Completed path left a reservation')
                run(f'mode{mode}_{phase}_type{kind}_grade{grade}_star{star}', menu_case)

    for kind in (0, 1):
        for failure in ('db_add', 'lock', 'no_db', 'nef_write', 'cancel_jpeg', 'alloc', 'slow_stock', 'missing_nef'):
            def failure_case(h, kind=kind, failure=failure):
                if failure == 'no_db': h.put64(DB, 0)
                else: h.fail[failure] = True
                f, st = shoot(h, kind=kind)
                names = set(h.files)
                jpeg, nef = f['name'], f['name'][:-3] + 'NEF'
                if failure in ('db_add', 'lock', 'no_db'):
                    need(jpeg in names and nef in names and (f['shot'] | 2) not in h.database, 'Registration failure did not use unregistered fallback')
                if failure == 'nef_write': need(jpeg in names and nef not in names and 'A:\\Z8PRESTP.BIN' in names, 'NEF failure lost JPEG/stop')
                if failure == 'cancel_jpeg' and kind == 1: need(jpeg in names and nef in names, 'Failed RAW-only cancellation did not preserve JPEG')
                if failure == 'alloc': need(jpeg in names and nef not in names, 'Scratch failure did not preserve JPEG')
                if failure in ('slow_stock', 'missing_nef'): need('A:\\Z8PRESTP.BIN' in names, 'Writer anomaly did not persist stop')
                if failure == 'missing_nef' and kind == 1:
                    need(st == 0 and jpeg not in names and nef not in names and f['shot'] not in h.database,
                         'Missing-file RAW-only behavior changed')
            run(f'type{kind}_{failure}', failure_case,
                'known_limitation_false_success_cancels_JPEG' if failure == 'missing_nef' and kind == 1 else 'expected_behavior')

    for failure in ('preview_zero', 'preview_oversize', 'capacity', 'overlap', 'desc_init'):
        def preview_failure(h, failure=failure):
            h.fail[failure] = True
            f, _ = shoot(h, kind=0, grade=0)
            need(f['name'][:-3] + 'NEF' in h.files, 'Missing preview lost the reduced-preview NEF')
        run('preview_' + failure + '_cleanup', preview_failure)

    def cancel(h):
        h.menu(0, 0, 0); f = h.frame(); h.mark(f); h.p2(f); h.pipeline(f)
        h.database.pop(f['shot'])  # Caller cancels the pending JPEG job.
        h.cleanup_contract(f); closed(h)
    run('cancel_pending_frame_with_extra_preview', cancel)

    def y_failure(h):
        h.menu(0,0,0);f=h.frame();h.mark(f);h.p2(f);h.fail['y_alloc']=True;h.pipeline(f)
        need(not h.live_y and f['e'] in h.live_e, 'Failed Y allocation consumed E')
        h.database.pop(f['shot']);h.cleanup_contract(f);closed(h)
    run('preview_allocation_failure_then_cancel', y_failure)

    def repeated_cleanup(h):
        f,_=shoot(h,kind=0,grade=0);before=h.summary()
        h.cleanup_contract(f);closed(h)
        need(h.summary()==before, 'Completed frame cleanup released twice')
    run('repeat_original_cleanup_after_completed_preview', repeated_cleanup)

    def menu_snapshot(h):
        h.menu(1,0,0);f=h.frame();h.mark(f)
        h.menu(3,2,1);h.p2(f);h.pipeline(f);h.p3(f);h.cleanup_contract(f);closed(h)
        need(list(h.files)==[f['name'][:-3]+'NEF'], 'Pending frame used later menu settings')
    run('pending_frame_keeps_submit_time_menu', menu_snapshot)

    def bad_metadata(h):
        h.menu(1,0,0);f=h.frame();h.mark(f);h.p2(f);h.pipeline(f)
        h.uc.mem_write(f['hp'],b'BAD!');h.p3(f);h.cleanup_contract(f);closed(h)
        need(list(h.files)==[f['name']], 'Invalid metadata did not fall back to JPEG')
    run('invalid_header_prepare_falls_back_and_releases', bad_metadata)

    def busy(h):
        h.menu(0, 0, 0)
        fs = []
        for i in range(6):
            f = h.frame(seq=i % 3 + 1, total=3, shot=(i + 1) * 16)
            h.mark(f, current=i >= 3); h.p2(f); h.pipeline(f); fs.append(f)
        need(len(h.live_e) == len(h.live_y) == 6, 'Pending burst ownership differs')
        for f in (fs[3], fs[0], fs[4], fs[1], fs[5], fs[2]):
            h.p3(f); h.cleanup_contract(f)
        closed(h); need(len(h.files) == 12 and h.summary()['reservations'] == 0, 'Interleaved triggers lost/cross-bound outputs')
    run('busy_second_trigger_and_out_of_order_writer', busy)

    def sticky(h):
        h.fail['nef_write'] = True; shoot(h, kind=1)
        h.fail.clear(); f, _ = shoot(h, kind=1, current=True, shot=32)
        need(f['name'] in h.files and f['name'][:-3] + 'NEF' not in h.files, 'Sticky stop did not suppress later NEF')
    run('failed_burst_then_current_frame_under_stop', sticky)

    def blocked(h):
        h.menu(0, 0, 0); f = h.frame(); h.mark(f); h.p2(f); h.pipeline(f)
        h.fail['blocked_stock'] = True
        try: h.p3(f)
        except BlockedIO: pass
        else: raise ValueError('Blocked writer unexpectedly returned')
        need(f['e'] in h.live_e and f['y'] in h.live_y and h.live_work, 'Blocked writer unexpectedly unwound')
    run('blocked_stock_writer_keeps_inflight_owners', blocked, 'known_limitation')
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--toolkit', type=Path, required=True)
    p.add_argument('--stock-main', type=Path)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    need(not args.report.exists(), 'Report already exists')
    code, symbols, _ = load_prebuilt(args.toolkit)
    pin = json.loads(PIN.read_text())
    stock = args.stock_main.read_bytes() if args.stock_main else None
    if stock is not None: need(digest(stock) == pin['stock_main_sha256'], 'Stock MAIN identity differs')
    rows = run_cases(code, symbols, stock)
    receipt = dict(schema=1, status='EXPECTED_BEHAVIORS_VERIFIED', upstream_commit=pin['commit'],
                   exact_prebuilt_code=True, code_sha256=digest(code), code_bytes=len(code),
                   harness_sha256=digest(Path(__file__).read_bytes()), helper_sha256=digest((ROOT/'tools/z8_he_history_audit.py').read_bytes()),
                   profile_sha256=digest(PIN.read_bytes()), cases=rows,
                   original_R_cleanup_executed=stock is not None, original_allocator_executed=False,
                   original_writer_executed=False, valid_HE_codec_input=False,
                   scope='Exact patch CPU execution; modeled database/writer/allocator/JPEG services; synthetic opaque payload',
                   hardware_verified=False, full_C15_C30_QEMU_verified=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'cases': len(rows), 'code_sha256': digest(code), 'status': receipt['status']}))


if __name__ == '__main__': main()
