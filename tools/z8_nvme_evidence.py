# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect passive NVMe evidence at a paused scope boundary, never accept NEF.

CPU pause need not drain asynchronous block work. Independent counters before
and after the exact stderr prefix therefore must agree. Pending commands,
including admin AER, and failed commands are retained rather than completed.
"""
import hashlib
import json
import re

CONTROLLER_ID = 'z8-recording-card'
CONTROLLER_PATH = '/machine/peripheral/' + CONTROLLER_ID
CARD_DEVICE = ('nvme,id=' + CONTROLLER_ID +
               ',drive=card,serial=Z8-LAB-CAPTURE,bus=dw-pcie,addr=0')
MAX_PREFIX_BYTES = 256 * 1024 * 1024
FIELDS = {
    'instance': 'bus devfn',
    'begin': 'request sqid cqid cid nsid opcode',
    'dispatch': 'request offset length supported lbads metadata pi',
    'done': 'request offset length status ret',
    'queued': 'request status',
    'posted': 'request cqid address status wire_status',
    'flush': 'request status ret',
    'boundary': 'request boundary status detail',
}
END_FIELDS = {'schema', 'instance', 'last_request_id', 'last_event_seq', 'virtual_ns'}
FIELD_BITS = dict(bus=32, devfn=32, sqid=16, cqid=16, cid=16, nsid=32,
                  opcode=8, lbads=8, metadata=16, pi=8, supported=1,
                  status=16, wire_status=16, boundary=32, virtual_ns=63)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_options(args):
    enabled = getattr(args, 'nvme_trace', False)
    require(type(enabled) is bool, 'nvme_trace must be Boolean')
    require(not enabled or getattr(args, 'card', False) is True,
            '--nvme-trace requires --card')
    return enabled


def enable_trace(guest):
    guest.command += ['-trace', 'enable=pci_nvme_audit_*']
    guest.data['nvme_trace'] = dict(enabled=True, controller_path=CONTROLLER_PATH,
        clock='QEMU_CLOCK_VIRTUAL nanoseconds; same clock as capture observations',
        scope='From controller creation through the independent paused end-state; collection only',
        nef_accepted=False)


def validate_row(row):
    require(isinstance(row, dict) and isinstance(row.get('kind'), str) and
            row['kind'] in FIELDS, 'Unknown NVMe audit event')
    kind = row['kind']
    require(set(row) == {'kind', 'virtual_ns', 'instance', 'seq', *FIELDS[kind].split()},
            'Missing or unexpected NVMe audit fields')
    for key, value in row.items():
        if key == 'kind':
            continue
        low, high = (-(1 << 31), 1 << 31) if key == 'ret' else (0, 1 << FIELD_BITS.get(key, 64))
        require(type(value) is int and low <= value < high, 'Invalid NVMe audit integer: ' + key)
    require(row['instance'] > 0 and row['seq'] > 0, 'Missing NVMe trace identity')
    if kind not in ('instance', 'boundary'):
        require(row['request'] > 0, 'Invalid NVMe request lifecycle')


def decode_prefix(data):
    rows = []
    for line in data.splitlines(keepends=True):
        if b'pci_nvme_audit_' not in line:
            continue
        require(line.endswith(b'\n'), 'Incomplete NVMe audit line at scope cutoff')
        match = re.search(rb'\bpci_nvme_audit_(\w+) (.*)\n$', line)
        require(match is not None, 'Malformed NVMe audit line')
        kind, body = match.groups()
        row = {'kind': kind.decode('ascii')}
        for token in body.split():
            key, separator, value = token.partition(b'=')
            key = key.decode('ascii')
            require(separator and key not in row, 'Malformed or duplicate NVMe audit field')
            row[key] = int(value, 0)
        validate_row(row)
        rows.append(row)
    return rows


def check_integrity(rows, end_state):
    """Validate log completeness, retaining pending/failed and boundary events.

    This intentionally does not call the fixture's completed_writes oracle:
    real firmware may leave AER pending at a valid evidence cutoff.
    """
    require(isinstance(end_state, dict) and set(end_state) == END_FIELDS,
            'Independent device end-state is required')
    require(all(type(v) is int and 0 <= v < (1 << 64) for v in end_state.values()) and
            end_state['schema'] == 1 and end_state['instance'] > 0 and
            end_state['virtual_ns'] < (1 << 63), 'Invalid independent end-state')
    require(bool(rows), 'No NVMe audit events found')
    requests, boundaries = {}, []
    previous_ns = 0
    for seq, row in enumerate(rows, 1):
        validate_row(row)
        require(row['instance'] == end_state['instance'], 'Foreign NVMe controller instance')
        require(row['seq'] == seq, 'Missing, duplicate or reordered NVMe event')
        require(previous_ns <= row['virtual_ns'] <= end_state['virtual_ns'],
                'Virtual time differs from independent end-state')
        previous_ns = row['virtual_ns']
        kind = row['kind']
        require((seq == 1) == (kind == 'instance'), 'Missing or repeated controller identity')
        if kind == 'instance':
            continue
        request_id = row['request']
        if kind == 'begin':
            require(request_id == len(requests) + 1, 'Request lifecycle reused or missing')
            requests[request_id] = {'begin': row}
        elif kind == 'boundary':
            require(request_id == 0 or request_id in requests, 'Boundary has no original request')
            boundaries.append(row)
        else:
            require(request_id in requests, 'Event has no original request')
            stages = requests[request_id]
            require(kind not in stages, 'Duplicate request stage')
            require('posted' not in stages, 'Request event after CQ delivery')
            if kind == 'posted':
                require('queued' in stages, 'CQ delivery precedes internal completion')
                require(row['cqid'] == stages['begin']['cqid'], 'CQ identity changed')
            elif kind in ('dispatch', 'done', 'flush'):
                require('queued' not in stages, 'Backend event follows internal completion')
            stages[kind] = row
    require(len(rows) == end_state['last_event_seq'] and
            len(requests) == end_state['last_request_id'], 'Trace differs from independent end-state')
    pending = [key for key, stages in requests.items() if 'posted' not in stages]
    writes = [stages for stages in requests.values()
              if stages['begin']['sqid'] != 0 and stages['begin']['opcode'] == 1]
    return dict(events=len(rows), requests_claimed=len(requests),
        pending_request_ids=pending,
        pending_aer_request_ids=[key for key in pending if requests[key]['begin']['sqid'] == 0 and
                                 requests[key]['begin']['opcode'] == 12],
        ordinary_write_done=sum('done' in stages for stages in writes),
        ordinary_write_cqe_posted=sum('posted' in stages for stages in writes),
        failed_event_sequences=[row['seq'] for row in rows if row.get('status', 0) != 0 or row.get('ret', 0) != 0],
        boundary_events=boundaries,
        all_commands_completed=not pending,
        complete_meaning='CQ delivery only, including failures; no durability or NEF acceptance',
        nef_accepted=False)


def collect(guest, run_failure=None):
    """Read two QOM counter snapshots around an exclusive exact stderr prefix.

    No stop/cont, queue drain, reset, guest memory write or file acceptance is
    performed here. A caller must pause first. Failures remain in the receipt.
    """
    receipt = dict(schema=1, controller_path=CONTROLLER_PATH, collection_complete=False,
        nef_accepted=False, run_failure=run_failure, issues=[],
        scope='Controller creation to paused prefix cutoff, before peer/QEMU cleanup; not process exit')
    issues = receipt['issues']

    def read_qmp(label, command, **kwargs):
        try:
            value = guest.qmp.command(command, **kwargs)
            receipt[label + '_raw'] = value
            if command == 'qom-get':
                require(isinstance(value, str), 'audit-state must be a JSON string')
                value = json.loads(value)
            receipt[label] = value
            return value
        except Exception as error:
            issues.append(dict(stage=label, error=repr(error)))
            return None

    status_before = read_qmp('status_before', 'query-status')
    before = read_qmp('end_state_before', 'qom-get', path=CONTROLLER_PATH, property='audit-state')
    data = None
    try:
        source = guest.out / 'stderr.log'
        target = guest.out / 'nvme-stderr-prefix.log'
        # Read a fixed-size prefix, not a moving EOF. Keep original stderr for
        # later cleanup events; no truncation/filtering of that file occurs.
        with source.open('rb') as stream:
            size = source.stat().st_size
            require(size <= MAX_PREFIX_BYTES, 'NVMe stderr prefix exceeds collector bound; original retained')
            data = stream.read(size)
        require(len(data) == size, 'Original stderr shortened during collection')
        with target.open('xb') as stream:
            stream.write(data)
        digest = hashlib.sha256(data).hexdigest()
        require(hashlib.sha256(target.read_bytes()).hexdigest() == digest, 'Saved stderr prefix hash mismatch')
        receipt['stderr_prefix'] = dict(path=target.name, source=source.name, bytes=len(data), sha256=digest)
    except Exception as error:
        issues.append(dict(stage='stderr_prefix', error=repr(error)))
    after = read_qmp('end_state_after', 'qom-get', path=CONTROLLER_PATH, property='audit-state')
    status_after = read_qmp('status_after', 'query-status')
    for label, status in (('status_before', status_before), ('status_after', status_after)):
        if not isinstance(status, dict) or status.get('running') is not False:
            issues.append(dict(stage=label, error='CPU was not independently observed stopped'))
    if before != after:
        issues.append(dict(stage='end_state', error='Independent snapshots changed; no drain or retry performed'))
    try:
        require(data is not None, 'No stderr prefix available')
        receipt['integrity'] = check_integrity(decode_prefix(data), after)
    except Exception as error:
        issues.append(dict(stage='integrity', error=repr(error)))
    receipt['collection_complete'] = not issues
    try:
        with (guest.out / 'nvme-evidence.json').open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2); stream.write('\n')
    except Exception as error:
        issues.append(dict(stage='receipt', error=repr(error)))
        receipt['collection_complete'] = False
    return receipt
