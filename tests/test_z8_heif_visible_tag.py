# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess,tempfile,unittest
from pathlib import Path
class VisibleTagTests(unittest.TestCase):
    def test_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as d:
            exe=Path(d)/'tag'
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(Path(__file__).with_suffix('.c')),'-o',str(exe)],check=True,capture_output=True)
                subprocess.run([str(exe)],check=True,capture_output=True)
