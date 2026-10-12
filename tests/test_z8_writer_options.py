# SPDX-License-Identifier: GPL-2.0-or-later
"""Public opt-in and prelaunch guards; synthetic fixtures, no firmware/QEMU."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_writer_integration as writer
from z8_writer_observer import MAIN_SHA,ObservationError


class WriterOptions(unittest.TestCase):
    def guest(self):
        return SimpleNamespace(data={'firmware_sha256':MAIN_SHA,'qemu_sha256':writer.BINARY_SHA})
    def test_default_off_has_no_file_or_guest_side_effects(self):
        g=self.guest();before=dict(g.data)
        with patch.object(writer,'verify_main',side_effect=AssertionError('default off read')):
            self.assertFalse(writer.configure(g,SimpleNamespace(),lambda:None))
        self.assertEqual(g.data,before);self.assertFalse(hasattr(g,'_native_writer_observer_enabled'))
    def test_non_boolean_option_is_rejected(self):
        for value in (0,1,[],None,'true'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                writer.configure(self.guest(),SimpleNamespace(native_writer_observer=value),lambda:None)
    def test_enabled_option_runs_static_guard_but_does_not_activate(self):
        g=self.guest();path=Path('user-owned-main.bin');check=lambda:None
        with patch.object(writer,'verify_main',return_value={'sha256':MAIN_SHA,'points':30}) as verify:
            self.assertTrue(writer.configure(g,SimpleNamespace(native_writer_observer=True,firmware=path),check))
        verify.assert_called_once_with(path)
        self.assertTrue(g._native_writer_observer_enabled);self.assertIs(g._writer_host_check,check)
        self.assertEqual(g.data['native_writer_request']['static_main_guard']['points'],30)
        self.assertFalse(g.data['native_writer_request']['native_accepted'])
    def test_unverified_runtime_is_rejected_without_fallback(self):
        g=self.guest();g.data['qemu_sha256']='unverified-new-build'
        with patch.object(writer,'verify_main') as verify,self.assertRaises(ObservationError):
            writer.configure(g,SimpleNamespace(native_writer_observer=True,firmware=Path('main.bin')),lambda:None)
        verify.assert_not_called();self.assertFalse(hasattr(g,'_native_writer_observer_enabled'))
    def test_bad_original_main_guard_does_not_enable_observation(self):
        g=self.guest()
        with patch.object(writer,'verify_main',side_effect=ObservationError('MAIN mismatch')):
            with self.assertRaises(ObservationError):writer.configure(g,SimpleNamespace(native_writer_observer=True,firmware=Path('main.bin')),lambda:None)
        self.assertFalse(hasattr(g,'_native_writer_observer_enabled'));self.assertNotIn('native_writer_request',g.data)


if __name__=='__main__':unittest.main()
