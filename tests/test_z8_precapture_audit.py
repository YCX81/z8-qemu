# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic checker contracts only: no firmware, capture, or real NEF is made."""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_precapture_audit as audit


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tiff_contract_bytes(marker):
    # Merely a TIFF-shaped parser fixture; NOT a decodable photograph/NEF.
    maker = b'NIKON CORPORATION\0'
    return (b'II*\0' + struct.pack('<I', 8) + struct.pack('<H', 1)
            + struct.pack('<HHII', 271, 2, len(maker), 26) + bytes(4)
            + maker + bytes([marker]) * 32)


class PrecaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.arrays = {}
        frames = []
        for i in range(4):
            samples = (np.arange(12, dtype=np.uint16) + i * 100).reshape(3, 4)
            self.arrays[f'f{i}'] = samples
            raw = samples.astype('<u2').tobytes()
            (self.root / f'p{i}.bin').write_bytes(raw)
            frames.append(dict(frame_id=f'f{i}', job_id=f'j{i}', producer=dict(
                path=f'p{i}.bin', sha256=digest(raw), width=4, height=3,
                stride_samples=4, bit_depth=14, encoding='u16le',
                address=0xA10000000 + i * 0x1000, origin='guest_producer_dma')))
        events = []

        def event(kind, t, frame=None, **values):
            row = dict(seq=len(events), event_id=f'e{len(events)}', kind=kind,
                       clock_id='virtual-0', t_ns=t, **values)
            if frame is not None:
                row.update(frame_id=f'f{frame}', job_id=f'j{frame}')
            events.append(row)

        event('dma_complete', 10, 0)
        event('enqueue', 11, 0)
        event('dma_complete', 20, 1)
        event('enqueue', 21, 1)
        event('evict', 29, 0, reason='capacity')
        event('dma_complete', 30, 2)
        event('enqueue', 31, 2)
        event('full_press_consumed', 40, consumer_pc=0x40123450, origin='original_firmware')
        event('submit', 41, 1, role='history')
        event('submit', 42, 2, role='history')
        event('dma_complete', 50, 3)
        event('submit', 51, 3, role='current')
        card = bytearray(4096)
        files = []
        for sequence, i in enumerate((1, 2, 3)):
            content = tiff_contract_bytes(i)
            path = f'file{i}.NEF'
            (self.root / path).write_bytes(content)
            offset = sequence * 1024
            card[offset:offset + len(content)] = content
            event('writer_complete', 60 + sequence * 2, i, file_id=f'n{i}',
                  output_index=sequence, bytes=len(content), origin='original_firmware')
            event('nvme_write_complete', 61 + sequence * 2, offset=offset, length=512, status=0)
            files.append(dict(file_id=f'n{i}', path=path, bytes=len(content), sha256=digest(content),
                              output_index=sequence, extents=[dict(offset=offset, length=len(content))]))
        event('capture_end', 70, dma_count=4, submit_count=3, writer_count=3, nvme_count=3)
        (self.root / 'card.raw').write_bytes(card)
        self.manifest = dict(schema='z8-precapture-audit-v1', run=dict(
            run_id='synthetic-contract', model='Nikon Z8', firmware_sha256='a' * 64,
            clock_id='virtual-0', clock_kind='qemu_virtual_ns', stopped=True,
            full_consumer_pc=0x40123450,
            error=None, evidence_kind='synthetic'), queue=dict(capacity=2), frames=frames,
            events=events, storage=dict(kind='guest_nvme_export', card_path='card.raw',
                                       sector_size=512, catalog_complete=True, nef_files=files))

    def decode(self, path):
        i = int(path.stem[4:])
        return audit.DecodedRaw(self.arrays[f'f{i}'].copy(), 16383, 'injected test decoder')

    def check(self, manifest=None, decoder=None):
        return audit.audit_manifest(manifest or self.manifest, self.root,
                                    decoder=decoder or self.decode)

    def assert_rejected(self, data, code, *, decoder=None):
        result = self.check(data, decoder)
        self.assertFalse(result['accepted'])
        self.assertIn(code, [x['code'] for x in result['issues']], result)

    def test_fifo_eviction_full_pixel_comparison_and_synthetic_not_native(self):
        result = self.check()
        self.assertEqual(result['status'], 'synthetic_checks_passed')
        self.assertFalse(result['accepted'])
        self.assertEqual(result['predicted_pre'], ['f1', 'f2'])
        self.assertEqual(result['predicted_post'], ['f3'])
        self.assertEqual(result['output_frames'], ['f1', 'f2', 'f3'])
        self.assertEqual([f['samples_compared'] for f in result['files']], [12, 12, 12])

    def test_injected_decoder_cannot_award_native_acceptance(self):
        self.manifest['run']['evidence_kind'] = 'native_guest_capture'
        result = self.check()
        self.assertEqual(result['status'], 'synthetic_checks_passed')
        self.assertFalse(result['accepted'])
        self.assertFalse(result['native_accepted'])

    def test_post_frame_at_trigger_tick_cannot_prove_post_trigger_time(self):
        m = copy.deepcopy(self.manifest)
        for position in (8, 9, 10):
            m['events'][position]['t_ns'] = 40
        self.assert_rejected(m, 'post_time')

    def test_full_consumer_address_requires_an_integer_observation(self):
        m = copy.deepcopy(self.manifest)
        m['events'][7]['consumer_pc'] = float(m['run']['full_consumer_pc'])
        self.assert_rejected(m, 'trigger')

    def test_cli_path_uses_fixed_rawpy_and_rejects_synthetic_evidence(self):
        self.assertEqual(audit.audit_manifest(self.manifest, self.root)['status'], 'incomplete')
        self.manifest['run']['evidence_kind'] = 'native_guest_capture'
        with patch.object(audit, 'decode_rawpy', side_effect=audit.AuditError('decoder_unavailable', 'missing', incomplete=True)):
            result = audit.audit_manifest(self.manifest, self.root)
        self.assertEqual(result['status'], 'incomplete')
        self.assertFalse(result['accepted'])

    def test_missing_actual_artifacts_is_incomplete(self):
        (self.root / 'file2.NEF').unlink()
        self.assertEqual(self.check()['status'], 'incomplete')

    def test_manifest_without_any_nef_is_incomplete(self):
        self.manifest['storage']['nef_files'] = []
        self.assertEqual(self.check()['status'], 'incomplete')

    def test_clock_and_original_full_consumption_are_required(self):
        for change, code in ((lambda m: m['events'][2].update(clock_id='host-time'), 'clock'),
                             (lambda m: m['events'][7].update(origin='host_queued'), 'trigger'),
                             (lambda m: m['events'][7].update(consumer_pc=0), 'trigger'),
                             (lambda m: m['events'][8].update(t_ns=39), 'event_order')):
            m = copy.deepcopy(self.manifest); change(m)
            self.assert_rejected(m, code)

    def test_duplicate_frame_job_and_event_ids_are_rejected(self):
        for field in ('frame_id', 'job_id'):
            m = copy.deepcopy(self.manifest)
            m['frames'][1][field] = m['frames'][0][field]
            self.assert_rejected(m, 'duplicate_identity')
        m = copy.deepcopy(self.manifest)
        m['events'][1]['event_id'] = m['events'][0]['event_id']
        self.assert_rejected(m, 'event_order')

    def test_capacity_eviction_cannot_remove_wrong_or_underfilled_frame(self):
        m = copy.deepcopy(self.manifest)
        m['events'][4].update(frame_id='f1', job_id='j1')
        self.assert_rejected(m, 'queue_eviction')
        m = copy.deepcopy(self.manifest); m['queue']['capacity'] = 3
        self.assert_rejected(m, 'queue_eviction')

    def test_submit_requires_matching_job_fifo_and_temporal_role(self):
        for change, code in ((lambda m: m['events'][8].update(job_id='j2'), 'job_identity'),
                             (lambda m: m['events'][8].update(frame_id='f2', job_id='j2'), 'submit_order'),
                             (lambda m: m['events'][8].update(role='current'), 'submit_role')):
            m = copy.deepcopy(self.manifest); change(m); self.assert_rejected(m, code)

    def test_counter_completeness_is_required(self):
        m = copy.deepcopy(self.manifest); m['events'][-1]['dma_count'] = 5
        self.assert_rejected(m, 'counter_coverage')

    def test_catalog_writer_and_sequence_must_cover_every_output(self):
        m = copy.deepcopy(self.manifest); m['storage']['nef_files'].pop()
        self.assert_rejected(m, 'output_coverage')
        m = copy.deepcopy(self.manifest); m['storage']['nef_files'][1]['output_index'] = 0
        self.assert_rejected(m, 'output_order')

    def test_card_bytes_and_nvme_completed_ranges_are_verified(self):
        card = bytearray((self.root / 'card.raw').read_bytes()); card[0] ^= 1
        (self.root / 'card.raw').write_bytes(card)
        self.assert_rejected(self.manifest, 'card_bytes')

    def test_nvme_failure_or_missing_coverage_is_rejected(self):
        m = copy.deepcopy(self.manifest); m['events'][13]['status'] = 1
        self.assert_rejected(m, 'nvme')
        m = copy.deepcopy(self.manifest); m['events'][13]['offset'] = 3072
        self.assert_rejected(m, 'nvme_coverage')

    def test_changed_file_or_producer_hash_is_rejected(self):
        (self.root / 'p1.bin').write_bytes(b'x' * 24)
        self.assert_rejected(self.manifest, 'producer_hash')

    def test_full_decode_finds_non_header_pixel_mismatch(self):
        def bad(path):
            row = self.decode(path)
            row.samples[-1, -1] ^= 1
            return row
        self.assert_rejected(self.manifest, 'pixels', decoder=bad)

    def test_static_pixels_relabelled_as_three_frames_are_rejected(self):
        for i in (2, 3):
            data = (self.root / 'p1.bin').read_bytes()
            (self.root / f'p{i}.bin').write_bytes(data)
            self.manifest['frames'][i]['producer']['sha256'] = digest(data)
            self.arrays[f'f{i}'] = self.arrays['f1'].copy()
        self.assert_rejected(self.manifest, 'pixel_identity')

    def test_retained_buffer_cannot_be_overwritten_by_later_dma(self):
        self.manifest['frames'][2]['producer']['address'] = self.manifest['frames'][1]['producer']['address']
        self.assert_rejected(self.manifest, 'buffer_ownership')

    def test_submitted_buffer_remains_owned_until_native_writer_returns(self):
        # Both history items have left the FIFO at ticks41/42. Submission is
        # not evidence that their source reads have already finished.
        self.manifest['frames'][3]['producer']['address'] = self.manifest['frames'][1]['producer']['address']
        self.assert_rejected(self.manifest, 'buffer_ownership')

    def test_pre_enqueue_gap_does_not_release_a_completed_producer(self):
        self.manifest['frames'][1]['producer']['address'] = self.manifest['frames'][0]['producer']['address']
        self.manifest['events'][1]['t_ns'] = 20
        self.manifest['events'][1], self.manifest['events'][2] = self.manifest['events'][2], self.manifest['events'][1]
        for seq, event in enumerate(self.manifest['events']):
            event['seq'] = seq
        self.assert_rejected(self.manifest, 'buffer_ownership')

    def test_evicted_buffer_can_be_reused(self):
        self.manifest['frames'][3]['producer']['address'] = self.manifest['frames'][0]['producer']['address']
        self.assertEqual(self.check()['status'], 'synthetic_checks_passed')

    def test_completed_writer_buffer_can_be_reused(self):
        self.manifest['frames'][3]['producer']['address'] = self.manifest['frames'][1]['producer']['address']
        events = self.manifest['events']
        events[10]['t_ns'] = events[11]['t_ns'] = 60
        # Native writer f1 returns before the new DMA at the same virtual tick.
        writer = events.pop(12)
        events.insert(10, writer)
        for seq, event in enumerate(events):
            event['seq'] = seq
        self.assertEqual(self.check()['status'], 'synthetic_checks_passed')

    def test_path_escape_shape_depth_and_unfinished_run_fail_closed(self):
        for change, code in ((lambda m: m['frames'][0]['producer'].update(path='../outside'), 'path'),
                             (lambda m: m['frames'][0]['producer'].update(width=True), 'schema'),
                             (lambda m: m['run'].update(stopped=False), 'run_incomplete')):
            m = copy.deepcopy(self.manifest); change(m); self.assert_rejected(m, code)
        self.assert_rejected(self.manifest, 'decode_geometry',
                             decoder=lambda path: audit.DecodedRaw(np.zeros((2, 4), dtype=np.uint16), 16383))
        self.assert_rejected(self.manifest, 'decode_depth',
                             decoder=lambda path: audit.DecodedRaw(self.decode(path).samples, 4095))

    def test_marker_bits_complement_and_full_pixels(self):
        for i, frame in enumerate(self.manifest['frames']):
            samples = np.full((16, 64), i + 10, dtype=np.uint16)
            for bit in range(32):
                value = (i >> bit) & 1
                samples[:8, bit*2:bit*2+2] = 16383*7//8 if value else 16383//8
                samples[8:, bit*2:bit*2+2] = 16383//8 if value else 16383*7//8
            self.arrays[f'f{i}'] = samples
            data = samples.astype('<u2').tobytes()
            (self.root / f'p{i}.bin').write_bytes(data)
            frame['producer'].update(width=64, height=16, stride_samples=64, sha256=digest(data),
                marker_scheme='cfa32-complement-v1', marker_frame_id=i)
        report = self.check()
        self.assertEqual(report['status'], 'synthetic_checks_passed')
        self.assertEqual([f['marker_id'] for f in report['files']], [1, 2, 3])
        data = bytearray((self.root / 'p1.bin').read_bytes()); data[1024] ^= 1
        (self.root / 'p1.bin').write_bytes(data)
        self.manifest['frames'][1]['producer']['sha256'] = digest(data)
        self.assert_rejected(self.manifest, 'marker')

    def test_marker_cropped_or_wrong_id_is_not_silently_ignored(self):
        self.manifest['frames'][0]['producer']['marker_scheme'] = 'cfa32-complement-v1'
        self.assert_rejected(self.manifest, 'marker')

    def test_equal_virtual_tick_is_not_strictly_pre_trigger(self):
        self.manifest['events'][6]['t_ns'] = 30
        self.manifest['events'][7]['t_ns'] = 30
        self.assert_rejected(self.manifest, 'pre_time')

    def test_at_least_two_pre_and_one_post_are_not_relaxed(self):
        # Moving the FULL observation before the second enqueue cannot create
        # a second historical frame from a later timestamp.
        m = copy.deepcopy(self.manifest)
        m['events'][7], m['events'][6] = m['events'][6], m['events'][7]
        for i, e in enumerate(m['events']): e.update(seq=i, t_ns=i*10)
        self.assert_rejected(m, 'queue_enqueue')

    def test_complete_one_pre_or_no_post_trace_still_fails_minimum_count(self):
        m = copy.deepcopy(self.manifest)
        trigger = m['events'].pop(7)
        m['events'].pop(6)  # f2 is now a current frame, never a history enqueue.
        trigger['t_ns'] = 29
        m['events'].insert(5, trigger)
        for index, event in enumerate(m['events']):
            event['seq'] = index
            if event['kind'] == 'submit' and event.get('frame_id') == 'f2':
                event['role'] = 'current'
        self.assert_rejected(m, 'pre_post_count')

        m = copy.deepcopy(self.manifest)
        m['frames'].pop()
        m['storage']['nef_files'].pop()
        m['events'] = [event for event in m['events']
                       if event.get('frame_id') != 'f3'
                       and not (event['kind'] == 'nvme_write_complete' and event['offset'] == 2048)]
        for index, event in enumerate(m['events']):
            event['seq'] = index
        m['events'][-1].update(dma_count=3, submit_count=2, writer_count=2, nvme_count=2)
        self.assert_rejected(m, 'pre_post_count')

    def test_missing_trigger_end_and_unknown_events_fail_closed(self):
        for position in (7, -1):
            m = copy.deepcopy(self.manifest)
            m['events'] = m['events'][:position] if position == -1 else m['events'][:7]
            self.assertEqual(self.check(m)['status'], 'incomplete')
        m = copy.deepcopy(self.manifest); m['events'][1]['kind'] = 'host_patch'
        self.assert_rejected(m, 'event_kind')

    def test_extent_overlap_and_short_extent_are_rejected(self):
        m = copy.deepcopy(self.manifest)
        m['storage']['nef_files'][1]['extents'][0]['offset'] = 0
        self.assert_rejected(m, 'card_extent')
        m = copy.deepcopy(self.manifest); m['storage']['nef_files'][0]['extents'][0]['length'] -= 1
        self.assert_rejected(m, 'card_extent')

    def test_missing_catalog_and_native_writer_coverage_are_not_passes(self):
        m = copy.deepcopy(self.manifest); m['storage']['catalog_complete'] = False
        self.assertEqual(self.check(m)['status'], 'incomplete')
        m = copy.deepcopy(self.manifest); m['storage']['nef_files'][0]['file_id'] = 'unwritten'
        self.assert_rejected(m, 'output_coverage')

    def test_decode_error_is_not_a_pass(self):
        def broken(path): raise ValueError('not a real NEF')
        self.assert_rejected(self.manifest, 'decode', decoder=broken)

    def test_cli_writes_incomplete_receipt_without_overwriting_inputs(self):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps(self.manifest))
        output = self.root / 'audit.json'
        before = {p.name: digest(p.read_bytes()) for p in self.root.iterdir()}
        with redirect_stdout(io.StringIO()):
            code = audit.main(['--manifest', str(manifest), '--output', str(output)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.read_text())['accepted'])
        self.assertFalse(json.loads(output.read_text())['native_accepted'])
        self.assertEqual(before, {name: digest((self.root / name).read_bytes()) for name in before})
        with redirect_stdout(io.StringIO()), patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit):
                audit.main(['--manifest', str(manifest), '--output', str(output)])

    def test_boolean_nvme_success_and_unbound_trigger_address_are_invalid(self):
        m = copy.deepcopy(self.manifest); m['events'][13]['status'] = False
        self.assert_rejected(m, 'nvme')
        m = copy.deepcopy(self.manifest); m['events'][7]['consumer_pc'] += 4
        self.assert_rejected(m, 'trigger')

    def test_invalid_manifest_cli_never_awards_native_acceptance(self):
        manifest = self.root / 'invalid.json'
        manifest.write_text('{')
        output = self.root / 'invalid-audit.json'
        with redirect_stdout(io.StringIO()):
            code = audit.main(['--manifest', str(manifest), '--output', str(output)])
        result = json.loads(output.read_text())
        self.assertEqual(code, 2)
        self.assertFalse(result['accepted'])
        self.assertFalse(result['native_accepted'])
        self.assertEqual(result['issues'][0]['code'], 'manifest_unavailable')


if __name__ == '__main__': unittest.main()
