# SPDX-License-Identifier: GPL-2.0-or-later
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

@unittest.skipIf(os.name=='nt','Requires installed Linux x265/GLib; never substitutes a mock ABI')
class CodecHelpers(unittest.TestCase):
    def test_real_10bit_encoder_normal_and_ndebug(self):
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','x265','glib-2.0'],text=True))
        with tempfile.TemporaryDirectory() as folder:
            for optimize in (['-O0'],['-O2','-DNDEBUG']):
                binary=Path(folder)/'codec'
                subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*optimize,
                    str(Path(__file__).with_name('test_z8_still_hevc_codec.c')),*flags,'-o',str(binary)],check=True,capture_output=True)
                result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=60)
                self.assertIn('Real x265 still codec contracts passed',result.stdout)
