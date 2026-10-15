# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit external RTC status for the Z8 MCU peer.

Wire layout adapted from WonderLantern/zr-qemu f9cc5d1 rtc_mcu_input.py.
This supplies a declared simulated wall clock through the original family7
receiver. It never changes OS time, guest PCs, task flags or recording permits.
Physical MCU timing and battery-backed clock behavior are not established.
"""
from datetime import datetime
from mcu_frames import frames


def status_body(timestamp, *, reset_flag=0, auxiliary_status=0):
    if not isinstance(timestamp,datetime) or timestamp.tzinfo is not None or not 2000<=timestamp.year<=2255:
        raise ValueError('Use an explicit naive camera wall-clock datetime in years 2000..2255')
    if type(reset_flag) is not int or reset_flag not in (0,1) or type(auxiliary_status) is not int or auxiliary_status not in range(4):
        raise ValueError('RTC status fields require bounded integer inputs')
    body=bytearray(14);body[0]=1
    body[4:10]=bytes((timestamp.year-2000,timestamp.month-1,timestamp.day-1,
                      timestamp.hour,timestamp.minute,timestamp.second))
    body[12],body[13]=reset_flag,auxiliary_status
    return bytes(body)


def enqueue_status(peer,timestamp,*,reset_flag=0,auxiliary_status=0):
    """Join the existing MCU queue; do not race a pending TX/RX handshake."""
    body=status_body(timestamp,reset_flag=reset_flag,auxiliary_status=auxiliary_status)
    packet=frames(body,family=7)[0]
    peer.pending.append((7,body,packet))
    peer.record.setdefault('rtc_status_inputs',[]).append(dict(
        timestamp=timestamp.isoformat(),reset_flag=reset_flag,auxiliary_status=auxiliary_status,
        family=7,body_hex=body.hex(),frame_hex=packet.hex(),
        classification='explicit simulated RTC status',physical_protocol_verified=False))
    peer.start_rx()
    return body
