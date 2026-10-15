# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess,tempfile,unittest
from pathlib import Path

class JPEGOwnershipHelpers(unittest.TestCase):
    def test_slots_and_source_leases(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                binary=Path(tmp)/'jpeg-owner'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(Path(__file__).with_suffix('.c')),'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
                self.assertIn('independent JPEG slots and read leases passed',result.stdout)
