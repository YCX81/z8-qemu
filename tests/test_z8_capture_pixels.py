# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent packed-bit fixtures and paused producer snapshot contracts."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_capture_pixels as pixels


def pack_row(values, bits):
    # Independent arbitrary-precision fixture, not the module's NumPy decoder.
    value = sum(sample << (i * bits) for i, sample in enumerate(values))
    return value.to_bytes((len(values) * bits + 7) // 8, 'little')


class DecoderTests(unittest.TestCase):
    def test_12_and_14_bit_rows_with_tail_samples_and_nonzero_padding(self):
        for bits in (12, 14):
            for width in (1, 2, 3, 4, 6, 9):
                values = [((i * 913) ^ (1 << (bits - 1))) & ((1 << bits) - 1) for i in range(width)]
                blob = pack_row(values, bits) + b'\xa5\x5a'
                with self.subTest(bits=bits, width=width):
                    actual = pixels.decode_packed_row(blob, width, bits)
                    self.assertEqual(actual, struct.pack('<' + 'H' * width, *values))

    def test_invalid_depth_width_and_short_row_are_rejected(self):
        for width, bits, blob in ((0, 12, b''), (True, 12, b'xx'), (4, 16, bytes(8)),
                                  (4, 14, bytes(6)), (32769, 14, b'')):
            with self.subTest(width=width, bits=bits), self.assertRaises(ValueError):
                pixels.decode_packed_row(blob, width, bits)

    def test_file_conversion_retains_packed_hash_and_declares_discarded_row_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'packed.bin'; target = root / 'decoded.bin'
            values = [[0, 1, 16383, 8192], [100, 500, 1000, 1500]]
            packed = b''.join(pack_row(row, 14).ljust(32, b'\xa5') for row in values)
            source.write_bytes(packed)
            receipt = pixels.unpack_file(source, target, width=4, height=2, stride=32, bits=14)
            self.assertEqual(target.read_bytes(), struct.pack('<8H', *(values[0] + values[1])))
            self.assertEqual(receipt['packed_sha256'], hashlib.sha256(packed).hexdigest())
            self.assertEqual(receipt['source_stride_bytes'], 32)
            self.assertEqual(receipt['target_stride_samples'], 4)
            self.assertEqual(receipt['padding_bytes_per_row'], 25)
            self.assertFalse(receipt['literal_memory_dump'])
            with self.assertRaises(FileExistsError):
                pixels.unpack_file(source, target, width=4, height=2, stride=32, bits=14)

    def test_truncated_trailing_and_impossible_geometry_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'packed'; target = Path(folder) / 'normalized'
            for length in (63, 65):
                source.write_bytes(bytes(length))
                with self.assertRaises(ValueError):
                    pixels.unpack_file(source, target, width=4, height=2, stride=32, bits=14)
                self.assertFalse(target.exists())
            source.write_bytes(bytes(64))
            for kw in (dict(width=4, height=2, stride=6, bits=14),
                       dict(width=16384, height=16384, stride=32768, bits=14),
                       dict(width=4, height=True, stride=32, bits=14)):
                with self.assertRaises(ValueError): pixels.unpack_file(source, target, **kw)
                self.assertFalse(target.exists())


class FakeQMP:
    def __init__(self, root, tagged=False):
        self.root = root; self.calls = []; self.changed_after_save = None
        self.status = {'running': False, 'status': 'debug'}
        self.short_save = False
        width, height = (64, 16) if tagged else (4, 2)
        stride = ((width * 14 + 7) // 8 + 31) & ~31
        self.values = [[(y * 113 + x * 97) & 16383 for x in range(width)] for y in range(height)]
        self.payload = b''.join(pack_row(row, 14).ljust(stride, b'\x87') for row in self.values)
        latched = {'0x0': 1, '0x4': width << 16, '0x8': height << 16, '0x10': 0,
                   '0x100': stride, '0x104': 1, '0x108': 1, '0x10c': 0,
                   '0x110': 1, '0x114': 0, '0x118': 0, '0x11c': 0}
        self.states = {
            'primary-d980-state': dict(enabled=True, stream_enabled=True, valid=0xfffff,
                submits=1, frames=1, pixel_dma_bytes=len(self.payload), pending=0x4000000,
                acks=0, cancelled=0, rejects=0, last_encoded=0x850000000,
                last_physical=0x850000000, packed14_enabled=True, cache_format=1, latched=latched),
            'sensor-frame-tag-state': dict(enabled=tagged, frame_id=1, virtual_ns=123456,
                physical=0x850000000, width=width, height=height, stride=stride, bits=14,
                packed_sha256=hashlib.sha256(self.payload).hexdigest(), scheme='cfa32-complement-v1'),
            'engine151-raw-output-state': dict(enabled=True, jobs=1, bytes=len(self.payload),
                source=0x850000000, target=0x860000000, frame=1, acks=0, pending=True),
        }

    def command(self, command, **args):
        self.calls.append((command, args))
        if command == 'query-status': return dict(self.status)
        if command == 'qom-get': return json.dumps(self.states[args['property']])
        if command == 'pmemsave':
            self_case = Path(args['filename'])
            if not self_case.is_relative_to(self.root) or self_case.exists():
                raise AssertionError('Snapshot path must be fresh and inside this run')
            if args['val'] not in (0x850000000, 0x860000000) or args['size'] != len(self.payload):
                raise AssertionError('Expected actual full physical producer')
            self_case.write_bytes(self.payload[:-1] if self.short_save else self.payload)
            if self.changed_after_save: self.changed_after_save(self)
            return {}
        raise AssertionError('Unexpected or mutating command: ' + command)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.g = SimpleNamespace(out=self.root, qmp=FakeQMP(self.root))
        self.guard = patch.object(pixels, 'load_bindings', return_value={'main_sha256': 'a' * 64})
        self.guard.start(); self.addCleanup(self.guard.stop)

    def test_untagged_snapshot_is_actual_memory_without_time_attribution(self):
        result = pixels.capture_d980(self.g, 'd980-1')
        producer = result['producer']
        self.assertEqual((self.root / producer['path']).read_bytes(), struct.pack('<8H', *sum(self.g.qmp.values, [])))
        self.assertEqual(producer['origin'], 'guest_producer_dma')
        self.assertEqual(producer['encoding'], 'u16le')
        self.assertEqual(producer['memory_bytes'], len(self.g.qmp.payload))
        self.assertIsNone(result['dma_completed_virtual_ns'])
        self.assertFalse(result['temporal_attribution_verified'])
        self.assertFalse(result['raw_precapture_verified'])
        self.assertTrue(result['device_identity_unchanged'])
        self.assertEqual(result['memory_read_transport'], 'qmp_pmemsave')
        self.assertEqual((self.root / result['packed']['path']).read_bytes(), self.g.qmp.payload)

    def test_tagged_full_hash_binds_both_d980_and_raw_target(self):
        self.g.qmp = FakeQMP(self.root, tagged=True)
        for function, label, address in ((pixels.capture_d980, 'dma', 0x850000000),
                                         (pixels.capture_raw_output, 'raw', 0x860000000)):
            result = function(self.g, label)
            self.assertEqual(result['producer']['address'], address)
            self.assertEqual(result['producer']['marker_frame_id'], 1)
            self.assertEqual(result['producer']['marker_scheme'], 'cfa32-complement-v1')
            self.assertEqual(result['dma_completed_virtual_ns'], 123456)
            self.assertTrue(result['temporal_attribution_verified'])
            self.assertEqual(result['packed']['sha256'], self.g.qmp.states['sensor-frame-tag-state']['packed_sha256'])

    def test_d980_metadata_errors_fail_before_dump(self):
        mutations = [lambda s: s.update(frames=0), lambda s: s.update(stream_enabled=False),
            lambda s: s.update(last_physical=0x150d9800), lambda s: s.update(last_physical=0x8ffffffe0),
            lambda s: s.update(pixel_dma_bytes=1), lambda s: s.update(cache_format=2),
            lambda s: s.update(packed14_enabled=False), lambda s: s.update(frames=True),
            lambda s: s.update(valid=0), lambda s: s['latched'].update({'0x100': 1}),
            lambda s: s['latched'].update({'0x4': (4 << 16) | 1}),
            lambda s: s['latched'].update({'0x108': 0})]
        for mutate in mutations:
            self.g.qmp = FakeQMP(self.root); mutate(self.g.qmp.states['primary-d980-state'])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): pixels.capture_d980(self.g, 'invalid')
            self.assertFalse(any(c == 'pmemsave' for c, _ in self.g.qmp.calls))

    def test_untagged_cache_dimensions_and_completion_counters_must_match(self):
        for mode in ('cache-width', 'counter', 'boolean-format'):
            self.g.qmp = FakeQMP(self.root)
            d = self.g.qmp.states['primary-d980-state']
            tag = self.g.qmp.states['sensor-frame-tag-state']
            if mode == 'cache-width': tag['width'] = 8
            elif mode == 'counter':
                d['frames'] = 2; tag['frame_id'] = 2
            else: d['cache_format'] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError): pixels.capture_d980(self.g, mode)
            self.assertFalse(any(c == 'pmemsave' for c, _ in self.g.qmp.calls))

    def test_ordinary_packed12_snapshot_decodes_actual_memory(self):
        q = self.g.qmp; d = q.states['primary-d980-state']
        d['latched']['0x0'] = 2; d['cache_format'] = 2; d['packed14_enabled'] = False
        q.states['sensor-frame-tag-state']['bits'] = 12
        q.payload = b''.join(pack_row(row, 12).ljust(32, b'\xa5') for row in q.values)
        result = pixels.capture_d980(self.g, 'twelve')
        self.assertEqual(result['producer']['bit_depth'], 12)
        self.assertEqual((self.root / result['producer']['path']).read_bytes(), struct.pack('<8H', *sum(q.values, [])))
        with self.assertRaises(ValueError): pixels.capture_raw_output(self.g, 'raw12')

    def test_running_guest_foreign_firmware_and_bad_labels_never_dump(self):
        self.g.qmp.status['running'] = True
        with self.assertRaises(ValueError): pixels.capture_d980(self.g, 'running')
        self.g.qmp.status['running'] = False
        with patch.object(pixels, 'load_bindings', side_effect=ValueError('wrong MAIN')), self.assertRaises(ValueError):
            pixels.capture_d980(self.g, 'foreign')
        for label in ('../escape', '', '/absolute', 'a/b'):
            with self.assertRaises(ValueError): pixels.capture_d980(self.g, label)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_mismatched_tag_hash_or_frame_is_not_temporal_evidence(self):
        for key, value in (('packed_sha256', '0' * 64), ('frame_id', 2), ('physical', 0x850001000),
                           ('bits', 12), ('scheme', 'unknown')):
            self.g.qmp = FakeQMP(self.root, tagged=True)
            self.g.qmp.states['sensor-frame-tag-state'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): pixels.capture_d980(self.g, 'bad-' + key)

    def test_snapshot_rejects_job_reuse_or_resume_during_dump(self):
        for name, mutate in (
            ('frame', lambda q: q.states['primary-d980-state'].update(frames=2)),
            ('resume', lambda q: q.status.update(running=True)),
            ('target', lambda q: q.states['engine151-raw-output-state'].update(target=0x870000000))):
            self.g.qmp = FakeQMP(self.root)
            self.g.qmp.changed_after_save = mutate
            with self.subTest(name=name), self.assertRaises(ValueError): pixels.capture_raw_output(self.g, name)

    def test_raw_output_requires_current_complete_job_and_disjoint_destination(self):
        for key, value in (('jobs', 0), ('frame', 2), ('bytes', 1), ('target', 0x850000020),
                           ('source', 0x850000020), ('pending', 1), ('enabled', False)):
            self.g.qmp = FakeQMP(self.root)
            self.g.qmp.states['engine151-raw-output-state'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): pixels.capture_raw_output(self.g, 'badraw')
            self.assertFalse(any(c == 'pmemsave' for c, _ in self.g.qmp.calls))

    def test_short_dump_and_existing_output_never_produce_accepted_receipt(self):
        self.g.qmp.short_save = True
        with self.assertRaises(ValueError): pixels.capture_d980(self.g, 'short')
        self.assertFalse((self.root / 'pixel-short/receipt.json').exists())
        self.g.qmp.short_save = False
        pixels.capture_d980(self.g, 'once')
        existing = (self.root / 'pixel-once/packed.bin').read_bytes()
        with self.assertRaises(FileExistsError): pixels.capture_d980(self.g, 'once')
        self.assertEqual((self.root / 'pixel-once/packed.bin').read_bytes(), existing)


if __name__ == '__main__':
    unittest.main()
