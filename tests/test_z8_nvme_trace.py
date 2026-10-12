# SPDX-License-Identifier: GPL-2.0-or-later
"""Real paused-CPU NVMe queue tests using an authored kernel and fresh file card.

Explicit CLI only: --qemu BINARY --output NEW_DIRECTORY. No Nikon firmware,
host block device, guest completion injection or camera acceptance is involved.
"""
import argparse
import copy
from collections import Counter
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
import unittest

from check_z8_machine import QMP, sha256

RAM = 0xa00010000
DBI = 0x1a800000
ATU = 0x1ab00000
APERTURE = 0x600000000
CARD_BYTES = 32 * 1024 * 1024
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


def require(value, message):
    if not value:
        raise AssertionError(message)


def parse_trace(text):
    """Strictly parse only our numeric passive trace schema, retaining failures."""
    rows = []
    for line in text.splitlines():
        match = re.search(r'\bpci_nvme_audit_(\w+) (.*)$', line)
        if not match:
            continue
        event, body = match.groups()
        require(event in FIELDS, 'Unknown NVMe audit event')
        row = {'kind': event}
        for token in body.split():
            key, separator, value = token.partition('=')
            require(separator and key not in row, 'Malformed or duplicate trace field')
            row[key] = int(value, 0)
        require({'virtual_ns', 'instance', 'seq'} <= row.keys(), 'Missing trace identity')
        require(set(row) == {'kind', 'virtual_ns', 'instance', 'seq', *FIELDS[event].split()},
                'Missing or unexpected trace fields')
        require(all(-(1 << 31) <= value < (1 << 31) if key == 'ret' else
                    0 <= value < (1 << 64) for key, value in row.items() if key != 'kind'),
                'Trace integer outside bounds')
        require(row['instance'] > 0 and row['seq'] > 0, 'Missing trace identity')
        if event not in ('instance', 'boundary'):
            require(row['request'] > 0, 'Invalid request lifecycle')
        rows.append(row)
    return rows


def completed_writes(rows, end_state=None):
    """Fail closed: all begun ordinary writes must finish and be CQ-delivered."""
    require(bool(rows), 'No passive NVMe completion trace events found')
    require(isinstance(end_state, dict) and set(end_state) ==
            {'schema', 'instance', 'last_request_id', 'last_event_seq', 'virtual_ns'},
            'Independent device end-state is required')
    require(all(type(value) is int and 0 <= value < (1 << 64) for value in end_state.values()) and
            end_state['schema'] == 1 and end_state['instance'] > 0,
            'Invalid device end-state')
    requests, instances = {}, {}
    for row in rows:
        instance = row['instance']
        previous = instances.get(instance)
        if previous is not None:
            require(row['seq'] == previous['seq'] + 1, 'Missing or duplicate NVMe event')
            require(row['virtual_ns'] >= previous['virtual_ns'], 'Virtual time regressed')
        instances[instance] = row
        kind = row['kind']
        if kind == 'instance':
            require(previous is None and row['seq'] == 1, 'Controller identity repeated')
            continue
        if kind == 'boundary':
            raise AssertionError('Abort/reset/delete boundary in scoped evidence')
        require(previous is not None, 'Missing original controller identity')
        key = (instance, row['request'])
        if kind == 'begin':
            require(key not in requests, 'Request lifecycle reused')
            requests[key] = {'begin': row}
            continue
        require(key in requests, 'Completion has no original request')
        request = requests[key]
        require(kind not in request, 'Duplicate request stage')
        if kind == 'dispatch':
            require('done' not in request and 'queued' not in request, 'Late dispatch')
        elif kind == 'done':
            require('dispatch' in request, 'Done has no actual backend dispatch')
            require('queued' not in request, 'Final backend result followed completion')
        elif kind == 'posted':
            require('queued' in request, 'CQ delivery precedes internal completion')
        request[kind] = row
    require(set(instances) == {end_state['instance']}, 'Controller differs from independent end-state')
    final = instances[end_state['instance']]
    require(final['seq'] == end_state['last_event_seq'] and
            final['virtual_ns'] <= end_state['virtual_ns'], 'Trace differs from independent end-state')
    request_ids = [key[1] for key in requests]
    require(len(request_ids) == end_state['last_request_id'] and
            (not request_ids or (min(request_ids) == 1 and
                                 max(request_ids) == end_state['last_request_id'])),
            'Request count/lifecycle range differs from independent end-state')
    writes = []
    for key, request in requests.items():
        begin = request['begin']
        if begin['sqid'] == 0 or begin['opcode'] != 1:
            require({'queued', 'posted'} <= request.keys(), 'Incomplete non-write command')
            require(request['queued']['status'] == 0 and request['posted']['status'] == 0,
                    'Non-write command failed')
            if begin['sqid'] and begin['opcode'] == 0:
                require('flush' in request and request['flush']['ret'] == 0 and
                        request['flush']['status'] == 0, 'FLUSH is not complete')
            continue
        require({'dispatch', 'done', 'queued', 'posted'} <= request.keys(), 'Incomplete write lifecycle')
        dispatch, done = request['dispatch'], request['done']
        require(dispatch['supported'] == 1 and dispatch['length'] > 0, 'Unsupported write path')
        require(dispatch['metadata'] == dispatch['pi'] == 0 and
                9 <= dispatch['lbads'] <= 31 and
                dispatch['offset'] % (1 << dispatch['lbads']) == 0 and
                dispatch['length'] % (1 << dispatch['lbads']) == 0, 'Invalid write geometry')
        require(done['ret'] == 0 and done['status'] == 0, 'Backend write failed')
        require(request['queued']['status'] == 0 and request['posted']['status'] == 0,
                'Write was not successfully delivered')
        require(done['offset'] == dispatch['offset'] and done['length'] == dispatch['length'],
                'Actual backend extent changed')
        require(request['posted']['cqid'] == begin['cqid'], 'CQ identity changed')
        require(request['posted']['wire_status'] >> 1 == 0, 'Wire CQE is not successful')
        writes.append(dict(begin, offset=done['offset'], length=done['length'],
                           status=done['status'], t_ns=done['virtual_ns']))
    return writes


