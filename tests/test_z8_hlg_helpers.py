# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess
from pathlib import Path
import tempfile
import unittest

class HLGHelpers(unittest.TestCase):
    def test_c_contracts_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'], ['-O2', '-DNDEBUG']):
                binary=Path(folder)/'hlg-helpers'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
                    str(Path(__file__).with_name('test_z8_hlg_pixels.c')),'-o',str(binary)],
                    check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('HLG pixel contracts passed',result.stdout)
