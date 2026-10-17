# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute pinned external HE-history hooks with explicit, bounded services.

No Nikon instructions, HE codec, firmware container, or upstream C source is
included. The caller supplies the separately licensed upstream checkout. Heap,
filesystem, DMA visibility and time are contracts, not camera implementations.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / 'profiles/z8-he-history-source.json'
CAVE = 0x40960000
MODE, LIMIT, DRAINED, WRITER = 0xa03bc6bc8, 0xa03bc6bac, 0xa03bc5638, 0xa03b891b8
PAYLOAD, OLD, NEW = 0x1058600, 0x1082400, 0x1082600
SERVICES = {
    'submit': 0x40f93098, 'free_e': 0x4104df5c, 'free_hdr': 0x4104d600,
    'xlate': 0x41186718, 'open': 0x402daa4c, 'create': 0x402dad04,
    'close': 0x402daaac, 'read': 0x402dab4c, 'write': 0x402dabb8,
    'exists': 0x402db1ac, 'rename': 0x402dad58, 'alloc': 0x40d2c408,
    'free': 0x40d2c448, 'clean': 0x40d2b8b8, 'clock': 0x40c7c778,
    'dcf_get': 0x4085a8e0, 'dcf_name': 0x4085bfe0, 'dcf_put': 0x4085aa7c,
}


