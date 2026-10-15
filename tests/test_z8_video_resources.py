# SPDX-License-Identifier: GPL-2.0-or-later
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from z8_resources import MAIN_SHA
from z8_video_resources import attach_processing_index,attach_recording_processing_index


class VideoResources(unittest.TestCase):
    def test_recording_index_is_separate_explicit_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            g=SimpleNamespace(out=Path(directory),data={'firmware_sha256':MAIN_SHA},command=[])
            preview=attach_processing_index(g)
            recording=attach_recording_processing_index(g)
            self.assertEqual(recording['selector'],10)
            self.assertEqual(recording['address'],0x47f5af4a)
            self.assertNotEqual(recording['file'],preview['file'])
            self.assertEqual((g.out/recording['file']).read_bytes(),b'\x01')
            self.assertFalse(recording['factory_coefficients_recovered'])
            self.assertFalse(recording['completion_or_readiness_supplied'])
            self.assertIn('addr=0x47f5af4a',g.command[-1])
            self.assertEqual(g.data['video_processing_index_input'],preview)
            self.assertEqual(g.data['video_recording_processing_index_input'],recording)
            with self.assertRaises(FileExistsError):attach_recording_processing_index(g)

    def test_recording_rejects_other_firmware(self):
        with tempfile.TemporaryDirectory() as directory:
            g=SimpleNamespace(out=Path(directory),data={'firmware_sha256':'wrong'},command=[])
            with self.assertRaises(ValueError):attach_recording_processing_index(g)
            self.assertEqual(g.command,[])
            self.assertEqual(list(g.out.iterdir()),[])

    def test_only_observed_external_index_and_honest_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            g=SimpleNamespace(out=Path(directory),data={'firmware_sha256':MAIN_SHA},command=[])
            result=attach_processing_index(g)
            self.assertEqual(result['selector'],16)
            self.assertEqual(result['address'],0x47f5af50)
            self.assertEqual((g.out/result['file']).read_bytes(),b'\x01')
            self.assertFalse(result['factory_coefficients_recovered'])
            self.assertFalse(result['guest_state_patch'])
            self.assertEqual(len(g.command),2)
            self.assertIn('addr=0x47f5af50',g.command[1])
            self.assertEqual(json.loads((g.out/'video-processing-index.json').read_text()),result)
            with self.assertRaises(FileExistsError):attach_processing_index(g)

    def test_identity_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            g=SimpleNamespace(out=Path(directory),data={'firmware_sha256':'wrong'},command=[])
            with self.assertRaises(ValueError):attach_processing_index(g)
            self.assertEqual(list(g.out.iterdir()),[])
            self.assertEqual(g.command,[])


if __name__=='__main__':unittest.main()
