# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity-bound rotation policy and scoped layout for a private QEMU image.

The caller explicitly chooses menu/video scope. This is not an update package
or a physical-camera installer. The original input is never written.
Video layout is an experimental uniform fit, not native portrait reflow;
it explicitly rejects menu unlocking and still requires runtime acceptance.
"""
import hashlib
import json
from pathlib import Path

from z8_resources import MAIN_SHA

MAIN_BYTES=81_807_709
IMAGE_BASE=0x40160000
PATCH_ADDRESS=0x4038e3e4
PATCH_BYTES=64
LEAF_SHA256='e086e5cd73433ff28d451d185f93909c5af0777fcfecded1dc0422c93c0d7b8c'


def build_plan(source,*,menu=False,video=False,menu_layout=False,video_layout=False,video_portrait=False,video_file_rotation=False):
    if type(menu) is not bool or type(video) is not bool or not (menu or video):
        raise ValueError('Explicit Boolean menu/video scope required')
    if type(menu_layout) is not bool or menu_layout and not menu:
        raise ValueError('Menu layout requires explicit menu unlock')
    if type(video_layout) is not bool or video_layout and (not video or menu or menu_layout):
        raise ValueError('Video layout requires video-only scope and original MENU behavior')
    if type(video_portrait) is not bool or video_portrait and (not video or menu or menu_layout or video_layout):
        raise ValueError('Video portrait requires video-only scope without uniform fit')
    if type(video_file_rotation) is not bool or video_file_rotation and (not video or menu):
        raise ValueError('File orientation requires explicit video-only scope')
    layout=menu_layout or video_layout
    marker=1 if video_layout else 0x20
    preserved_mask=f'0x{~marker&0xffffffff:08x}'
    if not isinstance(source,(bytes,bytearray)) or len(source)!=MAIN_BYTES:
        raise ValueError('Requires pinned original Z8 C3.11 MAIN')
    source=bytes(source)
    if hashlib.sha256(source).hexdigest()!=MAIN_SHA:
        raise ValueError('Wrong or already modified MAIN identity')
    offset=PATCH_ADDRESS-IMAGE_BASE
    if hashlib.sha256(source[offset:offset+PATCH_BYTES]).hexdigest()!=LEAF_SHA256:
        raise ValueError('Original rotation-mask leaf differs from reviewed code')
    from keystone import Ks,KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN
    # Same ABI as the original leaf: W0 mask and W1 enable remain unchanged;
    # X8/X9 are scratch. No stack, callbacks, allocator, or new mapping.
    filters=[]
    if menu and not menu_layout:filters.append('and w9,w9,#0xffffffdf')
    if video and not video_layout:filters.append('and w9,w9,#0xfffffffe')
    assembly=';'.join([
        'movz x8,#0x8308','movk x8,#0x322,lsl #16','movk x8,#0xa,lsl #32',
        'ldr w9,[x8]','tbz w1,#0,clear','orr w9,w9,w0','b filter',
        'clear: bic w9,w9,w0','filter:',*filters,'str w9,[x8]','ret'])
    replacement=bytes(Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm(assembly,addr=PATCH_ADDRESS)[0])
    if not replacement or len(replacement)>PATCH_BYTES or len(replacement)%4:
        raise ValueError('Replacement exceeds the original aligned leaf extent')
    replacement+=bytes.fromhex('1f2003d5')*((PATCH_BYTES-len(replacement))//4)
    image=bytearray(source);image[offset:offset+PATCH_BYTES]=replacement
    sites=[dict(address=PATCH_ADDRESS,bytes=PATCH_BYTES,original_sha256=LEAF_SHA256,
                replacement_sha256=hashlib.sha256(replacement).hexdigest())]
    if layout:
        # Retain the selected bit for logical layout. Scanout direction
        # and its change notification ignore only this bit; other locks and
        # Auto rotate info display retain their original behavior.
        for address in (0x4038d554,0x4038d57c,0x4038d62c):
            at=address-IMAGE_BASE;old=source[at:at+4]
            expected=bytes(Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm('cmp w8,#0',addr=address)[0])
            if old!=expected:raise ValueError('Original direction mask test differs')
            code=bytes(Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm(f'tst w8,#{preserved_mask}',addr=address)[0])
            image[at:at+4]=code
            sites.append(dict(address=address,bytes=4,original_sha256=hashlib.sha256(old).hexdigest(),
                              replacement_sha256=hashlib.sha256(code).hexdigest()))
        def emit(address,assembly,capacity):
            code=bytes(Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm(assembly,addr=address)[0])
            if len(code)>capacity or len(code)%4:raise ValueError('Menu helper exceeds original function extent')
            code+=bytes.fromhex('1f2003d5')*((capacity-len(code))//4)
            image[address-IMAGE_BASE:address-IMAGE_BASE+capacity]=code
        # Compact the same effective-direction getter, retaining its setting
        # call and 16-byte stack frame. The literal and helper fit inside the
        # two original function extents; no new MMU mapping or guest RAM.
        emit(0x4038d59c,';'.join([
            'str x30,[sp,#-16]!','mov w0,#0x1dcf','movk w0,#0x200,lsl #16',
            'bl #0x403ec398','ldr x8,#0x4038d5cc',
            'ldr w9,[x8,#0xc4]',f'tst w9,#{preserved_mask}',
            'ldr w8,[x8]','ccmp w0,#0,#4,eq','csel w0,wzr,w8,eq',
            'ldr x30,[sp],#16','ret']),48)
        image[0x4038d5cc-IMAGE_BASE:0x4038d5d4-IMAGE_BASE]=(0xa03228244).to_bytes(8,'little')
        filter_video='and w9,w9,#0xfffffffe;' if video and not video_layout else ''
        emit(PATCH_ADDRESS,'ldr x8,#0x4038d5cc;ldr w9,[x8,#0xc4];tbz w1,#0,clear;orr w9,w9,w0;b filtered;clear:bic w9,w9,w0;filtered:'+filter_video+'str w9,[x8,#0xc4];ret',36)
        # Shared logical-size getter serves image layers AND transparent
        # clearing. Only the selected scope plus exact 480x720 is changed; texture
        # dimensions, target resolutions and other sizes remain native.
        # TBNZ targets the nearby tail (its range is only +/-32 KiB); a full
        # B returns to the getter. The displaced load is replayed first.
        emit(0x4038d5d4,f'ldrh w8,[x8];ldr x17,#0x4038d5cc;ldr w17,[x17,#0xc4];tbnz w17,#{0 if video_layout else 5},#0x4038e408;b #0x40340be4',20)
        emit(0x4038e408,'cmp w8,#480;mov w17,#720;ccmp w9,w17,#0,eq;b.ne #0x40340be4;mov w8,#720;mov w9,#480;b #0x40340be4',28)
        at=0x40340be0
        expected=bytes(Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN).asm('ldrh w8,[x8]',addr=at)[0])
        if source[at-IMAGE_BASE:at-IMAGE_BASE+4]!=expected:raise ValueError('Original logical-size getter differs')
        emit(at,'b #0x4038d5d4',4)
        for address,size in ((0x4038d59c,76),(at,4)):
            sites.append(dict(address=address,bytes=size,
                original_sha256=hashlib.sha256(source[address-IMAGE_BASE:address-IMAGE_BASE+size]).hexdigest()))
        # Recompute hashes after filling the helper tail in the policy leaf.
        replacement=bytes(image[offset:offset+PATCH_BYTES])
        for row in sites:
            start=row['address']-IMAGE_BASE
            row['replacement_sha256']=hashlib.sha256(image[start:start+row['bytes']]).hexdigest()
    coordinate_edits=[];coordinate_omitted=[]
    if video_portrait:
        from z8_video_layout import patch_coordinates
        coordinate_edits,coordinate_sites,coordinate_omitted=patch_coordinates(source,image)
        sites.extend(coordinate_sites)
    if video_file_rotation:
        from z8_video_file_rotation import patch_matrix_lifetime
        sites.extend(patch_matrix_lifetime(source,image))
    mask=(0x20 if menu and not menu_layout else 0)|(1 if video and not video_layout else 0)
    return dict(image=bytes(image),replacement=replacement,source_sha256=MAIN_SHA,
                image_sha256=hashlib.sha256(image).hexdigest(),patch_address=PATCH_ADDRESS,
                patch_bytes=PATCH_BYTES,original_leaf_sha256=LEAF_SHA256,
                replacement_sha256=hashlib.sha256(replacement).hexdigest(),
                menu=menu,video=video,suppressed_mask=mask,video_portrait=video_portrait,
                video_file_rotation=video_file_rotation,
                portrait_coordinate_edits=coordinate_edits,portrait_coordinate_omitted=coordinate_omitted,
                menu_marker_retained=menu_layout,video_marker_retained=video_layout,
                effective_direction_ignored_mask=marker if layout else 0,
                patch_sites=sites,
                preserved='Auto rotate info display setting and all nonselected inhibit bits',
                classification='private_QEMU_policy_experiment',physical_camera_verified=False,
                new_executable_mapping=False,stack_used=layout,additional_stack_bytes=0)


def write_experiment(source,output,*,menu=False,video=False,menu_layout=False,video_layout=False,video_portrait=False,video_file_rotation=False):
    plan=build_plan(source,menu=menu,video=video,menu_layout=menu_layout,video_layout=video_layout,video_portrait=video_portrait,video_file_rotation=video_file_rotation)
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    with (output/'derived-main.bin').open('xb') as stream:stream.write(plan['image'])
    metadata={k:v for k,v in plan.items() if k not in ('image','replacement')}
    with (output/'manifest.json').open('x',encoding='utf-8') as stream:
        json.dump(metadata,stream,indent=2);stream.write('\n')
    return metadata
