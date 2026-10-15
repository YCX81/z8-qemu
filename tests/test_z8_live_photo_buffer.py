# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic host buffer tests, not camera capture acceptance."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_live_photo_buffer import FrameBuffer, BufferError


class BufferTests(unittest.TestCase):
    def test_three_second_30fps_contract(self):
        b = FrameBuffer(pre_ns=1_500_000_000, post_ns=1_500_000_000, max_bytes=1000)
        for i in range(90):
            if i == 45:
                b.trigger(1_500_000_000)
            pts = i * 1_000_000_000 // 30
            end = (i + 1) * 1_000_000_000 // 30
            b.append(pts, end - pts, bytes([i]), frame_id=i)
        with self.assertRaisesRegex(BufferError, 'already complete'):
            b.append(3_000_000_000, 33_333_333, b'x', frame_id=90)
        clip = b.finish()
        self.assertEqual(len(clip), 90)
        self.assertEqual(clip[-1].end_ns - clip[0].pts_ns, 3_000_000_000)
        self.assertEqual(b.still_offset_ns, 1_500_000_000)

    def buffer(self, **kwargs):
        return FrameBuffer(pre_ns=20, post_ns=20, max_bytes=100, **kwargs)

    def feed(self, b, start=0, stop=60):
        for t in range(start, stop, 10):
            b.append(t, 10, bytearray([t]), frame_id=t)

    def test_complete_window_retains_owned_pixels(self):
        b = self.buffer()
        payload = bytearray(b'abc')
        b.append(0, 10, payload, frame_id=0)
        payload[0] = 0
        self.feed(b, 10, 30)
        b.trigger(20)
        self.feed(b, 30, 40)
        clip = b.finish()
        self.assertEqual(clip[0].payload, b'abc')
        self.assertEqual([f.pts_ns for f in clip], [0, 10, 20, 30])
        self.assertEqual(b.still_offset_ns, 20)

    def test_eviction_retains_boundary_frame(self):
        b = self.buffer()
        self.feed(b)
        self.assertEqual([f.pts_ns for f in b.frames], [30, 40, 50])
        b.trigger(60)
        self.feed(b, 60, 80)
        self.assertEqual(len(b.finish()), 4)

    def test_non_frame_aligned_trigger(self):
        b = self.buffer()
        self.feed(b, 0, 30)
        b.trigger(25)
        self.feed(b, 30, 50)
        self.assertEqual([f.pts_ns for f in b.finish()], [0, 10, 20, 30, 40])
        self.assertEqual(b.still_offset_ns, 25)

    def test_insufficient_history(self):
        b = self.buffer()
        b.append(10, 10, b'x', frame_id=1)
        with self.assertRaisesRegex(BufferError, 'history'):
            b.trigger(20)

    def test_waits_for_post_window(self):
        b = self.buffer()
        self.feed(b, 0, 20)
        b.trigger(20)
        with self.assertRaisesRegex(BufferError, 'post'):
            b.finish()

    def test_gap_is_rejected(self):
        b = self.buffer()
        b.append(0, 10, b'a', frame_id=0)
        with self.assertRaisesRegex(BufferError, 'contiguous'):
            b.append(20, 10, b'b', frame_id=1)
        with self.assertRaises(BufferError):
            b.trigger(10)

    def test_overlapping_frame_is_rejected(self):
        b = self.buffer()
        b.append(0, 10, b'a', frame_id=0)
        with self.assertRaises(BufferError):
            b.append(5, 10, b'b', frame_id=1)

    def test_reused_identity_is_rejected(self):
        b = self.buffer()
        b.append(0, 10, b'a', frame_id=2)
        with self.assertRaisesRegex(BufferError, 'identity'):
            b.append(10, 10, b'b', frame_id=2)

    def test_byte_limit_does_not_discard_triggered_history(self):
        b = FrameBuffer(pre_ns=20, post_ns=20, max_bytes=3)
        self.feed(b, 0, 20)
        b.trigger(20)
        self.feed(b, 20, 30)
        with self.assertRaisesRegex(BufferError, 'capacity'):
            b.append(30, 10, b'z', frame_id=30)
        with self.assertRaises(BufferError):
            b.finish()

    def test_history_byte_limit_and_large_frame(self):
        b = FrameBuffer(pre_ns=20, post_ns=20, max_bytes=1)
        self.feed(b, 0, 20)
        self.assertEqual(b.bytes_used, 1)
        with self.assertRaises(BufferError):
            b.trigger(20)
        with self.assertRaises(BufferError):
            b.append(20, 10, b'xx', frame_id=20)

    def test_cancel_and_rearm(self):
        b = self.buffer()
        self.feed(b, 0, 20)
        b.trigger(20)
        with self.assertRaises(BufferError):
            b.trigger(20)
        b.cancel()
        self.assertEqual(b.bytes_used, 0)
        self.feed(b, 0, 40)
        b.trigger(40)
        self.feed(b, 40, 60)
        self.assertEqual(len(b.finish()), 4)
        with self.assertRaises(BufferError):
            b.append(60, 10, b'x', frame_id=60)

    def test_bad_input_and_future_trigger(self):
        for values in ((0, 20, 100), (20, -1, 100), (20, 20, 0), (True, 20, 100)):
            with self.assertRaises(ValueError):
                FrameBuffer(pre_ns=values[0], post_ns=values[1], max_bytes=values[2])
        b = self.buffer()
        with self.assertRaises(BufferError):
            b.finish()
        with self.assertRaises(BufferError):
            b.trigger(0)
        with self.assertRaises(BufferError):
            _ = b.still_offset_ns
        for args in ((0, 0, b'x', 0), (-1, 1, b'x', 0), (0, 1, b'', 0), (0, 1, b'x', -1), (0, 1, 123, 0)):
            with self.assertRaises(ValueError):
                b.append(args[0], args[1], args[2], frame_id=args[3])
        self.feed(b, 0, 20)
        with self.assertRaises(BufferError):
            b.trigger(30)
        with self.assertRaises(ValueError):
            b.trigger(True)


if __name__ == '__main__':
    unittest.main()