def need(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config(frames=30, flags=3, budget=1500000):
    need(1 <= frames <= 30, 'Invalid frame ceiling')
    raw = bytearray(64)
    raw[:16] = b'Z8PRECFGv1'.ljust(16, b'\0')
    struct.pack_into('<4I', raw, 16, flags, frames, budget, 0)
    struct.pack_into('<I', raw, 60, zlib.crc32(raw[:60]))
    return bytes(raw)


def compile_source(source, compiler, defines=()):
    """Compile unchanged pinned source; record rather than conceal byte drift."""
    source = Path(source).resolve()
    pin = json.loads(PIN.read_text())
    for name, expected in pin['source_sha256'].items():
        need(digest((source / name).read_bytes()) == expected, 'Source drift: ' + name)
    profile = json.loads((source / 'resources/precapture.json').read_text())
    # The external linker imports its package helper. Do not alter its files.
    before = list(sys.path)
    try:
        sys.path.insert(0, str(source))
        spec = importlib.util.spec_from_file_location('_he_external_linker', source / 'z8precapture/linker.py')
        linker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(linker)
        with tempfile.TemporaryDirectory(prefix='z8-he-hook-') as temp:
            obj = Path(temp) / 'hooks.o'
            args = [str(compiler), *profile['compiler_flags'], *['-D' + d for d in defines],
                    '-c', str(source / 'z8precapture/firmware.c'), '-o', str(obj)]
            subprocess.run(args, check=True, capture_output=True, text=True)
            object_bytes = obj.read_bytes()
            code, sections, symbols, _ = linker.link(object_bytes)
    finally:
        sys.path[:] = before
    return code, symbols, {
        'upstream_commit': pin['commit'], 'source_hashes_verified': True,
        'compiler': subprocess.check_output([str(compiler), '--version'], text=True).splitlines()[0],
        'compiler_flags': profile['compiler_flags'], 'defines': list(defines),
        'object_sha256': digest(object_bytes), 'linked_sha256': digest(code),
        'linked_bytes': len(code), 'reviewed_machine_code_match': digest(code) == profile['code_sha256'],
        'reviewed_code_sha256': profile['code_sha256'], 'sections': sections,
    }


def tiff(entries, minimum=0):
    """Authored little-endian TIFF fixture, not camera metadata."""
    result = bytearray(b'II*\0\x08\0\0\0')
    result.extend(struct.pack('<H', len(entries)) + bytes(12 * len(entries) + 4))
    for i, (tag, kind, count, value) in enumerate(entries):
        at = 10 + 12 * i
        struct.pack_into('<HHI', result, at, tag, kind, count)
        if len(value) <= 4:
            result[at + 8:at + 12] = value.ljust(4, b'\0')
        else:
            if len(result) & 1:
                result.append(0)
            struct.pack_into('<I', result, at + 8, len(result))
            result.extend(value)
    result.extend(bytes(max(0, minimum - len(result))))
    return bytes(result)


def synthetic_header(stream_bytes=4):
    """Enough Exif/MPF structure to exercise the real C writer, no valid HE."""
    mn = b'Nikon\0\x02\x11\0\0' + tiff([
        (0x000c, 5, 4, struct.pack('<8I', *([1, 1] * 4))),
        (0x0054, 7, 20, b'0100' + bytes(16)),
        (0x0016, 3, 4, struct.pack('<4H', 0, 0, 8256, 5504)),
    ])
    data = bytearray(0x500)
    data[:8] = b'II*\0\x08\0\0\0'
    # IFD0 -> Exif IFD; next IFD -> thumbnail IFD.
    struct.pack_into('<HHHIII', data, 8, 1, 0x8769, 4, 1, 26, 44)
    struct.pack_into('<HHHIII', data, 26, 1, 0x927c, 7, len(mn), 80, 0)
    struct.pack_into('<H', data, 44, 2)
    thumb_at = 80 + len(mn)
    struct.pack_into('<HHII', data, 46, 0x0201, 4, 1, thumb_at)
    struct.pack_into('<HHII', data, 58, 0x0202, 4, 1, 4)
    data[80:80 + len(mn)] = mn
    data[thumb_at:thumb_at + 4] = b'\xff\xd8\xff\xd9'
    exif = b'\xff\xd8\xff\xe1' + struct.pack('>H', len(data) + 8) + b'Exif\0\0' + data
    mp = bytearray(48)
    mpf = tiff([(0xb002, 7, 48, bytes(mp))])
    length = len(exif) + 8 + len(mpf)
    for i, size in enumerate((length + stream_bytes - 2, stream_bytes, stream_bytes)):
        struct.pack_into('<I', mp, i * 16 + 4, size)
    mpf = tiff([(0xb002, 7, 48, bytes(mp))])
    return exif + b'\xff\xe2' + struct.pack('>H', len(mpf) + 6) + b'MPF\0' + mpf


class BlockedIO(RuntimeError):
    """Deliberately non-returning modeled service; not a cooperative error."""


class Harness:
    STACK, HEAP, RETURN = 0x70000000, 0x72000000, 0x73000000

    def __init__(self, code, symbols, stock_main=None):
        import unicorn as u
        from unicorn import arm64_const as a
        self.u, self.a = u, a
        self.uc = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        self.mapped = set()
        self.map(CAVE, 0x10000)
        self.uc.mem_write(CAVE, code)
        self.map(0xa03b80000, 0x100000)
        self.map(self.STACK, 0x100000)
        self.map(self.HEAP, 0x100000)
        self.map(self.RETURN, 0x1000)
        self.symbols = dict(symbols)
        self.original_cleanup = stock_main is not None
        if stock_main is not None:
            # Read only from the caller's hash-checked local image. Never export.
            address = 0x40ea4000
            self.map(address, 0x2000)
            self.uc.mem_write(address, stock_main[address - 0x40160000:address - 0x40160000 + 0x2000])
            self.symbols['original_R_cleanup'] = {'va': hex(0x40ea44d0)}
        self.time = 1000
        self.frames, self.events, self.files, self.fds = [], [], {}, {}
        self.cfg = {'A:\\Z8PRE.CFG': config()}
        self.live_e, self.live_work, self.dirty = set(), set(), set()
        self.live_headers, self.issued_headers = set(), set()
        self.fail, self.writes, self.next_fd = {}, 0, 10
        self.put32(MODE, 3)
        self.put32(LIMIT, 30)
        for name, address in SERVICES.items():
            self.map(address, 4)
            # Authored AArch64 RET, not an original firmware instruction.
            self.uc.mem_write(address, struct.pack('<I', 0xd65f03c0))
            self.uc.hook_add(u.UC_HOOK_CODE, self.service, name, address, address)
        self.uc.hook_add(u.UC_HOOK_MEM_WRITE, self.memory_write, None, self.HEAP, self.HEAP + 0xfffff)

    def map(self, address, size):
        first, last = address & ~4095, (address + size + 4095) & ~4095
        start = None
        for p in range(first, last, 4096):
            if p not in self.mapped:
                if start is None:
                    start = p
                self.mapped.add(p)
            elif start is not None:
                self.uc.mem_map(start, p - start)
                start = None
        if start is not None:
            self.uc.mem_map(start, last - start)

    def put32(self, at, value): self.uc.mem_write(at, struct.pack('<I', value))
    def put64(self, at, value): self.uc.mem_write(at, struct.pack('<Q', value))
    def get32(self, at): return struct.unpack('<I', self.uc.mem_read(at, 4))[0]
    def get64(self, at): return struct.unpack('<Q', self.uc.mem_read(at, 8))[0]

    def string(self, at):
        for n in range(128):
            if not self.uc.mem_read(at + n, 1)[0]:
                return bytes(self.uc.mem_read(at, n)).decode('ascii')
        raise ValueError('Unterminated service path')

    def memory_write(self, uc, access, address, size, value, data):
        self.dirty.update(range(address, address + size))

    def payload_read(self, uc, access, address, size, value, frame):
        if access == self.u.UC_MEM_WRITE:
            raise ValueError('CPU wrote opaque HE payload')
        raise ValueError('CPU read opaque HE payload')

    def frame(self, seq=1, total=1, shot=16, mode=3, allocation=NEW):
        i = len(self.frames)
        r = 0x900000000 + i * 0x10000
        e, m, backing, job, lp, da = r + 0x1000, r + 0x2000, r + 0x6000, r + 0x7000, r + 0x8000, r + 0x9000
        base = 0x920000000 + i * 0x2000000
        payload = base + 512
        tail = base + OLD
        self.map(r, 0x10000)
        self.map(base, NEW)
        self.put64(r + 0x748, e); self.put64(r + 0x790, m)
        self.put64(e + 8, backing); self.put64(backing, base); self.put32(backing + 8, allocation)
        self.put32(e + 0x30, 3); self.put32(e + 0x34, 5); self.put32(e + 0x38, 0x15902058)
        self.put64(e + 0x40, payload)
        self.uc.mem_write(payload, bytes([i + 1]) * 64)
        self.uc.hook_add(self.u.UC_HOOK_MEM_READ | self.u.UC_HOOK_MEM_WRITE,
                         self.payload_read, i, payload, payload + PAYLOAD - 1)
        self.put32(m, 3); self.put32(m + 0x120, seq); self.put32(m + 0x2508, shot)
        self.put32(DRAINED, total); self.put32(MODE, mode)
        self.put32(job + 0xbc, 1); self.put64(job + 0x18, r)
        j = job + 0xc0 + 0x48
        self.put32(j, shot); self.put64(j + 8, m); self.put64(j + 0x18, lp); self.put64(j + 0x10, da)
        header = synthetic_header()
        hp = r + 0xa000
        self.uc.mem_write(hp, header)
        self.put64(lp, hp); self.put32(lp + 8, len(header)); self.put32(lp + 0xc, len(header))
        self.put32(lp + 0x1c, 2); self.put32(lp + 0x2c, 2)
        for k in range(3):
            desc, data = r + 0xb000 + k * 0x40, r + 0xc000 + k * 0x100
            self.put64(da + k * 8, desc); self.put64(desc + 8, data); self.put32(desc + 0x10, 4)
            self.uc.mem_write(data, b'\xff\xd8\xff\xd9')
        name = f'A:\\DCIM\\100NCZ_8\\DSC_{shot // 16:04}.JPG'.encode()
        self.uc.mem_write(r + 0xd000, name + b'\0'); self.put32(r + 0xd020, len(name))
        f = dict(r=r, e=e, m=m, base=base, tail=tail, payload=payload, job=job, j=j, name=name.decode(), hp=hp)
        self.frames.append(f); self.live_e.add(e)
        return f

    def call(self, entry, *args):
        for i in range(8):
            self.uc.reg_write(self.a.UC_ARM64_REG_X0 + i, args[i] if i < len(args) else 0)
        self.uc.reg_write(self.a.UC_ARM64_REG_SP, self.STACK + 0xff000)
        self.uc.reg_write(self.a.UC_ARM64_REG_LR, self.RETURN)
        address = int(self.symbols[entry]['va'], 16)
        self.uc.emu_start(address, self.RETURN, timeout=5000000, count=2000000)
        need(self.uc.reg_read(self.a.UC_ARM64_REG_PC) == self.RETURN, 'Hook execution bound exhausted')
        return self.uc.reg_read(self.a.UC_ARM64_REG_X0)

    def p1(self, f): return self.call('precapture_history_entry', f['r'], 1)
    def p2(self, f): return self.call('precapture_tdec_release', f['r'])
    def p3(self, f, hres=None):
        self.put64(WRITER, f['job'])
        hres = f['r'] + 0xe000 if hres is None else hres
        if hres not in self.issued_headers:
            self.issued_headers.add(hres); self.live_headers.add(hres)
        return self.call('precapture_writer_file_done', hres)

    def cleanup_contract(self, f):
        """Execute supplied original cleanup, or explicitly model its contract."""
        if self.original_cleanup:
            self.call('original_R_cleanup', f['r'])
            self.events.append({'service': 'original_R_cleanup_returned'})
            return
        e = self.get64(f['r'] + 0x748)
        if e:
            self.release_e(e)
            self.put64(f['r'] + 0x748, 0)
        self.events.append({'service': 'modeled_R_cleanup', 'e': e})

    def release_e(self, e):
        if not e:
            return  # Assumed null-safe allocator service, recorded in docs.
        need(e in self.live_e, 'Double/unknown E release')
        self.live_e.remove(e)
        self.events.append({'service': 'free_e', 'e': e})

    def service(self, uc, address, size, name):
        x = [uc.reg_read(self.a.UC_ARM64_REG_X0 + i) for i in range(5)]
        value = 0
        if name == 'submit':
            self.events.append({'service': name, 'r': x[0], 'keep': x[1]}); value = 0x1234
        elif name == 'free_e': self.release_e(x[0])
        elif name == 'free_hdr':
            need(x[0] in self.live_headers, 'Double/unknown header release')
            self.live_headers.remove(x[0])
            self.events.append({'service': name, 'address': x[0]})
        elif name == 'xlate': value = x[0]
        elif name == 'clock':
            self.time += 100; self.put64(x[0], self.time)
        elif name == 'clean':
            self.dirty.difference_update(range(x[0], x[0] + x[1]))
        elif name == 'alloc':
            if not self.fail.get('alloc'):
                need(not self.live_work, 'Reentrant scratch allocation')
                value = self.HEAP; self.live_work.add(value)
        elif name == 'free':
            need(x[0] in self.live_work, 'Double/unknown scratch free')
            self.live_work.remove(x[0])
        elif name == 'open':
            path = self.string(x[0]); value = -1
            if path in self.cfg:
                value = self.next_fd; self.next_fd += 1; self.fds[value] = ('cfg', path)
        elif name == 'read':
            kind, path = self.fds[x[0]]
            need(kind == 'cfg', 'Unexpected read')
            data = self.cfg[path][:x[2]]; uc.mem_write(x[1], data); self.put32(x[3], len(data))
        elif name == 'exists': value = 0 if self.string(x[0]) in self.files else -1
        elif name == 'create':
            path = self.string(x[0])
            value = -1
            if not (self.fail.get('create') and path.endswith('.NE_')) and not (self.fail.get('stop_create') and path.endswith('.BIN')):
                value = self.next_fd; self.next_fd += 1
                self.files[path] = {'bytes': 0, 'sha': hashlib.sha256(), 'prefix': b'', 'writes': []}
                self.fds[value] = ('output', path)
        elif name == 'write':
            kind, path = self.fds[x[0]]; need(kind == 'output', 'Unexpected output descriptor')
            target = self.files[path]
            if path.endswith('.NE_'):
                self.writes += 1
                if self.fail.get('blocked') == self.writes: raise BlockedIO('Filesystem call did not return')
                if self.fail.get('advance') == self.writes: self.time += 2000000
            count = x[2]
            if self.fail.get('write') == self.writes and path.endswith('.NE_'):
                count = max(0, count - 1); value = -1
            if self.HEAP <= x[1] < self.HEAP + 0x100000:
                need(not any(x[1] <= p < x[1] + count for p in self.dirty), 'DMA consumed unclean CPU output')
            raw = bytes(uc.mem_read(x[1], count))
            offset = target['bytes']
            target['sha'].update(raw); target['bytes'] += count
            if len(target['prefix']) < 8192:
                target['prefix'] += raw[:8192 - len(target['prefix'])]
            target['writes'].append({'address': x[1], 'offset': offset, 'bytes': count, 'sha256': digest(raw)})
            self.put32(x[3], count)
        elif name == 'close':
            kind, path = self.fds.pop(x[0])
            if kind == 'output' and path.endswith('.NE_') and self.fail.get('close'): value = -1
        elif name == 'rename':
            old, new = self.string(x[0]), self.string(x[1])
            if self.fail.get('rename'): value = -1
            else:
                need(old in self.files and new not in self.files, 'Invalid rename')
                self.files[new] = self.files.pop(old)
        elif name in ('dcf_get', 'dcf_name', 'dcf_put'):
            job = self.get64(WRITER)
            f = next(f for f in self.frames if f['job'] == job)
            if name == 'dcf_get': value = f['r'] + 0xd100
            elif name == 'dcf_name': value = f['r'] + 0xd000
        else: raise ValueError('Unmodeled service: ' + name)
        uc.reg_write(self.a.UC_ARM64_REG_X0, value & ((1 << 64) - 1))
        uc.reg_write(self.a.UC_ARM64_REG_PC, uc.reg_read(self.a.UC_ARM64_REG_LR))

    def summary(self):
        return {'live_e': len(self.live_e), 'live_scratch': len(self.live_work), 'open_files': len(self.fds),
                'live_headers': len(self.live_headers),
                'e_frees': sum(x['service'] == 'free_e' for x in self.events),
                'header_frees': sum(x['service'] == 'free_hdr' for x in self.events),
                'files': {p: {'bytes': f['bytes'], 'sha256': f['sha'].hexdigest(),
                              'raw_binding_verified': f.get('raw_binding_verified', False)} for p, f in self.files.items()}}


def run_cases(code, symbols, stock_main=None):
    rows = []

    def run(name, body, expected='pass'):
        h = Harness(code, symbols, stock_main)
        body(h)
        rows.append({'case': name, 'result': expected, **h.summary()})
        print(name + ': ' + expected, flush=True)

    def check_output(h, f):
        name = f['name'][:-3] + 'NEF'
        data = h.files[name]
        prefix = data['prefix']

        def ifd(at):
            count = struct.unpack_from('<H', prefix, at)[0]
            need(count <= 160 and at + 2 + count * 12 + 4 <= len(prefix), 'Output IFD exceeds retained prefix')
            return {struct.unpack_from('<H', prefix, at + 2 + i * 12)[0]:
                    struct.unpack_from('<HHII', prefix, at + 2 + i * 12) for i in range(count)}

        top = ifd(struct.unpack_from('<I', prefix, 4)[0])
        sub = top[0x14a][3]
        raw = ifd(struct.unpack_from('<I', prefix, sub + 4)[0])
        for tag, value in ((0x100, 8280), (0x101, 5520), (0x102, 14), (0x103, 34713), (0x117, PAYLOAD)):
            need(raw[tag][3] == value, 'Unexpected RAW tag: ' + hex(tag))
        start = raw[0x111][3]
        writes = [w for w in data['writes'] if start <= w['offset'] < start + PAYLOAD]
        offset = 0
        for w in writes:
            need(w['offset'] == start + offset and w['address'] == f['payload'] + offset, 'Cross-frame/noncontiguous HE DMA')
            offset += w['bytes']
        need(offset == PAYLOAD, 'HE payload was truncated')
        data['raw_binding_verified'] = True

    def complete(h):
        f = h.frame(); need(h.p1(f) == 0x1234, 'P1 changed original submit result')
        h.p2(f); need(f['e'] in h.live_e and h.get32(f['tail'] + 0x14) == 2, 'P2 failed retention')
        h.p3(f)
        need(not h.live_e and not h.live_work and not h.fds, 'Normal path leaked')
        need(any(p.endswith('.NEF') for p in h.files), 'Writer did not publish NEF')
        need(not any(p.endswith('.BIN') for p in h.files), 'Normal path stopped')
        check_output(h, f)
        # A later file has its own header; it must not release E again.
        h.p3(f, hres=f['r'] + 0xe100)
        need(h.summary()['e_frees'] == 1 and h.summary()['header_frees'] == 2, 'P3 replay double free')
    run('normal_write_then_different_file_header', complete)

    def refusal(h, change):
        f = h.frame(); h.p1(f); change(h, f); h.p2(f)
        need(not h.live_e and h.get64(f['r'] + 0x748) == 0, 'P2 refusal leaked')
    for name, change in [
        ('missing_config', lambda h, f: h.cfg.clear()),
        ('both_cards_configured', lambda h, f: h.cfg.update({'B:\\Z8PRE.CFG': config()})),
        ('bad_config_crc', lambda h, f: h.cfg.update({'A:\\Z8PRE.CFG': bytes(64)})),
        ('config_disabled', lambda h, f: h.cfg.update({'A:\\Z8PRE.CFG': config(flags=0)})),
        ('C30_disabled', lambda h, f: h.cfg.update({'A:\\Z8PRE.CFG': config(flags=1)})),
        ('stale_record', lambda h, f: setattr(h, 'time', h.time + 120000001)),
        ('record_crc_corrupt', lambda h, f: h.put32(f['tail'] + 0x78, 0)),
        ('resource_identity_changed', lambda h, f: h.put64(f['tail'] + 0x28, 1)),
    ]:
        run(name, lambda h, c=change: refusal(h, c))

    for name, args in [('post_FULL_frame', {'seq': 2}), ('unsupported_C120', {'mode': 4}),
                       ('history_above_30', {'total': 31}), ('old_allocation_no_tail', {'allocation': OLD})]:
        def excluded(h, args=args):
            f = h.frame(**args); h.p1(f); h.p2(f)
            need(not h.live_e and not h.files, 'Excluded frame retained')
        run(name, excluded)

    for mode, total in ((6, 5), (6, 15), (3, 9), (3, 30)):
        def burst(h, mode=mode, total=total):
            h.put32(LIMIT, total)
            frames = [h.frame(seq=i + 1, total=total, shot=(i + 1) * 16, mode=mode) for i in range(total)]
            for f in frames: h.p1(f); h.p2(f)
            need(len(h.live_e) == total, 'Burst retention count differs')
            # Reversed completion tests frame-local binding; original ordering is not claimed.
            for f in reversed(frames): h.p3(f); check_output(h, f)
            need(not h.live_e and len(h.files) == total, 'Burst completion lost/cross-bound a frame')
        run(f'C{15 if mode == 6 else 30}_history_{total}_reverse_completion', burst)

    for mode in (3, 6):
        def ceiling(h, mode=mode):
            h.cfg['A:\\Z8PRE.CFG'] = config(frames=2)
            for i in range(5):
                f = h.frame(seq=i + 1, total=5, shot=(i + 1) * 16, mode=mode)
                h.p1(f); h.p2(f)
                if i >= 3: h.p3(f)
            need(len(h.files) == 2 and not h.live_e, 'Most-recent-N selection differs')
        run(f'mode{mode}_recent_two_ceiling', ceiling)

    def cancellation(h):
        f = h.frame(); h.p1(f); h.p2(f); h.cleanup_contract(f); h.cleanup_contract(f)
        need(not h.live_e and h.summary()['e_frees'] == 1, 'Cleanup contract release differs')
    cleanup_kind = 'original' if stock_main is not None else 'modeled'
    run('JPEG_never_arrives_' + cleanup_kind + '_R_cleanup', cancellation)

    def mismatched_writer(h):
        f = h.frame(); h.p1(f); h.p2(f); h.put64(f['j'] + 8, 1); h.p3(f)
        need(f['e'] in h.live_e and not h.files, 'Mismatched JPEG consumed E')
        h.cleanup_contract(f)
    run('wrong_JPEG_identity_then_' + cleanup_kind + '_cleanup', mismatched_writer)

    for name in ('alloc', 'create', 'close', 'rename'):
        def failure(h, name=name):
            f = h.frame(); h.p1(f); h.p2(f); h.fail[name] = True; h.p3(f)
            need(not h.live_e and not h.live_work and not h.fds, 'Failure leaked resources')
            need(not any(p.endswith('.NEF') for p in h.files), 'Failure published final NEF')
            if name != 'alloc': need('A:\\Z8PRESTP.BIN' in h.files, 'I/O failure did not stop')
        run(name + '_failure', failure)

    for at in (1, 2, 5, 10, 20):
        def write_failure(h, at=at):
            f = h.frame(); h.p1(f); h.p2(f); h.fail['write'] = at; h.p3(f)
            need(not h.live_e and not h.live_work and not h.fds, 'Short write leaked')
            need('A:\\Z8PRESTP.BIN' in h.files, 'Short write did not persist stop')
            need(not any(p.endswith('.NEF') for p in h.files), 'Partial NEF published')
        run(f'partial_write_{at}', write_failure)

    def timeout(h):
        f = h.frame(); h.p1(f); h.p2(f); h.fail['advance'] = 5; h.p3(f)
        need(not h.live_e and not h.live_work and 'A:\\Z8PRESTP.BIN' in h.files, 'Budget cleanup differs')
    run('cooperative_budget_overrun', timeout)

    def blocked(h):
        f = h.frame(); h.p1(f); h.p2(f); h.fail['blocked'] = 5
        try: h.p3(f)
        except BlockedIO: pass
        else: raise ValueError('Blocked I/O control returned')
        need(f['e'] in h.live_e and bool(h.live_work), 'Blocked call unexpectedly unwound')
    run('blocked_IO_does_not_unwind', blocked, 'known_limitation_reproduced')

    def duplicate_p2(h):
        f = h.frame(); h.p1(f); h.p2(f); h.p2(f)
        need(f['e'] not in h.live_e and h.get64(f['r'] + 0x748) == 0, 'P2 behavior changed')
        need(not h.files, 'Duplicate P2 unexpectedly wrote')
    run('replayed_P2_frees_retained_E', duplicate_p2, 'call_once_precondition_reproduced')

    def duplicate_p3(h):
        f = h.frame(); h.p1(f); h.p2(f); h.p3(f)
        try: h.p3(f)
        except ValueError as exc: need('Double/unknown header release' in str(exc), 'Unexpected replay failure')
        else: raise ValueError('Duplicate P3 did not attempt duplicate header free')
    run('replayed_P3_double_header_free', duplicate_p3, 'call_once_precondition_reproduced')

    def duplicate_p1(h):
        f = h.frame(); h.p1(f); h.p2(f); h.p1(f); h.p3(f)
        need(not h.files and f['e'] in h.live_e, 'P1 replay behavior changed')
        h.cleanup_contract(f)
    run('replayed_P1_erases_retained_state', duplicate_p1, 'call_once_precondition_reproduced')

    def repeat(h):
        for i in range(6):
            f = h.frame(shot=(i + 1) * 16)
            h.p1(f); h.p2(f); h.p3(f)
            check_output(h, f)
            need(not h.live_e and not h.live_work, 'Completed burst leaked into next')
        need(len(h.files) == 6, 'Repeated burst output differs')
    run('six_completed_bursts_same_boot', repeat)

    def reuse(h):
        f = h.frame()
        for generation in range(3):
            if generation:
                # Explicit caller allocation contract: same addresses, new owner.
                need(f['e'] not in h.live_e, 'Reuse before E release')
                h.live_e.add(f['e']); h.put64(f['r'] + 0x748, f['e'])
            shot = (generation + 1) * 16
            h.put32(f['m'] + 0x2508, shot); h.put32(f['j'], shot)
            f['name'] = f'A:\\DCIM\\100NCZ_8\\DSC_{generation + 1:04}.JPG'
            h.uc.mem_write(f['r'] + 0xd000, f['name'].encode() + b'\0')
            h.uc.mem_write(f['payload'], bytes([generation + 1]) * 64)
            h.p1(f); h.p2(f)
            need(h.get32(f['tail'] + 0x70) == shot, 'Stale shot binding after reuse')
            if generation == 1:
                h.cleanup_contract(f)
            else:
                h.p3(f, hres=f['r'] + 0xe000 + generation * 0x100)
                check_output(h, f)
            need(not h.live_e and not h.live_work, 'Reused generation leaked')
        need(len(h.files) == 2 and h.summary()['e_frees'] == 3, 'Canceled/reused frame contaminated outputs')
    run('same_R_E_reused_after_completion_and_cancellation', reuse)

    def busy(h):
        a = h.frame(shot=16); h.p1(a); h.p2(a)
        b = h.frame(shot=32); h.p1(b); h.p2(b)
        h.p3(a); h.p3(b)
        check_output(h, a); check_output(h, b)
        need(not h.live_e and len(h.files) == 2, 'Overlapping retained frames cross-bound')
    run('second_trigger_before_first_writer', busy)

    def stop_next(h):
        f = h.frame(); h.p1(f); h.p2(f); h.fail['write'] = 1; h.p3(f)
        writes = h.writes; h.fail.clear()
        f = h.frame(shot=32); h.p1(f); h.p2(f); h.p3(f)
        need(h.writes == writes and not h.live_e, 'Sticky stop allowed another write')
    run('failure_blocks_next_burst', stop_next)

    def stop_failure(h):
        f = h.frame(); h.p1(f); h.p2(f)
        h.fail.update(write=1, stop_create=True); h.p3(f)
        need('A:\\Z8PRESTP.BIN' not in h.files, 'Stop creation failure unexpectedly persisted')
        h.fail.clear()
        f = h.frame(shot=32); h.p1(f); h.p2(f); h.p3(f); check_output(h, f)
    run('stop_marker_create_failure_allows_retry', stop_failure, 'known_limitation_reproduced')

    for flags in (3 | 4, 3 | 8, 3 | 4 | 8):
        def options(h, flags=flags):
            h.cfg['A:\\Z8PRE.CFG'] = config(flags=flags)
            complete(h)
        run(f'optional_flags_{flags}', options)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--compiler', required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--stock-main', type=Path, help='Optional local, fully hash-checked C3.11 module; never exported')
    a = p.parse_args()
    need(not a.report.exists(), 'Report already exists')
    code, symbols, provenance = compile_source(a.source, a.compiler)
    import unicorn
    provenance.update(harness_sha256=digest(Path(__file__).read_bytes()),
                      profile_sha256=digest(PIN.read_bytes()), unicorn_version=unicorn.__version__)
    stock = a.stock_main.read_bytes() if a.stock_main else None
    if stock is not None:
        need(digest(stock) == json.loads(PIN.read_text())['stock_main_sha256'], 'Stock MAIN identity differs')
    rows = run_cases(code, symbols, stock)
    negatives = []
    for define, reason in [('NEG_READ_PAYLOAD', 'CPU read opaque HE payload'), ('NEG_NO_CLEAN', 'DMA consumed unclean CPU output')]:
        bad_code, bad_symbols, _ = compile_source(a.source, a.compiler, (define,))
        h = Harness(bad_code, bad_symbols); f = h.frame(); h.p1(f); h.p2(f)
        try: h.p3(f)
        except ValueError as e: need(reason in str(e), 'Unexpected negative-control error: ' + str(e))
        else: raise ValueError('Defective negative control accepted: ' + define)
        negatives.append({'define': define, 'rejected': True, 'reason': reason})
    report = {'schema': 1, 'status': 'PASS', 'scope': 'Bounded ARM64 hook execution with authored synthetic Exif/MPF, opaque invalid HE bytes and modeled services',
              'provenance': provenance, 'cases': rows, 'negative_controls': negatives,
              'original_firmware_cleanup_executed': stock is not None, 'stock_QEMU_boot_verified': False,
              'original_cleanup_scope': 'R+0x748 with other resource pointers zero; allocator release is modeled',
              'valid_HE_decode_verified': False, 'hardware_verified': False, 'camera_update_generated': False,
              'real_filesystem_or_power_loss_verified': False, 'physical_timing_verified': False}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'cases': len(rows), 'negative_controls': len(negatives),
                      'reviewed_machine_code_match': provenance['reviewed_machine_code_match']}))


if __name__ == '__main__':
    main()
