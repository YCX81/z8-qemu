# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard the simulation loader before any QEMU process or output is created."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_c10_image as image
import z8_picture_control as pc


def branch(text, address):
    """Independent encoding of the one B instruction required by compose."""
    target = int(text.removeprefix('b #'))
    delta = target - address
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError('Invalid branch target')
    return struct.pack('<I', 0x14000000 | ((delta // 4) & 0x3FFFFFF))


class SourceMappings(unittest.TestCase):
    def test_rom_and_initialized_scatter_have_distinct_verified_mappings(self):
        self.assertEqual(image.source_offset(pc.BASE + 12, 4, pc.MAIN_BYTES), 12)
        self.assertEqual(image.source_offset(0xA02983CC0, 8, pc.MAIN_BYTES), 0x4C8ECC0)
        self.assertEqual(image.source_offset(0xA02983CD8, 8, pc.MAIN_BYTES), 0x4C8ECD8)
        self.assertEqual(image.source_offset(0xA02AB4FFC, 4, pc.MAIN_BYTES), 0x4DBFFFC)

    def test_unmapped_cross_boundary_and_empty_ranges_are_rejected(self):
        cases = ((pc.BASE - 1, 4, pc.MAIN_BYTES),
                 (pc.BASE + pc.MAIN_BYTES - 3, 4, pc.MAIN_BYTES),
                 (0xA02AB4FFC, 8, pc.MAIN_BYTES),
                 (0xA02808000, 4, 0x4B13003),
                 (pc.BASE, 0, pc.MAIN_BYTES),
                 (pc.BASE, -1, pc.MAIN_BYTES))
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                image.source_offset(*args)


class GuardedComposition(unittest.TestCase):
    """Small artificial inputs isolate guards; they are not firmware evidence."""
    def setUp(self):
        self.source = bytes(range(256)) * 16
        self.core = dict(patches=[self.change(16)], regions=[
            dict(address=pc.CODE_BASE, data=bytes(0x4000), permissions='rx'),
            dict(address=pc.DATA_BASE, data=b'core-data'.ljust(0x32000, b'\0'), permissions='rw')],
            entries=dict(save_callback=pc.SAVE_CALLBACK, load_callback=pc.LOAD_CALLBACK))
        self.menu = dict(patches=[self.change(24)], regions=[
            dict(address=pc.CODE_BASE + 0x4000, data=b'menu', permissions='rx')])
        self.labels = dict(patches=[self.change(32)], regions=[
            dict(address=pc.CODE_BASE + 0x7000, data=b'labels', permissions='rx')])
        self.list_plan = dict(patches=[self.change(36)], regions=[
            dict(address=pc.CODE_BASE + 0x3A00, data=bytes(0x600), permissions='rx')],
            core_tail_reservation=dict(address=pc.CODE_BASE + 0x3A00, bytes=0x600, verified_zero=True))
        self.main_menu = dict(patches=[self.change(44)], regions=[],
            core_data_overlay=dict(address=pc.DATA_BASE + 0x1C000, expected=bytes(0x3000),
                                   data=b'authored metadata'.ljust(0x3000, b'\0')))
        self.media = dict(address=pc.CODE_BASE + 0x8000, data=bytes(0x1000))
        self.memory = dict(patch=self.change(40), metadata={'scope': 'authored test reservation'})
        for name, result in (('build_plan', self.core), ('build_menu_plan', self.menu),
                             ('build_label_plan', self.labels), ('build_media_code', self.media),
                             ('build_list_plan', self.list_plan),
                             ('build_main_menu_plan', self.main_menu),
                             ('reservation_patch', self.memory)):
            p = patch.object(image, name, return_value=result)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(image, '_assemble', side_effect=branch)
        p.start()
        self.addCleanup(p.stop)

    def change(self, offset):
        return dict(address=pc.BASE + offset, expected=self.source[offset:offset + 4],
                    replacement=b'NEW!', name=f'test patch {offset}')

    def test_combines_all_plans_without_mutating_inputs_and_wires_guest_media(self):
        before = deepcopy((self.core, self.menu, self.labels, self.media))
        combined = image.compose(self.source)
        self.assertEqual((self.core, self.menu, self.labels, self.media), before)
        expected = bytearray(self.source)
        for offset in (16, 24, 32, 36, 40, 44):
            expected[offset:offset + 4] = b'NEW!'
        self.assertEqual(combined['image'], expected)
        self.assertEqual(combined['source_sha256'], hashlib.sha256(self.source).hexdigest())
        self.assertEqual(set(combined['plans']), {'core', 'menu', 'labels', 'list', 'main_menu'})
        data = next(r['data'] for r in combined['regions'] if r['address'] == pc.DATA_BASE)
        self.assertEqual(data[0x1C000:0x1F000], self.main_menu['core_data_overlay']['data'])
        self.assertEqual(combined['memory_reservation'], self.memory['metadata'])
        code = next(r['data'] for r in combined['regions'] if r['address'] == pc.CODE_BASE)
        for address, target in ((pc.SAVE_CALLBACK, image.SAVE), (pc.LOAD_CALLBACK, image.LOAD)):
            encoded = struct.unpack_from('<I', code, address - pc.CODE_BASE)[0]
            self.assertEqual(encoded, 0x14000000 | (((target - address) // 4) & 0x3FFFFFF))
        self.assertEqual(self.source, bytes(range(256)) * 16)

    def test_original_byte_mismatch_fails_before_output_directory_creation(self):
        self.menu['patches'][0]['expected'] = b'BAD!'
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'must-not-exist'
            with self.assertRaisesRegex(ValueError, 'Original patch bytes'):
                image.build_experiment(self.source, output)
            self.assertFalse(output.exists())

    def test_main_menu_overlay_refuses_existing_data_and_out_of_range_target(self):
        row = self.core['regions'][1]
        data = bytearray(row['data']); data[0x1C000] = 1; row['data'] = bytes(data)
        with self.assertRaisesRegex(ValueError, 'empty reservation'):
            image.compose(self.source)
        data[0x1C000] = 0; row['data'] = bytes(data)
        self.main_menu['core_data_overlay']['address'] = pc.DATA_BASE + 0x40000
        with self.assertRaisesRegex(ValueError, 'empty reservation'):
            image.compose(self.source)

    def test_non_length_preserving_and_empty_patches_are_rejected(self):
        for old, new in ((b'', b''), (b'abc', b'defg')):
            with self.subTest(old=old):
                self.core['patches'][0].update(expected=old, replacement=new)
                with self.assertRaisesRegex(ValueError, 'length'):
                    image.compose(self.source)

    def test_overlapping_patch_spans_not_only_duplicate_starts_are_rejected(self):
        self.menu['patches'] = [self.change(18)]
        with self.assertRaisesRegex(ValueError, 'Overlapping source patches'):
            image.compose(self.source)

    def test_loader_regions_reject_overlap_wrong_permissions_empty_and_outside_reservation(self):
        for address, data, permission, message in (
                (pc.CODE_BASE + 0x3FFF, b'overlap', 'rx', 'Overlapping'),
                (pc.CODE_BASE + 0x4000, b'rw-code', 'rw', 'reservation'),
                (pc.DATA_BASE + 0x32000, b'rx-data', 'rx', 'reservation'),
                (pc.DATA_BASE + 0x38000, b'scratch', 'rw', 'reservation'),
                (pc.CODE_BASE + 0xFFFF, b'XX', 'rx', 'reservation'),
                (pc.CODE_BASE + 0x4000, b'', 'rx', 'reservation')):
            with self.subTest(address=hex(address), permission=permission):
                self.menu['regions'] = [dict(address=address, data=data, permissions=permission)]
                with self.assertRaisesRegex(ValueError, message):
                    image.compose(self.source)

    def test_callback_instruction_must_fit_inside_executable_owner(self):
        for address in (pc.CODE_BASE + 0x3FFE, pc.DATA_BASE, pc.CODE_BASE + 0x6000):
            with self.subTest(address=hex(address)):
                self.core['entries']['save_callback'] = address
                with self.assertRaises(ValueError):
                    image.compose(self.source)

    def test_media_entry_must_fit_inside_executable_media_region(self):
        self.media['data'] = bytes(4)  # SAVE exists, LOAD is outside this blob.
        with self.assertRaises(ValueError):
            image.compose(self.source)

    def test_explicit_scatter_offset_cannot_disagree_with_verified_mapping(self):
        self.core['patches'][0]['source_offset'] = 17
        with self.assertRaises(ValueError):
            image.compose(self.source)

    def test_writes_inspectable_artifacts_to_fresh_directory_only(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'fresh'
            result = image.build_experiment(self.source, output)
            self.assertEqual(result['derived_main'].read_bytes(), result['image'])
            metadata = json.loads((output / 'experiment.json').read_text())
            self.assertTrue(metadata['simulation_only'])
            self.assertFalse(metadata['camera_update_container'])
            self.assertEqual(metadata['original_sha256'], hashlib.sha256(self.source).hexdigest())
            self.assertEqual(metadata['derived_sha256'], hashlib.sha256(result['image']).hexdigest())
            self.assertEqual([int(p['source_offset'], 16) for p in metadata['source_patches']], [16, 24, 32, 36, 44, 40])
            self.assertEqual(metadata['memory_reservation'], self.memory['metadata'])
            for region in result['loader_regions']:
                raw = region['path'].read_bytes()
                self.assertEqual(len(raw), region['bytes'])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), region['sha256'])
                self.assertEqual(region['path'].parent, output)
            sentinel = output / 'user-data.txt'
            sentinel.write_text('leave me alone')
            snapshot = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaises(FileExistsError):
                image.build_experiment(self.source, output)
            self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, snapshot)


class PinnedSourceGuard(unittest.TestCase):
    def test_unrecognized_main_is_rejected_without_outputs(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'bad-input'
            with self.assertRaisesRegex(ValueError, 'pinned original'):
                image.build_experiment(b'not a firmware image', output)
            self.assertFalse(output.exists())


@unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Set Z8_MAIN to pinned C3.11 MAIN')
class OriginalMainComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(os.environ['Z8_MAIN'])
        cls.source = cls.path.read_bytes()
        cls.combined = image.compose(cls.source)

    def test_derivative_changes_only_guarded_spans_and_keeps_original_file(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), pc.MAIN_SHA256)
        self.assertEqual(len(self.combined['image']), pc.MAIN_BYTES)
        restored = bytearray(self.combined['image'])
        for p in self.combined['patches']:
            offset = image.source_offset(p['address'], len(p['expected']), len(self.source))
            self.assertEqual(self.source[offset:offset + len(p['expected'])], p['expected'])
            self.assertEqual(restored[offset:offset + len(p['replacement'])], p['replacement'])
            restored[offset:offset + len(p['expected'])] = p['expected']
        self.assertEqual(restored, self.source)

    def test_scatter_pointer_edits_and_builtin_prefix_exist_before_native_list_rebuild(self):
        data = next(r['data'] for r in self.combined['regions'] if r['address'] == pc.DATA_BASE)
        core = self.combined['plans']['core']
        for old_list, descriptor, new_list in ((0xA02983D04, 0xA02983CB0, pc.LIST0),
                                               (0xA02983DA4, 0xA02983CC8, pc.LIST1)):
            offset = image.source_offset(descriptor + 16, 8, len(self.source))
            self.assertEqual(struct.unpack_from('<Q', self.source, offset)[0], old_list)
            self.assertEqual(struct.unpack_from('<Q', self.combined['image'], offset)[0], new_list)
            prefix_offset = image.source_offset(old_list, 31 * 4, len(self.source))
            prefix = self.source[prefix_offset:prefix_offset + 31 * 4]
            self.assertEqual(data[new_list - pc.DATA_BASE:new_list - pc.DATA_BASE + 31 * 4], prefix)
            words = struct.unpack('<31I', prefix)
            self.assertEqual(len(set(words)), 31)
            self.assertTrue({37, 38, 39}.issubset(words))
            p = next(p for p in core['patches'] if p['address'] == descriptor + 16)
            self.assertEqual(p['source_offset'], offset)
            self.assertEqual(p['offset_space'], 'initialized_guest_ram')
            # Original scatter publishes count=40 before the native rebuild.
            # Every visible initial entry must therefore be initialized.
            count_offset = image.source_offset(descriptor + 8, 4, len(self.source))
            count = struct.unpack_from('<I', self.source, count_offset)[0]
            self.assertEqual(count, 40)
            original_list = self.source[prefix_offset:prefix_offset + count * 4]
            self.assertEqual(data[new_list - pc.DATA_BASE:new_list - pc.DATA_BASE + count * 4], original_list)
        self.assertEqual(struct.unpack_from('<10I', data, pc.CODES - pc.DATA_BASE), (*range(28, 37), 40))


if __name__ == '__main__':
    unittest.main()
