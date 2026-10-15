# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity-bound Z8 guest with the shared QMP, QTest and RSP transport.

Device properties are explicit functional hypotheses. This owner neither
loads Z9 resource fixtures nor patches firmware, task flags or CPU registers.
"""
import os
from pathlib import Path
import re
import tempfile

from guest import Guest as SharedOwner
from probe import sha
from z8_bringup import load_profile, validate_firmware
from z8_properties import load_model_profile, verification_scope

ROOT = Path(__file__).resolve().parents[1]


def media_arguments(image, firmware, *, create=False):
    """Prepare an explicit independent NAND image; never create it here."""
    if image is None:
        if create:
            raise ValueError('Creating media requires an explicit NAND image')
        return []
    image, firmware = Path(image).resolve(), Path(firmware).resolve()
    if ',' in str(image) or '\x00' in str(image):
        raise ValueError('NAND image path cannot contain a comma or NUL')
    if image == firmware or (image.exists() and os.path.samefile(image, firmware)):
        raise ValueError('NAND media must be independent from the original MAIN')
    if create and image.exists():
        raise FileExistsError('Refusing to create over an existing NAND image')
    if not create and not image.is_file():
        raise FileNotFoundError('NAND image does not exist; request creation explicitly')
    if not image.parent.is_dir():
        raise FileNotFoundError('NAND image parent directory does not exist')
    return ['z8-nand-image=' + str(image)] + (['z8-nand-image-create=on'] if create else [])


def validate_properties(properties):
    supported = {item.split('=', 1)[0] for item in load_model_profile()['properties']}
    # Explicit opt-in for portrait experiments; no change to the default model
    # profile or claim about physical GPU precision.
    supported.add('z8-secondary-resample-model')
    supported.add('z8-video-interface-model')
    supported.add('z8-video-ring-stop-model')
    supported.add('raw-pipeline-151c-completion-model')
    supported.add('raw-pipeline-1514-completion-model')
    supported.add('raw-source-boundary-model')
    supported.add('raw-pipeline-input-pair-model')
    supported.add('z8-video-data-model')
    supported.update(('video-processing-config-model','video-initial-reset-model','video-nr-reset-model'))
    supported.update(('wave5-vpu-model','wave5-host-codec-diagnostic','wave5-lossless','wave5-irq'))
    supported.add('z8-yuv-data-model')
    supported.add('z8-yuv-dma-model')
    supported.add('z8-yuv-single-model')
    supported.add('z8-video-ring-repeat-model')
    supported.add('z8-video-source-end-model')
    supported.add('z8-video-serial-input')
    seen = set()
    result = []
    for item in properties:
        if not isinstance(item, str) or not re.fullmatch(r'[a-z0-9-]+=[^,\s\x00]+', item):
            raise ValueError('Malformed functional device property')
        name = item.split('=', 1)[0]
        if name in ('z8-secondary-resample-model','z8-video-interface-model','z8-video-ring-stop-model') and item.split('=', 1)[1] not in ('on', 'off'):
            raise ValueError('Optional video/rotation model must be on or off')
        if name not in supported or name in seen:
            raise ValueError('Unknown or duplicate functional device property: ' + name)
        seen.add(name)
        result.append(item)
    return result


class Guest(SharedOwner):
    def __init__(self, firmware, binary, raw, out, *, properties, instruction_clock=True,
                 nand_image=None, create_nand_image=False):
        if os.name == 'nt':
            raise ValueError('Use the selected Linux/WSL environment')
        profile = load_profile()
        self.firmware = Path(firmware).resolve()
        identity = validate_firmware(self.firmware, profile)
        properties = validate_properties(properties)
        media = media_arguments(nand_image, self.firmware, create=create_nand_image)
        if media and not {'bootstrap-fixtures=on', 'nand-fixture=on'} <= set(properties):
            raise ValueError('Persistent NAND requires explicit bootstrap and NAND model')
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
        self.command = [str(binary), '-M', ','.join(['nikon-z8-bringup', *properties, *media]),
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
        self.data['verification_scope'] = verification_scope()
        self.data['functional_properties_profile'] = 'profiles/z8-model-properties.json'
        self.data['runtime_profile'] = dict(instruction_clock=bool(instruction_clock),
                                            service_core_events='service-core-event-model=on' in properties,
                                            cycle_accurate=False, firmware_timeouts_changed=False)
        if media:
            self.data['persistent_nand'] = dict(path=str(Path(nand_image).resolve()),
                                                create_requested=create_nand_image,
                                                stock_settings_round_trip_verified=False)
        self.proc = self.qmp = self.qt = self.fp = self.gdb = None
        self.irq = {}
        self._log_offset = 0
        self._fault = False

    def __exit__(self, kind, error, traceback):
        self.data['verification_scope'] = verification_scope(menu_verified=self.data['menu_verified'])
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
