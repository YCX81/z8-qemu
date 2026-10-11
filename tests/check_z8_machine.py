# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute authored AArch64 code to check the real Z8 QOM/strict-stop boundary.

No Nikon firmware is loaded. This test intentionally invokes the underlying
machine directly; the production Z8 harness continues to reject synthetic code.
The instruction encodings were independently checked with Capstone 5.0.7.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time

ENTRY = 0x40160000
UNKNOWN_ADDRESS = 0x12345600
MACHINE = 'nikon-z8-bringup'
PAYLOAD = struct.pack('<4IQ',
                      0x58000080,   # ldr x0, [PC + 16]: literal UNKNOWN_ADDRESS
                      0xb9400001,   # ldr w1, [x0]: deliberately unmodeled read
                      0x14000000,   # b .: stop-position guard, no further MMIO
                      0xd503201f,   # nop
                      UNKNOWN_ADDRESS)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class QMP:
    def __init__(self, path, deadline):
        self.deadline = deadline
        self.socket = socket.socket(socket.AF_UNIX)
        self.stream = None
        self.events = []
        try:
            self.timeout()
            self.socket.connect(str(path))
            self.stream = self.socket.makefile('rwb', buffering=0)
            self.greeting = self.read()
            if 'QMP' not in self.greeting:
                raise RuntimeError('Missing QMP greeting from owned process')
            self.command('qmp_capabilities')
        except BaseException:
            self.close()
            raise

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Five-second synthetic test deadline reached')
        self.socket.settimeout(min(1, remaining))

    def read(self):
        self.timeout()
        line = self.stream.readline()
        if not line:
            raise EOFError('Owned process closed QMP')
        return json.loads(line)

    def command(self, operation, **arguments):
        self.timeout()
        self.stream.write(json.dumps(dict(execute=operation, arguments=arguments)).encode() + b'\n')
        while True:
            item = self.read()
            if 'event' in item:
                self.events.append(item)
            elif 'error' in item:
                raise RuntimeError(f'{operation}: {item["error"]}')
            elif 'return' in item:
                return item['return']

    def registers(self, cpu=0):
        return self.command('human-monitor-command', **{'command-line': 'info registers',
                                                       'cpu-index': cpu})

    def close(self):
        if self.stream:
            self.stream.close()
        self.socket.close()


def pc(registers):
    match = re.search(r'\bPC=([0-9a-fA-F]+)', registers)
    if not match:
        raise ValueError('CPU register evidence has no PC')
    return int(match[1], 16)


def check_machine(binary):
    binary = Path(binary).resolve()
    result = dict(classification='authored_synthetic_machine_test', checks=[],
                  firmware_used=False, menu_verified=False, camera_acceptance=False,
                  physical_hardware_verified=False, machine=MACHINE, machine_properties=[],
                  synthetic_payload_hex=PAYLOAD.hex(),
                  synthetic_payload_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                  test_source_sha256=sha256(Path(__file__).resolve()),
                  qemu=str(binary), qemu_sha256=sha256(binary),
                  timeout_seconds=5, error=None)

    def check(name, condition):
        result['checks'].append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)

    proc = qmp = None
    started = time.monotonic()
    deadline = started + 5
    with tempfile.TemporaryDirectory(prefix='z8-machine-check-') as folder:
        root = Path(folder)
        payload = root / 'authored-input.bin'
        payload.write_bytes(PAYLOAD)
        errors = root / 'stderr.log'
        command = [str(binary), '-M', MACHINE, '-kernel', str(payload),
                   '-accel', 'tcg,thread=single', '-smp', '4', '-display', 'none',
                   '-serial', 'none', '-monitor', 'none', '-S', '-no-reboot',
                   '-qmp', f'unix:{root}/qmp.sock,server=on,wait=off']
        result['command'] = command
        try:
            with errors.open('xb') as stream:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=stream)
                result['owned_pid'] = proc.pid
                while not (root / 'qmp.sock').exists():
                    if proc.poll() is not None:
                        raise RuntimeError('Owned QEMU exited before machine/QMP initialization')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Five-second synthetic test startup deadline reached')
                    time.sleep(.01)
                qmp = QMP(root / 'qmp.sock', deadline)
                machines = qmp.command('query-machines')
                check('nikon-z8-bringup is registered', any(m['name'] == MACHINE for m in machines))
                result['initial_status'] = qmp.command('query-status')
                check('guest begins paused', not result['initial_status']['running'])
                result['initial_cpu0_registers'] = qmp.registers()
                check('CPU0 starts at authored entry', pc(result['initial_cpu0_registers']) == ENTRY)
                result['rvbar'] = qmp.command('qom-get', path='/machine/cpu0', property='rvbar')
                check('CPU0 RVBAR is the expected entry', result['rvbar'] == ENTRY)
                result['bootstrap_fixture'] = qmp.command('qom-get', path='/machine', property='bootstrap-fixtures')
                check('bootstrap fixture defaults off', result['bootstrap_fixture'] is False)
                qmp.command('cont')
                while True:
                    status = qmp.command('query-status')
                    if not status['running']:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Strict MMIO stop did not occur within five seconds')
                    time.sleep(.01)
                result['final_status'] = status
                result['final_cpu0_registers'] = qmp.registers()
                result['stderr'] = errors.read_text(errors='replace')
                match = re.search(r'Z9_UNKNOWN cpu=(\d+) pc=0x([0-9a-f]+) '
                                  r'(read|write) addr=0x([0-9a-f]+) size=(\d+)', result['stderr'])
                check('strict parent handler logged the unknown access', match is not None)
                result['access'] = dict(cpu=int(match[1]), pc=int(match[2], 16),
                                        direction=match[3], address=int(match[4], 16), size=int(match[5]))
                check('unknown access is CPU0 read at the authored instruction',
                      result['access']['cpu'] == 0 and result['access']['pc'] == ENTRY + 4
                      and result['access']['direction'] == 'read')
                check('unknown address and access width match the authored load',
                      result['access']['address'] == UNKNOWN_ADDRESS and result['access']['size'] == 4)
                check('VM stopped in debug state', status['status'] == 'debug')
                check('CPU0 remains at the guard after the completed MMIO instruction',
                      pc(result['final_cpu0_registers']) == ENTRY + 8)
                check('no bootstrap fixture log was emitted', 'Z9_FIXTURE' not in result['stderr'])
                check('authored instructions remain unchanged', payload.read_bytes() == PAYLOAD)
        except Exception as exc:
            result['error'] = repr(exc)
        finally:
            if qmp:
                try:
                    qmp.command('quit')
                except Exception as exc:
                    result['quit_error'] = repr(exc)
                result['qmp_events'] = list(qmp.events)
                qmp.close()
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=max(.01, min(.5, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    proc.terminate()
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
            result['passed'] = (result['error'] is None and result['owned_qemu_stopped']
                                and result['returncode'] == 0)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check_machine(args.qemu)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
