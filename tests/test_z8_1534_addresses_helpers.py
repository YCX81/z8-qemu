# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class NativeAddressHelpers(unittest.TestCase):
 def test_native_fixture_normal_and_ndebug(self):
  f=ROOT/'tests/fixtures/z8-1534-p22-native.bin'
  if not f.is_file():
   self.skipTest('Private native MMIO fixture is not distributed with source-only checkouts')
  receipt=json.loads(f.with_suffix('.json').read_text())
  self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(),receipt['sha256'])
  with tempfile.TemporaryDirectory(prefix='p22-address-') as d:
   for flags in (['-O0'],['-O2','-DNDEBUG']):
    exe=Path(d)/'test'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,'-I',str(ROOT/'qemu'),str(ROOT/'tests/test_z8_1534_addresses.c'),'-o',str(exe)],check=True)
    subprocess.run([str(exe),str(f)],check=True)
if __name__=='__main__':unittest.main()
