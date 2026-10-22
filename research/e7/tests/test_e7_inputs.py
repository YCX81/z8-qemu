# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from e7_inputs import checked_file, bird_source, new_output_dir, private_firmware_dir


class InputContracts(unittest.TestCase):
    def test_file_hash_and_absence_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'sample.txt'
            with self.assertRaises(FileNotFoundError): checked_file(path)
            path.write_bytes(b'authored test data')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(checked_file(path, digest), path.resolve())
            self.assertEqual(checked_file(path), path.resolve())
            with self.assertRaises(ValueError): checked_file(path, '0'*64)
            with self.assertRaises(FileNotFoundError): checked_file(Path(tmp))

    def test_peer_is_explicit_and_hash_pinned(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): bird_source()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'E7_BIRD_SOURCE': tmp}):
                with self.assertRaises(FileNotFoundError): bird_source()
                (Path(tmp)/'core.py').write_text('# synthetic incompatible peer\n')
                with self.assertRaises(ValueError): bird_source()

    def test_firmware_path_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError): private_firmware_dir()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {'E7_FIRMWARE_DIR': tmp}):
                self.assertEqual(private_firmware_dir(), Path(tmp).resolve())
            with patch.dict(os.environ, {'E7_FIRMWARE_DIR': str(Path(tmp)/'missing')}):
                with self.assertRaises(FileNotFoundError): private_firmware_dir()

    def test_outputs_are_fresh_and_outside_public_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'new'/'run'
            self.assertEqual(new_output_dir(path), path.resolve())
            (path/'receipt.txt').write_text('preserve')
            with self.assertRaises(FileExistsError): new_output_dir(path)
            self.assertEqual((path/'receipt.txt').read_text(), 'preserve')
        repo = Path(__file__).resolve().parents[3]
        with self.assertRaises(ValueError): new_output_dir(repo/'research/e7/generated')

    def test_private_checkout_output_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Use an authored temporary checkout to avoid changing the real .local.
            repo = Path(tmp)
            with patch('e7_inputs.REPO', repo):
                path = repo/'.local'/'run'
                self.assertEqual(new_output_dir(path), path.resolve())
