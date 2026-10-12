# SPDX-License-Identifier: GPL-2.0-or-later
"""Z8-specific functional inputs; shared models remain explicit hypotheses."""
import json
from pathlib import Path
import re

from z8_bringup import load_profile
from z9_global1_boundary_input import Peer as BoundaryPeer

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'profiles/z8-model-properties.json'


def validate_model_profile(profile):
    identity = load_profile()
    expected = dict(model=identity['model'], version=identity['version'],
                    machine='nikon-z8-bringup', firmware_sha256=identity['sha256'],
                    firmware_bytes=identity['bytes'])
    if profile.get('schema') != 1 or any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError('Functional properties require the pinned Z8 MAIN identity')
    properties = profile.get('properties')
    if not isinstance(properties, list) or not properties:
        raise ValueError('Nonempty functional properties required')
    names = []
    for item in properties:
        if not isinstance(item, str) or not re.fullmatch(r'[a-z0-9-]+=[^,\s\x00]+', item):
            raise ValueError('Malformed functional device property')
        names.append(item.split('=', 1)[0])
    if len(names) != len(set(names)):
        raise ValueError('Duplicate functional device property')
    classified = []
    for group in profile.get('parameter_classes', []):
        if (not group.get('source') or not group.get('assumptions')
                or group.get('physical_hardware_verified') is not False):
            raise ValueError('Each property class requires source and unverified hardware scope')
        classified.extend(group.get('properties', []))
    if sorted(classified) != sorted(names):
        raise ValueError('Every property requires exactly one provenance class')
    boundary = profile.get('diagnostic_boundary', {})
    period = boundary.get('period_ms')
    if (type(period) is not int or not 10 <= period <= 1000
            or type(boundary.get('auxiliary_masked')) is not bool
            or boundary.get('z8_physical_timing_verified') is not False
            or not boundary.get('source') or not boundary.get('assumptions')):
        raise ValueError('Bounded diagnostic milliseconds and unverified timing scope required')
    return profile


def load_model_profile():
    """No override flag: Z8 runners always use the identity-bound local profile."""
    return validate_model_profile(json.loads(PROFILE.read_text(encoding='utf-8')))


def make_boundary_peer(guest):
    settings = load_model_profile()['diagnostic_boundary']
    guest.data['z8_diagnostic_boundary'] = settings.copy()
    return BoundaryPeer(guest, settings['period_ms'],
                        auxiliary_masked=settings['auxiliary_masked'])


def verification_scope(*, menu_verified=False):
    """Assisted menu evidence never promotes bare boot or complete-camera claims."""
    return dict(bare_model_full_boot_verified=False,
                external_mcu_assisted_menu_verified=bool(menu_verified),
                complete_camera_verified=False, physical_hardware_verified=False)
