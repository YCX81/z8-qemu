# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess
from pathlib import Path
import tempfile
import unittest
class OwnershipHelpers(unittest.TestCase):
    def test_normal_and_ndebug_keep_checks(self):
        source=Path(__file__).with_name('test_buffer_ownership.c')
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                binary=Path(folder)/'ownership'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(source),'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('buffer ownership checks passed',result.stdout)
