# SPDX-License-Identifier: GPL-2.0-or-later
"""Run only the pinned Z8 MAIN until an observed stop or a bounded time limit.

This is early bring-up on inherited Z9 mappings. No Z9 ROM fixture, MCU peer,
firmware edit, CPU redirection, camera connection or menu claim is supplied.
The optional bootstrap MMIO fixture supplies unmeasured Z9-derived inputs.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'profiles' / 'z8-c311.json'
MACHINE = 'nikon-z8-bringup'
ENTRY = 0x40160000


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def address(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def validate_profile(profile):
    for field in ('model', 'version'):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise ValueError(f'Profile requires {field}')
    if type(profile.get('bytes')) is not int or not 4 <= profile['bytes'] <= 128 * 1024**2 - 0x160000:
        raise ValueError('Invalid profile bytes')
    if not isinstance(profile.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', profile['sha256']):
        raise ValueError('Invalid profile SHA-256')
    if any(address(profile.get(field, 0)) != ENTRY for field in ('load_address', 'entry_point')):
        raise ValueError('Inherited board requires load and entry at 0x40160000')
    prefix = profile.get('entry_prefix_hex', '')
    if not isinstance(prefix, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{8})+', prefix):
        raise ValueError('Profile requires a nonempty aligned entry fingerprint')
    if len(prefix) // 2 > profile['bytes']:
        raise ValueError('Profile entry fingerprint exceeds payload')
    return profile


def load_profile():
    # Deliberately no CLI profile override or identity-bypass option.
    return validate_profile(json.loads(PROFILE.read_text(encoding='utf-8')))


def validate_firmware(path, profile):
    validate_profile(profile)
    path = Path(path)
    if path.stat().st_size != profile['bytes']:
        raise ValueError('Firmware size differs from the pinned Z8 MAIN')
    digest = sha256(path)
    if digest != profile['sha256']:
        raise ValueError('Firmware SHA-256 differs from the pinned Z8 MAIN')
    expected = bytes.fromhex(profile['entry_prefix_hex'])
    with path.open('rb') as stream:
        if stream.read(len(expected)) != expected:
            raise ValueError('Firmware entry fingerprint differs from the profile')
    return dict(bytes=path.stat().st_size, sha256=digest)


def validate_runtime(seconds):
    if not math.isfinite(seconds) or not .1 <= seconds <= 120:
        raise ValueError('Runtime must be between 0.1 and 120 seconds')
    return seconds


def create_output(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


class QMP:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(2)
        self.events = []
        self.stream = None
        try:
            self.socket.connect(str(path))
            self.stream = self.socket.makefile('rwb', buffering=0)
            self.greeting = self.read()
            if 'QMP' not in self.greeting:
                raise RuntimeError('Missing QMP greeting')
            self.command('qmp_capabilities')
        except BaseException:
            self.close()
            raise

    def read(self):
        line = self.stream.readline()
        if not line:
            raise EOFError('Owned QEMU disconnected from QMP')
        return json.loads(line)

    def command(self, operation, **arguments):
        self.stream.write(json.dumps(dict(execute=operation, arguments=arguments)).encode() + b'\n')
        while True:
            message = self.read()
            if 'event' in message:
                self.events.append(message)
            elif 'error' in message:
                raise RuntimeError(f'QMP {operation}: {message["error"]}')
            elif 'return' in message:
                return message['return']

    def registers(self):
        return {str(cpu['cpu-index']): self.command('human-monitor-command', **{
            'command-line': 'info registers', 'cpu-index': cpu['cpu-index']})
            for cpu in self.command('query-cpus-fast')}

    def close(self):
        if self.stream:
            self.stream.close()
        self.socket.close()


def run(firmware, binary, output, seconds, *, bootstrap_fixture=False, bus_window_fixture=False,
        serial_fixture=False, nand_fixture=False):
    seconds = validate_runtime(seconds)
    if any(type(value) is not bool for value in (bootstrap_fixture, bus_window_fixture, serial_fixture, nand_fixture)):
        raise ValueError('Fixture options must be explicit booleans')
    if nand_fixture and not bootstrap_fixture:
        raise ValueError('--nand-fixture requires --bootstrap-fixture')
    if serial_fixture and not (bootstrap_fixture and bus_window_fixture):
        raise ValueError('--serial-fixture requires --bootstrap-fixture and --bus-window-fixture')
    if bus_window_fixture and not bootstrap_fixture:
        raise ValueError('--bus-window-fixture requires --bootstrap-fixture')
    profile = load_profile()
    firmware, binary = Path(firmware).resolve(), Path(binary).resolve()
    identity = validate_firmware(firmware, profile)
    binary_sha = sha256(binary)
    output = create_output(output)
    source_paths = [Path(__file__).resolve(), ROOT / 'qemu/nikon-z9-lab.c', PROFILE]
    properties = ['bootstrap-fixtures=on'] if bootstrap_fixture else []
    if bus_window_fixture:
        properties.append('bus-window-fixture=on')
    if serial_fixture:
        properties.append('serial-fixture=on')
    if nand_fixture:
        properties.append('nand-fixture=on')
    assumptions = [
        'CPU, GIC, RAM windows, boot DMA, UART and basic setup behavior are inherited '
        'Z9 model hypotheses, not independently measured Z8 hardware.',
        'A static match of firmware register accesses supports a bounded experiment; '
        'it does not establish physical register values or hardware compatibility.',
        'Unknown accesses retain the parent model strict stop; no menu, camera or '
        'physical sensor acceptance follows from execution progress.',
    ]
    if bootstrap_fixture:
        assumptions.extend([
            'Explicit bootstrap-fixtures=on experiment: inherited Z9 unmeasured SoC '
            'revision/strap inputs include 0x0d150000=0x10000000 and 0x0d151060=0.',
            'The same parent fixture also permits its bounded syscon/bus retention '
            'and modeled setup/status behavior, including unmeasured memory-controller '
            'mode 0x1180002c=0, secondary SoC identification 0x0d15000c=0 and '
            'system-probe input 0x0d153000=0. This flag enables that entire inherited '
            'bootstrap fixture, not only the first revision-register read.',
            'Only the options listed in machine_properties are enabled. No '
            'Z9 model-properties profile, virtual ROM input or MCU peer is loaded.',
        ])
    else:
        assumptions.append('No optional bootstrap fixture is enabled; the first '
                           'unmodeled access is preserved as the default baseline.')
    if bus_window_fixture:
        assumptions.extend([
            'Z8 C3.11 MAIN static evidence: the 17 x 24-byte descriptor table at '
            '0x44b7dcb4 (SHA-256 f07fcbfdd7b322afcd91a9cd2522e4d4f7e986d165affb0728a2478f108c71a8), '
            'used by query function 0x41185b94, matches the inherited window register '
            'bases and effective slot counts. Paired groups 3/4/10 use mask 0x418.',
            'bus-window-fixture=on supplies inherited experimental initial values: '
            'default 128 MiB apertures, with 512 MiB ranges for groups 1/2/15. '
            'These bootloader initial values are not recovered from MAIN; descriptor '
            'layout agreement does not verify those values, physical DRAM capacity, '
            'transfer behavior or Z8 hardware operation.',
        ])
    if serial_fixture:
        assumptions.extend([
            'serial-fixture=on enables inherited functional FIFO/control behavior '
            'for all 11 serial channels and paired-transfer semantics for all 24 PDMA '
            'channels; it is not restricted to the first observed channel 7 request.',
            'The 64-word FIFO capacity, synchronous prefetch/progress and IRQ mappings '
            'are inherited model assumptions with unmeasured physical timing. '
            'Enabling a supported transfer may prefetch original guest bytes and '
            'update TX DMA progress; this does not establish serial communication.',
            'This runner supplies no serial host clock, no external replies and no '
            'MCU/peripheral peer model. It never writes serial-clock or supplies RX '
            'payloads. Peripheral replies and communication completion remain unverified.',
        ])
    if nand_fixture:
        assumptions.extend([
            'nand-fixture=on supplies a virtual blank NAND: 1 LUN, 2048 blocks, '
            '64 pages/block, 2048 main + 64 spare bytes/page; virtual IDs 0x00da00ec. '
            'Unread pages return 0xff, writes only clear bits in volatile host RAM, '
            'and block erase discards those pages. There is no real flash dump, '
            'persistent camera storage, recovered calibration or hardware connection.',
            'Z8 static evidence: 0x40c5d244 registers IRQ 0x89 through handler '
            '0x40c5d270 -> 0x40c5e2a0; the ISR acknowledges 0x17800110/128/138/14c '
            'using W1C and consumes status at 0x17800014. 0x40c5e398 tests ready bit 9; '
            '0x40c5e640..668 configures global IRQ enable and error masks. This matches '
            'the inherited register contract without establishing physical flash geometry.',
            'NAND commands and DMA finish synchronously in the inherited model; '
            'DLL training is instantaneous and BCH correction, worn/bad blocks and '
            'physical timing are absent. Final status reads are diagnostic observations, '
            'not by themselves proof that a guest ISR executed or a camera booted.',
        ])
    result = dict(classification='z8_early_bringup_with_inherited_z9_hardware_hypotheses',
                  profile=profile, firmware=str(firmware), firmware_identity=identity,
                  qemu=str(binary), qemu_sha256=binary_sha,
                  sources_sha256={str(path): sha256(path) for path in source_paths},
                  machine=MACHINE, machine_properties=properties, parent_machine='nikon-z9-lab',
                  assumptions=assumptions,
                  unknown_log_marker='Z9_UNKNOWN (inherited strict parent handler)',
                  ram_mib=1024, cpus=4, runtime_limit_seconds=seconds,
                  menu_verified=False, camera_acceptance=False, physical_hardware_verified=False,
                  firmware_patches=[], guest_ram_patches=[], cpu_register_redirection=False,
                  virtual_rom_inputs=[], stop_reason='starting', error=None,
                  serial_host_clock_supplied=False, serial_communication_verified=False,
                  nand_flash_dump_loaded=False,
                  initial_cpu_registers={}, final_cpu_registers={}, qmp_events=[])
    proc = qmp = None
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='wl-z8-') as folder:
            runtime = Path(folder)
            snapshot = runtime / 'verified-main.bin'
            # QEMU receives an identical, revalidated snapshot, closing the
            # identity-check/load race without writing the user's input.
            shutil.copyfile(firmware, snapshot)
            result['snapshot_identity'] = validate_firmware(snapshot, profile)
            command = [str(binary), '-M', ','.join([MACHINE, *properties]), '-kernel', str(snapshot),
                       '-accel', 'tcg,thread=single', '-smp', '4', '-m', '1024M',
                       '-display', 'none', '-serial', 'none', '-monitor', 'none',
                       '-S', '-no-reboot', '-qmp', f'unix:{runtime}/qmp.sock,server=on,wait=off']
            result['command'] = command
            try:
                with (output / 'stderr.log').open('xb') as errors, (output / 'stdout.log').open('xb') as stdout:
                    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=errors)
                    result['owned_pid'] = proc.pid
                    startup_deadline = time.monotonic() + 10
                    while not (runtime / 'qmp.sock').exists():
                        if proc.poll() is not None:
                            raise RuntimeError('Owned QEMU exited before QMP became ready')
                        if time.monotonic() >= startup_deadline:
                            raise TimeoutError('Owned QEMU QMP startup timeout')
                        time.sleep(.02)
                    qmp = QMP(runtime / 'qmp.sock')
                    result['qmp_greeting'] = qmp.greeting
                    result['initial_status'] = qmp.command('query-status')
                    result['initial_cpu_registers'] = qmp.registers()
                    execution_started = time.monotonic()
                    qmp.command('cont')
                    while True:
                        status = qmp.command('query-status')
                        if not status['running']:
                            result['stop_reason'] = 'guest_stopped'
                            break
                        if time.monotonic() - execution_started >= seconds:
                            result['stop_reason'] = 'runtime_limit'
                            qmp.command('stop')
                            break
                        time.sleep(.02)
                    result['execution_wall_seconds'] = time.monotonic() - execution_started
            finally:
                # Freeze before collecting evidence. Never continue a strict
                # unknown-access stop and never target a process we did not own.
                if qmp and proc.poll() is None:
                    try:
                        if qmp.command('query-status')['running']:
                            qmp.command('stop')
                        result['final_status'] = qmp.command('query-status')
                        result['final_cpu_registers'] = qmp.registers()
                        if nand_fixture:
                            # All selected registers have side-effect-free modeled
                            # reads. Do not write masks/status or service an IRQ.
                            nand_status = {'command_status': 0x14, 'interrupt_enable': 0x114,
                                           'error_pending': 0x128, 'complete_pending': 0x138,
                                           'other_pending': 0x14c, 'error_mask': 0x130,
                                           'other_mask': 0x154}
                            result['final_nand_status'] = {
                                name: qmp.command('human-monitor-command', **{
                                    'command-line': f'xp /1wx {0x17800000 + offset:#x}'})
                                for name, offset in nand_status.items()}
                    except Exception as exc:
                        result['capture_error'] = repr(exc)
                    try:
                        qmp.command('quit')
                    except Exception as exc:
                        result['quit_error'] = repr(exc)
                if proc and proc.poll() is None:
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=2)
                if qmp:
                    result['qmp_events'] = list(qmp.events)
                    qmp.close()
    except KeyboardInterrupt:
        result['stop_reason'] = 'user_interrupted'
    except Exception as exc:
        result['stop_reason'] = 'runner_error'
        result['error'] = repr(exc)
    finally:
        result['owned_qemu_stopped'] = proc is None or proc.poll() is not None
        result['process_returncode'] = proc.returncode if proc else None
        result['total_wall_seconds'] = time.monotonic() - started
        try:
            result['input_unchanged'] = sha256(firmware) == identity['sha256']
        except OSError as exc:
            result['input_unchanged'] = False
            result['input_recheck_error'] = repr(exc)
        log_path = output / 'stderr.log'
        if log_path.exists():
            result['unknown_accesses'] = [line for line in log_path.read_text(errors='replace').splitlines()
                                         if 'Z9_UNKNOWN' in line or 'Z9_ACCESS_CONTEXT' in line]
            if result['unknown_accesses'] and result['error'] is None:
                result['stop_reason'] = 'unknown_mmio'
        (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True, help='Exact extracted Z8 C3.11 MAIN')
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private evidence directory')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--bootstrap-fixture', action='store_true',
                        help='Explicit experiment with inherited, unmeasured Z9 bootstrap MMIO inputs')
    parser.add_argument('--bus-window-fixture', action='store_true',
                        help='Explicit inherited initial-window experiment; requires --bootstrap-fixture')
    parser.add_argument('--serial-fixture', action='store_true',
                        help='Inherited serial/PDMA model only, no peer or clocks; requires both other fixture options')
    parser.add_argument('--nand-fixture', action='store_true',
                        help='Volatile blank virtual NAND, no camera flash dump; requires --bootstrap-fixture')
    args = parser.parse_args()
    if os.name == 'nt':
        parser.error('Run this QEMU harness in Linux or the selected Nikon WSL distribution')
    try:
        result = run(args.firmware, args.qemu, args.output, args.seconds,
                     bootstrap_fixture=args.bootstrap_fixture,
                     bus_window_fixture=args.bus_window_fixture,
                     serial_fixture=args.serial_fixture,
                     nand_fixture=args.nand_fixture)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(stop_reason=result['stop_reason'], menu_verified=False,
                          result=str(args.output.resolve() / 'result.json'))))
    if result['error'] or not result['owned_qemu_stopped'] or not result['input_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