def reject_evidence(rows, end_state):
    try:
        completed_writes(rows, end_state)
    except AssertionError:
        return
    raise AssertionError('Incomplete or failed evidence was accepted')


class NvmeMachine:
    def __init__(self, binary, directory, kernel, card, *, trace=True, block_options=None):
        self.folder = directory
        directory.mkdir()
        self.log_path = directory / 'stderr.log'
        self.log = self.log_path.open('wb')
        self.runtime = tempfile.TemporaryDirectory(prefix='z8-nvme-qtest-')
        runtime = Path(self.runtime.name)
        self.qmp = self.sock = self.stream = None
        self.bar = None
        self.queues = {}
        self.cq_records = []
        props = ('nikon-z8-bringup,bootstrap-fixtures=on,pcie-empty-fixture=on,'
                 'pcie-link-diagnostic-mask=4,pcie-dbi-model=on,pcie-ram-dma-input=on')
        command = [str(binary), '-M', props, '-kernel', str(kernel), '-smp', '4',
                   '-m', '512M', '-S', '-display', 'none', '-serial', 'none',
                   '-monitor', 'none', '-qtest-log', '/dev/null',
                   '-qmp', f'unix:{runtime}/qmp,server=on,wait=off',
                   '-qtest', f'unix:{runtime}/qtest,server=on,wait=off']
        command += (block_options if block_options is not None else
                    ['-drive', f'id=card,if=none,file={card},format=raw'])
        command += ['-device', 'nvme,id=nvme-audit-test,drive=card,serial=QTEST-AUDIT,bus=dw-pcie,addr=0']
        if trace:
            # Existing events also exist in the unpatched red-baseline binary.
            command += ['-trace', 'enable=pci_nvme_*']
        (directory / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
        self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=self.log)
        deadline = time.monotonic() + 10
        try:
            while not (runtime / 'qtest').exists():
                if self.proc.poll() is not None:
                    raise RuntimeError(self.log_path.read_text(errors='replace'))
                if time.monotonic() > deadline:
                    raise TimeoutError('Owned NVMe QTest startup timeout')
                time.sleep(.01)
            self.qmp = QMP(runtime / 'qmp', deadline)
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.settimeout(3)
            self.sock.connect(str(runtime / 'qtest'))
            self.stream = self.sock.makefile('rwb', buffering=0)
            require(not self.qmp.command('query-status')['running'], 'CPU must stay paused')
        except Exception:
            self.close()
            raise

    def q(self, command):
        self.stream.write(command.encode() + b'\n')
        reply = self.stream.readline().decode().strip()
        require(reply.startswith('OK'), f'{command}: {reply}')
        return reply.split()[1] if len(reply.split()) > 1 else None

    def read(self, address, size=4):
        return int(self.q(f'read{ {1:"b",2:"w",4:"l",8:"q"}[size]} {address:#x}'), 0)

    def write(self, address, value, size=4):
        self.q(f'write{ {1:"b",2:"w",4:"l",8:"q"}[size]} {address:#x} {value:#x}')

    def memory(self, address, data):
        self.q(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def bytes(self, address, size):
        return bytes.fromhex(self.q(f'read {address:#x} {size:#x}')[2:])

    def atu(self, index, kind, base, limit, target):
        for offset, value in ((0, kind), (8, base & 0xffffffff), (12, base >> 32),
                              (16, limit & 0xffffffff), (20, target & 0xffffffff),
                              (24, target >> 32), (4, 0x80000000)):
            self.write(ATU + index * 0x200 + offset, value)

    def initialize(self):
        # QTest owns PCI configuration; no firmware instruction is executed.
        self.write(DBI + 4, 0, 2)
        self.write(DBI + 0x18, 0x00010100)
        self.atu(0, 4, 0x50000000, 0x50000fff, 0x01000000)
        config = APERTURE + 0x50000000
        require(self.read(config, 2) != 0xffff, 'NVMe endpoint not decoded')
        self.write(config + 4, 0, 2)
        self.write(config + 0x10, 0xffffffff)
        mask = self.read(config + 0x10)
        size = ((~(mask & 0xfffffff0)) & 0xffffffff) + 1
        require(size >= 0x2000 and size & (size - 1) == 0, 'Invalid BAR0 sizing')
        require(size <= 0x100000, 'BAR0 exceeds test memory window')
        self.write(config + 0x10, 0x10000000)
        self.write(config + 0x14, 0)
        self.write(DBI + 0x20, 0x10001000)
        self.atu(1, 0, 0x70000000, 0x700fffff, 0x10000000)
        self.write(DBI + 4, 6, 2)
        self.write(config + 4, 6, 2)
        self.bar = APERTURE + 0x70000000
        cap = self.read(self.bar, 8)
        require(cap not in (0, 0xffffffffffffffff), 'NVMe CAP unavailable')
        self.stride = 4 << ((cap >> 32) & 15)
        self.queues[0] = dict(sq=RAM, cq=RAM + 0x1000, size=8, tail=0, head=0, phase=1)
        self.memory(RAM, bytes(0x2000))
        self.write(self.bar + 0x24, 7 | (7 << 16))
        self.write(self.bar + 0x28, RAM, 8)
        self.write(self.bar + 0x30, RAM + 0x1000, 8)
        self.write(self.bar + 0x14, 1 | (6 << 16) | (4 << 20))
        self.until(lambda: self.read(self.bar + 0x1c) & 1, 'NVMe ready')

    def until(self, predicate, label, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            require(self.proc.poll() is None, 'Owned QEMU exited: ' + self.log_path.read_text(errors='replace')[-1000:])
            if time.monotonic() > deadline:
                raise TimeoutError(label)
            time.sleep(.001)

    def submit(self, qid, opcode, cid, nsid=0, prp=0, words=(), prp2=0):
        queue = self.queues[qid]
        command = bytearray(64)
        struct.pack_into('<BBHI', command, 0, opcode, 0, cid, nsid)
        struct.pack_into('<Q', command, 24, prp)
        struct.pack_into('<Q', command, 32, prp2)
        for index, value in enumerate(words):
            struct.pack_into('<I', command, 40 + index * 4, value)
        self.memory(queue['sq'] + queue['tail'] * 64, command)
        queue['tail'] = (queue['tail'] + 1) % queue['size']
        self.write(self.bar + 0x1000 + 2 * qid * self.stride, queue['tail'])

    def completion(self, qid, cid, *, acknowledge=True):
        queue = self.queues[qid]
        address = queue['cq'] + queue['head'] * 16
        self.until(lambda: self.read(address + 14, 2) & 1 == queue['phase'], 'Actual CQE')
        result, reserved, head, sqid, actual_cid, status = struct.unpack('<IIHHHH', self.bytes(address, 16))
        require((sqid, actual_cid) == (qid, cid), f'Wrong queue/CID {(sqid, actual_cid)}')
        self.cq_records.append(dict(address=address, sqid=sqid, cid=actual_cid,
                                    status=status, result=result))
        if acknowledge:
            queue['head'] = (queue['head'] + 1) % queue['size']
            if queue['head'] == 0:
                queue['phase'] ^= 1
            self.write(self.bar + 0x1000 + (2 * qid + 1) * self.stride, queue['head'])
        return status >> 1

    def trace(self):
        require(not self.qmp.command('query-status')['running'], 'Scope snapshot requires paused CPU')
        self.end_state = json.loads(self.qmp.command(
            'qom-get', path='/machine/peripheral/nvme-audit-test', property='audit-state'))
        text = self.log_path.read_text(errors='replace')
        (self.folder / 'trace-scope.log').write_text(text)
        rows = parse_trace(text)
        after = json.loads(self.qmp.command(
            'qom-get', path='/machine/peripheral/nvme-audit-test', property='audit-state'))
        require(self.end_state == after, 'NVMe state changed while collecting scope boundary')
        (self.folder / 'trace-scope.json').write_text(json.dumps(dict(
            boundary='Snapshot while owned paused QEMU is alive, before cleanup quit/reset',
            log_bytes=len(text.encode()), events=len(rows),
            last_seq=rows[-1]['seq'] if rows else None,
            last_virtual_ns=rows[-1]['virtual_ns'] if rows else None,
            end_state=self.end_state,
            guest_boundaries_retained=True), indent=2) + '\n')
        return rows

    def create_queue(self, qid, size=4):
        sq = RAM + (2 + 2 * qid) * 0x1000
        cq = sq + 0x1000
        self.memory(sq, bytes(0x2000))
        self.submit(0, 5, 0x100 + qid, prp=cq, words=(qid | ((size - 1) << 16), 1))
        require(self.completion(0, 0x100 + qid) == 0, 'Create CQ failed')
        self.submit(0, 1, 0x200 + qid, prp=sq, words=(qid | ((size - 1) << 16), 1 | (qid << 16)))
        require(self.completion(0, 0x200 + qid) == 0, 'Create SQ failed')
        self.queues[qid] = dict(sq=sq, cq=cq, size=size, tail=0, head=0, phase=1)

    def close(self):
        if self.qmp:
            try:
                self.qmp.command('quit')
            except Exception:
                pass
            self.qmp.close()
        if self.stream:
            self.stream.close()
        if self.sock:
            self.sock.close()
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.log.close()
        self.runtime.cleanup()
        require(self.proc.poll() is not None, 'Owned QEMU not stopped')

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def run(binary, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    kernel = output / 'authored-zero.bin'
    kernel.write_bytes(bytes(4))
    card = output / 'card.raw'
    with card.open('xb') as stream:
        stream.truncate(CARD_BYTES)
    checks, expected = [], []
    machine = None
    try:
        with NvmeMachine(binary, output / 'q0', kernel, card) as machine:
            machine.initialize()
            machine.create_queue(1)
            machine.create_queue(2)
            checks.append(dict(name='actual admin queues with paused authored kernel', passed=True))
            for generation in range(6):
                for qid in (1, 2):
                    lba = 16 + generation * 2 + qid
                    data = bytes((i + 31 * generation + qid) % 256 for i in range(512))
                    address = RAM + 0x10000 + qid * 0x1000
                    machine.memory(address, data)
                    machine.submit(qid, 1, 7, nsid=1, prp=address, words=(lba, 0, 0))
                    expected.append((lba * 512, data, qid))
                for qid in (1, 2):
                    require(machine.completion(qid, 7) == 0, 'Actual WRITE failed')
            machine.submit(1, 0, 8, nsid=1)
            require(machine.completion(1, 8) == 0, 'Actual FLUSH failed')
            require(not machine.qmp.command('query-status')['running'], 'CPU unexpectedly ran')
            checks.append(dict(name='repeat CID, two SQs and reused request slots complete', passed=True))
            # Bound evidence before shutdown's original controller reset/drain.
            rows = machine.trace()
        with card.open('rb') as stream:
            for offset, data, _ in expected:
                stream.seek(offset)
                require(stream.read(len(data)) == data, 'Actual backing card bytes differ')
        checks.append(dict(name='independently read completed ordinary file writes', passed=True))
        writes = completed_writes(rows, machine.end_state)
        require(len(writes) == len(expected), 'Completed WRITE count differs')
        require({(x['offset'], x['sqid'], x['cid'], x['length']) for x in writes} ==
                {(offset, qid, 7, 512) for offset, _, qid in expected}, 'Trace identity/extent mismatch')
        begins = {(r['instance'], r['request']): r for r in rows if r['kind'] == 'begin'}
        actual = Counter((r['address'], r['sqid'], r['cid'], r['status']) for r in machine.cq_records)
        observed = Counter((r['address'], begins[r['instance'], r['request']]['sqid'],
                            begins[r['instance'], r['request']]['cid'], r['wire_status'])
                           for r in rows if r['kind'] == 'posted')
        require(actual == observed, 'Trace CQE addresses/identities differ from actual guest memory')
        checks.append(dict(name='passive backend completion and successful CQE delivery', passed=True))
        cutoff = next(index for index, row in enumerate(rows) if row['kind'] == 'begin'
                      and row['request'] == machine.end_state['last_request_id'])
        require(rows[cutoff - 1]['kind'] == 'posted', 'Cutoff is not a complete-request boundary')
        reject_evidence(rows[:cutoff], machine.end_state)
        checks.append(dict(name='whole completed-request suffix omission rejected by independent QOM counters', passed=True))
    except Exception as error:
        checks.append(dict(name='NVMe queue/completion evidence', passed=False, error=repr(error)))

    owned = []

    def fixture(name, trace=True, error_type=None, error=5, delay=0):
        image = output / (name + '.raw')
        with image.open('xb') as stream:
            stream.truncate(CARD_BYTES)
        block = None
        if error_type:
            rule = dict(event='none', iotype=error_type, errno=error, once=True,
                        immediately=False)
            if delay:
                rule['delay-ns'] = delay
            graph = dict(driver='blkdebug', **{'node-name': 'card', 'image':
                         dict(driver='raw', file=dict(driver='file', filename=str(image))),
                         'inject-error': [rule]})
            block = ['-blockdev', json.dumps(graph, separators=(',', ':'))]
        m = NvmeMachine(binary, output / name, kernel, image, trace=trace, block_options=block)
        owned.append(m)
        return m, image

    def check(name, function):
        try:
            function()
            checks.append(dict(name=name, passed=True))
        except Exception as error:
            checks.append(dict(name=name, passed=False, error=repr(error)))

    def request_rows(rows, cid, sqid=1):
        begin = [r for r in rows if r['kind'] == 'begin' and r['cid'] == cid and r['sqid'] == sqid]
        require(len(begin) == 1, 'Expected one actual request lifecycle')
        return {r['kind']: r for r in rows if r.get('request') == begin[0]['request']
                and r['instance'] == begin[0]['instance']}

    def write(m, cid, lba, value=0x5a):
        m.memory(RAM + 0x10000, bytes([value]) * 512)
        m.submit(1, 1, cid, nsid=1, prp=RAM + 0x10000, words=(lba, 0, 0))

    def multiple_prps():
        m, image = fixture('multiple-prps')
        data = bytes((index * 29 + 17) % 256 for index in range(6144))
        first, second = RAM + 0x20000, RAM + 0x24000
        with m:
            m.initialize()
            m.create_queue(1)
            m.memory(first, data[:4096])
            m.memory(second, data[4096:])
            m.submit(1, 1, 9, nsid=1, prp=first, prp2=second, words=(32, 0, 11))
            require(m.completion(1, 9) == 0, 'Two-PRP WRITE failed')
            writes = completed_writes(m.trace(), m.end_state)
            require(len(writes) == 1 and writes[0]['offset'] == 32 * 512 and
                    writes[0]['length'] == len(data), 'Multi-PRP actual extent truncated')
        with image.open('rb') as stream:
            stream.seek(32 * 512)
            require(stream.read(len(data)) == data, 'Noncontiguous PRPs did not reach actual card')
    check('noncontiguous PRPs preserve the complete multi-page WRITE extent', multiple_prps)

    def backpressure():
        m, image = fixture('backpressure')
        with m:
            m.initialize()
            m.create_queue(1, size=2)
            write(m, 11, 8)
            require(m.completion(1, 11, acknowledge=False) == 0, 'First write failed')
            write(m, 12, 9, 0xa5)
            m.until(lambda: len([r for r in parse_trace(m.log_path.read_text(errors='replace'))
                                 if r['kind'] == 'queued']) >= 4, 'Both real writes internally complete')
            rows = m.trace()
            second = request_rows(rows, 12)
            require('done' in second and 'queued' in second and 'posted' not in second,
                    'CQ-full request falsely delivered or not actually completed')
            reject_evidence(rows, m.end_state)
            require(m.completion(1, 11) == 0 and m.completion(1, 12) == 0, 'CQ drain failed')
            require(len(completed_writes(m.trace(), m.end_state)) == 2, 'Backpressure lost a request')
        with image.open('rb') as stream:
            stream.seek(8 * 512)
            require(stream.read(1024) == bytes([0x5a]) * 512 + bytes([0xa5]) * 512,
                    'Backpressure card bytes differ')
    check('actual CQ-full completion is distinct from later CQE delivery', backpressure)

    def invalid_bounds():
        m, image = fixture('invalid-bounds')
        before = sha256(image)
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 21, CARD_BYTES // 512)
            status = m.completion(1, 21)
            require(status != 0, 'Out-of-range write succeeded')
            rows = m.trace()
            request = request_rows(rows, 21)
            require('dispatch' not in request and 'done' not in request,
                    'Synchronous rejection fabricated a backend completion')
            require(request['queued']['status'] == request['posted']['status'] == status,
                    'Original error status lost')
            reject_evidence(rows, m.end_state)
        require(sha256(image) == before, 'Rejected write modified card')
    check('synchronous invalid range has no backend WRITE completion', invalid_bounds)

    def backend_error():
        m, image = fixture('backend-error', error_type='write')
        before = sha256(image)
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 31, 8)
            status = m.completion(1, 31)
            require(status != 0, 'Injected backend EIO was not propagated')
            rows = m.trace()
            request = request_rows(rows, 31)
            require(request['done']['ret'] == -5 and request['done']['status'] == status,
                    'Final backend error does not match original CQ')
            reject_evidence(rows, m.end_state)
        require(sha256(image) == before, 'Failed backend WRITE changed card')
    check('actual asynchronous block backend EIO stays failed', backend_error)

    def flush_error():
        m, _ = fixture('flush-error', error_type='flush')
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 32, 8)
            require(m.completion(1, 32) == 0, 'Pre-flush WRITE failed')
            m.submit(1, 0, 33, nsid=1)
            status = m.completion(1, 33)
            require(status != 0, 'Injected FLUSH error not propagated')
            rows = m.trace()
            flush = request_rows(rows, 33)
            require(flush['flush']['ret'] == -5 and flush['flush']['status'] == status,
                    'FLUSH final result missing')
            require('done' not in flush and 'dispatch' not in flush, 'FLUSH created false written extent')
            reject_evidence(rows, m.end_state)
    check('FLUSH failure is recorded separately and never creates a WRITE extent', flush_error)

    def abort_pending():
        m, _ = fixture('abort-pending', error_type='write', error=0, delay=300000000)
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 41, 8)
            m.until(lambda: 'pci_nvme_audit_dispatch ' in m.log_path.read_text(errors='replace'),
                    'Actual pending backend write')
            require('done' not in request_rows(m.trace(), 41), 'I/O completed before abort test')
            m.submit(0, 8, 42, words=(1 | (41 << 16),))
            require(m.completion(0, 42) == 0, 'Original Abort admin request failed')
            status = m.completion(1, 41)
            rows = m.trace()
            require(any(r['kind'] == 'boundary' and r['boundary'] == 2 for r in rows), 'Abort intent missing')
            target = request_rows(rows, 41)
            require(target['done']['status'] == status, 'Abort target final status was changed')
            # The original controller owns the cancel race and status conversion.
            reject_evidence(rows, m.end_state)
    check('actual Abort is distinguished from target final completion', abort_pending)

    def reset_boundary():
        m, _ = fixture('reset-boundary')
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 51, 8)
            require(m.completion(1, 51) == 0, 'Pre-reset WRITE failed')
            m.write(m.bar + 0x14, 0)
            m.until(lambda: m.read(m.bar + 0x1c) & 1 == 0, 'Original reset completion')
            rows = m.trace()
            require(any(r['kind'] == 'boundary' and r['boundary'] == 1 for r in rows), 'Reset boundary missing')
            reject_evidence(rows, m.end_state)
            old_max = max(r['request'] for r in rows if r['kind'] == 'begin')
            m.initialize()
            m.create_queue(1)
            write(m, 51, 9, 0x61)
            require(m.completion(1, 51) == 0, 'Post-reset repeated CID WRITE failed')
            rows = m.trace()
            ids = [r['request'] for r in rows if r['kind'] == 'begin']
            require(len(ids) == len(set(ids)) and ids[-1] > old_max, 'Controller reset reused lifecycle IDs')
            reject_evidence(rows, m.end_state)
    check('actual controller reset invalidates an unbounded evidence scope', reset_boundary)

    def delete_boundary():
        m, _ = fixture('delete-boundary')
        with m:
            m.initialize()
            m.create_queue(1)
            write(m, 61, 8)
            require(m.completion(1, 61) == 0, 'Pre-delete WRITE failed')
            m.submit(0, 0, 62, words=(1,))
            require(m.completion(0, 62) == 0, 'Original Delete SQ failed')
            rows = m.trace()
            require(any(r['kind'] == 'boundary' and r['boundary'] == 3 for r in rows), 'Delete SQ boundary missing')
            reject_evidence(rows, m.end_state)
            old_max = max(r['request'] for r in rows if r['kind'] == 'begin')
            m.submit(0, 4, 63, words=(1,))
            require(m.completion(0, 63) == 0, 'Delete CQ failed')
            m.create_queue(1)
            write(m, 61, 9)
            require(m.completion(1, 61) == 0, 'Recreated SQ WRITE failed')
            rows = m.trace()
            ids = [r['request'] for r in rows if r['kind'] == 'begin']
            require(len(ids) == len(set(ids)) and ids[-1] > old_max, 'Recreated queue reused lifecycle IDs')
            reject_evidence(rows, m.end_state)
    check('actual Delete SQ is an explicit lifecycle boundary', delete_boundary)

    def enabled_disabled():
        results = []
        for enabled in (False, True):
            m, image = fixture('trace-on' if enabled else 'trace-off', trace=enabled)
            with m:
                m.initialize()
                m.create_queue(1)
                write(m, 71, 8, 0x76)
                require(m.completion(1, 71) == 0, 'A/B WRITE failed')
                m.submit(1, 0, 72, nsid=1)
                require(m.completion(1, 72) == 0, 'A/B FLUSH failed')
                rows = m.trace()
                require(not m.qmp.command('query-status')['running'], 'A/B CPU ran')
                if enabled:
                    require(len(completed_writes(rows, m.end_state)) == 1, 'Enabled trace missing WRITE')
                else:
                    require(rows == [], 'Disabled tracing emitted audit output')
            results.append((sha256(image), m.cq_records, m.end_state))
        require(results[0] == results[1], 'Trace changed original CQE bytes or backing card')
    check('trace disabled/enabled has identical real CQ results and card bytes', enabled_disabled)

    report = dict(passed=all(x['passed'] for x in checks), checks=checks,
                  qemu_sha256=sha256(binary), kernel_sha256=sha256(kernel),
                  card_sha256=sha256(card), firmware_used=False, cpu_execution=False,
                  camera_acceptance=False,
                  owned_qemu_stopped=(machine is None or machine.proc.poll() is not None) and
                  all(m.proc.poll() is not None for m in owned))
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


