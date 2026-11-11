# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed Z8 C3.11 virtual poses, through the I2C input only.

Left/right name body rotation as viewed from behind. The fixed LCD pixels
counter-rotate. This is a synthetic axis convention, not sensor calibration.
"""
import json
import struct

from z8_resources import MAIN_SHA

POSES = {
    'landscape': dict(sample_hex='c00000000000', xyz=[-1024,0,0], classification=0),
    'left': dict(sample_hex='0000c0000000', xyz=[0,-1024,0], classification=1),
    'right': dict(sample_hex='000040000000', xyz=[0,1024,0], classification=2),
}
DRIVER = 0xa02e560c8


def _paused(guest):
    if guest.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Orientation observations require the pinned original Z8 C3.11 MAIN')
    if 'Z9_UNKNOWN' in guest.log():
        raise RuntimeError('Cannot accept orientation after an unmodeled hardware access')
    if guest.qmp.command('query-status')['running']:
        raise RuntimeError('Orientation access requires an owned paused guest')


def set_pose(guest, pose):
    """Change only the virtual sensor source; firmware must consume it normally."""
    if not isinstance(pose,str) or pose not in POSES:
        raise ValueError('Pose must be landscape, left or right')
    _paused(guest)
    sample = POSES[pose]['sample_hex']
    guest.qmp.command('qom-set',path='/machine',property='orientation-input',value=sample)
    actual = guest.qmp.command('qom-get',path='/machine',property='orientation-input')
    if actual != sample:
        raise RuntimeError('Orientation source readback mismatch')
    record = dict(pose=pose,sample_hex=sample,classification='synthetic_I2C_input',
                  native_ui_verified=False,physical_calibration_verified=False)
    guest.data.setdefault('orientation_inputs',[]).append(record)
    return record


def read_orientation(guest):
    """Observe raw, filtered and classified values without native function calls."""
    _paused(guest)
    blob = guest.read_bytes(DRIVER,0x1c8)
    if len(blob) != 0x1c8:
        raise RuntimeError('Short native orientation state read')
    return dict(device=json.loads(guest.qmp.command('qom-get',path='/machine',property='orientation-state')),
                native_raw_xyz=list(struct.unpack_from('<3h',blob,0x28)),
                filtered_xyz=list(struct.unpack_from('<3f',blob,0xcc)),
                angles_degrees=list(struct.unpack_from('<3f',blob,0xb8)),
                classification=struct.unpack_from('<I',blob,0xc4)[0],
                sampling_window=struct.unpack_from('<I',blob,0x1a0)[0],
                native_ui_verified=False,physical_calibration_verified=False)
