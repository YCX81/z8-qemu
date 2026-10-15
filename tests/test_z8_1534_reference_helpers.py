# SPDX-License-Identifier: GPL-2.0-or-later
import os,subprocess,tempfile,unittest
from pathlib import Path
class ReferenceHelpers(unittest.TestCase):
    def test_c_comparison_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as d:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                exe=Path(d)/('1534.exe' if os.name=='nt' else '1534')
                subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',*flags,
                    str(Path(__file__).with_name('test_z8_1534_reference.c')),'-o',str(exe)],check=True,capture_output=True)
                r=subprocess.run([str(exe)],check=True,capture_output=True,text=True)
                self.assertIn('1534 reference contracts passed',r.stdout)
if __name__=='__main__':unittest.main()
