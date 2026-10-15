# SPDX-License-Identifier: GPL-2.0-or-later
from pathlib import Path
import subprocess
import tempfile
import unittest

class BackendHelpers(unittest.TestCase):
    def test_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                binary=Path(folder)/'helpers'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
                    str(Path(__file__).with_name('test_z8_still_hevc_backend.c')),'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('backend helper contracts passed',result.stdout)
