# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh paused-CPU instruction translation and original-byte guard.

All physical reads are exactly four bytes in a currently recognized flatview
region. Translation results are evidence, never an alias rule or a cache.
"""
import hashlib
import json
from z8_writer_code_policy import cpu_state, decode, parse_translation, safe_regions
from z8_writer_observer import EXPECTED, ObservationError


class RuntimeCodeReader:
    MAX_READS = 8192
    MAX_VIEWS = 32
    MAX_EVIDENCE = 64 << 20

    def __init__(self, guest, geometry, host_check):
        self.g, self.geometry, self.host_check = guest, geometry, host_check
        self.evidence_bytes = 0
        self.failed = False
        self.receipt = dict(scope='Fresh actual paused CPU translation at activation and each writer hit',
            mapping_reused=False, assumed_aliases=[], native_accepted=False,
            guest_ram_writes=False, guest_register_writes=False, guest_event_injection=False,
            limits=dict(reads=self.MAX_READS, distinct_views=self.MAX_VIEWS, evidence_bytes=self.MAX_EVIDENCE),
            geometry=geometry, views={}, reads=[], first_failure=None)
        # Activation errors must remain visible even before observer.enabled.
        self.g.data['native_code_guard_receipt'] = self.receipt

    def persist(self):
        self.receipt['evidence_bytes'] = self.evidence_bytes
        (self.g.out / 'native-code-guards.json').write_text(json.dumps(self.receipt, indent=2) + '\n')

    def _paused(self):
        self.host_check()
        status = self.g.qmp.command('query-status')
        if not isinstance(status, dict) or status.get('running') is not False:
            raise ObservationError('Code guard requires an already paused guest')
        return status

    def _translation(self, cpu, va):
        self.host_check()
        return parse_translation(self.g.qmp.hmp(f'gva2gpa {va:#x}', cpu))

    def _view(self, row, side):
        self.host_check()
        tree = self.g.qmp.hmp('info mtree -f', 0)
        if not isinstance(tree, str) or len(tree) > 1 << 20:
            raise ObservationError('Invalid bounded physical memory flatview')
        key = hashlib.sha256(tree.encode()).hexdigest()
        row[side]['mtree_sha256'] = key
        if key not in self.receipt['views']:
            if len(self.receipt['views']) >= self.MAX_VIEWS:
                raise ObservationError('Code flatview evidence cap exceeded')
            self.receipt['views'][key] = tree
            self.evidence_bytes += len(tree.encode())
        regions, warnings = safe_regions(tree, self.geometry['base_memory'], True)
        row[side]['region_warnings'] = warnings
        return regions

    @staticmethod
    def _region(regions, pa):
        selected = [r for r in regions if type(pa) is int and 0 <= pa <= (1 << 64) - 4
                    and pa % 4 == 0 and r['base'] <= pa and pa + 4 <= r['end']]
        if len(selected) != 1:
            raise ObservationError('Code PA is unmapped, MMIO, unknown, ambiguous or outside a complete safe four-byte span')
        return selected[0]

    def _sample(self, row, side, cpu, va):
        state = row.setdefault(side, {})
        row['last_action'] = side + '-paused-state'
        state['status'] = self._paused()
        state['cpu_state'] = cpu_state(self.g, cpu)
        if not {'PC', 'SP', 'PSTATE'} <= state['cpu_state']['fields'].keys():
            raise ObservationError('Code guard CPU PC/SP/PSTATE unavailable')
        if row['phase']=='hit' and side=='before' and state['cpu_state']['pc']!=va:
            raise ObservationError('Actual paused CPU is no longer at the observed code boundary')
        row['last_action'] = side + '-exact-translation'
        state['exact'] = self._translation(cpu, va)
        row['last_action'] = side + '-page-translation'
        state['page'] = self._translation(cpu, va & ~4095)
        exact, page = state['exact'], state['page']
        if exact['state'] != 'mapped' or page['state'] != 'mapped':
            raise ObservationError('Code VA/page translation unavailable')
        state['page_offset_consistent'] = (page['pa'] % 4096 == 0 and
                                         exact['pa'] == page['pa'] + (va & 4095))
        if not state['page_offset_consistent']:
            raise ObservationError('Exact/page code translation disagrees')
        row['last_action'] = side + '-physical-flatview'
        regions = self._view(row, side)
        row['safe_region_' + side] = self._region(regions, exact['pa'])
        return state

    def read(self, cpu, va, *, phase):
        if self.failed:
            raise ObservationError('Code reader invalidated by the retained first failure')
        row = dict(index=len(self.receipt['reads']),
                   cpu=cpu if type(cpu) is int else repr(cpu)[:256],
                   va=va if type(va) is int else repr(va)[:256], phase=phase,
                   expected_hex=EXPECTED[va].hex() if type(va) is int and va in EXPECTED else None,
                   verified=False, read_skipped=True, last_action='validate-request')
        # Preserve the rejected request at the boundary as well (at most one
        # extra row, since integration invalidates the observer on any failure).
        self.receipt['reads'].append(row)
        try:
            # Reserve room for two bounded views and CPU/reply evidence before
            # starting a query. Any failed reader latches and adds no more rows.
            if row['index'] >= self.MAX_READS or self.evidence_bytes >= self.MAX_EVIDENCE-(4 << 20):
                raise ObservationError('Code guard evidence budget exhausted')
            if type(cpu) is not int or cpu not in range(4) or type(va) is not int or va not in EXPECTED:
                raise ObservationError('Invalid actual CPU or unreserved code VA')
            if phase not in ('activation', 'hit'):
                raise ObservationError('Code guard phase must be activation or actual hit')
            self.host_check()
            clock = decode(self.g.qmp.command('qom-get', path='/machine', property='runtime-clock-state'))
            row['virtual_ns'] = clock.get('virtual_ns')
            if type(row['virtual_ns']) is not int or row['virtual_ns'] < 0:
                raise ObservationError('Invalid paused code-guard virtual timestamp')
            before = self._sample(row, 'before', cpu, va)
            row['last_action'] = 'physical-read4'
            self._paused()
            data = self.g.read_bytes(before['exact']['pa'], 4)
            row['read_skipped'] = False
            if not isinstance(data, bytes) or len(data) != 4:
                raise ObservationError('Short or invalid code read')
            row['actual_hex'] = data.hex()
            row['opcode_matches'] = data == EXPECTED[va]
            # Record post-read evidence even on a wrong opcode.
            after = self._sample(row, 'after', cpu, va)
            # Get registers again after post translations: if another actor
            # resumed or changed state in this interval, do not accept it.
            after['cpu_state'] = cpu_state(self.g, cpu)
            after['status'] = self._paused()
            row['mapping_unchanged'] = all(before[k] == after[k] for k in ('exact', 'page'))
            row['cpu_state_unchanged'] = before['cpu_state']['fields'] == after['cpu_state']['fields']
            row['safe_region_unchanged'] = row['safe_region_before'] == row['safe_region_after']
            if not row['mapping_unchanged']:
                raise ObservationError('Code translation changed across read')
            if not row['cpu_state_unchanged']:
                raise ObservationError('Paused CPU state changed across code read')
            if not row['safe_region_unchanged']:
                raise ObservationError('Code physical region/type changed across read')
            if not row['opcode_matches']:
                raise ObservationError('Translated instruction differs from original MAIN opcode')
            row['verified'] = True
            row['last_action'] = 'complete'
            return data
        except Exception as error:
            self.failed = True
            row['error'] = repr(error)
            if self.receipt['first_failure'] is None:
                self.receipt['first_failure'] = dict(index=row['index'], cpu=row['cpu'], va=row['va'], reason=str(error))
            raise ObservationError(f'Code guard CPU {cpu!r} VA {va!r}: {error}') from error
        finally:
            self.evidence_bytes += len(json.dumps(row).encode())
            self.persist()

    def activation_word(self, va):
        word = None
        for cpu in range(4):
            word = self.read(cpu, va, phase='activation')
        return word
