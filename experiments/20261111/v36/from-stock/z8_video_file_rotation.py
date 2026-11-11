# SPDX-License-Identifier: GPL-2.0-or-later
"""Private Z8 C3.11 experiment: latch a matrix in each configured mux object.

This changes two existing A64 function extents. It is not an update package;
real recording, physical pose mapping and segment lifetime are unverified.
"""
import hashlib
import struct

from z8_resources import MAIN_SHA

IMAGE_BASE=0x40160000
MAIN_BYTES=81_807_709
CONFIG_TAIL=0x40cf3b4c
VIDEO_MATRIX=0x40cef3f0
SITES=((CONFIG_TAIL,284,'874850c86312c34fcd871608aa07f4b560a73c5b02e6e5de95c2a6e247223a69'),
       (VIDEO_MATRIX,32,'1d7b0e8b03f520e37d335e400222d883ff9d4e272dffef5b8a5ef26781b9f161'))
POSE_MATRICES=tuple(struct.pack('>9i',*values) for values in (
    (65536,0,0,0,65536,0,0,0,1<<30),
    (0,-65536,0,65536,0,0,0,0,1<<30),
    (0,65536,0,-65536,0,0,0,0,1<<30)))
RATES=((24000,24,1),(25000,25,1),(30000,30,1),(50000,25,2),
       (60000,30,2),(100000,25,4),(120000,30,4))


def patch_matrix_lifetime(source,image):
    """Compose with a separately reviewed UI image, guarding both mux spans."""
    if not isinstance(source,(bytes,bytearray)) or len(source)!=MAIN_BYTES:
        raise ValueError('Requires pinned original Z8 C3.11 MAIN')
    if hashlib.sha256(source).hexdigest()!=MAIN_SHA:
        raise ValueError('Wrong or already modified MAIN identity')
    if not isinstance(image,bytearray) or len(image)!=len(source):
        raise ValueError('Target must be a same-size mutable derived image')
    for address,size,digest in SITES:
        at=address-IMAGE_BASE
        if hashlib.sha256(source[at:at+size]).hexdigest()!=digest:
            raise ValueError('Original mux span differs from reviewed code')
        if image[at:at+size]!=source[at:at+size]:
            raise ValueError('Mux patch overlaps a preceding edit')
    from keystone import Ks,KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN
    assembler=Ks(KS_ARCH_ARM64,KS_MODE_LITTLE_ENDIAN)
    # Keep the original failure/return block at 3b94/3b98. The preceding
    # validation branches still target it. Equivalent rate selection frees
    # room for the successful-config snapshot; no original fields are dropped.
    assembly=';'.join([
        'str x11,[x0,#0x508]','str w9,[x0,#0x820]','str w12,[x0,#0x824]',
        'adr x8,rate_table','mov w16,#7',
        'rate_loop: ldp w14,w15,[x8],#8','cmp w9,w14','b.eq found',
        'subs w16,w16,#1','b.ne rate_loop','b latch',
        'found: and w14,w15,#0xff','lsr w15,w15,#8',
        'str w14,[x0,#0x828]','str w15,[x0,#0x838]',
        'cbz w13,latch','ldr w14,[x0]','b aux',
        'invalid: mov w8,#3','mov w0,w8','ret',
        'aux: cmp w10,#0','mov w8,#0x15c','mov w9,#0x160',
        'csel w8,w8,w9,eq','cmp w14,#0','mov w9,#0x68',
        'mov w12,#0xc','csel w9,w9,w12,eq',
        'add x8,x11,x8','add x8,x8,x9','str x8,[x0,#0x508]',
        'latch: add x2,x0,#0x128','movi v0.16b,#0','stp q0,q0,[x2]',
        'mov w8,#0x40','str w8,[x2,#32]',
        'movz x8,#0x8244','movk x8,#0x322,lsl #16','movk x8,#0xa,lsl #32',
        'ldr w9,[x8]','sub w10,w9,#1','cmp w10,#2','b.hs identity',
        'cmp w9,#1','mov w8,#0x100','mov w10,#0xffff',
        'csel w11,w10,w8,eq','csel w8,w8,w10,eq',
        'str w11,[x2,#4]','str w8,[x2,#12]','b done',
        'identity: mov w8,#0x100','str w8,[x2]','str w8,[x2,#16]',
        'done: mov w0,#0','ret',
        'rate_table: '+ ';'.join(f'.word {rate},{base|(factor<<8)}' for rate,base,factor in RATES)])
    tail=bytes(assembler.asm(assembly,addr=CONFIG_TAIL)[0])
    # Existing prologue set X19 = context and reserved stack[20..43].
    # Retain the configured snapshot there instead of constructing identity.
    header=bytes(assembler.asm(';'.join([
        'cbz x19,#0x40cef558','add x8,x19,#0x128','ldp q0,q1,[x8]',
        'ldr w8,[x8,#32]','stp q0,q1,[sp,#0x20]',
        'str w8,[sp,#0x40]','nop','nop']),addr=VIDEO_MATRIX)[0])
    expected_failure=bytes(assembler.asm('mov w8,#3;mov w0,w8;ret',addr=0x40cf3b94)[0])
    if len(tail)!=284 or tail[72:84]!=expected_failure or len(header)!=32:
        raise ValueError('Replacement does not fit the reviewed function extents')
    result=[]
    for (address,size,digest),replacement in zip(SITES,(tail,header)):
        at=address-IMAGE_BASE
        image[at:at+size]=replacement
        result.append(dict(address=address,bytes=size,original_sha256=digest,
                           replacement_sha256=hashlib.sha256(replacement).hexdigest()))
    return result


def build_plan(source):
    image=bytearray(source)
    sites=patch_matrix_lifetime(source,image)
    return dict(image=bytes(image),source_sha256=MAIN_SHA,
                image_sha256=hashlib.sha256(image).hexdigest(),patch_sites=sites,
                classification='private_QEMU_per_mux_matrix_experiment',
                snapshot_address=0x40cf3a24,video_header_address=0x40cef3d0,
                context_matrix_offset=0x128,pose_values=[0,1,2],unknown_pose=0,
                movie_audio_timecode_modified=False,new_executable_mapping=False,
                additional_stack_bytes=0,physical_camera_verified=False,
                real_recording_verified=False,segment_lifetime_verified=False)
