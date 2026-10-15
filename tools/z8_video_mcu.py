# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-bound replay of Z8's declared boot family4/type3 status.

Based on WonderLantern ZR shared146's status peer. Z8 C3.11 retains stale
bytes beyond the declared eight-byte TX payload; they are checksummed and
recorded, never interpreted as request arguments. No voltage, charge,
image-completion or task-state meaning is invented for the supplied input.
"""
import hashlib
from pathlib import Path
import struct

from mcu_frames import frames
from z8_resources import MAIN_SHA


def status68_input(previous_body, value):
    """Set only C3.11's externally decoded status enum, not a guest flag.

    0x4101c554..0x4101c5a4 maps body[0x68] values 0..2 to
    event 0x03040002 payload+0x24. Its physical meaning is unverified.
    The caller must deliver the result over the original family-1 RX path.
    """
    if (not isinstance(previous_body, bytes) or len(previous_body) != 128
            or previous_body[0] != 3 or type(value) is not int
            or value not in (0, 1, 2)):
        raise ValueError('C3.11 type-3 status body and enum 0..2 required')
    body = bytearray(previous_body)
    body[0x68] = value
    return bytes(body)


def reply_for(wire,status):
    if not isinstance(wire,bytes) or len(wire)!=256 or sum(wire)&255!=255:
        return None
    if wire[:8]!=bytes((4,2,8,0,0,0,0,0)) or wire[252:255]!=bytes((1,1,0)):
        return None
    if wire[8:16]!=struct.pack('<II',2,1):
        return None
    if status!=bytes([3])+bytes(111):
        raise ValueError('Only the already-declared Z8 boot status may be repeated')
    return status,frames(status,family=4)[0]


def enqueue_status68(peer, previous_body, value):
    """Explicit Z8-only external input, serialized by the normal MCU peer."""
    if peer.g.data.get('firmware_sha256') != MAIN_SHA:
        raise ValueError('Z8 C3.11 status bindings required')
    body = status68_input(previous_body, value)
    wire = frames(body, family=1)[0]
    peer.record.setdefault('z8_status68_inputs', []).append(dict(
        body_hex=body.hex(), prior_sha256=hashlib.sha256(previous_body).hexdigest(),
        wire_sha256=hashlib.sha256(wire).hexdigest(), value=value,
        classification='explicit_synthetic_MCU_status_enum',
        physical_semantics_verified=False, firmware_patch=False))
    peer.pending.append((1, body, wire))
    peer.start_rx()
    return body


def attach(peer):
    if peer.g.data.get('firmware_sha256')!=MAIN_SHA:
        raise ValueError('Z8 C3.11 status bindings required')
    if peer.record.get('z8_status_query_peer'):
        raise ValueError('Status peer already attached')
    status=bytes.fromhex(peer.g.data['z8_boot']['battery_status_input_body_hex'])
    if status!=bytes([3])+bytes(111):
        raise ValueError('The boot status differs from the verified Z8 input')
    original=peer.queue_reply
    peer.record['z8_status_query_peer']=dict(
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        status_sha256=hashlib.sha256(status).hexdigest(),physical_battery_verified=False,
        image_completion=False,firmware_patch=False,replies=[])

    def queued(wire):
        value=reply_for(wire,status)
        if value is None:
            return original(wire)
        body,packet=value
        peer.record['z8_status_query_peer']['replies'].append(dict(
            tx_index=peer.tx['row']['index'],request_sha256=hashlib.sha256(wire).hexdigest(),
            uninterpreted_padding_sha256=hashlib.sha256(wire[16:252]).hexdigest(),
            reply_sha256=hashlib.sha256(packet).hexdigest()))
        peer.pending.append((4,body,packet))

    peer.queue_reply=queued
