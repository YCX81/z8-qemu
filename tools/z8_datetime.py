# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit synthetic Z8 C3.11 external DateTime/RTC input.

The original family7 decoder 4101a110 -> 4101a15c maps a 14-byte wire type1
to logical type3, date/time payload and two status words. This is not captured
MCU traffic or a recovered physical RTC. Status12/13 retain wire names because
their full physical meanings are not established. No guest RAM is written.
"""
from datetime import datetime

MAIN_SHA='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'


def rtc_body(moment, *, status12=0, status13=0):
    """Encode an explicit local, whole-second datetime and bounded wire enums."""
    if (not isinstance(moment,datetime) or moment.tzinfo is not None or
            moment.microsecond or not 2000<=moment.year<=2255):
        raise ValueError('explicit naive whole-second datetime in wire range 2000..2255 required')
    if type(status12) is not int or status12 not in (0,1):
        raise ValueError('status12 must be wire enum 0 or 1')
    if type(status13) is not int or status13 not in (0,1,2):
        raise ValueError('status13 must be wire enum 0, 1 or 2')
    return bytes((1,0,0,0,moment.year-2000,moment.month-1,moment.day-1,
                  moment.hour,moment.minute,moment.second,0,0,status12,status13))


def validate_rtc_body(body):
    """Reject unsupported types/lengths/reserved fields before MCU queueing."""
    if not isinstance(body,(bytes,bytearray)) or len(body)!=14 or body[0]!=1:
        raise ValueError('14-byte type1 RTC input required')
    body=bytes(body)
    if any(body[i] for i in (1,2,3,10,11)):
        raise ValueError('reserved RTC input fields must be zero')
    moment=datetime(2000+body[4],body[5]+1,body[6]+1,body[7],body[8],body[9])
    return rtc_body(moment,status12=body[12],status13=body[13])
