# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit diagnostic video resource input, not recovered factory parameters."""
import hashlib
import json
from z8_resources import MAIN_SHA


def attach_processing_index(guest):
    """Supply record one only for the observed selector16 in external ROM.

    Native41170d5c reads zero, then32-bit subtraction underflows at41170d70.
    The input avoids that invalid lookup using the existing neutral record;
    it does not recover the absent coefficients or validate video processing.
    """
    return _attach_index(guest,16,'video-processing-index','video_processing_index_input')


def attach_recording_processing_index(guest):
    """Opt in to neutral record1 for observed recording selector10.

    live-rtc11 saved original ELR41170d84 with selector10 and an underflowed
    32-bit index. This external ROM input is a functional hypothesis; it does
    not recover Nikon's coefficients or modify a loaded guest RAM instance.
    Native resource loading/copying and subsequent lookup must be verified.
    """
    return _attach_index(guest,10,'video-recording-processing-index',
                         'video_recording_processing_index_input')


def _attach_index(guest,selector,stem,record_key):
    if guest.data.get('firmware_sha256')!=MAIN_SHA:
        raise ValueError('Video resource input requires the pinned original MAIN')
    path=guest.out/(stem+'.bin')
    metadata=guest.out/(stem+'.json')
    if path.exists() or metadata.exists():raise FileExistsError('Video input outputs must be new')
    raw=b'\x01'
    address=0x47f5af40+selector
    result=dict(classification='explicit diagnostic external ROM input',selector=selector,
                address=address,record=1,file=path.name,bytes=1,
                sha256=hashlib.sha256(raw).hexdigest(),firmware_sha256=MAIN_SHA,
                factory_coefficients_recovered=False,guest_state_patch=False,
                completion_or_readiness_supplied=False,
                source_pcs=['0x41170d20','0x41170d5c','0x41170d70','0x41170d84'],
                limits='Existing neutral record only; no video coefficients, data or completion supplied')
    with path.open('xb') as stream:stream.write(raw)
    with metadata.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2);stream.write('\n')
    guest.command+=['-device',f'loader,file={path},addr={address:#x},force-raw=on']
    guest.data[record_key]=result
    return result
