# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest NAND media transactions; authored zero kernel, no firmware execution.

Run explicitly with --qemu. Every emulator is owned by this test and stopped.
These checks establish media persistence, not C10 settings or camera acceptance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import tempfile
import time

from check_z8_machine import QMP, sha256

REG = 0x17800000
RAM = 0xa00010000
LOCAL = 0x40010000
PAGE = 2112
HEADER = 72


class Machine:
    def __init__(self, binary, folder, kernel, properties=(), machine='nikon-z8-bringup',
                 ignore_file_size_signal=False):
        self.folder = Path(folder)
        self.folder.mkdir()
        self.log_path = self.folder / 'stderr.log'
        self.log = self.log_path.open('wb')
        # UNIX sockets need the Linux filesystem and a short path under WSL.
        self.runtime = tempfile.TemporaryDirectory(prefix='z8-nand-qtest-')
        sockets = Path(self.runtime.name)
        self.qmp = self.sock = self.stream = None
        self.command = [str(binary), '-M', ','.join([machine, 'bootstrap-fixtures=on',
                        'nand-fixture=on', *properties]), '-kernel', str(kernel),
                        '-smp', '4', '-m', '512M', '-S', '-display', 'none',
                        '-serial', 'none', '-monitor', 'none', '-qtest-log', '/dev/null',
                        '-qmp', f'unix:{sockets}/qmp.sock,server=on,wait=off',
                        '-qtest', f'unix:{sockets}/qtest.sock,server=on,wait=off']
        self.proc = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=self.log,
                                     preexec_fn=(lambda: signal.signal(signal.SIGXFSZ, signal.SIG_IGN))
                                     if ignore_file_size_signal else None)
        deadline = time.monotonic() + 8
        try:
            while not (sockets / 'qtest.sock').exists():
                if self.proc.poll() is not None:
                    raise RuntimeError(self.log_path.read_text(errors='replace'))
                if time.monotonic() >= deadline:
                    raise TimeoutError('Owned QEMU startup timeout')
                time.sleep(.01)
            self.qmp = QMP(sockets / 'qmp.sock', deadline)
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.settimeout(3)
            self.sock.connect(str(sockets / 'qtest.sock'))
            self.stream = self.sock.makefile('rwb', buffering=0)
        except Exception as exc:
            self.close()
            # QMP sockets may be created before the board rejects bad media.
            # Preserve the actual startup diagnostic across that socket race.
            raise RuntimeError(self.log_path.read_text(errors='replace') or repr(exc)) from exc

    def q(self, command):
        self.stream.write(command.encode() + b'\n')
        reply = self.stream.readline().decode().strip()
        if not reply.startswith('OK'):
            raise RuntimeError(f'{command}: {reply}')
        return reply.split()[1] if len(reply.split()) > 1 else None

    def wl(self, offset, value):
        self.q(f'writel {REG + offset:#x} {value:#x}')

    def setup(self):
        self.q('writel 0x17a0032c 1')
        self.q('writel 0x17a00300 7')

    def transfer(self, first, data=None, *, count=1, offset=0, size=16, success=True):
        if data is not None:
            assert len(data) == count * size
            self.q(f'write {RAM:#x} {len(data):#x} 0x{data.hex()}')
        self.wl(0x138, 0xffffffff)
        self.wl(0x128, 0xffffffff)
        self.wl(4, first)
        self.wl(8, LOCAL)
        self.wl(12, 0)
        self.wl(0x400, (offset << 16) | 1)
        self.wl(0x404, (size << 16) | size)
        self.wl(0, (0x40302100 if data is not None else 0x40302200) | (count - 1))
        assert int(self.q(f'readl {REG + 0x14:#x}'), 0) == (0x8000 if success else 0xc000)
        assert int(self.q(f'readl {REG + (0x138 if success else 0x128):#x}'), 0) & 1
        if data is None and success:
            return bytes.fromhex(self.q(f'read {RAM:#x} {count * size:#x}')[2:])

    def erase(self, first, *, success=True):
        self.wl(4, first)
        self.wl(0, 0x40101000)
        assert int(self.q(f'readl {REG + 0x14:#x}'), 0) == (0x8000 if success else 0xc000)

    def state(self):
        return json.loads(self.qmp.command('qom-get', path='/machine', property='z8-nand-image-state'))

    def close(self):
        if self.qmp:
            try:
                self.qmp.command('quit')
            except Exception:
                pass
            self.qmp.close()
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self.log.close()
        self.runtime.cleanup()
        assert self.proc.poll() is not None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def run(binary, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    with tempfile.TemporaryDirectory(prefix='media-', dir=output) as tmp:
        root = Path(tmp)
        kernel = root / 'authored-zero.bin'
        kernel.write_bytes(bytes(4))
        original = sha256(kernel)
        image = root / 'nand.media'
        counter = 0

        def machine(properties=(), **kwargs):
            nonlocal counter
            counter += 1
            return Machine(binary, root / f'q{counter}', kwargs.pop('kernel', kernel), properties, **kwargs)

        def props(path=image, create=False):
            return [f'z8-nand-image={path}', *(['z8-nand-image-create=on'] if create else [])]

        def reject(properties, expected, **kwargs):
            try:
                with machine(properties, **kwargs):
                    raise AssertionError('Expected startup rejection: ' + expected)
            except RuntimeError as exc:
                assert expected in str(exc), str(exc)

        def check(name, function):
            try:
                function()
                cases.append(dict(name=name, passed=True))
            except Exception as exc:
                cases.append(dict(name=name, passed=False, error=repr(exc)))

        def ephemeral():
            for model in ['nikon-z8-bringup', 'nikon-z9-lab']:
                with machine(machine=model) as m:
                    m.setup()
                    m.transfer(7, bytes(range(16)))
                    assert m.transfer(7) == bytes(range(16))
                with machine(machine=model) as m:
                    m.setup()
                    assert m.transfer(7) == b'\xff' * 16
            assert not image.exists()
        check('disabled media preserves Z8/Z9 ephemeral NAND', ephemeral)
        check('missing image requires explicit creation', lambda: reject(props(), 'does not exist'))

        pattern = bytes(range(32))
        def program():
            with machine(props(create=True)) as m:
                m.setup()
                assert m.transfer(7, count=2, offset=2080) == b'\xff' * 32
                m.transfer(7, pattern, count=2, offset=2080)
                assert m.transfer(7, count=2, offset=2080) == pattern
                m.transfer(7, b'\x0f' * 32, count=2, offset=2080)
                assert m.transfer(7, count=2, offset=2080) == bytes(x & 15 for x in pattern)
                assert m.transfer(7, offset=0) == b'\xff' * 16
                before = image.read_bytes()
                m.transfer(63, b'\x00' * 32, count=2, success=False)
                assert image.read_bytes() == before
                state = m.state()
                assert state['enabled'] and state['pages'] == 2 and state['commits'] >= 2
            assert sha256(kernel) == original
        check('program spare bytes, NAND bitwise semantics and rejected crossing', program)

        def restart_erase():
            with machine(props()) as m:
                m.setup()
                assert m.transfer(7, count=2, offset=2080) == bytes(x & 15 for x in pattern)
                m.transfer(70, b'\x5a' * 16)
                m.erase(0)
            with machine(props()) as m:
                m.setup()
                assert m.transfer(7, count=2, offset=2080) == b'\xff' * 32
                assert m.transfer(70) == b'\x5a' * 16
        check('new process restores program and block erase', restart_erase)

        def exclusive():
            with machine(props()) as owner:
                owner.setup()
                reject(props(), 'locked')
                assert owner.transfer(70) == b'\x5a' * 16
        check('independent processes cannot share writable media', exclusive)

        def corrupt():
            valid = image.read_bytes()
            for label, offset in [('magic', 0), ('version', 8), ('reserved', 12),
                                  ('geometry', 56), ('pages', 60), ('block-pages', 64),
                                  ('count', 68), ('page-index', HEADER)]:
                modified = bytearray(valid)
                if label == 'page-index':
                    struct.pack_into('<I', modified, offset, 131072)
                else:
                    modified[offset] ^= 0x80
                modified[-32:] = hashlib.sha256(modified[:-32]).digest()
                bad = root / f'bad-{label}.media'
                bad.write_bytes(modified)
                reject(props(bad, True), 'invalid')
                assert bad.read_bytes() == modified
            bad = root / 'checksum.media'
            bad.write_bytes(valid[:-1] + bytes([valid[-1] ^ 1]))
            reject(props(bad), 'checksum')
            bad.write_bytes(valid[:-1])
            reject(props(bad), 'invalid')
            duplicate = bytearray(valid[:HEADER] + valid[HEADER:-32] * 2 + bytes(32))
            struct.pack_into('<I', duplicate, 68, 2)
            duplicate[-32:] = hashlib.sha256(duplicate[:-32]).digest()
            bad.write_bytes(duplicate)
            reject(props(bad), 'invalid')
            bad.write_bytes(bytes(16))
            reject(props(bad), 'invalid')
            modified = bytearray(valid)
            modified[24] ^= 1
            modified[-32:] = hashlib.sha256(modified[:-32]).digest()
            bad.write_bytes(modified)
            reject(props(bad), 'MAIN identity')
        check('corrupt, truncated and malformed media fail closed', corrupt)

        def source_guard():
            reject(props(kernel, True), 'source MAIN')
            hard = root / 'source-hardlink.media'
            hard.hardlink_to(kernel)
            reject(props(hard, True), 'source MAIN')
            link = root / 'source-symlink.media'
            link.symlink_to(kernel)
            reject(props(link, True), 'source MAIN')
            assert sha256(kernel) == original
        check('source MAIN and aliases cannot become writable media', source_guard)

        def identity():
            other = root / 'other-zero.bin'
            other.write_bytes(bytes(8))
            reject(props(), 'MAIN identity', kernel=other)
            reject(props(), 'requires Z8', machine='nikon-z9-lab')
            reject(['z8-nand-image-create=on'], 'requires z8-nand-image')
            reject([*props(), 'nand-fixture=off'], 'requires bootstrap-fixtures and nand-fixture')
            reject(['z8-nand-image=relative.media'], 'absolute independent media path')
            reject(props(), 'readable regular source MAIN', kernel=root / 'missing-main.bin')
            reject(props(), 'readable regular source MAIN', kernel=root)
        check('media identity and machine opt-in boundaries', identity)

        def io_failure():
            with machine(props()) as m:
                m.setup()
                old = image.read_bytes()
                backup = root / 'held.media'
                image.rename(backup)
                image.mkdir()
                m.transfer(70, b'\x00' * 16, success=False)
                assert m.transfer(70) == b'\x5a' * 16
                assert m.state()['failed_commits'] == 1
                image.rmdir()
                backup.rename(image)
                assert image.read_bytes() == old
            with machine(props()) as m:
                m.setup()
                assert m.transfer(70) == b'\x5a' * 16
        check('failed media commit reports NAND error and rolls back RAM', io_failure)

        def disk_write_failure():
            import resource
            limited = root / 'limited.media'
            with machine(props(limited, True), ignore_file_size_signal=True) as m:
                m.setup()
                initial = limited.read_bytes()
                old = resource.prlimit(m.proc.pid, resource.RLIMIT_FSIZE)
                try:
                    resource.prlimit(m.proc.pid, resource.RLIMIT_FSIZE, (1024, old[1]))
                    m.transfer(9, b'\x12' * 16, success=False)
                finally:
                    resource.prlimit(m.proc.pid, resource.RLIMIT_FSIZE, old)
                assert m.transfer(9) == b'\xff' * 16
                assert limited.read_bytes() == initial
                assert m.state()['failed_commits'] == 1
            with machine(props(limited)) as m:
                m.setup()
                assert m.transfer(9) == b'\xff' * 16
        check('short file-write failure preserves durable image and absent page', disk_write_failure)

        def immutable():
            with machine(props()) as m:
                for name, value in [('z8-nand-image', str(root / 'other.media')),
                                    ('z8-nand-image-create', True)]:
                    try:
                        m.qmp.command('qom-set', path='/machine', property=name, value=value)
                        raise AssertionError('Media property changed after realization')
                    except RuntimeError as exc:
                        assert 'machine creation' in str(exc)
                assert not (root / 'other.media').exists()
        check('media properties are immutable after machine creation', immutable)
        assert sha256(kernel) == original
    return dict(classification='QTest persistent logical NAND, not C10 acceptance',
                firmware_used=False, cpu_execution=False, physical_hardware_verified=False,
                qemu_sha256=sha256(binary), cases=cases, passed=all(x['passed'] for x in cases))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name == 'nt':
        parser.error('Run QTest in the selected Linux/WSL environment')
    result = run(args.qemu.resolve(), args.output)
    (args.output / 'qtest-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
