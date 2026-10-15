# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess
from pathlib import Path
import tempfile
import unittest

class HSFCOwnershipHelpers(unittest.TestCase):
    def test_independent_slots_and_reserved_jpeg_ids(self):
        source=Path(__file__).with_suffix('.c')
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                binary=Path(folder)/'hsfc-ownership'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(source),'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('independent HSFC ownership checks passed',result.stdout)
