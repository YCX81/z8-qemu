# SPDX-License-Identifier: GPL-2.0-or-later
"""Own a derived rotation-policy experiment, keeping its original MAIN input.

This loader uses the existing boot/MCU services. It neither flashes a camera
nor makes the patched image pass an original-image identity check.
"""
import hashlib

from z8_orientation_acceptance import OrientationGuest
from z8_orientation_unlock import write_experiment,PATCH_ADDRESS,PATCH_BYTES


class UnlockedGuest(OrientationGuest):
    def __init__(self,*args,unlock_menu=False,unlock_video=False,menu_layout=False,video_layout=False,video_portrait=False,video_file_rotation=False,**kwargs):
        if (type(unlock_menu) is not bool or type(unlock_video) is not bool or
                not (unlock_menu or unlock_video)):
            raise ValueError('Explicit Boolean rotation unlock scope required')
        if type(menu_layout) is not bool or menu_layout and not unlock_menu:
            raise ValueError('Menu layout requires explicit menu unlock')
        if type(video_layout) is not bool or video_layout and (not unlock_video or unlock_menu or menu_layout):
            raise ValueError('Video layout requires video-only scope and original MENU behavior')
        if type(video_portrait) is not bool or video_portrait and (not unlock_video or unlock_menu or menu_layout or video_layout):
            raise ValueError('Video portrait requires video-only scope without uniform fit')
        if type(video_file_rotation) is not bool or video_file_rotation and (not unlock_video or unlock_menu):
            raise ValueError('File orientation requires explicit video-only scope')
        self.unlock_menu=unlock_menu;self.unlock_video=unlock_video
        self.menu_layout=menu_layout
        self.video_layout=video_layout
        self.video_portrait=video_portrait
        self.video_file_rotation=video_file_rotation
        super().__init__(*args,**kwargs)

    def __enter__(self):
        try:
            directory=self.out/'rotation-image'
            metadata=write_experiment(self.firmware.read_bytes(),directory,
                                      menu=self.unlock_menu,video=self.unlock_video,menu_layout=self.menu_layout,
                                      video_layout=self.video_layout,video_portrait=self.video_portrait,
                                      video_file_rotation=self.video_file_rotation)
            derived=directory/'derived-main.bin'
            index=self.command.index('-kernel')+1
            if self.command[index]!=str(self.firmware):
                raise ValueError('Another experiment already replaced the kernel')
            self.command[index]=str(derived)
            self.data['rotation_unlock']=metadata
            self.data['executed_kernel_sha256']=metadata['image_sha256']
            self.data['classification']='derived_Z8_rotation_policy_with_synthetic_peripherals'
            self.data['program_modified_in_simulation']=True
        except BaseException as exc:
            self.__exit__(type(exc),exc,None)
            raise
        super().__enter__()
        try:
            actual=self.read_bytes(PATCH_ADDRESS,PATCH_BYTES)
            if hashlib.sha256(actual).hexdigest()!=metadata['replacement_sha256']:
                raise RuntimeError('Loaded rotation policy differs from the guarded image')
            self.data['rotation_unlock']['loaded_leaf_verified']=True
            for row in metadata.get('patch_sites',[]):
                actual=self.read_bytes(row['address'],row['bytes'])
                if hashlib.sha256(actual).hexdigest()!=row['replacement_sha256']:
                    raise RuntimeError('Loaded rotation/layout site differs from the guarded image')
            self.data['rotation_unlock']['loaded_patch_sites_verified']=True
        except BaseException as exc:
            self.__exit__(type(exc),exc,None)
            raise
        return self
