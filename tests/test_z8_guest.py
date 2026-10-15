# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity and lifecycle preparation for the Z8 functional guest."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


class VideoC1Properties(unittest.TestCase):
    def test_c1_options_remain_explicit_and_boolean(self):
        import z8_guest
        from z8_properties import load_model_profile
        names=('z8-video-c1-model','z8-video-c1-primary-boundaries-model',
               'z8-video-c1-source-end-model','z8-video-c1-fourth-output-model',
               'z8-video-processing-config-model')
        defaults=load_model_profile()['properties']
        for name in names:
            self.assertNotIn(name+'=on',defaults)
            self.assertEqual(z8_guest.validate_properties([name+'=on']),[name+'=on'])
            with self.assertRaises(ValueError):z8_guest.validate_properties([name+'=guess'])
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_guest


class GuestIdentity(unittest.TestCase):
    def test_media_arguments_require_separate_explicit_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); firmware=root/'main.bin'; firmware.write_bytes(b'input')
            image=root/'media.img'
            with self.assertRaises(ValueError): z8_guest.media_arguments(None,firmware,create=True)
            with self.assertRaises(FileNotFoundError): z8_guest.media_arguments(image,firmware)
            with self.assertRaises(ValueError): z8_guest.media_arguments(firmware,firmware)
            args=z8_guest.media_arguments(image,firmware,create=True)
            self.assertIn('z8-nand-image-create=on',args)
            self.assertFalse(image.exists())
            image.write_bytes(b'opaque-existing-media')
            self.assertNotIn('z8-nand-image-create=on',z8_guest.media_arguments(image,firmware))
            with self.assertRaises(FileExistsError): z8_guest.media_arguments(image,firmware,create=True)

    def test_wrong_firmware_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / 'wrong.bin'; bad.write_bytes(b'not a firmware')
            with self.assertRaises(ValueError):
                z8_guest.Guest(bad, bad, bad, root / 'out', properties=[])
            self.assertFalse((root / 'out').exists())

    def test_command_binds_z8_and_explicit_properties(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            firmware = root / 'authored.bin'; firmware.write_bytes(bytes.fromhex('1f2003d5') * 4)
            raw = root / 'scene.raw'; raw.write_bytes(b'synthetic-scene')
            binary = root / 'qemu'; binary.write_bytes(b'synthetic-binary')
            profile = dict(model='Nikon Z8',version='C3.11',bytes=16,
                           sha256=hashlib.sha256(firmware.read_bytes()).hexdigest(),
                           load_address='0x40160000',entry_point='0x40160000')
            with patch.object(z8_guest, 'load_profile', return_value=profile):
                guest = z8_guest.Guest(firmware,binary,raw,root/'out',
                                      properties=['bootstrap-fixtures=on'],instruction_clock=False)
            try:
                machine = guest.command[guest.command.index('-M') + 1]
                self.assertEqual(machine,'nikon-z8-bringup,bootstrap-fixtures=on')
                self.assertNotIn('-icount',guest.command)
                self.assertEqual(guest.data['firmware_sha256'],profile['sha256'])
                self.assertFalse(guest.data['menu_verified'])
                self.assertFalse(guest.data['liveview_verified'])
                self.assertFalse(guest.data['runtime_profile']['instruction_clock'])
                self.assertFalse(any(guest.data['verification_scope'].values()))
                self.assertEqual(guest.command[guest.command.index('-kernel')+1],str(firmware))
                guest.data['menu_verified'] = True
            finally:
                guest.__exit__(None,None,None)
            result = json.loads((root/'out/result.json').read_text())
            self.assertTrue(result['owned_qemu_stopped'])
            scope = result['verification_scope']
            self.assertTrue(scope.pop('external_mcu_assisted_menu_verified'))
            self.assertFalse(any(scope.values()))
            self.assertEqual(firmware.read_bytes(),bytes.fromhex('1f2003d5')*4)
            with patch.object(z8_guest,'load_profile',return_value=profile),self.assertRaises(ValueError):
                z8_guest.Guest(firmware,binary,raw,root/'invalid,output',properties=[])
            self.assertFalse((root/'invalid,output').exists())

    def test_duplicate_or_malformed_properties_rejected(self):
        for properties in (['foo=1','foo=2'],['foo,bar=1'],['firmware=/elsewhere'],['no-value']):
            with self.subTest(properties=properties), self.assertRaises(ValueError):
                z8_guest.validate_properties(properties)


if __name__ == '__main__':
    unittest.main()
