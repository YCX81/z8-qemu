# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned Video file type portrait acceptance through original MCU keys.

Screenshots are unchanged QEMU pixels. No native-call injection or setting
confirmation. All six rows, both portrait poses, chrome and return are checked.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

from PIL import Image
from boot import key_body
from z8_boot import boot
from z8_c10_acceptance import Session
from z8_c10_gui import menu_key_body
from z8_orientation import POSES,read_orientation,set_pose
from z8_orientation_unlock_run import UnlockedGuest
from z8_orientation_acceptance import OrientationGuest
from z8_resources import MAIN_SHA
from z8_properties import load_model_profile
from z8_run import menu_profile,state


# Static native chrome outside the scrolling option list. Bounds are in fixed
# 1024x682 LCD pixels, not in an edited or host-rotated screenshot.
CHROME={
    'right':((286,2,336,612),(286,616,738,680),(690,2,738,106)),
    'left':((688,72,738,680),(286,2,738,68),(286,576,334,680)),
}


def coverage(image,box):
    data=image.crop(box).tobytes()
    return sum(max(data[i:i+3])>=32 for i in range(0,len(data),3))/(len(data)//3)


def load_reference(directory):
    directory=Path(directory)
    data=json.loads((directory/'result.json').read_text())
    report=data['video_orientation_acceptance']
    if (data.get('firmware_sha256')!=MAIN_SHA or not data.get('input_unchanged') or
        data.get('program_modified_in_simulation') or data.get('cpu_register_redirection') or
        not report.get('completed') or report.get('firmware_patches') or
        any(not item['passed'] for item in data['checks'])):
        raise ValueError('Requires completed unmodified pinned MAIN landscape reference')
    captures={item['name']:item for item in report['captures']};reference=[]
    for index in range(6):
        name='04-filetype-landscape-top' if index==0 else f'landscape-row-{index}'
        with Image.open(directory/(name+'.png')) as image:rgb=image.convert('RGB')
        digest=hashlib.sha256(rgb.tobytes()).hexdigest()
        if rgb.size!=(1024,682) or digest!=captures[name]['rgb_sha256']:
            raise ValueError('Landscape reference pixel identity differs')
        reference.append(dict(name=name,rgb_sha256=digest,solid_fraction=coverage(rgb,(108,82,916,602))))
    return dict(directory=str(directory),qemu_sha256=data['qemu_sha256'],captures=reference)


def run(args,*,baseline=False,reference=None):
    properties=load_model_profile()['properties']+['z8-secondary-resample-model=on']
    owner=OrientationGuest if baseline else UnlockedGuest
    options={} if baseline else dict(unlock_menu=True,unlock_video=True,menu_layout=True)
    with owner(args.firmware,args.qemu,args.raw,args.output,properties=properties,**options) as g:
        boot(g,lambda message:print(message if baseline else message.replace('original Z8','derived Z8 rotation'),flush=True))
        if not baseline:g.data['z8_boot']['classification']='derived_menu_layout_boot_with_synthetic_MCU'
        session=Session(g)
        report=dict(classification='native_video_file_type_portrait_UI',completed=False,captures=[],keys=[],redraw_comparisons=[],
            host_pixel_rotation=False,cpu_register_redirection=False,firmware_patches=not baseline,
            presentation='native uniform fit of 720x480 menu; original scrolling retained',
            settings_confirmed=False,visual_review_required=True,physical_verified=False,
            video_liveview_or_recording_verified=False,touch_verified=False)
        g.data['video_orientation_acceptance']=report
        if reference:report['original_landscape_reference']=reference
        def clock():return state(g,'runtime-clock-state')['virtual_ns']/1e9
        def until(predicate,timeout=90):
            end=time.monotonic()+timeout
            while not predicate():
                if time.monotonic()>end:raise TimeoutError('Native video MENU condition')
                session.run(.25)
        def dwell(seconds):
            end=clock()+seconds;until(lambda:clock()>=end)
        def send(body,seconds):
            start=len(session.peer.record['rx']);session.peer.enqueue_external(body,2)
            def delivered():
                rows=[row for row in session.peer.record['rx'][start:] if row.get('family')==2 and
                      row.get('body_hex')==body.hex() and row.get('complete')]
                if len(rows)>1:raise RuntimeError('Nonunique MCU input retirement')
                return len(rows)==1
            until(delivered);dwell(seconds)
            report['keys'].append(dict(body_hex=body.hex(),retired=True,virtual_seconds=clock()))
        def press(key):
            send(menu_key_body(key),.4 if key=='menu' else .16);send(menu_key_body(),.5)
        def frame(name,record=True):
            path=g.out/(name+'.ppm');g.qmp.command('screendump',filename=str(path))
            with Image.open(path) as image:
                rgb=image.convert('RGB');digest=hashlib.sha256(rgb.tobytes()).hexdigest()
                if record:image.save(g.out/(name+'.png'))
            if record:
                report['captures'].append(dict(name=name,rgb_sha256=digest,size=list(rgb.size),
                    orientation=read_orientation(g),mask=g.rd(0xa03228308),ui_direction=g.rd(0xa03228244),
                    scanout=state(g,'engine1548-scanout-state')))
                (g.out/'video-orientation.json').write_text(json.dumps(report,indent=2)+'\n')
                print('CAPTURE '+name,flush=True)
            return rgb,digest
        def pose(name):
            set_pose(g,name);target=POSES[name]
            def consumed():
                row=read_orientation(g)
                return row['native_raw_xyz']==target['xyz'] and row['classification']==target['classification']
            until(consumed);dwell(1)
            g.check('native input and UI direction '+name,consumed() and g.rd(0xa03228244)==target['classification'])
        def ready():
            lcd=state(g,'engine1548-scanout-state')
            return lcd.get('active') and lcd.get('frames',0)>=8
        def check_page(image,reference,pose_name,label):
            g.check('fixed LCD size '+label,image.size==(1024,682))
            bbox=image.getbbox()
            g.check('native menu inside portrait viewport '+label,bbox is not None and 282<=bbox[0]<bbox[2]<=742)
            for index,box in enumerate(CHROME[pose_name]):
                g.check('static title/sidebar/footer retained '+label+':'+str(index),
                        image.crop(box).tobytes()==reference.crop(box).tobytes())
        def check_content(image,index,label):
            # Same MCU trajectory in the ORIGINAL landscape UI, not a pose
            # round trip (which legitimately recenters the focused row).
            # These corresponding list/description regions differ in scale.
            # Normalize solid coverage; 2 percentage points allow raster
            # rounding. Missing a row/description produces a much larger loss.
            # This detects missing regions, not OCR/text correctness.
            actual=coverage(image,(338,72,686,610))
            expected=reference['captures'][index]['solid_fraction']
            report['redraw_comparisons'].append(dict(name=label,incremental_solid_fraction=actual,
                original_landscape_solid_fraction=expected,absolute_difference=abs(actual-expected)))
            g.check('content coverage matches original landscape '+label,abs(actual-expected)<=.02)
        try:
            until(ready);pose('landscape');send(key_body(),2)
            expected=menu_profile(g)['menu_rgb_sha256']
            for attempt in range(2):
                press('menu');end=clock()+10
                while clock()<end:
                    if frame('menu-probe',False)[1]==expected:break
                    session.run(.25)
                if frame('menu-probe',False)[1]==expected:break
            else:raise RuntimeError('Known landscape MENU baseline absent')
            frame('00-photo-menu-landscape')
            for key in ('left','down','right'):press(key)
            frame('01-video-menu-landscape')
            for _ in range(5):press('down')
            frame('02-video-filetype-focused');press('right')
            frame('03-filetype-landscape-selected')
            # The pinned cold selection is H.265 8-bit, row four. No OK is
            # sent; focus traversal and LEFT cancellation preserve selection.
            for _ in range(4):press('up')
            frame('04-filetype-landscape-top')
            if baseline:
                for index in range(1,6):
                    press('down');frame(f'landscape-row-{index}')
                for _ in range(5):press('up')
                frame('landscape-top-restored')
                report.update(classification='original_video_file_type_landscape_baseline',completed=True)
                return g.data
            for pose_name in ('right','left'):
                pose(pose_name);base,digest=frame(pose_name+'-row-0')
                g.check('MENU layout marker retained '+pose_name,g.rd(0xa03228308)&0x20!=0)
                check_page(base,base,pose_name,pose_name+'-top')
                check_content(base,0,pose_name+'-0')
                g.check('native menu cache participates in scanout '+pose_name,
                        state(g,'engine1548-scanout-state')['background_format']=='NV16')
                previous=digest
                for index in range(1,6):
                    press('down');image,current=frame(f'{pose_name}-row-{index}')
                    g.check('native focus moves '+pose_name+':'+str(index),current!=previous)
                    check_page(image,base,pose_name,pose_name+'-'+str(index));previous=current
                    check_content(image,index,f'{pose_name}-{index}')
                for _ in range(5):press('up')
                restored,restored_hash=frame(pose_name+'-top-restored')
                check_page(restored,base,pose_name,pose_name+'-restored')
                g.check('same top page restored '+pose_name,restored_hash==digest)
                press('left');frame(pose_name+'-parent-return')
                press('right');frame(pose_name+'-selected-reentered')
                for _ in range(4):press('up')
                _,reentered=frame(pose_name+'-top-reentered')
                g.check('same page after return and reenter '+pose_name,reentered==digest)
            send(key_body(half=True),.5);send(key_body(),.5);dwell(2)
            frame('photo-left-return');pose('landscape');frame('photo-landscape-return')
            report['completed']=True
        finally:
            (g.out/'video-orientation.json').write_text(json.dumps(report,indent=2)+'\n')
            session.peer.close()
    return g.data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('firmware','qemu','raw','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--landscape-reference',type=Path,help='Reuse a completed original landscape reference; otherwise create one')
    args=parser.parse_args()
    directory=args.landscape_reference
    if directory is None:
        directory=args.output.with_name(args.output.name+'-landscape-reference')
        baseline_args=argparse.Namespace(**{**vars(args),'output':directory})
        run(baseline_args,baseline=True)
    run(args,reference=load_reference(directory))


if __name__=='__main__':main()
