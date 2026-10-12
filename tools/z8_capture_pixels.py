# SPDX-License-Identifier: GPL-2.0-or-later
"""Paused, identity-bound snapshots of actual D980 / native151 RAW RAM.

QEMU 10.2.4 qapi/machine.json declares pmemsave(val:uint64, size:size,
filename:str). Only that structured physical-memory read is used. Packed
sources are preserved; normalized uint16 files have explicit conversion
receipts and are never described as literal RAM dumps or replacement NEFs.
"""
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from z8_capture_bindings import load_bindings

MAX_SAMPLES = 64 * 1024 * 1024
MAX_PACKED_BYTES = 128 * 1024 * 1024
IMAGE_BEGIN, IMAGE_END = 0x800000000, 0x900000000
D980, TAG, RAW = 'primary-d980-state', 'sensor-frame-tag-state', 'engine151-raw-output-state'


def integer(value, name, low=0, high=(1 << 64) - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}')
    return value


def geometry(width, height, stride, bits):
    width = integer(width, 'width', 1, 16384)
    height = integer(height, 'height', 1, 16384)
    if type(bits) is not int or bits not in (12, 14):
        raise ValueError('Only the explicit packed12/packed14 model conventions are supported')
    needed = (width * bits + 7) // 8
    stride = integer(stride, 'packed stride', needed, 65536)
    if width * height > MAX_SAMPLES or stride * height > MAX_PACKED_BYTES:
        raise ValueError('Producer exceeds bounded pixel snapshot size')
    return needed


def decode_packed_row(row, width, bits):
    needed = geometry(width, 1, len(row), bits)
    # Three adjacent bytes cover every 12/14-bit sample, including partial
    # final groups. Decode visible samples only; retain padding in packed.bin.
    data = np.frombuffer(bytes(row[:needed]) + bytes(2), dtype=np.uint8).astype(np.uint32)
    offsets = np.arange(width, dtype=np.uint32) * bits
    at, shift = offsets // 8, offsets % 8
    words = data[at] | (data[at + 1] << 8) | (data[at + 2] << 16)
    return ((words >> shift) & ((1 << bits) - 1)).astype('<u2').tobytes()


def unpack_file(source, target, *, width, height, stride, bits):
    needed = geometry(width, height, stride, bits)
    source, target = Path(source), Path(target)
    if not source.is_file() or source.stat().st_size != stride * height:
        raise ValueError('Packed snapshot is truncated or has trailing bytes')
    packed_sha, normalized_sha = hashlib.sha256(), hashlib.sha256()
    with source.open('rb') as input_file, target.open('xb') as output:
        for _ in range(height):
            row = input_file.read(stride)
            if len(row) != stride:
                raise ValueError('Packed source changed during row conversion')
            packed_sha.update(row)
            normalized = decode_packed_row(row, width, bits)
            output.write(normalized)
            normalized_sha.update(normalized)
        if input_file.read(1):
            raise ValueError('Packed source grew during row conversion')
    return dict(method='little_endian_contiguous_packed_bits_to_u16le_by_row',
                source_encoding=f'packed{bits}le', source_stride_bytes=stride,
                target_encoding='u16le', target_stride_samples=width,
                width=width, height=height, bit_depth=bits,
                padding_bytes_per_row=stride - needed,
                padding_policy='Preserved in original packed file; omitted from normalized visible-sample rows',
                packed_sha256=packed_sha.hexdigest(), normalized_sha256=normalized_sha.hexdigest(),
                normalized_bytes=width * height * 2, literal_memory_dump=False,
                physical_packing_verified=False)


def _paused(guest):
    status = guest.qmp.command('query-status')
    if status.get('running') is not False or status.get('status') not in ('paused', 'debug'):
        raise ValueError('Pixel snapshots require an already paused guest')


def _states(guest, raw_output):
    _paused(guest)
    result = {}
    for name in (D980, TAG, RAW) if raw_output else (D980, TAG):
        state = guest.qmp.command('qom-get', path='/machine', property=name)
        state = json.loads(state) if isinstance(state, str) else state
        if not isinstance(state, dict):
            raise ValueError('Malformed producer device state')
        result[name] = state
    return result


