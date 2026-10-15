# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned Z8 portrait-input and original MENU acceptance; no camera access.

Use a Linux output directory. All screenshots are untouched QEMU pixels.
The report separates input/driver checks from the required visual review.
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
from z8_guest import Guest
from z8_orientation import POSES,set_pose,read_orientation
from z8_properties import load_model_profile
from z8_resources import attach
from z8_run import menu_profile,state


class OrientationGuest(Guest):
    def __enter__(self):
        try:
            attach(self,native_sampling=True,battery_limits=True,orientation_sampling=True)
        except BaseException as exc:
            self.__exit__(type(exc),exc,None)
            raise
        return super().__enter__()


def run(args):
    properties=load_model_profile()['properties']+['z8-secondary-resample-model=on']
    with OrientationGuest(args.firmware,args.qemu,args.raw,args.output,properties=properties) as g:
        boot(g);s=Session(g)
        report=dict(classification='original_Z8_UI_with_synthetic_orientation',
                    captures=[],keys=[],completed=False,host_pixel_rotation=False,
                    firmware_patches=False,visual_review_required=True,physical_verified=False)
        g.data['orientation_acceptance']=report

        def virtual():return state(g,'runtime-clock-state')['virtual_ns']/1e9
        def until(predicate,timeout=90):
            deadline=time.monotonic()+timeout
            while not predicate():
                if time.monotonic()>=deadline:raise TimeoutError('Native orientation/UI condition')
                s.run(.25)
        def dwell(seconds):
            target=virtual()+seconds
            until(lambda:virtual()>=target)
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
            send(menu_key_body(key),.4 if key=='menu' else .16)
            send(menu_key_body(),.5)
        def ready():
            lcd=state(g,'engine1548-scanout-state')
            return bool(lcd.get('active') and lcd.get('frames',0)>=8)
        def frame(name,record=True):
            path=g.out/(name+'.ppm');g.qmp.command('screendump',filename=str(path))
            with Image.open(path) as im:
                rgb=im.convert('RGB');digest=hashlib.sha256(rgb.tobytes()).hexdigest()
                if record:im.save(g.out/(name+'.png'))
                size=list(im.size)
            if record:
                row=dict(name=name,rgb_sha256=digest,size=size,orientation=read_orientation(g),
                         scanout=state(g,'engine1548-scanout-state'))
                report['captures'].append(row)
                (g.out/'orientation.json').write_text(json.dumps(report,indent=2)+'\n')
            return digest
        def pose(name):
            set_pose(g,name);target=POSES[name]
            def consumed():
                item=read_orientation(g)
                return item['native_raw_xyz']==target['xyz'] and item['classification']==target['classification']
            until(consumed);dwell(1)
            g.check('original driver consumed '+name,consumed())

        until(ready);dwell(2)
        for index,name in enumerate(('landscape','left','right')):
            pose(name);frame(f'{index:02d}-photo-{name}')
        # Original UI may use the first MENU event to drain live view. Wait
        # for its established pixel baseline before sending one bounded retry.
        expected=menu_profile(g)['menu_rgb_sha256']
        send(key_body(),2)
        for attempt in range(2):
            press('menu');deadline=virtual()+10
            while virtual()<deadline:
                if frame('menu-probe',False)==expected:break
                s.run(.25)
            if frame('menu-probe',False)==expected:break
        else:raise RuntimeError('Original MENU baseline not reached')
        frame('03-menu-right-pose')
        # The accepted cold baseline focuses Shooting menu bank. Two RIGHT
        # presses reach its native Manage menu banks page (Rename/Copy).
        # Move focus and return without invoking either operation.
        press('right');press('right');frame('04-bank-page')
        press('down');frame('05-bank-focus-moved')
        press('up');frame('06-bank-focus-restored')
        press('left');frame('07-menu-back')
        send(key_body(half=True),.5);until(ready);dwell(2)
        frame('08-photo-right-return')
        pose('landscape');frame('09-photo-landscape-return')
        report['completed']=True
        (g.out/'orientation.json').write_text(json.dumps(report,indent=2)+'\n')
        s.peer.close()
    return g.data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('firmware','qemu','raw','output'):p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();run(args)


if __name__=='__main__':main()
