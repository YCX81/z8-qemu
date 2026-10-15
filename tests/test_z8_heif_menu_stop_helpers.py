# SPDX-License-Identifier: GPL-2.0-or-later
import os,subprocess,tempfile,unittest
from pathlib import Path

class MenuStopHelpers(unittest.TestCase):
    def test_c_contracts(self):
        with tempfile.TemporaryDirectory() as d:
            for flags in (['-O0'],['-O2','-DNDEBUG']):
                exe=Path(d)/('menu-stop.exe' if os.name=='nt' else 'menu-stop')
                subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',*flags,
                    str(Path(__file__).with_name('test_z8_heif_menu_stop.c')),'-o',str(exe)],check=True,capture_output=True)
                r=subprocess.run([str(exe)],check=True,capture_output=True,text=True)
                self.assertIn('HEIF menu stop contracts passed',r.stdout)
if __name__=='__main__':unittest.main()
