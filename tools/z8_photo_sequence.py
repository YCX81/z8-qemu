# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-job spans, adapted from z9-qemu c6dbfc4 capture_repeat_flow.

Counter lineage is model identity; it is not a native allocator generation.
Snapshots may precede W1C. Such a snapshot does not award photo recovery.
"""
from z8_capture_pixels import integer, _physical, MAX_PACKED_BYTES

def raw_job_span(current, previous, expected):
    previous = {'jobs':0, 'bytes':0} if previous is None else previous
    job = integer(current.get('jobs'), 'RAW job', 1)
    old = integer(previous.get('jobs'), 'previous RAW job')
    total = integer(current.get('bytes'), 'cumulative RAW bytes', 1)
    prior = integer(previous.get('bytes'), 'previous cumulative RAW bytes')
    expected = integer(expected, 'per-job geometry', 1, MAX_PACKED_BYTES)
    integer(current.get('frame'), 'RAW source frame', 1)
    acks = integer(current.get('acks'), 'RAW acknowledgements')
    pending = current.get('pending')
    # 151 ACKs count NV16 operations too: bind the RAW job by bytes/frame,
    # rather than assuming shared acknowledgement count equals RAW job count.
    if (current.get('enabled') is not True or type(pending) is not bool or
            job != old+1 or total-prior != expected or acks < job-int(pending)):
        raise ValueError('One new complete RAW job and its exact counter delta required')
    source = _physical(current.get('source'), expected)
    target = _physical(current.get('target'), expected)
    if source < target+expected and target < source+expected:
        raise ValueError('RAW source and target overlap')
    return expected

def capture_photo_output(guest, label, previous=None):
    from z8_capture_pixels import _states, _description, RAW, capture_raw_output
    before = _states(guest, True)
    desc = _description(before, True)
    old = None if previous is None else previous['device_states_before'][RAW]
    amount = raw_job_span(before[RAW], old, desc['memory_bytes'])
    receipt = capture_raw_output(guest, label)
    if before != receipt['device_states_before']:
        raise ValueError('RAW job changed between admission and snapshot')
    receipt['photo_sequence'] = dict(job=desc['job'], frame=desc['frame'],
        per_job_bytes=amount, cumulative_bytes=before[RAW]['bytes'],
        previous_job=0 if old is None else old['jobs'],
        previous_bytes=0 if old is None else old['bytes'],
        native_owner_verified=False, photo_recovery_verified=False)
    # Preserve the base receipt and add a separately named sequence receipt.
    import json
    from pathlib import Path
    directory = Path(guest.out)/('pixel-'+label)
    with (directory/'photo-sequence.json').open('x', encoding='utf-8') as out:
        json.dump(receipt, out, indent=2); out.write('\n')
    return receipt
