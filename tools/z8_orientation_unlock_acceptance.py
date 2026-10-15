# SPDX-License-Identifier: GPL-2.0-or-later
"""Derived-image MENU portrait acceptance using original MCU navigation.

No native function injection, host pixel rotation, or physical camera access.
The video policy bit has a separate instruction test; this run verifies MENU.
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
from z8_properties import load_model_profile
from z8_run import menu_profile,state


def run(args):
    properties=load_model_profile()['properties']+['z8-secondary-resample-model=on']
    with UnlockedGuest(args.firmware,args.qemu,args.raw,args.output,properties=properties,
                       unlock_menu=True,unlock_video=args.unlock_video) as g:
        boot(g,lambda message:print(message.replace('original Z8','derived Z8 rotation'),flush=True))
        g.data['z8_boot']['classification']='derived_rotation_policy_boot_with_synthetic_MCU'
        s=Session(g)
        report=dict(classification='derived_rotation_MENU_acceptance',completed=False,
                    captures=[],keys=[],host_pixel_rotation=False,firmware_patches=True,
                    visual_review_required=True,physical_verified=False,
                    video_ui_verified=False,full_portrait_reflow=False)
        g.data['orientation_unlock_acceptance']=report
        def virtual():return state(g,'runtime-clock-state')['virtual_ns']/1e9
        def until(predicate,timeout=90):
            deadline=time.monotonic()+timeout
            while not predicate():
                if time.monotonic()>=deadline:raise TimeoutError('Native MENU/orientation condition')
                s.run(.25)
        def dwell(seconds):
            target=virtual()+seconds;until(lambda:virtual()>=target)
        def send(body,seconds):
            start=len(s.peer.record['rx']);s.peer.enqueue_external(body,2)
            def delivered():
                rows=[r for r in s.peer.record['rx'][start:] if r.get('family')==2 and
                      r.get('body_hex')==body.hex() and r.get('complete')]
                if len(rows)>1:raise RuntimeError('Nonunique MCU input retirement')
                return len(rows)==1
            until(delivered);dwell(seconds)
            report['keys'].append(dict(body_hex=body.hex(),retired=True,virtual_seconds=virtual()))
        def press(key):
            send(menu_key_body(key),.4 if key=='menu' else .16);send(menu_key_body(),.5)
        def ready():
            lcd=state(g,'engine1548-scanout-state')
            return bool(lcd.get('active') and lcd.get('frames',0)>=8)
        def frame(name,record=True):
            path=g.out/(name+'.ppm');g.qmp.command('screendump',filename=str(path))
            with Image.open(path) as image:
                rgb=image.convert('RGB');digest=hashlib.sha256(rgb.tobytes()).hexdigest()
                if record:image.save(g.out/(name+'.png'))
            if record:
                report['captures'].append(dict(name=name,rgb_sha256=digest,size=list(rgb.size),
                    orientation=read_orientation(g),mask=g.rd(0xa03228308),
                    ui_direction=g.rd(0xa03228244),scanout=state(g,'engine1548-scanout-state')))
                print('CAPTURE '+name,flush=True)
            return rgb,digest
        def pose(name):
            set_pose(g,name);target=POSES[name]
            def consumed():
                row=read_orientation(g)
                return row['native_raw_xyz']==target['xyz'] and row['classification']==target['classification']
            until(consumed);dwell(1)
            g.check('original driver consumed '+name,consumed())
            g.check('MENU direction '+name,g.rd(0xa03228244)==target['classification'])

        try:
            until(ready);pose('landscape');send(key_body(),2)
            expected=menu_profile(g)['menu_rgb_sha256']
            for attempt in range(2):
                press('menu');deadline=virtual()+10
                while virtual()<deadline:
                    if frame('menu-probe',False)[1]==expected:break
                    s.run(.25)
                if frame('menu-probe',False)[1]==expected:break
            else:raise RuntimeError('Original landscape MENU baseline not reached')
            frame('00-main-landscape');press('right');press('right');frame('01-manage-landscape')
            for number,name in enumerate(('right','left'),2):
                pose(name);before,digest=frame(f'{number}-manage-{name}-rename')
                g.check('selected MENU inhibit bit suppressed '+name,g.rd(0xa03228308)&0x20==0)
                press('down');after,changed=frame(f'{number}-manage-{name}-copy')
                g.check('native focus changes pixels '+name,changed!=digest)
                # These fixed LCD strips contain the native heading, outside
                # the Rename/Copy rows. Loss here caught the SRC_OVER defect.
                box=(0,0,165,before.height) if name=='right' else (859,0,before.width,before.height)
                g.check('heading retained across focus change '+name,
                        before.crop(box).tobytes()==after.crop(box).tobytes())
                press('up');_,restored=frame(f'{number}-manage-{name}-restored')
                g.check('native focus restores same page '+name,restored==digest)
            press('left');frame('04-bank-back')
            press('right');frame('05-manage-reentered')
            send(key_body(half=True),.5);send(key_body(),.5);dwell(2);frame('06-photo-left-return')
            pose('landscape');frame('07-photo-landscape-return')
            report['completed']=True
        finally:
            (g.out/'orientation-unlock.json').write_text(json.dumps(report,indent=2)+'\n')
            s.peer.close()
    return g.data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('firmware','qemu','raw','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--unlock-video',action='store_true',help='Also suppress the identified video i-menu policy bit')
    run(parser.parse_args())


if __name__=='__main__':main()
