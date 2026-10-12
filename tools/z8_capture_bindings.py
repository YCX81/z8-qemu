# SPDX-License-Identifier: GPL-2.0-or-later
"""Pinned Z8 capture observation interfaces; never supply capture results."""
import json
from pathlib import Path
import struct

from z8_bringup import load_profile as firmware_profile, validate_firmware

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'profiles/z8-c311-capture.json'


def validate_profile(profile):
    identity = firmware_profile()
    expected = dict(model=identity['model'], version=identity['version'],
                    main_bytes=identity['bytes'], main_sha256=identity['sha256'])
    if profile.get('schema') != 1 or any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError('Capture bindings require the pinned Z8 C3.11 identity')
    bindings = profile.get('bindings', {})
    if not bindings or any(type(a) is not int or a % 4 or not 0x40160000 <= a < 0x41600000
                           for a in bindings.values()):
        raise ValueError('Observation bindings require aligned original code addresses')
    return profile


def load_profile():
    return validate_profile(json.loads(PROFILE.read_text(encoding='utf-8')))


def load_bindings(guest):
    profile = load_profile()
    if guest.data.get('firmware_sha256') != profile['main_sha256']:
        raise ValueError('Capture guest identity does not match Z8 C3.11')
    validate_firmware(guest.firmware, firmware_profile())
    return profile


def observation_points(profile=None):
    profile = load_profile() if profile is None else validate_profile(profile)
    return {address: label for label, address in profile['bindings'].items()}


def decode_key_state(data):
    if len(data) != 44:
        raise ValueError('Original key consumer requires exactly 44 observed bytes')
    words = struct.unpack('<11I', data)
    return dict(words=list(words), half=bool(words[0] & (1 << 31)),
                full=bool(words[0] & (1 << 30)),
                scope='Original consumed key state; not capture completion')


def read_gpio_bindings(guest, logical_ids=(0x23, 0x24)):
    """Resolve current native descriptors using Z8 tables; never read GPIO MMIO.

    Default to the two shutter contacts. CCOMP feedback may be uninitialized
    before its original service starts; callers must not invent descriptors.
    """
    profile = load_bindings(guest)
    gpio = profile['gpio']
    def read(address, size):
        if type(address) is not int or address % 4 or not 0xa00000000 <= address <= 0xa20000000 - size:
            raise ValueError('Capture descriptor is outside bounded working RAM')
        data = guest.read_bytes(address, size)
        if len(data) != size:
            raise ValueError('Short capture descriptor read')
        return data
    def word64(address):
        return struct.unpack('<Q', read(address, 8))[0]
    if word64(gpio['logical_table_pointer']) != gpio['logical_table_rom']:
        raise ValueError('Native GPIO table differs from the pinned Z8 board getter')
    known = {row['logical_id']: row for row in gpio['contacts']}
    if len(set(logical_ids)) != len(logical_ids) or any(i not in known for i in logical_ids):
        raise ValueError('Unknown or duplicate capture GPIO logical ID')
    rows = []
    for logical_id in logical_ids:
        expected = known[logical_id]
        pointer = word64(gpio['object_table'] + expected['index'] * 8)
        descriptor = read(pointer, 0x30)
        index, bank, bit = struct.unpack_from('<III', descriptor)
        irq_pointer = struct.unpack_from('<Q', descriptor, 0x20)[0]
        if (index, bank, bit) != (expected['index'], expected['bank'], expected['bit']):
            raise ValueError('Native capture GPIO identity/bank/bit differs')
        if not descriptor[0xc]:
            raise ValueError('Native capture GPIO input is disabled')
        register = word64(gpio['input_register_table'] + bank * 8)
        if register != expected['input_register']:
            raise ValueError('Native capture GPIO input register differs')
        irq = read(irq_pointer, 0x40)
        irq_bank, irq_bit, intid = struct.unpack_from('<III', irq, 4)
        callback = struct.unpack_from('<Q', irq, 0x18)[0]
        if (irq_bank, irq_bit, intid) != (expected['irq_bank'], expected['irq_bit'], expected['intid']):
            raise ValueError('Native capture GPIO IRQ binding differs')
        if expected['callback'] is not None and callback != expected['callback']:
            raise ValueError('Native capture GPIO callback not registered as expected')
        rows.append(dict(logical_id=logical_id, index=index, descriptor=pointer,
                         bank=bank, bit=bit, input_register=register, intid=intid,
                         irq_descriptor=irq_pointer, callback=callback,
                         classification='passive native descriptor observation'))
    return rows
