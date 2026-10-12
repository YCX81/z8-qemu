# SPDX-License-Identifier: GPL-2.0-or-later
"""Host orchestration contracts; synthetic pixels never certify C10 glyphs."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_c10_menu_acceptance as menu
from z8_c10_gui import MAIN_SHA, menu_key_body


class Session:
    def __init__(self, out):
        self.g = self
        self.out = out
        self.data = {'firmware_sha256': MAIN_SHA}
        self.qmp = self
        self.peer = self
        self.record = {'rx': []}
        self.events = []
        self.current = 40
        self.pending = []
        self.accept_frames = True
        self.ok_count = 0
        self.bad_c10 = False
        self.short_frame = False

    def enqueue_external(self, body, family):
        self.events.append(('frame', body, family))
        self.pending.append(body)

    def run(self, seconds):
        self.events.append(('run', seconds))
        for body in self.pending:
            self.record['rx'].append(dict(family=2, body_hex=body.hex(), complete=self.accept_frames))
            if body == menu_key_body('ok'):
                self.ok_count += 1
                self.current = 39 if self.ok_count == 1 or self.bad_c10 else 40
        self.pending.clear()

    def command(self, name, **args):
        if name == 'query-status': return {'running': False}
        if name == 'screendump':
            data = b'P6\n2 1\n255\n' + bytes(range(6))
            Path(args['filename']).write_bytes(data[:-1] if self.short_frame else data)
            return {}
        raise AssertionError(name)

    def log(self): return ''

    def call(self, address, *args, **kwargs):
        self.events.append(('call', address, args))
        if address == menu.CURRENT_GETTER: return 1
        if address == menu.SETTING_GETTER: return 36 if self.current == 39 else self.current
        raise AssertionError(hex(address))

    def read_bytes(self, address, size):
        if address == menu.SELECTOR_OUTPUT and size == 4:
            return struct.pack('<I', self.current)
        raise AssertionError((address, size))


class MenuAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Session(Path(self.directory.name))
        self.output = self.session.out / 'native-menu'
        self.rgb = hashlib.sha256(bytes(range(6))).hexdigest()

    def run_menu(self, **kwargs):
        return menu.run_menu_acceptance(self.session, expected_menu_rgb_sha256=self.rgb,
                                        output=self.output, **kwargs)

    def test_real_frame_sequence_getters_and_unmodified_pixels(self):
        result = self.run_menu()
        frames = [event[1] for event in self.session.events if event[0] == 'frame']
        keys = ['menu'] + ['down'] * 13 + ['right', 'up', 'ok', 'right', 'down', 'ok', 'right']
        self.assertEqual(frames, [menu_key_body()] + [body for key in keys
                          for body in (menu_key_body(key), menu_key_body())])
        self.assertTrue(result['mcu_selection_verified'])
        self.assertFalse(result['c10_glyph_visually_verified'])
        self.assertFalse(result['c10_highlight_visually_verified'])
        self.assertEqual([row['selector'] for row in result['selections']], [40, 39, 40, 40])
        self.assertEqual(result['clock_basis'], 'Session.run wall-clock pacing; guest virtual duration is not asserted')
        self.assertTrue((self.output / '06-c10-reopened.ppm').is_file())
        self.assertEqual(json.loads((self.output / 'result.json').read_text())['status'], 'selection_verified_visual_review_required')
        self.assertTrue(all(event[1] in (menu.CURRENT_GETTER, menu.SETTING_GETTER)
                            for event in self.session.events if event[0] == 'call'))

    def test_bad_starting_menu_stops_before_down_navigation(self):
        self.rgb = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'MENU baseline'):
            self.run_menu()
        self.assertNotIn(('frame', menu_key_body('down'), 2), self.session.events)

    def test_c10_selector_failure_is_recorded_not_visual_success(self):
        self.session.bad_c10 = True
        with self.assertRaisesRegex(RuntimeError, 'selector'):
            self.run_menu()
        result = json.loads((self.output / 'result.json').read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['mcu_selection_verified'])
        self.assertFalse(result['c10_glyph_visually_verified'])

    def test_unfinished_frame_fails_and_does_not_send_next_key(self):
        self.session.accept_frames = False
        with self.assertRaisesRegex(RuntimeError, 'completed MCU'):
            self.run_menu()
        self.assertNotIn(('frame', menu_key_body('menu'), 2), self.session.events)

    def test_invalid_inputs_and_existing_output_have_no_guest_effects(self):
        for kwargs in ({'hold_wall_seconds': float('nan')}, {'release_wall_seconds': -1},
                       {'overall_wall_seconds': 0}, {'hold_wall_seconds': True}):
            with self.assertRaises(ValueError): self.run_menu(**kwargs)
            self.assertEqual(self.session.events, [])
        self.output.mkdir()
        with self.assertRaises(FileExistsError): self.run_menu()
        self.assertEqual(self.session.events, [])

    def test_bad_current_and_truncated_capture_fail_before_traversal(self):
        self.session.current = 39
        with self.assertRaisesRegex(RuntimeError, 'selector'):
            self.run_menu()
        self.assertNotIn(('frame', menu_key_body(), 2), self.session.events)

    def test_truncated_frame_is_not_accepted(self):
        self.session.short_frame = True
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            self.run_menu()

    def test_deadline_after_press_still_releases_the_key(self):
        clock = [0.0]
        run = self.session.run
        def advance(seconds):
            run(seconds)
            if self.session.record['rx'][-1]['body_hex'] == menu_key_body('menu').hex():
                clock[0] = 2.0
        self.session.run = advance
        with patch.object(menu.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                self.run_menu(overall_wall_seconds=1)
        frames = [e[1] for e in self.session.events if e[0] == 'frame']
        self.assertEqual(frames, [menu_key_body(), menu_key_body('menu'), menu_key_body()])

    def test_original_getter_failures_and_unsafe_initial_state(self):
        for problem in ('getter', 'short', 'identity', 'running', 'unknown'):
            with self.subTest(problem=problem):
                self.output = self.session.out / problem
                session = Session(self.session.out)
                if problem == 'getter': session.call = lambda *a, **k: 0
                if problem == 'short': session.read_bytes = lambda *a: b''
                if problem == 'identity': session.data['firmware_sha256'] = 'wrong'
                if problem == 'running': session.command = lambda *a, **k: {'running': True}
                if problem == 'unknown': session.log = lambda: 'Z9_UNKNOWN'
                with self.assertRaises((RuntimeError, ValueError)):
                    menu.run_menu_acceptance(session, expected_menu_rgb_sha256=self.rgb, output=self.output)
                self.assertFalse(any(event[0] == 'frame' for event in session.events))

    def test_press_transport_failure_attempts_release_and_preserves_cause(self):
        run = self.session.run
        def fail_menu(seconds):
            run(seconds)
            if self.session.record['rx'][-1]['body_hex'] == menu_key_body('menu').hex():
                raise RuntimeError('press transport failed')
        self.session.run = fail_menu
        with self.assertRaisesRegex(RuntimeError, 'press transport failed'):
            self.run_menu()
        frames = [e[1] for e in self.session.events if e[0] == 'frame']
        self.assertEqual(frames[-2:], [menu_key_body('menu'), menu_key_body()])


if __name__ == '__main__': unittest.main()
