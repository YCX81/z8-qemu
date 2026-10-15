# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in external RTC sample, adapted from WonderLantern shared146.

Z8 C3.11 family7 table 0x440c2160 binds 0x40cc85f0. Its type1
decoder 0x4101a15c produces the date/time and status consumed at
0x40cc8040. These are synthetic inputs, not measured MCU traffic.
"""
from datetime import datetime
import hashlib
from mcu_frames import frames
from z8_resources import MAIN_SHA


def status_body(timestamp):
    if not isinstance(timestamp, datetime) or not 2000 <= timestamp.year <= 2255:
        raise ValueError('RTC year must fit the observed unsigned offset from 2000')
    body = bytearray(14)
    body[0] = 1
    body[4:10] = bytes((timestamp.year - 2000, timestamp.month - 1,
                        timestamp.day - 1, timestamp.hour, timestamp.minute,
                        timestamp.second))
    # Explicit reset=0, auxiliary=0; decoded by the original Z8 instructions.
    return bytes(body)


def enqueue(peer, timestamp):
    if peer.g.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Z8 C3.11 RTC bindings required')
    body = status_body(timestamp)
    wire = frames(body, family=7)[0]
    peer.record.setdefault('z8_rtc_inputs', []).append(dict(
        timestamp=timestamp.isoformat(), body_hex=body.hex(),
        wire_sha256=hashlib.sha256(wire).hexdigest(),
        physical_protocol_verified=False, firmware_patch=False,
        classification='explicit_synthetic_MCU_RTC_input'))
    peer.pending.append((7, body, wire))
    peer.start_rx()
    return body