def _physical(address, size):
    integer(address, 'physical producer address')
    if address % 32 or not IMAGE_BEGIN <= address <= IMAGE_END - size:
        raise ValueError('Producer span must lie entirely in declared physical image RAM')
    return address


def _description(states, raw_output):
    d = states[D980]
    if d.get('enabled') is not True or d.get('stream_enabled') is not True:
        raise ValueError('Actual D980 stream is not enabled')
    frame = integer(d.get('frames'), 'D980 frame counter', 1)
    integer(d.get('submits'), 'D980 submissions', 1)
    for name in ('acks', 'cancelled', 'rejects'):
        integer(d.get(name), 'D980 ' + name)
    pending = integer(d.get('pending'), 'D980 pending')
    if pending not in (0, 0x04000000) or d['rejects']:
        raise ValueError('D980 state has unsupported pending or rejected work')
    if d['acks'] + d['cancelled'] + bool(pending) != frame:
        raise ValueError('D980 completion counters do not account for every completed DMA')
    c = d.get('latched', {})
    mode = integer(c.get('0x0'), 'latched D980 packing mode', 1, 2)
    if (type(d.get('cache_format')) is not int or d['cache_format'] != mode
            or (mode == 1 and d.get('packed14_enabled') is not True)):
        raise ValueError('Latched mode and actual packed producer cache differ')
    w = integer(c.get('0x4'), 'latched width word', 1, 0xffffffff)
    h = integer(c.get('0x8'), 'latched height word', 1, 0xffffffff)
    width, height, bits = w >> 16, h >> 16, 14 if mode == 1 else 12
    stride = c.get('0x100')
    needed = geometry(width, height, stride, bits)
    if (w & 65535 or h & 65535 or (width | height) & 1 or stride != ((needed + 31) & ~31)):
        raise ValueError('Latched D980 geometry does not match the complete producer contract')
    required = {'0x10': 0, '0x104': 1, '0x108': 1, '0x110': 1,
                '0x114': 0, '0x118': 0, '0x11c': 0}
    if any(type(c.get(key)) is not int or c[key] != value for key, value in required.items()):
        raise ValueError('Incomplete or unsupported D980 latched configuration')
    bank = integer(c.get('0x10c'), 'D980 bank', 0, 1)
    mask = 0xfff | (0xff << (12 + bank * 8))
    if integer(d.get('valid'), 'D980 valid fields') & mask != mask:
        raise ValueError('D980 has not received every required configuration field')
    size = stride * height
    if integer(d.get('pixel_dma_bytes'), 'completed D980 bytes') < size:
        raise ValueError('No complete D980 DMA exists for this geometry')
    source = _physical(d.get('last_physical'), size)
    integer(d.get('last_encoded'), 'original encoded producer address', 1)
    address, job = source, frame
    if raw_output:
        r = states[RAW]
        job = integer(r.get('jobs'), 'RAW output jobs', 1, frame)
        integer(r.get('acks'), 'RAW output acknowledgements')
        if (r.get('enabled') is not True or type(r.get('pending')) is not bool or bits != 14
                or integer(r.get('frame'), 'RAW output input frame', 1) != frame
                or r.get('source') != source or integer(r.get('bytes'), 'RAW output bytes') < size):
            raise ValueError('RAW output does not identify the current complete D980 input')
        address = _physical(r.get('target'), size)
        if address < source + size and source < address + size:
            raise ValueError('RAW output overlaps its source')
    return dict(width=width, height=height, stride=stride, bits=bits, memory_bytes=size,
                address=address, sensor_address=source, frame=frame, job=job)


