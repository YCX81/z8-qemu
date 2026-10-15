# SPDX-License-Identifier: GPL-2.0-or-later
import subprocess
from pathlib import Path
import tempfile
import unittest
from z8_still_hevc_fixture import RESET, INIT, CONFIG


class StillHEVCHelpers(unittest.TestCase):
    def test_c_contracts_normal_and_ndebug(self):
        with tempfile.TemporaryDirectory() as folder:
            for flags in (['-O0'], ['-O2', '-DNDEBUG']):
                binary = Path(folder) / 'still-hevc-helpers'
                subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', *flags,
                    str(Path(__file__).with_name('test_z8_still_hevc.c')), '-o', str(binary)],
                    check=True, capture_output=True)
                for geometry in (0x15802040, 0x00d40140, 0x04380654):
                    config = dict(CONFIG)
                    if geometry != 0x15802040:
                        config.update({0x310:geometry,0x318:0x23c if geometry==0x00d40140 else 0x278,
                            0xf10:448 if geometry==0x00d40140 else 2176,0xf20:0,0x200:1,
                            0x80:0x5b,0x128:0x1020001,0xfe8:0x20000770,0xf50:8192,0xf54:0})
                        for off in (0xe48,0xe50,0xe58,0xe98,0xea0,0xea8):config.pop(off)
                    fixture=Path(folder)/'request.txt'
                    fixture.write_text(''.join(f'{off:x} {value:x}\n' for off,value in [*RESET,*INIT,*config.items()]))
                    result = subprocess.run([str(binary),str(fixture)], check=True, capture_output=True, text=True)
                    self.assertIn('Still HEVC protocol contracts passed', result.stdout)
