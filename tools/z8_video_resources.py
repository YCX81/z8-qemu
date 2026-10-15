# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit diagnostic video resource input, not recovered factory parameters."""
import hashlib
import json
from z8_resources import MAIN_SHA


def attach_processing_index(guest, *, recording=False):
    """Supply neutral record one for observed external-ROM selectors.

    Native41170d5c reads zero, then32-bit subtraction underflows at41170d70.
    The default covers selector16; recording=True also covers observed10.
    The input avoids that invalid lookup using the existing neutral record;
    it does not recover the absent coefficients or validate video processing.
    """
    if type(recording) is not bool:raise ValueError('Use an explicit recording input selection')
    if guest.data.get('firmware_sha256')!=MAIN_SHA:
        raise ValueError('Video resource input requires the pinned original MAIN')
    path=guest.out/'video-processing-index.bin'
    metadata=guest.out/'video-processing-index.json'
    extra=guest.out/'video-processing-index-recording.bin'
    if path.exists() or metadata.exists() or (recording and extra.exists()):raise FileExistsError('Video input outputs must be new')
    raw=b'\x01'
    result=dict(classification='explicit diagnostic external ROM input',selector=16,
                address=0x47f5af50,record=1,file=path.name,bytes=1,
                sha256=hashlib.sha256(raw).hexdigest(),firmware_sha256=MAIN_SHA,
                factory_coefficients_recovered=False,guest_state_patch=False,
                completion_or_readiness_supplied=False,
                source_pcs=['0x41170d20','0x41170d5c','0x41170d70','0x41170d84'],
                limits='Existing neutral record only; no video coefficients, data or completion supplied')
    if recording:
        # Original recording after valid RTC reaches the same lookup with
        # selector10. Its private resource copy also contains a zero index.
        # Supply only that observed external byte; do not broaden the table.
        result.update(recording_selector=10,recording_address=0x47f5af4a,
                      recording_file=extra.name,recording_bytes=1,recording_sha256=result['sha256'])
    with path.open('xb') as stream:stream.write(raw)
    with metadata.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2);stream.write('\n')
    guest.command+=['-device',f'loader,file={path},addr=0x47f5af50,force-raw=on']
    if recording:
        with extra.open('xb') as stream:stream.write(raw)
        guest.command+=['-device',f'loader,file={extra},addr=0x47f5af4a,force-raw=on']
    guest.data['video_processing_index_input']=result
    return result
