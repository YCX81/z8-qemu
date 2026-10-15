# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess
import tempfile
from pathlib import Path
import unittest

class HLGPipelineHelpers(unittest.TestCase):
    def test_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'], ['-O2','-DNDEBUG']):
                binary=Path(folder)/'hlg-pipeline'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
                    str(Path(__file__).with_name('test_z8_hlg_pipeline.c')),'-o',str(binary)],
                    check=True,capture_output=True)
                out=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('HLG C1 contracts passed',out.stdout)
