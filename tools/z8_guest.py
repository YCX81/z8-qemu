# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity-bound Z8 guest with the shared QMP, QTest and RSP transport.

Device properties are explicit functional hypotheses. This owner neither
loads Z9 resource fixtures nor patches firmware, task flags or CPU registers.
"""
import json
import os
from pathlib import Path
import re
import tempfile

from guest import Guest as SharedOwner
from probe import sha
from z8_bringup import load_profile, validate_firmware

ROOT = Path(__file__).resolve().parents[1]


def validate_properties(properties):
    supported = {item.split('=', 1)[0] for item in
                 json.loads((ROOT / 'tools/model-properties.json').read_text())['properties']}
    supported.add('z8-sd-empty-fixture')
    seen = set()
    result = []
    for item in properties:
        if not isinstance(item, str) or not re.fullmatch(r'[a-z0-9-]+=[^,\s\x00]+', item):
            raise ValueError('Malformed functional device property')
        name = item.split('=', 1)[0]
        if name not in supported or name in seen:
            raise ValueError('Unknown or duplicate functional device property: ' + name)
        seen.add(name)
        result.append(item)
    return result


class Guest(SharedOwner):
    def __init__(self, firmware, binary, raw, out, *, properties, instruction_clock=True):
        if os.name == 'nt':
            raise ValueError('Use the selected Linux/WSL environment')
        profile = load_profile()
        self.firmware = Path(firmware).resolve()
        identity = validate_firmware(self.firmware, profile)
        properties = validate_properties(properties)
        binary, raw = Path(binary).resolve(), Path(raw).resolve()
        binary_sha, raw_sha = sha(binary), sha(raw)
        if ',' in str(raw):
            raise ValueError('RAW input path cannot contain a comma')
        if 'statistics-model=on' in properties:
            properties += ['statistics-raw=' + str(raw)]
        self.out = Path(out).resolve()
        if ',' in str(self.out):
            raise ValueError('Output path cannot contain a comma in QEMU loader options')
        self.out.mkdir(parents=True, exist_ok=False)
        self.temp = tempfile.TemporaryDirectory(prefix='wl-z8-runtime-')
        self.runtime = Path(self.temp.name)
        self.log_path = self.out / 'stderr.log'
        self.qtest_log_path = Path('/dev/null')
        self.ram_mib = 1024
        self.accelerator = None
        self.intercept_irq = False
        self.command = [str(binary), '-M', ','.join(['nikon-z8-bringup', *properties]),
                        '-kernel', str(self.firmware), '-accel', 'tcg,thread=single']
        if instruction_clock:
            self.command += ['-icount', 'shift=0,sleep=on']
        self.command += ['-smp', '4', '-m', '1024M', '-display', 'none', '-serial', 'none',
                         '-monitor', 'none', '-S', '-no-reboot',
                         '-qmp', f'unix:{self.runtime}/qmp.sock,server=on,wait=off',
                         '-gdb', f'unix:{self.runtime}/gdb.sock,server=on,wait=off',
                         '-qtest', f'unix:{self.runtime}/qtest.sock,server=on,wait=off',
                         '-qtest-log', '/dev/null']
        self.data = dict(checks=[], irq_events=[], firmware_sha256=identity['sha256'],
                         qemu_sha256=binary_sha, raw_sha256=raw_sha, profile=profile,
                         machine_properties=properties,
                         classification='z8_functional_guest_with_explicit_device_hypotheses',
                         cpu_register_redirection=False, ram_patches=[],
                         menu_verified=False, liveview_verified=False, physical_hardware_verified=False)
        self.data['runtime_profile'] = dict(instruction_clock=bool(instruction_clock),
                                            service_core_events='service-core-event-model=on' in properties,
                                            cycle_accurate=False, firmware_timeouts_changed=False)
        self.proc = self.qmp = self.qt = self.fp = self.gdb = None
        self.irq = {}
        self._log_offset = 0
        self._fault = False

    def __exit__(self, kind, error, traceback):
        cancelled=getattr(self,'cancel_event',None)
        if kind is not None and issubclass(kind,InterruptedError) and cancelled is not None and cancelled.is_set():
            self.data['cancelled']=True
        if self.proc is not None and self.proc.poll() is None and self.qmp is not None:
            try:
                self.qmp.command('stop')
                self.data['final_status'] = self.qmp.command('query-status')
                self.data['final_registers'] = {str(cpu): self.qmp.hmp('info registers', cpu)
                                                for cpu in range(4)}
                for prop in ('serial-state', 'runtime-clock-state', 'engine1548-scanout-state',
                             'raw-pipeline-state'):
                    self.data[prop] = self.qmp.command('qom-get', path='/machine', property=prop)
                self.qmp.command('screendump', filename=str(self.out / 'final.ppm'))
            except Exception as exc:
                self.data['final_capture_error'] = repr(exc)
        try:
            self.data['input_unchanged'] = sha(self.firmware) == self.data['firmware_sha256']
        except OSError:
            self.data['input_unchanged'] = False
        return super().__exit__(kind, error, traceback)