class TraceParserTests(unittest.TestCase):
    def end_state(self, **changes):
        return dict(schema=1, instance=1, last_request_id=1, last_event_seq=6,
                    virtual_ns=0) | changes

    def successful_rows(self):
        values = [
            ('instance', dict(bus=1, devfn=0)),
            ('begin', dict(request=1, sqid=2, cqid=3, cid=7, nsid=1, opcode=1)),
            ('dispatch', dict(request=1, offset=4096, length=1024, supported=1,
                              lbads=9, metadata=0, pi=0)),
            ('done', dict(request=1, offset=4096, length=1024, status=0, ret=0)),
            ('queued', dict(request=1, status=0)),
            ('posted', dict(request=1, cqid=3, address=RAM, status=0, wire_status=1)),
        ]
        # Equal virtual times are legitimate while CPU execution is paused.
        text = '\n'.join('pci_nvme_audit_' + kind + ' ' + ' '.join(
            f'{key}={value}' for key, value in dict(virtual_ns=0, instance=1, seq=i, **data).items())
            for i, (kind, data) in enumerate(values, 1))
        return parse_trace(text)

    def test_complete_lifecycle_retains_queue_identity_and_extent(self):
        rows = self.successful_rows()
        result = completed_writes(rows, self.end_state())
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['sqid'], result[0]['cqid'], result[0]['cid'],
                          result[0]['nsid'], result[0]['offset'], result[0]['length']),
                         (2, 3, 7, 1, 4096, 1024))

    def test_independent_end_state_is_required(self):
        with self.assertRaisesRegex(AssertionError, 'end.state'):
            completed_writes(self.successful_rows())

    def test_truncation_at_complete_request_boundary_is_rejected(self):
        rows = self.successful_rows()
        second = [dict(row, seq=row['seq'] + 5, request=2) for row in rows[1:]]
        complete = rows + second
        # An independent device snapshot knows another complete request exists.
        end_state = dict(schema=1, instance=1, last_request_id=2,
                         last_event_seq=11, virtual_ns=0)
        self.assertEqual(len(completed_writes(complete, end_state)), 2)
        with self.assertRaisesRegex(AssertionError, 'end.state'):
            completed_writes(rows, end_state)

    def test_end_state_identity_counts_time_and_types_are_verified(self):
        for changes in ({'instance': 2}, {'last_request_id': 0},
                        {'last_request_id': 2}, {'last_event_seq': 5},
                        {'last_event_seq': 7}, {'schema': 2},
                        {'last_request_id': True}, {'virtual_ns': -1}):
            with self.subTest(changes=changes), self.assertRaisesRegex(AssertionError, 'end.state'):
                completed_writes(self.successful_rows(), self.end_state(**changes))
        rows = self.successful_rows()
        for row in rows:
            row['virtual_ns'] = 1
        with self.assertRaisesRegex(AssertionError, 'end.state'):
            completed_writes(rows, self.end_state())

    def test_no_completion_evidence_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'No passive'):
            completed_writes(parse_trace('pci_nvme_write cid 7\n'))

    def test_malformed_identity_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'identity'):
            parse_trace('pci_nvme_audit_done status=0')

    def test_backend_failure_abort_status_and_cq_failure_are_rejected(self):
        for index, field, value in ((3, 'ret', -5), (3, 'status', 7),
                                    (4, 'status', 1), (5, 'wire_status', 3)):
            with self.subTest(index=index, field=field):
                rows = self.successful_rows()
                rows[index][field] = value
                with self.assertRaises(AssertionError):
                    completed_writes(rows, self.end_state())

    def test_absent_duplicate_and_out_of_order_stages_are_rejected(self):
        original = self.successful_rows()
        variants = [original[:-1], original[1:], original[:2] + original[3:],
                    original[:3] + [original[3], original[3]] + original[4:],
                    original[:3] + [original[4], original[3]] + original[5:]]
        for rows in variants:
            with self.subTest(kinds=[r['kind'] for r in rows]), self.assertRaises(AssertionError):
                completed_writes(rows, self.end_state())

    def test_unknown_identity_extent_and_unsupported_geometry_are_rejected(self):
        for index, field, value in ((3, 'request', 9), (3, 'offset', 8192),
                                    (3, 'length', 512), (5, 'cqid', 4),
                                    (2, 'supported', 0), (2, 'metadata', 8),
                                    (2, 'pi', 1), (2, 'offset', 4097),
                                    (2, 'lbads', 8), (2, 'length', 0)):
            with self.subTest(index=index, field=field):
                rows = self.successful_rows()
                rows[index][field] = value
                with self.assertRaises(AssertionError):
                    completed_writes(rows, self.end_state())

    def test_lifecycle_and_event_sequences_cannot_be_reused(self):
        rows = self.successful_rows()
        duplicate = dict(rows[1], seq=7)
        with self.assertRaisesRegex(AssertionError, 'lifecycle reused'):
            completed_writes(rows + [duplicate], self.end_state())
        rows = self.successful_rows()
        rows[-1]['seq'] += 1
        with self.assertRaisesRegex(AssertionError, 'Missing or duplicate'):
            completed_writes(rows, self.end_state())

    def test_regressing_time_and_boundary_are_fail_closed(self):
        rows = self.successful_rows()
        rows[0]['virtual_ns'] = 2
        with self.assertRaisesRegex(AssertionError, 'regressed'):
            completed_writes(rows, self.end_state())
        for boundary in (1, 2, 3, 4, 5, 6):
            rows = self.successful_rows()
            rows.append(dict(kind='boundary', virtual_ns=0, instance=1, seq=7,
                             request=0, boundary=boundary, status=0, detail=0))
            with self.subTest(boundary=boundary), self.assertRaisesRegex(AssertionError, 'boundary'):
                completed_writes(rows, self.end_state())

    def test_duplicate_unknown_or_out_of_range_trace_fields_are_rejected(self):
        for line in (
                'pci_nvme_audit_unknown virtual_ns=0 instance=1 seq=1',
                'pci_nvme_audit_instance virtual_ns=0 instance=1 seq=1 bus=1 devfn=0 bus=1',
                'pci_nvme_audit_instance virtual_ns=0 instance=1 seq=1 bus=1 devfn=0 extra=4',
                'pci_nvme_audit_instance virtual_ns=-1 instance=1 seq=1 bus=1 devfn=0',
                'pci_nvme_audit_instance virtual_ns=0 instance=0 seq=1 bus=1 devfn=0'):
            with self.subTest(line=line), self.assertRaises(AssertionError):
                parse_trace(line)

    def test_nonwrite_failure_and_incomplete_flush_are_rejected(self):
        base = self.successful_rows()
        rows = [copy.deepcopy(base[0]), dict(base[1], opcode=0),
                dict(base[4], seq=3), dict(base[5], seq=4)]
        with self.assertRaisesRegex(AssertionError, 'FLUSH'):
            completed_writes(rows, self.end_state(last_event_seq=4))
        rows[1]['opcode'] = 2
        rows[2]['status'] = 1
        with self.assertRaisesRegex(AssertionError, 'Non-write'):
            completed_writes(rows, self.end_state(last_event_seq=4))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.qemu.resolve(), args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
