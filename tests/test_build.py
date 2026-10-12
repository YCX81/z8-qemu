# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic download responses and portable Linux environment contracts."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build
from guest import Guest
from check_service_events import SyntheticGuest


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.work = Path(self.folder.name)
        self.archive = self.work / f'qemu-{build.VERSION}.tar.xz'
        self.payload = b'authored archive stand-in, not QEMU or firmware'
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.addCleanup(patch.stopall)
        patch.object(build, 'SHA256', self.digest).start()
        self.sleep = patch.object(build.time, 'sleep').start()

    def test_verified_cache_requires_no_network(self):
        self.archive.write_bytes(self.payload)
        with patch.object(build.urllib.request, 'urlopen') as request:
            self.assertEqual(build.verified_archive(self.work), self.archive)
        request.assert_not_called()

    def test_download_is_temporary_until_hash_passes(self):
        def open_response(*args, **kwargs):
            self.assertFalse(self.archive.exists())
            self.assertEqual(kwargs['timeout'], build.DOWNLOAD_TIMEOUT_SECONDS)
            return io.BytesIO(self.payload)
        with patch.object(build.urllib.request, 'urlopen', side_effect=open_response):
            self.assertEqual(build.verified_archive(self.work), self.archive)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(list(self.work.iterdir()), [self.archive])

    def test_bad_cache_is_replaced_only_after_valid_download(self):
        self.archive.write_bytes(b'corrupt cache')
        def open_response(*args, **kwargs):
            self.assertEqual(self.archive.read_bytes(), b'corrupt cache')
            return io.BytesIO(self.payload)
        with patch.object(build.urllib.request, 'urlopen', side_effect=open_response):
            build.verified_archive(self.work)
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_temporary_network_failure_retries_then_succeeds(self):
        with patch.object(build.urllib.request, 'urlopen', side_effect=[
                URLError('synthetic timeout'), io.BytesIO(self.payload)]) as request:
            build.verified_archive(self.work)
        self.assertEqual(request.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_bad_responses_are_never_cached_and_retries_are_bounded(self):
        with patch.object(build.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: io.BytesIO(b'bad hash')) as request:
            with self.assertRaisesRegex(RuntimeError, 'after 3 attempts'):
                build.verified_archive(self.work)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_total_deadline_cleans_partial_download(self):
        with patch.object(build.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: io.BytesIO(self.payload)), \
                patch.object(build.time, 'monotonic', side_effect=[0, 121] * 3):
            with self.assertRaisesRegex(RuntimeError, 'after 3 attempts'):
                build.verified_archive(self.work)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.work.iterdir()), [])

    def test_interrupted_download_cleans_partial_without_retry(self):
        with patch.object(build.urllib.request, 'urlopen', side_effect=KeyboardInterrupt) as request:
            with self.assertRaises(KeyboardInterrupt):
                build.verified_archive(self.work)
        request.assert_called_once()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_explicit_archive_is_checked_but_never_replaced(self):
        supplied = self.work / 'user-provided.tar.xz'
        with patch.object(build.urllib.request, 'urlopen') as request:
            with self.assertRaises(FileNotFoundError):
                build.verified_archive(self.work, supplied)
            supplied.write_bytes(b'bad supplied archive')
            with self.assertRaisesRegex(RuntimeError, 'SHA-256 mismatch'):
                build.verified_archive(self.work, supplied)
            self.assertEqual(supplied.read_bytes(), b'bad supplied archive')
            supplied.write_bytes(self.payload)
            self.assertEqual(build.verified_archive(self.work, supplied), supplied)
        request.assert_not_called()


@unittest.skipIf(os.name == 'nt', 'Runtime contracts require Linux/WSL')
class DistributionTests(unittest.TestCase):
    def test_build_orchestration_accepts_any_distribution_without_invoking_compiler(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, WSL_DISTRO_NAME='ubuntu-noble'):
            root = Path(folder)
            supplied = root / 'authored.tar.xz'
            with tarfile.open(supplied, 'w:xz') as archive:
                member = tarfile.TarInfo(f'qemu-{build.VERSION}/synthetic.txt')
                data = b'authored extraction input'
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            for name in build.SOURCES:
                target = root / name
                target.parent.mkdir(exist_ok=True)
                target.write_text('synthetic reviewed source')
            work = root / 'work'
            binary = work / 'build/qemu-system-aarch64'
            calls = []
            def pretend_command(command, **kwargs):
                calls.append(command)
                if command[0] == 'ninja':
                    binary.write_bytes(b'authored non-executable stand-in')
            with patch.object(build, 'ROOT', root), \
                    patch.object(build, 'SHA256', build.digest(supplied)), \
                    patch.object(build.subprocess, 'run', side_effect=pretend_command), \
                    patch.object(build.urllib.request, 'urlopen') as network, \
                    patch.object(sys, 'argv', ['build.py', '--work-dir', str(work),
                                               '--archive', str(supplied), '--jobs', '2']):
                build.main()
            network.assert_not_called()
            self.assertEqual(calls[0][0], 'python3')
            self.assertEqual(calls[2], ['ninja', '-j', '2', 'qemu-system-aarch64'])
            manifest = json.loads((root / '.local/build-manifest.json').read_text())
            self.assertEqual(manifest['binary_sha256'], build.digest(binary))
            self.assertEqual(set(manifest['sources']), set(build.SOURCES))
            self.assertIn('qemu/nikon-z9-lab.c', manifest['sources'])

    def test_named_distribution_reaches_normal_firmware_identity_check(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, WSL_DISTRO_NAME='ubuntu-noble'):
            firmware = Path(folder) / 'synthetic.bin'
            firmware.write_bytes(b'not firmware')
            with self.assertRaisesRegex(ValueError, 'user-supplied Z9'):
                Guest(firmware, firmware, firmware, Path(folder) / 'out')

    def test_named_distribution_can_prepare_authored_service_program(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, WSL_DISTRO_NAME='ubuntu-noble'):
            binary = Path(folder) / 'fake-qemu'
            binary.write_bytes(b'not executable; no process is launched')
            guest = SyntheticGuest(binary, Path(folder) / 'out')
            self.assertFalse(guest.data['firmware_used'])
            guest.__exit__(None, None, None)


if __name__ == '__main__':
    unittest.main()
