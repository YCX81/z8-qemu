# SPDX-License-Identifier: GPL-2.0-or-later
"""Host orchestration contracts only; these mocks do not validate GUI pixels."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_c10_gui as gui


class Session:
    def __init__(self, directory):
        self.g = self
        self.out = Path(directory)
        self.data = {'firmware_sha256': gui.MAIN_SHA}
        self.calls = []
        self.memory = {}
        self.qmp = self
        self.running = False
        self.key = 0x5f
        self.index = 3
        self.unknown = False
        self.put(gui.POOL, '<Q', 0xa03100000)
        for address in gui.FONT_CALLBACKS:
            self.put(address, '<Q', 0x4032e70c)
        self.put(gui.ACTIVE_LIST, '<Q', gui.LIST_OBJECT)
        self.put(gui.LIST_OBJECT + 0x20, '<Q', gui.CONFIG)
        self.put(gui.CONFIG, '<III', 41, 1, 40)
        self.put(gui.CONFIG + 0x18, '<QQ', gui.LABELS, gui.CELLS)
        self.put(gui.STATE, '<II', 0, 10)
        self.put(gui.SELECTORS, '<10I', *range(31, 41))
        self.put(gui.LABELS, '<41H', *([0x45a] * 40 + [0x7ff0]))

    def put(self, address, fmt, *values):
        for offset, value in enumerate(struct.pack(fmt, *values)):
            self.memory[address + offset] = value

    def read_bytes(self, address, length):
        return bytes(self.memory.get(address + i, 0) for i in range(length))

    def log(self): return 'Z9_UNKNOWN' if self.unknown else ''

    def call(self, address, *args, **kwargs):
        self.calls.append((address, args))
        return {gui.CURRENT_INDEX: self.index, gui.INDEX_KEY: self.key}.get(address, 0)

    def run(self, seconds): self.calls.append(('run', seconds))

    def command(self, operation, **kwargs):
        if operation == 'query-status': return {'running': self.running}
        if operation == 'screendump':
            Path(kwargs['filename']).write_bytes(b'P6\n2 1\n255\n' + b'\x10\x20\x30' * 2)
        return {}


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Session(self.directory.name)

    def test_inspection_reports_native_graph_without_writes(self):
        state = gui.inspect_picture_control(self.session)
        self.assertEqual(state['page_key'], 0x5f)
        self.assertEqual(state['c10_row'], 40)
        self.assertEqual(state['focused_row'], 40)
        self.assertTrue(state['native_graph_matches'])
        self.assertEqual(self.session.calls, [(gui.CURRENT_INDEX, ()), (gui.INDEX_KEY, (3,))])

    def test_native_redraw_capture_is_not_automatically_visual_acceptance(self):
        result = gui.capture_picture_control(self.session, settle_seconds=.01)
        self.assertIn((gui.REFRESH, ()), self.session.calls)
        self.assertNotIn((gui.FAMILY_INIT, ()), self.session.calls)
        self.assertTrue(Path(result['capture']).is_file())
        self.assertEqual(result['image_dimensions'], [2, 1])
        self.assertFalse(result['c10_glyph_visually_verified'])
        self.assertFalse(result['whole_camera_menu_clicks'])
        self.assertTrue(result['native_gui_graph_verified'])

    def test_rebuild_uses_original_family_allocator_not_bare_row_ctor(self):
        gui.capture_picture_control(self.session, rebuild=True, settle_seconds=.01)
        self.assertIn((gui.FAMILY_INIT, ()), self.session.calls)
        self.assertNotIn((0x4045341c, ()), self.session.calls)

    def test_wrong_page_null_fonts_and_unknown_mmio_do_not_rebuild(self):
        for case in ('page', 'fonts', 'identity', 'running', 'unknown'):
            with self.subTest(case=case):
                session = Session(self.directory.name)
                if case == 'page': session.key = 4
                if case == 'fonts': session.put(gui.FONT_CALLBACKS[0], '<Q', 0)
                if case == 'identity': session.data['firmware_sha256'] = 'wrong'
                if case == 'running': session.running = True
                if case == 'unknown': session.unknown = True
                with self.assertRaises((ValueError, RuntimeError)):
                    gui.capture_picture_control(session, rebuild=True)
                self.assertNotIn((gui.FAMILY_INIT, ()), session.calls)

    def test_bad_compact_list_or_focus_is_not_screen_success(self):
        for case in ('count', 'mode', 'pointer', 'duplicate', 'label', 'focus', 'pool'):
            with self.subTest(case=case):
                session = Session(self.directory.name)
                if case == 'count': session.put(gui.STATE + 4, '<I', 11)
                if case == 'mode': session.put(gui.STATE, '<I', 1)
                if case == 'pointer': session.put(gui.CONFIG + 0x20, '<Q', 0)
                if case == 'duplicate': session.put(gui.SELECTORS, '<I', 40)
                if case == 'label': session.put(gui.LABELS + 80, '<H', 0x46a)
                if case == 'focus': session.put(gui.CONFIG + 8, '<I', 39)
                if case == 'pool': session.put(gui.POOL, '<Q', 0)
                with self.assertRaises((ValueError, RuntimeError)):
                    gui.capture_picture_control(session)
                self.assertFalse((session.out / 'c10-native-gui.ppm').exists())

    def test_capture_name_is_owned_and_existing_file_is_preserved(self):
        for name in ('../outside.ppm', 'x/y.ppm', 'wrong.png', ''):
            with self.assertRaises(ValueError):
                gui.capture_picture_control(self.session, filename=name)
        target = self.session.out / 'c10-native-gui.ppm'
        target.write_bytes(b'existing')
        with self.assertRaises(FileExistsError): gui.capture_picture_control(self.session)
        self.assertEqual(target.read_bytes(), b'existing')

    def test_ppm_rejects_truncation_and_not_a_frame(self):
        for raw in (b'', b'P6\n2 1\n255\nabc', b'P6\n0 1\n255\n', b'P3\n1 1\n255\nabc'):
            with self.assertRaises(ValueError): gui.ppm_dimensions(raw)
        self.assertEqual(gui.ppm_dimensions(b'P6\n# QEMU\n1 1\n255\n\x0a\x00\xff'), (1, 1))

    def test_short_reads_and_unbounded_current_index_are_rejected(self):
        self.session.read_bytes = lambda address, length: b''
        with self.assertRaisesRegex(RuntimeError, 'Short'): gui.inspect_picture_control(self.session)
        self.session.call = lambda *args: 224
        with self.assertRaisesRegex(RuntimeError, 'index'): gui.inspect_picture_control(self.session)

    def test_page_change_during_native_render_is_not_accepted(self):
        self.session.run = lambda seconds: setattr(self.session, 'key', 4)
        with self.assertRaisesRegex(RuntimeError, 'changed'): gui.capture_picture_control(self.session)
        self.assertFalse(self.session.data['c10_native_gui'][0]['native_gui_graph_verified'])

    def test_index_domain_is_twelve_initialized_slots_not_224_page_types(self):
        self.session.index = 11
        self.assertEqual(gui.inspect_picture_control(self.session)['page_index'], 11)
        self.session.index = 12
        with self.assertRaisesRegex(RuntimeError, 'index'): gui.inspect_picture_control(self.session)
        self.assertNotIn((gui.INDEX_KEY, (12,)), self.session.calls)

    def test_dynamic_movie_page_uses_mode_one_and_selector_ninety(self):
        self.session.key = 0xbd
        self.session.put(gui.STATE, '<II', 1, 10)
        self.session.put(gui.SELECTORS, '<10I', *range(81, 91))
        result = gui.capture_picture_control(self.session, mode=1, rebuild=True, settle_seconds=.01)
        self.assertEqual(result['after_redraw']['mode'], 1)
        self.assertEqual(result['after_redraw']['selectors'][-1], 90)
        self.assertTrue(result['native_gui_graph_verified'])

    def test_primary_photo_page_cannot_claim_movie_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'mode'):
            gui.capture_picture_control(self.session, mode=1, rebuild=True)
        self.assertNotIn((gui.FAMILY_INIT, ()), self.session.calls)

    def test_photo_key_ten_uses_its_independent_native_graph(self):
        self.session.key = 0x10
        self.session.put(gui.ACTIVE_LIST, '<Q', 0xa032f4f60)
        self.session.put(0xa032f4f60 + 0x20, '<Q', 0xa032f5b10)
        self.session.put(0xa032f5b10, '<III', 41, 1, 40)
        self.session.put(0xa032f5b10 + 0x18, '<QQ', gui.LABELS, gui.CELLS_ALT)
        self.session.put(0xa032f5778, '<Q', 0xa03100000)
        result = gui.capture_picture_control(self.session, rebuild=True, settle_seconds=.01)
        self.assertIn((0x4048c44c, ()), self.session.calls)
        self.assertNotIn((gui.FAMILY_INIT, ()), self.session.calls)
        self.assertTrue(result['native_gui_graph_verified'])

    def test_navigation_uses_native_manager_and_retains_parent_tile_on_return(self):
        session = self.session
        session.put(0xa032f84e0, '<12I', 1, 2, 0x5f, *([0] * 9))
        original_call = session.call

        def native(address, *args, **kwargs):
            result = original_call(address, *args, **kwargs)
            if address == 0x4044dc88:
                session.put(0xa0283b55c, '<I', args[0])
                session.put(0xa0283b558, '<I', 0)
            elif address == 0x404d8dc0:
                session.index = args[0]
                session.key = 0x5f
            elif address == 0x4044ebf8:
                session.put(0xa0283b558, '<I', args[0])
            return result

        session.call = native
        navigation = gui.navigate_picture_control(session, parent_page=0x4f, mode=0)
        self.assertEqual(navigation['selected_tile'], 2)
        self.assertEqual(navigation['opened']['c10_row'], 40)
        self.assertIn((0x404d8dc0, (2,)), session.calls)
        self.assertIn((0x4044ebf8, (2,)), session.calls)
        returned = gui.return_to_parent(session, navigation)
        self.assertTrue(returned['parent_tile_retained'])
        self.assertFalse(returned['whole_camera_menu_clicks'])

    def test_navigation_rejects_unlisted_tile_and_unsupported_parent(self):
        with self.assertRaises(ValueError): gui.navigate_picture_control(self.session, parent_page=0x123)
        self.assertEqual(self.session.calls, [])
        self.session.put(0xa0283b55c, '<I', 0x4f)
        with self.assertRaisesRegex(RuntimeError, 'tile'):
            gui.navigate_picture_control(self.session, parent_page=0x4f)
        self.assertNotIn((0x4044ebf8, (2,)), self.session.calls)

    def test_observed_mcu_menu_keys_preserve_original_release_frame(self):
        release = gui.menu_key_body()
        self.assertEqual(len(release), 124)
        self.assertEqual([(i, value) for i, value in enumerate(release) if value], [(0, 1), (4, 1)])
        for key, field in {'right': 0x22, 'left': 0x23, 'up': 0x24, 'down': 0x25,
                           'ok': 0x2a, 'menu': 0x43}.items():
            body = gui.menu_key_body(key)
            self.assertEqual([i for i in range(124) if body[i] != release[i]], [field])
            self.assertEqual(body[field], 1)
        for bad in ('diagonal', 'delete', 22, True, []):
            with self.assertRaises(ValueError): gui.menu_key_body(bad)


if __name__ == '__main__': unittest.main()