def _tag(states, desc):
    tag = states[TAG]
    if type(tag.get('enabled')) is not bool:
        raise ValueError('Sensor-tag enable state is missing')
    expected = dict(frame_id=desc['frame'], physical=desc['sensor_address'],
                    width=desc['width'], height=desc['height'], stride=desc['stride'], bits=desc['bits'])
    if any(type(tag.get(key)) is not int or tag[key] != value for key, value in expected.items()):
        raise ValueError('Sensor tag frame, address or geometry differs from actual producer')
    # This QOM property also exposes the last DMA cache geometry when the
    # marker is disabled. It must still agree with the current latched state.
    if not tag['enabled']:
        return None
    integer(tag['frame_id'], 'marker frame ID', 1, 0xffffffff)
    integer(tag.get('virtual_ns'), 'actual sensor DMA virtual time', 1)
    if (tag.get('scheme') != 'cfa32-complement-v1' or desc['width'] < 64 or desc['height'] < 16
            or not isinstance(tag.get('packed_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', tag['packed_sha256'])):
        raise ValueError('Invalid sensor-tag provenance')
    return tag


def _sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _capture(guest, label, *, raw_output):
    if not isinstance(label, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', label):
        raise ValueError('Snapshot label must be a simple fresh name')
    profile = load_bindings(guest)
    before = _states(guest, raw_output)
    desc = _description(before, raw_output)
    tag = _tag(before, desc)
    root = Path(guest.out).resolve(strict=True)
    directory = root / ('pixel-' + label)
    directory.mkdir(mode=0o700, exist_ok=False)
    packed, normalized = directory / 'packed.bin', directory / 'pixels-u16le.bin'
    try:
        guest.qmp.command('pmemsave', val=desc['address'], size=desc['memory_bytes'], filename=str(packed))
        after = _states(guest, raw_output)
        if before != after:
            raise ValueError('Producer job or sensor frame changed while saving RAM')
        if not packed.is_file() or packed.stat().st_size != desc['memory_bytes']:
            raise ValueError('Physical memory snapshot is missing or truncated')
        packed_sha = _sha(packed)
        if tag and packed_sha != tag['packed_sha256']:
            raise ValueError('Actual full producer RAM differs from the sensor frame hash')
        conversion = unpack_file(packed, normalized, width=desc['width'], height=desc['height'],
                                 stride=desc['stride'], bits=desc['bits'])
        if conversion['packed_sha256'] != packed_sha or _sha(normalized) != conversion['normalized_sha256']:
            raise ValueError('Snapshot changed during conversion')
        producer = dict(origin='guest_producer_dma', path=normalized.relative_to(root).as_posix(),
            sha256=conversion['normalized_sha256'], encoding='u16le', width=desc['width'],
            height=desc['height'], stride_samples=desc['width'], bit_depth=desc['bits'],
            address=desc['address'], memory_bytes=desc['memory_bytes'])
        if tag:
            producer.update(marker_scheme=tag['scheme'], marker_frame_id=tag['frame_id'])
        result = dict(kind='engine151_raw_output' if raw_output else 'd980_dma',
            main_sha256=profile['main_sha256'], producer=producer,
            packed=dict(path=packed.relative_to(root).as_posix(), sha256=packed_sha,
                        bytes=desc['memory_bytes'], stride_bytes=desc['stride'], encoding=f'packed{desc["bits"]}le'),
            conversion=conversion, device_states_before=before, device_states_after=after,
            device_identity_unchanged=True, device_frame_id=desc['frame'], device_job_counter=desc['job'],
            memory_read_transport='qmp_pmemsave', paused_during_snapshot=True,
            temporal_attribution_verified=tag is not None,
            dma_completed_virtual_ns=tag['virtual_ns'] if tag else None,
            timing_scope='External sensor DMA completion, not physical exposure time' if tag else 'Snapshot only; no pre/post-trigger time attribution',
            raw_precapture_verified=False, guest_state_modified=False,
            scope='Actual producer RAM only; no NEF generated or capture acceptance awarded')
        with (directory / 'receipt.json').open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2); output.write('\n')
        return result
    except Exception as error:
        with (directory / 'failure.json').open('x', encoding='utf-8') as output:
            json.dump(dict(status='failed', error=repr(error), device_states_before=before,
                           raw_precapture_verified=False), output, indent=2)
        raise


def capture_d980(guest, label):
    return _capture(guest, label, raw_output=False)


def capture_raw_output(guest, label):
    return _capture(guest, label, raw_output=True)
