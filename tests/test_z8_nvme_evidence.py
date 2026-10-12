# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic passive collector contracts; no firmware or emulator is started."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_capture_probe as probe
import z8_nvme_evidence as evidence


def fixture(failed=False, pending_opcode=12):
    status = 0x280 if failed else 0
    values = [
        ('instance', dict(bus=0, devfn=0)),
        ('begin', dict(request=1, sqid=1, cqid=2, cid=7, nsid=1, opcode=1)),
        ('dispatch', dict(request=1, offset=4096, length=512, supported=1, lbads=9, metadata=0, pi=0)),
        ('done', dict(request=1, offset=4096, length=512, status=status, ret=-5 if failed else 0)),
        ('queued', dict(request=1, status=status)),
        ('posted', dict(request=1, cqid=2, address=0xa00010000, status=status, wire_status=status * 2 + 1)),
        ('begin', dict(request=2, sqid=0 if pending_opcode == 12 else 1,
                       cqid=0 if pending_opcode == 12 else 2, cid=7, nsid=0, opcode=pending_opcode)),
    ]
    rows = [dict(kind=kind, virtual_ns=1000, instance=1, seq=index, **data)
            for index, (kind, data) in enumerate(values, 1)]
    end = dict(schema=1, instance=1, last_request_id=2, last_event_seq=7, virtual_ns=1000)
    return rows, end


def encode(rows):
    return ('unrelated original stderr line\n' + '\n'.join(
        '[trace] pci_nvme_audit_' + row['kind'] + ' ' + ' '.join(
            f'{key}={value}' for key, value in row.items() if key != 'kind') for row in rows) + '\n').encode()


class FakeQMP:
    def __init__(self, before, after=None, running=False):
        self.states = iter([before, copy.deepcopy(before) if after is None else after])
        self.running = running
        self.calls = []

    def command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == 'query-status':
            return dict(running=self.running, status='running' if self.running else 'paused')
        if command != 'qom-get':
            raise AssertionError('Collector must use read-only QMP calls')
        if kwargs != dict(path=evidence.CONTROLLER_PATH, property='audit-state'):
            raise AssertionError('Wrong stable controller identity')
        value = next(self.states)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


