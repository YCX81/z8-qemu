# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows or interoperability-enabled WSL FFprobe check of P8 QTest output."""
import argparse,json,subprocess,os
from pathlib import Path
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--ffprobe',type=Path,required=True)
    p.add_argument('--output',type=Path,help='Fresh JSON receipt; defaults to ROOT/vui-streams.json; never overwrites')
    a=p.parse_args();output=a.output or a.root/'vui-streams.json'
    if output.exists():raise FileExistsError('Use a fresh --output; existing VUI evidence is read-only')
    files=sorted((a.root/'out-backend/encode').glob('tile*.h265'))
    if len(files)!=22:raise ValueError('Missing complete main-image QTest result')
    files += [a.root/'out-aux/preview/aux2.h265',a.root/'out-aux/small/aux1.h265'];rows=[]
    for f in files:
        path=str(f.resolve())
        if os.name!='nt' and a.ffprobe.suffix=='.exe':path=subprocess.check_output(['wslpath','-w',path],text=True,timeout=10).strip()
        result=subprocess.run([str(a.ffprobe),'-v','error','-f','hevc','-i',path,'-show_streams','-of','json'],check=True,capture_output=True,text=True,timeout=30)
        stream=json.loads(result.stdout)['streams'][0]
        if [stream.get(k) for k in ('pix_fmt','color_range','color_primaries','color_transfer','color_space')]!=[
            'yuv420p10le','pc','bt2020','arib-std-b67','bt2020nc']:raise ValueError(stream)
        rows.append(dict(file=str(f),stream=stream))
    with output.open('x') as f:f.write(json.dumps(dict(passed=True,streams=rows),indent=2)+'\n')
    print('Actual full-range HLG SPS:',len(rows),'streams passed')
if __name__=='__main__':main()
