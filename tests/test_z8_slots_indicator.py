# SPDX-License-Identifier: GPL-2.0-or-later
"""IND-1: liveview Picture Control indicator, copy-to-camera page and the
card-input fallback added to the C32/C64 derived MAIN. Simulation only."""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_slots_core import NativeSlots, build_plan
from z8_slots_layout import Layout
from z8_slots_main_menu import build_main_menu_plan
from z8_slots_labels import build_label_plan, CONTENT_SETTER
from z8_slots_image import compose
from z8_picture_control import make_flexible_np3, _assemble

ICONS, TEXTS = 0x42AE751C, 0x42AE7604
PHOTO_GETTER = 0x403F9608
ECB4 = 0x4041ECB4
LV_ENTRY = 0x4041A86C
CARD_LOAD, CARD_VALID, CARD_ALLOC, CARD_FREE, CARD_PARSE = 0x410AE940, 0x40863C4C, 0x40D2C408, 0x40D2C448, 0x410A924C


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Pinned original MAIN required')
class IndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ['Z8_MAIN']).read_bytes()

    def fixture(self, n=32, copy_page=False):
        g = NativeSlots(self.source, Layout(n)); self.addCleanup(g.close)
        g.uc.mem_map(0xA03200000, 0x300000)
        plan = build_main_menu_plan(self.source, g.layout, extend_copy_page=copy_page)
        for r in plan['regions']:
            if r['permissions'] == 'rx':
                g.uc.mem_map(r['address'], len(r['data']))
            else:
                self.assertEqual(bytes(g.uc.mem_read(r['address'], len(r['data']))), bytes(len(r['data'])))
            g.uc.mem_write(r['address'], r['data'])
        for p in plan['patches']:
            self.assertEqual(bytes(g.uc.mem_read(p['address'], len(p['expected']))), p['expected'])
            g.uc.mem_write(p['address'], p['replacement'])
        return g, plan

    # ---------------------------------------------------------- copy page
    def test_copy_to_camera_page_is_opt_in(self):
        _g, p = self.fixture(32)
        self.assertIsNone(p['copy_page'])
        self.assertFalse(any(0x40601708 <= x['address'] < 0x40601728 for x in p['patches']))

    def test_copy_to_camera_page_lists_every_slot(self):
        for n in (32, 64):
            g, p = self.fixture(n, copy_page=True)
            page = p['copy_page']
            self.assertEqual(page['count'], n)
            self.assertEqual(len(page['extensions']), n - 9)
            pairs = bytes(g.uc.mem_read(page['pairs'], n * 8))
            for slot in range(1, n + 1):
                selector, key = struct.unpack_from('<II', pairs, (slot - 1) * 8)
                self.assertEqual(selector, g.layout.selector(slot, 0))
                if slot >= 10:
                    self.assertEqual(key, 0x7A00 + slot)
            # The page getter publishes the configured table and count; the
            # generic lookup then maps every configured key to its selector.
            out = g.layout.address('scratch') + 0x700
            table = g.call(page['getter'], out)
            self.assertEqual(table, page['pairs'])
            self.assertEqual(struct.unpack('<I', g.uc.mem_read(out, 4))[0], n)
            for slot in (1, 9, 10, 20, n):
                index = g.call(0x403EB788, page['pairs'], n, 0x7A00 + slot if slot >= 10 else
                               struct.unpack_from('<I', pairs, (slot - 1) * 8 + 4)[0], 0)
                self.assertEqual(struct.unpack_from('<I', pairs, index * 8)[0], g.layout.selector(slot, 0))

    # ------------------------------------------------ liveview indicator
    def test_liveview_indicator_uses_owned_slot_text(self):
        from unicorn import UC_HOOK_CODE
        g, _ = self.fixture(32)
        g.import_np3(32, make_flexible_np3('C32', 32), 0)
        icons = struct.unpack_from('<2H', self.source, ICONS - 0x40160000 + 49 * 4)
        self.assertEqual(icons, (0x1824, 0x1824))
        state = g.layout.address('scratch') + 0x600
        captured = {}

        def getter(uc, address, size, data):
            uc.reg_write(g.r.UC_ARM64_REG_X0, g.layout.selector(32, 0))
            uc.reg_write(g.r.UC_ARM64_REG_PC, uc.reg_read(g.r.UC_ARM64_REG_LR))

        def ecb4(uc, address, size, data):
            captured['w0'] = uc.reg_read(g.r.UC_ARM64_REG_X0) & 0xffffffff
            captured['w3'] = uc.reg_read(g.r.UC_ARM64_REG_X3) & 0xffff
            captured['w4'] = uc.reg_read(g.r.UC_ARM64_REG_X4) & 0xffff
            uc.emu_stop()

        h1 = g.uc.hook_add(UC_HOOK_CODE, getter, begin=PHOTO_GETTER, end=PHOTO_GETTER)
        h2 = g.uc.hook_add(UC_HOOK_CODE, ecb4, begin=ECB4, end=ECB4)
        self.addCleanup(lambda: g.uc and (g.uc.hook_del(h1), g.uc.hook_del(h2)))
        for variant in (0, 1):
            captured.clear()
            g.uc.reg_write(g.r.UC_ARM64_REG_X19, 0)        # display index 0 (Auto) from the producer
            g.uc.reg_write(g.r.UC_ARM64_REG_X20, variant)   # icon/text highlight variant
            g.uc.reg_write(g.r.UC_ARM64_REG_X21, state)
            g.uc.reg_write(g.r.UC_ARM64_REG_X22, 1)
            g.uc.reg_write(g.r.UC_ARM64_REG_SP, g.layout.address('stack') + 0x8000)
            g.uc.emu_start(LV_ENTRY, ECB4, count=200)
            self.assertEqual(g.uc.reg_read(g.r.UC_ARM64_REG_PC), ECB4)
            ecb4(g.uc, ECB4, 4, None)
            self.assertEqual(captured.get('w0'), 0x24)
            self.assertEqual(captured.get('w3'), g.layout.label_id(32, 1))     # owned "C-32" text (FC record)
            self.assertEqual(captured.get('w4'), g.layout.label_id(32, 1))

    def test_liveview_indicator_leaves_builtin_rows_to_original(self):
        from unicorn import UC_HOOK_CODE
        g, _ = self.fixture(32)
        reached = {}

        def getter(uc, address, size, data):
            uc.reg_write(g.r.UC_ARM64_REG_X0, g.layout.selector(5, 0))  # builtin C5
            uc.reg_write(g.r.UC_ARM64_REG_PC, uc.reg_read(g.r.UC_ARM64_REG_LR))

        def original(uc, address, size, data):
            reached['pc'] = address; uc.emu_stop()

        h1 = g.uc.hook_add(UC_HOOK_CODE, getter, begin=PHOTO_GETTER, end=PHOTO_GETTER)
        h2 = g.uc.hook_add(UC_HOOK_CODE, original, begin=LV_ENTRY + 4, end=LV_ENTRY + 4)
        self.addCleanup(lambda: g.uc and (g.uc.hook_del(h1), g.uc.hook_del(h2)))
        g.uc.reg_write(g.r.UC_ARM64_REG_X19, 0)
        g.uc.reg_write(g.r.UC_ARM64_REG_X22, 1)
        g.uc.reg_write(g.r.UC_ARM64_REG_X21, g.layout.address('scratch') + 0x600)
        g.uc.reg_write(g.r.UC_ARM64_REG_SP, g.layout.address('stack') + 0x8000)
        g.uc.emu_start(LV_ENTRY, LV_ENTRY + 8, count=200)
        self.assertEqual(reached.get('pc'), LV_ENTRY + 4)  # resumed the original comparison

    # --------------------------------------------- owned widget text set
    def test_widget_content_setter_routes_owned_ids_only(self):
        g, _ = self.fixture(32)
        labels = build_label_plan(self.source, g.layout)
        for r in labels['regions']:
            if r['permissions'] == 'rx':
                g.uc.mem_map(r['address'], len(r['data']))
            g.uc.mem_write(r['address'], r['data'])
        for p in labels['patches']:
            self.assertEqual(bytes(g.uc.mem_read(p['address'], len(p['expected']))), p['expected'])
            g.uc.mem_write(p['address'], p['replacement'])
        obj = g.layout.address('scratch') + 0x500
        g.uc.mem_write(obj + 12, struct.pack('<I', 1))       # original kind 1
        self.assertEqual(g.call(CONTENT_SETTER, obj, 1, g.layout.label_id(10, 0)), 1)
        self.assertEqual(struct.unpack('<I', g.uc.mem_read(obj + 12, 4))[0], 3)  # converted to ASCII text

    def test_widget_content_setter_preserves_original_ids(self):
        from unicorn import UC_HOOK_CODE
        g, _ = self.fixture(32)
        labels = build_label_plan(self.source, g.layout)
        for r in labels['regions']:
            if r['permissions'] == 'rx':
                g.uc.mem_map(r['address'], len(r['data']))
            g.uc.mem_write(r['address'], r['data'])
        for p in labels['patches']:
            g.uc.mem_write(p['address'], p['replacement'])
        reached = {}

        def original(uc, address, size, data):
            reached['pc'] = address; uc.emu_stop()

        h = g.uc.hook_add(UC_HOOK_CODE, original, begin=CONTENT_SETTER + 4, end=CONTENT_SETTER + 4)
        self.addCleanup(lambda: g.uc and g.uc.hook_del(h))
        g.uc.reg_write(g.r.UC_ARM64_REG_X0, g.layout.address('scratch') + 0x500)
        g.uc.reg_write(g.r.UC_ARM64_REG_X1, 1)
        g.uc.reg_write(g.r.UC_ARM64_REG_X2, 0x45)  # a builtin content id
        g.uc.reg_write(g.r.UC_ARM64_REG_SP, g.layout.address('stack') + 0x8000)
        g.uc.emu_start(CONTENT_SETTER, CONTENT_SETTER + 8, count=200)
        self.assertEqual(reached.get('pc'), CONTENT_SETTER + 4)

    # ------------------------------------------- card-input fall back
    def test_card_input_services_fall_back_when_no_experimental_params(self):
        g = NativeSlots(self.source, Layout(32)); self.addCleanup(g.close)
        # params is cleared on a fresh plan (never written here).
        self.assertEqual(bytes(g.uc.mem_read(g.layout.address('params'), 16)), bytes(16))
        targets = {g.entries['file']: CARD_LOAD, g.entries['file_valid']: CARD_VALID,
                   g.entries['file_alloc']: CARD_ALLOC, g.entries['file_free']: CARD_FREE,
                   g.entries['parsed_copy']: CARD_PARSE}
        for entry, original in targets.items():
            g.uc.reg_write(g.r.UC_ARM64_REG_X0, 0x200)
            g.uc.reg_write(g.r.UC_ARM64_REG_X1, g.layout.address('scratch'))
            g.uc.reg_write(g.r.UC_ARM64_REG_SP, g.layout.address('stack') + 0x8000)
            g.uc.emu_start(entry, original, count=200)
            self.assertEqual(g.uc.reg_read(g.r.UC_ARM64_REG_PC), original)  # resumed the original callee

    def test_card_input_file_service_uses_params_when_import_active(self):
        g = NativeSlots(self.source, Layout(32)); self.addCleanup(g.close)
        params = g.layout.address('params')
        g.uc.mem_write(params, struct.pack('<QQ', 0xA047180D0, 0x100))  # as import_np3 sets
        out = g.layout.address('scratch') + 0x400
        self.assertEqual(g.call(g.entries['file'], 0, out), 1)
        pointer, length, flag = struct.unpack('<QII', g.uc.mem_read(out, 16))
        self.assertEqual((pointer, length, flag), (0xA047180D0, 0x100, 0))

    def test_import_clears_experimental_params_on_exit(self):
        g = NativeSlots(self.source, Layout(32)); self.addCleanup(g.close)
        g.import_np3(32, make_flexible_np3('C32', 32), 0)
        self.assertEqual(bytes(g.uc.mem_read(g.layout.address('params'), 16)), bytes(16))

    # ----------------------------------------------------- composition
    def test_compose_keeps_original_source_views_and_single_image(self):
        for n in (32, 64):
            result = compose(self.source, Layout(n))
            self.assertEqual(len(result['image']), len(self.source))
            changed = sum(a != b for a, b in zip(result['image'], self.source))
            self.assertGreater(changed, 0)
            # Every source patch names bytes that match the original image.
            for patch in result['patches']:
                off = patch['source_offset']
                self.assertEqual(self.source[off:off + len(patch['expected'])], patch['expected'])

    def test_card_call_sites_are_the_verified_original_instructions(self):
        plan = build_plan(self.source, Layout(32))
        wanted = {0x410ADCA8: 'bl #0x410ae940', 0x410AC834: 'bl #0x410ae940',
                  0x410DAC58: 'bl #0x410a924c', 0x410AC7EC: 'bl #0x40863c4c',
                  0x410AC7FC: 'bl #0x40d2c408', 0x410AC924: 'bl #0x40d2c448',
                  0x410D9968: 'sub sp,sp,#0x1f0'}
        by_address = {p['address']: p for p in plan['patches']}
        for address, asm in wanted.items():
            self.assertIn(address, by_address)
            self.assertEqual(by_address[address]['expected'], _assemble(asm, address))


if __name__ == '__main__':
    unittest.main()