class CollectionContracts(unittest.TestCase):
    def test_explicit_trace_requires_card_before_any_preparation(self):
        with patch.object(probe, 'PreparedGuest') as constructor:
            with self.assertRaisesRegex(ValueError, 'card'):
                probe.run(SimpleNamespace(nvme_trace=True, card=False))
            constructor.assert_not_called()
        self.assertFalse(evidence.validate_options(SimpleNamespace(card=False)))

    def test_enabled_command_uses_named_controller_and_all_audit_events(self):
        guest = SimpleNamespace(command=['qemu'], data={})
        evidence.enable_trace(guest)
        self.assertEqual(guest.command, ['qemu', '-trace', 'enable=pci_nvme_audit_*'])
        self.assertIn('id=' + evidence.CONTROLLER_ID + ',', evidence.CARD_DEVICE)
        self.assertEqual(guest.data['nvme_trace']['controller_path'], evidence.CONTROLLER_PATH)
        self.assertFalse(guest.data['nvme_trace']['nef_accepted'])

    def test_pending_aer_is_retained_without_claiming_a_photo(self):
        rows, end = fixture()
        receipt = evidence.check_integrity(evidence.decode_prefix(encode(rows)), end)
        self.assertEqual(receipt['pending_aer_request_ids'], [2])
        self.assertEqual(receipt['pending_request_ids'], [2])
        self.assertEqual(receipt['requests_claimed'], 2)
        self.assertEqual(receipt['ordinary_write_done'], 1)
        self.assertEqual(receipt['ordinary_write_cqe_posted'], 1)
        self.assertFalse(receipt['all_commands_completed'])
        self.assertFalse(receipt['nef_accepted'])

    def test_pending_write_and_backend_failure_are_preserved(self):
        rows, end = fixture(failed=True, pending_opcode=1)
        receipt = evidence.check_integrity(rows, end)
        self.assertEqual(receipt['pending_request_ids'], [2])
        self.assertEqual(receipt['pending_aer_request_ids'], [])
        self.assertEqual(receipt['failed_event_sequences'], [4, 5, 6])
        self.assertFalse(receipt['nef_accepted'])

    def test_complete_request_boundary_truncation_requires_independent_counters(self):
        rows, end = fixture()
        with self.assertRaisesRegex(ValueError, 'end-state'):
            evidence.check_integrity(rows[:-1], end)

    def test_missing_duplicate_foreign_and_reordered_events_are_rejected(self):
        rows, end = fixture()
        variants = [rows[1:], rows[:3] + rows[4:], rows + [rows[-1]],
                    rows[:3] + [rows[4], rows[3]] + rows[5:]]
        foreign = copy.deepcopy(rows); foreign[4]['instance'] = 2; variants.append(foreign)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                evidence.check_integrity(changed, end)

    def test_bad_lifecycle_and_end_state_are_not_coerced(self):
        rows, end = fixture()
        for replacement in (None, {}, dict(end, last_request_id=True),
                            dict(end, instance=2), dict(end, last_request_id=3),
                            dict(end, virtual_ns=999)):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                evidence.check_integrity(rows, replacement)
        wrong = copy.deepcopy(rows); wrong[-1]['request'] = 1
        with self.assertRaises(ValueError): evidence.check_integrity(wrong, end)
        wrong = copy.deepcopy(rows); wrong[3]['request'] = 99
        with self.assertRaises(ValueError): evidence.check_integrity(wrong, end)

    def test_parser_does_not_hide_bad_or_incomplete_audit_lines(self):
        rows, _ = fixture()
        for data in (encode(rows)[:-1], encode(rows) + b'pci_nvme_audit_unsupported x=1\n',
                     encode(rows).replace(b'seq=3 ', b'seq=3 seq=3 ', 1),
                     encode(rows).replace(b'length=512 ', b'length=no ', 1)):
            with self.subTest(data=data[-90:]), self.assertRaises(ValueError):
                evidence.decode_prefix(data)

    def test_reset_and_abort_boundaries_remain_in_collection(self):
        rows, end = fixture()
        boundary = dict(kind='boundary', virtual_ns=1000, instance=1, seq=8,
                        request=0, boundary=1, status=0, detail=0)
        receipt = evidence.check_integrity(rows + [boundary], dict(end, last_event_seq=8))
        self.assertEqual(receipt['boundary_events'], [boundary])
        self.assertEqual(receipt['pending_aer_request_ids'], [2])
        self.assertFalse(receipt['nef_accepted'])

    def test_flush_invalid_ranges_and_stage_reordering(self):
        rows, end = fixture(pending_opcode=0)
        rows += [dict(kind='flush', virtual_ns=1000, instance=1, seq=8,
                      request=2, status=0, ret=0)]
        self.assertEqual(len(evidence.decode_prefix(encode(rows))), 8)
        variants = []
        for index, key, value in ((1, 'request', True), (1, 'cid', 65536),
                                  (1, 'opcode', 256), (3, 'ret', 1 << 31),
                                  (2, 'virtual_ns', 1 << 63), (0, 'kind', [])):
            changed = copy.deepcopy(rows); changed[index][key] = value; variants.append(changed)
        changed = copy.deepcopy(rows); changed[-1]['request'] = 1; variants.append(changed)
        changed = copy.deepcopy(rows); changed[5]['cqid'] = 3; variants.append(changed)
        changed = copy.deepcopy(rows); changed[4], changed[3] = changed[3], changed[4]
        for index, row in enumerate(changed, 1): row['seq'] = index
        variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                evidence.check_integrity(changed, dict(end, last_event_seq=8))

    def test_native_counter_absence_and_trace_disabled_do_not_pass(self):
        with self.assertRaises(ValueError):
            evidence.check_integrity([], dict(schema=1, instance=1,
                                             last_request_id=0, last_event_seq=1, virtual_ns=0))
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evidence.validate_options(SimpleNamespace(nvme_trace=value, card=True))

    def test_startup_opt_in_and_default_off_are_real_command_paths(self):
        class BeforeStart(Exception): pass
        class Prepared:
            def __init__(self, out):
                self.out, self.command, self.data = out, ['qemu', '-M', 'nikon-z8-bringup'], {}
            def __enter__(self): raise BeforeStart()
            def __exit__(self, *args): raise AssertionError('No QEMU should be started')
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp); prepared = Prepared(folder)
                args = SimpleNamespace(nvme_trace=enabled, card=True, seconds=15,
                    output=folder, firmware=folder/'unused-main', qemu=folder/'unused-qemu',
                    raw=folder/'unused-raw', capture_properties=None, tagged_input=False,
                    capture_selector=False, capture_pixels=False, gpio=False)
                with patch.object(probe, 'PreparedGuest', return_value=prepared), \
                     patch.object(probe, 'contact_sequence', return_value=([], 0)), \
                     patch('virtual_recording_card.create', return_value={'synthetic': True}), \
                     self.assertRaises(BeforeStart):
                    probe.run(args)
                self.assertIn(evidence.CARD_DEVICE, prepared.command)
                self.assertEqual('enable=pci_nvme_audit_*' in prepared.command, enabled)
                self.assertEqual('nvme_trace' in prepared.data, enabled)
                sources = prepared.data['probe_sources']
                # Its controller constant and argument guard are used even
                # when tracing is off, so their source is always identified.
                self.assertTrue(any(Path(name).name == 'z8_nvme_evidence.py' for name in sources))

    def guest(self, folder, before, after=None, running=False):
        return SimpleNamespace(out=folder, data={'nvme_trace': dict(enabled=True)},
                               qmp=FakeQMP(before, after, running))

    def test_collect_keeps_exact_prefix_and_independent_stable_snapshots(self):
        rows, end = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); original = encode(rows)
            (folder / 'stderr.log').write_bytes(original)
            guest = self.guest(folder, end)
            receipt = evidence.collect(guest)
            self.assertTrue(receipt['collection_complete'])
            self.assertEqual(receipt['end_state_before'], end)
            self.assertEqual(receipt['end_state_after'], end)
            self.assertEqual((folder / receipt['stderr_prefix']['path']).read_bytes(), original)
            self.assertEqual(receipt['stderr_prefix']['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual((folder / 'stderr.log').read_bytes(), original)
            self.assertEqual(len(guest.qmp.calls), 4)
            self.assertFalse(receipt['nef_accepted'])
            saved = json.loads((folder / 'nvme-evidence.json').read_text())
            self.assertEqual(saved, receipt)

    def test_unstable_or_running_scope_keeps_evidence_but_is_incomplete(self):
        rows, end = fixture()
        for after, running in ((dict(end, last_event_seq=8), False), (end, True)):
            with self.subTest(after=after, running=running), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp); (folder / 'stderr.log').write_bytes(encode(rows))
                receipt = evidence.collect(self.guest(folder, end, after, running), run_failure='original capture failed')
                self.assertFalse(receipt['collection_complete'])
                self.assertEqual(receipt['end_state_before'], end)
                self.assertEqual(receipt['end_state_after'], after)
                self.assertEqual(receipt['run_failure'], 'original capture failed')
                self.assertEqual((folder / 'nvme-stderr-prefix.log').read_bytes(), encode(rows))
                self.assertTrue(receipt['issues'])

    def test_missing_getter_and_malformed_log_remain_reviewable(self):
        rows, end = fixture()
        for before, after, data in ((RuntimeError('missing getter'), RuntimeError('missing getter'), encode(rows)),
                                    ('not json', end, encode(rows)), (end, end, b'pci_nvme_audit_done broken\n')):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp); (folder / 'stderr.log').write_bytes(data)
                receipt = evidence.collect(self.guest(folder, before, after))
                self.assertFalse(receipt['collection_complete'])
                self.assertEqual((folder / 'nvme-stderr-prefix.log').read_bytes(), data)
                self.assertTrue(receipt['issues'])

    def test_existing_evidence_is_not_overwritten(self):
        rows, end = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); (folder / 'stderr.log').write_bytes(encode(rows))
            (folder / 'nvme-stderr-prefix.log').write_bytes(b'earlier evidence')
            receipt = evidence.collect(self.guest(folder, end))
            self.assertFalse(receipt['collection_complete'])
            self.assertEqual((folder / 'nvme-stderr-prefix.log').read_bytes(), b'earlier evidence')

    def test_missing_oversized_or_existing_receipt_preserves_original(self):
        rows, end = fixture()
        for kind in ('missing', 'oversized', 'receipt'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                if kind != 'missing': (folder / 'stderr.log').write_bytes(encode(rows))
                if kind == 'receipt': (folder / 'nvme-evidence.json').write_text('earlier receipt')
                with patch.object(evidence, 'MAX_PREFIX_BYTES', 1 if kind == 'oversized' else 2**20):
                    receipt = evidence.collect(self.guest(folder, end))
                self.assertFalse(receipt['collection_complete'])
                self.assertTrue(receipt['issues'])
                if kind != 'missing': self.assertEqual((folder/'stderr.log').read_bytes(), encode(rows))
                if kind == 'receipt': self.assertEqual((folder/'nvme-evidence.json').read_text(), 'earlier receipt')

    def test_probe_finalization_collects_after_stop_and_before_peer_cleanup(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            guest = SimpleNamespace(out=Path(tmp), data=dict(capture_trace={}, nvme_trace={'enabled': True}),
                qmp=SimpleNamespace(command=lambda command, **kw: calls.append(command)))
            peer = SimpleNamespace(close=lambda: calls.append('peer.close'))
            def collect(g, run_failure=None):
                self.assertEqual(calls, ['stop']); calls.append('collect')
                return dict(collection_complete=True, nef_accepted=False)
            with patch.object(probe, 'state', return_value={}), patch.object(evidence, 'collect', side_effect=collect):
                probe.finalize_trace(guest, peer, SimpleNamespace(report=lambda: {}))
            self.assertEqual(calls, ['stop', 'collect', 'peer.close'])
            self.assertFalse(guest.data['nvme_evidence']['nef_accepted'])

    def test_probe_collection_error_cannot_hide_capture_failure_or_cleanup(self):
        for failure, enabled in ((None, True), (TimeoutError('native timeout'), True), (None, False)):
            with self.subTest(failure=failure, enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                calls = []
                guest = SimpleNamespace(out=Path(tmp), data=dict(capture_trace={}, nvme_trace={'enabled': enabled}),
                    qmp=SimpleNamespace(command=lambda command, **kw: calls.append(command)))
                peer = SimpleNamespace(close=lambda: calls.append('peer.close'))
                receipt = dict(collection_complete=False, nef_accepted=False, issues=['missing end-state'])
                with patch.object(probe, 'state', return_value={}), \
                     patch.object(evidence, 'collect', return_value=receipt) as collector:
                    if failure is None and enabled:
                        with self.assertRaises(RuntimeError):
                            probe.finalize_trace(guest, peer, SimpleNamespace(report=lambda: {}), failure)
                    else:
                        probe.finalize_trace(guest, peer, SimpleNamespace(report=lambda: {}), failure)
                self.assertEqual(calls[-1], 'peer.close')
                self.assertEqual(collector.call_count, int(enabled))
                if failure is not None:
                    self.assertEqual(guest.data['capture_trace']['failure'], repr(failure))
                    collector.assert_called_once_with(guest, run_failure=repr(failure))


if __name__ == '__main__':
    unittest.main()
