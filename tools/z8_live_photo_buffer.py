# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only, single-stream Live Photo frame retention contract.

Payloads must be independently usable frames (e.g. owned YUV copies), NOT
interdependent H.264/HEVC packets. No firmware hooks, codec, muxer, audio or
camera acceptance is provided. The caller supplies one monotonic clock,
actual exposure/trigger correlation, frame duration and increasing identities.
This reference model is single-threaded and deliberately rejects frame gaps.
"""
from collections import deque
from dataclasses import dataclass


class BufferError(RuntimeError):
    """The requested temporal window cannot be accepted."""


@dataclass(frozen=True)
class Frame:
    pts_ns: int
    duration_ns: int
    frame_id: int
    payload: bytes

    @property
    def end_ns(self):
        return self.pts_ns + self.duration_ns


class FrameBuffer:
    """Own frame bytes under a hard retained-payload budget.

The budget excludes Python object overhead and temporary input copies. Before
triggering, retain the pre-window plus at most one boundary frame. After
triggering, never evict selected history to accommodate new frames: fail closed.
Call cancel() to release references and rearm after completion or failure.
"""

    def __init__(self, *, pre_ns, post_ns, max_bytes):
        if any(type(v) is not int or v <= 0 for v in (pre_ns, post_ns, max_bytes)):
            raise ValueError('positive integer pre/post durations and byte budget required')
        self.pre_ns, self.post_ns, self.max_bytes = pre_ns, post_ns, max_bytes
        self.cancel()

    def cancel(self):
        self._frames = deque()
        self.bytes_used = 0
        self._trigger = None
        self._last_id = None
        self._last_end = None
        self._state = 'armed'

    @property
    def frames(self):
        return tuple(self._frames)

    @property
    def still_offset_ns(self):
        if self._trigger is None:
            raise BufferError('no trigger')
        return self._trigger - self._frames[0].pts_ns

    def _check(self):
        if self._state in ('failed', 'finished'):
            raise BufferError('cancel required before reuse')

    def _fail(self, message):
        self._state = 'failed'
        raise BufferError(message)

    def _evict(self):
        self.bytes_used -= len(self._frames.popleft().payload)

    def append(self, pts_ns, duration_ns, payload, *, frame_id):
        self._check()
        if (type(pts_ns) is not int or pts_ns < 0 or
                type(duration_ns) is not int or duration_ns <= 0 or
                type(frame_id) is not int or frame_id < 0 or
                not isinstance(payload, (bytes, bytearray)) or not payload):
            raise ValueError('integer timestamps, positive duration, identity and frame bytes required')
        if self._last_id is not None and frame_id <= self._last_id:
            self._fail('frame identity must increase')
        if self._last_end is not None and pts_ns != self._last_end:
            self._fail('frame intervals must be contiguous')
        if self._trigger is not None and pts_ns >= self._trigger + self.post_ns:
            raise BufferError('post window already complete; finish before reuse')
        if len(payload) > self.max_bytes:
            self._fail('frame exceeds capacity')
        if self._trigger is None:
            while self._frames and self._frames[0].end_ns <= pts_ns - self.pre_ns:
                self._evict()
            while self._frames and self.bytes_used + len(payload) > self.max_bytes:
                self._evict()
        elif self.bytes_used + len(payload) > self.max_bytes:
            self._fail('capture exceeds capacity; retained history was not evicted')
        frame = Frame(pts_ns, duration_ns, frame_id, bytes(payload))
        self._frames.append(frame)
        self.bytes_used += len(frame.payload)
        self._last_id, self._last_end = frame_id, frame.end_ns

    def trigger(self, timestamp_ns):
        self._check()
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError('nonnegative integer trigger timestamp required')
        if self._trigger is not None:
            raise BufferError('capture already triggered')
        if (not self._frames or timestamp_ns < self._frames[-1].pts_ns or
                timestamp_ns > self._frames[-1].end_ns):
            raise BufferError('trigger must lie in latest frame interval')
        start = timestamp_ns - self.pre_ns
        if self._frames[0].pts_ns > start:
            raise BufferError('insufficient pre-trigger history')
        while self._frames[0].end_ns <= start:
            self._evict()
        self._trigger = timestamp_ns
        self._state = 'capturing'

    def finish(self):
        self._check()
        if self._trigger is None:
            raise BufferError('no trigger')
        if self._frames[-1].end_ns < self._trigger + self.post_ns:
            raise BufferError('incomplete post-trigger window')
        self._state = 'finished'
        return self.frames
