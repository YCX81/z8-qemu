# SPDX-License-Identifier: GPL-2.0-or-later
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_z8_hevc_vui_streams as check

class VUIWrapperTests(unittest.TestCase):
    def test_existing_receipt_rejected_before_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'vui-streams.json';old.write_text('old evidence')
            with patch.object(sys,'argv',['check','--root',str(root),'--ffprobe','unused']),patch.object(check.subprocess,'run') as run:
                with self.assertRaises(FileExistsError):check.main()
                run.assert_not_called()
            self.assertEqual(old.read_text(),'old evidence')
    def test_explicit_fresh_output_preserves_previous_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tiles=root/'out-backend/encode';tiles.mkdir(parents=True)
            for n in range(22):(tiles/f'tile{n:02}.h265').write_bytes(b'fixture')
            old=root/'vui-streams.json';old.write_text('old evidence');new=root/'new.json'
            stream=dict(pix_fmt='yuv420p10le',color_range='pc',color_primaries='bt2020',color_transfer='arib-std-b67',color_space='bt2020nc')
            with patch.object(sys,'argv',['check','--root',str(root),'--ffprobe','mock-ffprobe','--output',str(new)]), \
                 patch.object(check.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps({'streams':[stream]}))) as run,patch('builtins.print'):
                check.main()
                self.assertEqual(run.call_count,24)
            self.assertEqual(len(json.loads(new.read_text())['streams']),24)
            self.assertEqual(old.read_text(),'old evidence')
if __name__=='__main__':unittest.main()
