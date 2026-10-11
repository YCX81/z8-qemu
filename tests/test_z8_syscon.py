# SPDX-License-Identifier: GPL-2.0-or-later
"""Check bounded Z8 syscon retention through real QTest MMIO transactions.

The CPU stays paused and the four-byte kernel is authored zero data. These
tests establish emulator behavior, not Nikon firmware or physical acceptance.
"""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from check_z8_machine import QMP, sha256

ADDRESSES = (0x0d152000, 0x0d152008, 0x0d152010, 0x0d152018)
VALUES = (0x9e, 0x11223344, 0x8899aabb, 0xdeadbeef)


def transaction(stream, command):
    stream.write(command.encode() + b'\n')
    reply = stream.readline().decode().strip()
    if not reply.startswith('OK'):
        raise RuntimeError(f'QTest {command}: {reply}')
    return int(reply.split()[1], 0) if len(reply.split()) > 1 else None


def case(binary, name, *, machine='nikon-z8-bringup', bootstrap=True, operation=None,
         steps=None, extra_properties=()):
    result = dict(name=name, machine=machine, bootstrap=bootstrap, error=None,
                  cpu_execution=False, firmware_used=False)
    qmp = sock = stream = proc = None
    started = time.monotonic()
    deadline = started + 5
    with tempfile.TemporaryDirectory(prefix='z8-syscon-') as folder:
        root = Path(folder)
        kernel = root / 'authored-zero.bin'
        kernel.write_bytes(bytes(4))
        errors = root / 'stderr.log'
        target = machine + (',bootstrap-fixtures=on' if bootstrap else '')
        if extra_properties:
            target += ',' + ','.join(extra_properties)
        command = [str(binary), '-M', target, '-kernel', str(kernel), '-smp', '4',
                   '-display', 'none', '-serial', 'none', '-monitor', 'none', '-S',
                   '-qmp', f'unix:{root}/qmp.sock,server=on,wait=off',
                   '-qtest', f'unix:{root}/qtest.sock,server=on,wait=off',
                   '-qtest-log', '/dev/null']
        try:
            with errors.open('xb') as log:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=log)
                while not (root / 'qtest.sock').exists():
                    if proc.poll() is not None:
                        raise RuntimeError('Owned QEMU exited before QTest startup')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('QTest startup exceeded five seconds')
                    time.sleep(.01)
                qmp = QMP(root / 'qmp.sock', deadline)
                sock = socket.socket(socket.AF_UNIX)
                sock.settimeout(1)
                sock.connect(str(root / 'qtest.sock'))
                stream = sock.makefile('rwb', buffering=0)
                if steps is not None:
                    result['transactions'] = []
                    for command, expected in steps:
                        actual = transaction(stream, command)
                        result['transactions'].append(dict(command=command, expected=expected, actual=actual))
                        if actual != expected:
                            raise AssertionError(f'{command}: expected {expected}, got {actual}')
                elif operation is None:
                    for address, value in zip(ADDRESSES, VALUES):
                        transaction(stream, f'writel {address:#x} {value:#x}')
                    actual = [transaction(stream, f'readl {address:#x}') for address in ADDRESSES]
                    result['initial_readback'] = actual
                    if actual != list(VALUES):
                        raise AssertionError('Four independent words did not retain their values')
                    changed = actual[0] ^ 0x100
                    transaction(stream, f'writel {ADDRESSES[0]:#x} {changed:#x}')
                    result['after_software_rmw'] = [transaction(stream, f'readl {address:#x}')
                                                    for address in ADDRESSES]
                    if result['after_software_rmw'] != [changed, *VALUES[1:]]:
                        raise AssertionError('Software RMW corrupted an independent slot')
                else:
                    result['operation'] = operation
                    verb, address, width = operation
                    command = f'{verb} {address:#x}'
                    if verb.startswith('write'):
                        command += ' 0x9e'
                    transaction(stream, command)
                result['status'] = qmp.command('query-status')
                result['stderr'] = errors.read_text(errors='replace')
                if result['status']['running']:
                    raise AssertionError('Synthetic QTest unexpectedly ran a CPU')
                if operation is None:
                    if 'Z9_UNKNOWN' in result['stderr']:
                        raise AssertionError('Allowed configuration word reached strict stop')
                else:
                    match = re.search(r'Z9_UNKNOWN .*? (read|write) addr=0x([0-9a-f]+) size=(\d+)', result['stderr'])
                    direction = 'write' if operation[0].startswith('write') else 'read'
                    if not match or (match[1], int(match[2], 16), int(match[3])) != (direction, operation[1], operation[2]):
                        raise AssertionError('Rejected access did not reach exact strict-stop boundary')
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            if qmp:
                try:
                    qmp.command('quit')
                except Exception as exc:
                    result['quit_error'] = repr(exc)
                qmp.close()
            if stream:
                stream.close()
            if sock:
                sock.close()
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=.5)
            result['owned_qemu_stopped'] = proc is None or proc.poll() is not None
            result['returncode'] = proc.returncode if proc else None
            result['elapsed_seconds'] = time.monotonic() - started
            if errors.exists():
                result['stderr'] = errors.read_text(errors='replace')
            result['passed'] = result['error'] is None and result['owned_qemu_stopped'] and result['returncode'] == 0
    return result


def run(binary):
    binary = Path(binary).resolve()
    cases = [case(binary, 'four independent words and software RMW')]
    for verb, width in (('readb', 1), ('readw', 2), ('readq', 8),
                        ('writeb', 1), ('writew', 2), ('writeq', 8)):
        cases.append(case(binary, f'reject {verb}', operation=(verb, ADDRESSES[0], width)))
    # 0x0d152014 is an existing, separately supported divider register.
    for address in (0x0d151ffc, 0x0d152004, 0x0d15200c, 0x0d15201c, 0x0d152020):
        for verb in ('readl', 'writel'):
            cases.append(case(binary, f'reject neighboring {verb} {address:#x}', operation=(verb, address, 4)))
    for address in ADDRESSES:
        cases.append(case(binary, f'bootstrap off {address:#x}', bootstrap=False,
                          operation=('writel', address, 4)))
        cases.append(case(binary, f'original Z9 unchanged {address:#x}', machine='nikon-z9-lab',
                          operation=('writel', address, 4)))
    for address in (0x0d150158, 0x0d15015c, 0x0d150160, 0x0d150258, 0x0d15025c, 0x0d150260):
        cases.append(case(binary, f'ZR-only word remains unavailable {address:#x}',
                          operation=('writel', address, 4)))
    return dict(classification='synthetic_qtest_syscon_retention', firmware_used=False,
                cpu_execution=False, menu_verified=False, physical_hardware_verified=False,
                qemu_sha256=sha256(binary), source_sha256=sha256(Path(__file__).resolve()),
                cases=cases, passed=all(item['passed'] for item in cases))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.qemu)
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
