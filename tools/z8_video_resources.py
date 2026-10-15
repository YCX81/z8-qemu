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


def attach_recording_index(guest):
    """Observed recording selector10, using the same declared neutral record.

    c1-rtc-native01 faulted at41170d84 with selector10 and index zero.
    This opt-in external ROM input supplies no factory coefficients.
    """
    return _attach_index(guest,10,'video-recording-index','video_recording_index_input')


def _attach_index(guest,selector,name,key):
    if guest.data.get('firmware_sha256')!=MAIN_SHA:
        raise ValueError('Video resource input requires the pinned original MAIN')
    path=guest.out/(name+'.bin')
    metadata=guest.out/(name+'.json')
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
    guest.data[key]=result
    return result
