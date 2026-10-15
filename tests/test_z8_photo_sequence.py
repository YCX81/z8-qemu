# SPDX-License-Identifier: GPL-2.0-or-later
"""Counter delta and current-frame failures must precede memory exports."""
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_photo_sequence import raw_job_span, capture_photo_output
import z8_capture_pixels as pixels
from test_z8_capture_pixels import FakeQMP

class PhotoSequenceTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(jobs=1, bytes=2048)
        self.current = dict(enabled=True, jobs=2, bytes=4096, acks=2, pending=False,
                            frame=9, source=0x850000000, target=0x860000000)
    def test_delta_not_cumulative_and_pending_is_observation_only(self):
        self.assertEqual(raw_job_span(self.current, self.previous, 2048), 2048)
        self.current.update(pending=True, acks=1)
        self.assertEqual(raw_job_span(self.current, self.previous, 2048), 2048)
    def test_skipped_duplicate_backwards_and_bad_length(self):
        for changes in ({'jobs':1}, {'jobs':3}, {'bytes':2048}, {'bytes':4097},
                        {'pending':0}, {'acks':0}, {'acks':True}, {'jobs':True},
                        {'enabled':False}, {'frame':0}, {'source':0},
                        {'target':0x850000000}, {'target':0x8ffffffe0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                raw_job_span(dict(self.current, **changes), self.previous, 2048)
    def test_first_and_reset_require_new_sequence(self):
        row = dict(self.current, jobs=1, bytes=2048, acks=1)
        self.assertEqual(raw_job_span(row, None, 2048), 2048)
        with self.assertRaises(ValueError): raw_job_span(row, self.previous, 2048)
        with self.assertRaises(ValueError): raw_job_span(self.current, None, 2048)

class SnapshotSequenceTests(unittest.TestCase):
    def test_two_actual_fake_qmp_snapshots_and_gap_before_export(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pixels,'load_bindings',return_value={'main_sha256':'a'*64}):
            root=Path(folder);qmp=FakeQMP(root);g=SimpleNamespace(out=root,qmp=qmp)
            first=capture_photo_output(g,'one')
            self.assertEqual(first['photo_sequence']['per_job_bytes'],len(qmp.payload))
            self.assertTrue((root/'pixel-one/photo-sequence.json').is_file())
            qmp.states[pixels.D980].update(frames=2,acks=1,pixel_dma_bytes=2*len(qmp.payload))
            qmp.states[pixels.TAG]['frame_id']=2
            qmp.states[pixels.RAW].update(jobs=2,frame=2,bytes=2*len(qmp.payload),acks=1)
            second=capture_photo_output(g,'two',first)
            self.assertEqual(second['photo_sequence']['previous_job'],1)
            self.assertEqual(second['photo_sequence']['cumulative_bytes'],2*len(qmp.payload))
            self.assertEqual(second['producer']['memory_bytes'],len(qmp.payload))
            qmp.states[pixels.D980].update(frames=4,acks=3,pixel_dma_bytes=4*len(qmp.payload))
            qmp.states[pixels.TAG]['frame_id']=4
            qmp.states[pixels.RAW].update(jobs=4,frame=4,bytes=4*len(qmp.payload),acks=3)
            with self.assertRaises(ValueError):capture_photo_output(g,'gap',second)
            self.assertFalse((root/'pixel-gap').exists())

if __name__ == '__main__': unittest.main()
