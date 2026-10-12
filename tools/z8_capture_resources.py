# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in synthetic ROM cells and passive guards for original Z8 C3.11.

These are authored missing-resource hypotheses, not recovered calibration.
The original MAIN, live RAM, capture policy and hardware model are unchanged.
"""
import hashlib
from pathlib import Path
import struct

from z8_bringup import load_profile, validate_firmware
from z8_resources import MAIN_SHA

ROM_BASE = 0x47e80000
RESOURCE_BASE = 0xa0496f6a0
DESCRIPTOR_POINTER = 0xa0291da98
MODE14_SELECTOR = 0xa02aa880c
SPECS = {
    'timing': dict(option='capture_timing_input', address=0x47f63022, value=1200, size=2,
        mode=0, variant=1, selector=1, subselector=3,
        meaning='Synthetic line coefficient for original mode0/variant1/selector1/sub3; physical timing unverified'),
    'processing': dict(option='hsfc_processing_input', address=0x47f5af4f, value=1, size=1,
        mode=14, selector=15,
        meaning='Synthetic selector15 chooses record1; record body and physical calibration remain missing'),
}
POINTS = {
    'timing': {0x41172254: 'resource_timing_preload', 0x41172258: 'resource_timing_loaded',
               0x4117239c: 'resource_timing_fallback'},
    'processing': {0x41170d5c: 'resource_processing_preload', 0x41170d60: 'resource_processing_loaded',
                   0x41170d84: 'resource_processing_record'},
}


def require(value, message):
    if not value:
        raise ValueError(message)


def options(args):
    flags = {key: getattr(args, spec['option'], False) for key, spec in SPECS.items()}
    require(all(type(flag) is bool for flag in flags.values()), 'Resource input options must be Boolean')
    return [key for key, flag in flags.items() if flag]


def attach(guest, args):
    selected = options(args)
    if not selected:
        return None
    require(guest.data.get('firmware_sha256') == MAIN_SHA, 'Resource inputs require original Z8 C3.11 identity')
    require(getattr(guest, 'proc', None) is None, 'Resource inputs are startup-only, never live writes')
    profile = load_profile()
    require(profile['sha256'] == MAIN_SHA and profile['bytes'] == 81807709, 'Pinned original MAIN profile changed')
    identity = validate_firmware(guest.firmware, profile)
    require(guest.command.count('-kernel') == 1 and
            Path(guest.command[guest.command.index('-kernel') + 1]).resolve() == Path(guest.firmware).resolve(),
            'Resource inputs require the verified original MAIN as executed kernel')
    paths = {key: guest.out / ('z8-capture-' + key + '-input.bin') for key in selected}
    if guest.data.get('capture_resource_inputs') or any(path.exists() for path in paths.values()):
        raise FileExistsError('Resource input files and receipt must be fresh')
    require(all(',' not in str(path) for path in paths.values()), 'Loader path cannot contain a comma')
    record = dict(source_main=dict(path=str(Path(guest.firmware).resolve()), **identity),
        generator=dict(file=Path(__file__).name, sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        startup_loader_only=True, original_main_modified=False, guest_ram_modified_by_observer=False,
        capture_policy_modified=False, hardware_calibration=False, nef_accepted=False,
        observation_scope='First hit per contact, not a complete lookup or frame history',
        inputs={}, observations=[])
    for key, path in paths.items():
        spec = SPECS[key]
        data = spec['value'].to_bytes(spec['size'], 'little')
        with path.open('xb') as stream:
            stream.write(data)
        digest = hashlib.sha256(data).hexdigest()
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Resource input file hash mismatch')
        guest.command += ['-device', f'loader,file={path},addr={spec["address"]:#x},force-raw=on']
        record['inputs'][key] = dict(file=path.name, address=hex(spec['address']), bytes=len(data),
            sha256=digest, value=spec['value'], supplied=True, actual_lookup_verified=False,
            status='unobserved', synthetic=True, hardware_calibration=False, meaning=spec['meaning'],
            applicable={name: spec[name] for name in ('mode','variant','selector','subselector') if name in spec},
            source_main_sha256=MAIN_SHA, observer_pcs=[hex(pc) for pc in POINTS[key]])
    guest.data['capture_resource_inputs'] = record
    return record


def observation_points(guest):
    keys = guest.data.get('capture_resource_inputs', {}).get('inputs', {})
    return {pc: name for key in keys for pc, name in POINTS[key].items()}


def read_ram(guest, address, size, alignment=8):
    require(type(address) is int and address % alignment == 0 and
            0xa00000000 <= address <= 0xa40000000-size, 'Native resource pointer outside bounded Z8 RAM')
    data = guest.read_bytes(address, size)
    require(len(data) == size, 'Short native resource read')
    return data


def resource_base(guest):
    base = struct.unpack('<Q', read_ram(guest, RESOURCE_BASE, 8))[0]
    require(base % 8 == 0 and 0xa00000000 <= base <= 0xa40000000-0x100000,
            'Loaded resource base outside bounded Z8 RAM')
    descriptor = struct.unpack('<Q', read_ram(guest, DESCRIPTOR_POINTER, 8))[0]
    require(descriptor == base, 'Original resource descriptor/global disagree')
    return base


def cells(guest, base, key):
    spec = SPECS[key]; offset = spec['address'] - ROM_BASE
    ram = read_ram(guest, base+offset, spec['size'], spec['size'])
    rom = guest.read_bytes(spec['address'], spec['size'])
    require(len(rom) == spec['size'], 'Short mapped ROM read')
    require(ram == rom == spec['value'].to_bytes(spec['size'], 'little'),
            'Original loader RAM/ROM cells differ from the supplied hypothesis')
    return dict(resource_ram_base=hex(base), ram_address=hex(base+offset),
                rom_address=hex(spec['address']), ram_value=int.from_bytes(ram,'little'),
                rom_value=int.from_bytes(rom,'little'))


def timing(guest, pc, regs, row):
    data = read_ram(guest, regs['X19'], 0x1a8)
    mode, variant, sub = (struct.unpack_from('<I', data, off)[0] for off in (0,0x174,0xe0))
    selector = regs['X10'] & 0xffffffff
    row.update(mode=mode, variant=variant, selector=selector, subselector=sub,
               native_fallback=pc == 0x4117239c, input_pointer=hex(regs['X19']))
    if (mode,variant,sub) != (0,1,3):
        return
    row['applicable'] = True
    require(pc != 0x4117239c, 'Target timing lookup used original fallback')
    require(selector == 1, 'Target timing selector changed')
    base = resource_base(guest)
    offset = 0xe2ed2 + selector*0xc0 + sub*0x30
    require(offset == 0xe3022 and regs['X21'] == base+offset,
            'Target timing pointer differs from original selector expression')
    row.update(cells(guest, base, 'timing'))
    if pc == 0x41172258:
        value = regs['X20'] & 0xffffffff
        row['native_result'] = value
        require(value == 1200, 'Original timing LDRH result differs')
        row['native_load_completed'] = True


def processing(guest, pc, regs, row):
    mode = struct.unpack('<I', read_ram(guest, regs['X00'], 4, 4))[0]
    row.update(mode=mode, selector=regs['X08'] & 0xffffffff, input_pointer=hex(regs['X00']))
    if mode != 14:
        return
    row['applicable'] = True
    selector = struct.unpack('<I', read_ram(guest, MODE14_SELECTOR, 4, 4))[0]
    require(selector == row['selector'] == 15, 'Original mode14 selector is not slot15')
    base = resource_base(guest)
    row.update(cells(guest, base, 'processing'))
    if pc in (0x41170d5c, 0x41170d60):
        require(regs['X09'] == RESOURCE_BASE and regs['X13'] == base and regs['X10'] == 0xdaf40,
                'Original processing lookup base/offset changed')
        if pc == 0x41170d5c:
            require(regs['X14'] + regs['X10'] == base+0xdaf4f,
                    'Original LDRB address is not the supplied slot')
        else:
            row['native_result'] = regs['X14'] & 0xffffffff
            require(row['native_result'] == 1, 'Original processing LDRB result differs')
            row['native_load_completed'] = True
    else:
        # The index subtraction has already contributed to X13. Original
        # d7c/d80 then reuse X14 for the next record field's offset.
        row['native_record_offsets'] = dict(current=hex(regs['X15']), next=hex(regs['X14']))
        require(regs['X13'] == base and regs['X15'] == 0xdaf66 and regs['X14'] == 0xdaf6c,
                'Original record1 address/field offsets changed')
        row['record_read_address'] = hex(regs['X13']+regs['X15'])
        row['record_contents_verified'] = False


def observe(guest, pc, regs, *, cpu, t_ns):
    keys = guest.data.get('capture_resource_inputs', {}).get('inputs', {})
    matches = [key for key in keys if pc in POINTS[key]]
    require(len(matches) == 1, 'Unknown or disabled resource observation point')
    key = matches[0]; record = guest.data['capture_resource_inputs']; item = keys[key]
    require(len(record['observations']) < 256, 'Resource observer exceeded 256 samples')
    row = dict(input=key, pc=hex(pc), cpu=cpu, t_ns=t_ns, applicable=False,
        guard_passed=False, native_load_completed=False, status='not_applicable')
    record['observations'].append(row)
    try:
        require(guest.data.get('firmware_sha256') == MAIN_SHA, 'Original MAIN identity changed')
        require(guest.qmp.command('query-status').get('running') is False, 'Resource observation requires paused guest')
        require(regs.get('PC') == pc, 'Native PC differs from observation point')
        (timing if key == 'timing' else processing)(guest, pc, regs, row)
        if row['applicable']:
            row.update(status='matched', guard_passed=True)
            if row['native_load_completed']:
                item.update(actual_lookup_verified=True, status='original_load_observed')
        return row
    except Exception as error:
        row.update(status='failed', error=repr(error))
        item.update(status='guard_failed', actual_lookup_verified=False)
        raise
