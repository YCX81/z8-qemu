# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only, fail-closed JPEG/MOV metadata gate; NOT media/camera acceptance.

Supported profile: one JPEG Exif Apple MakerNote, unfragmented MOV, CFR video,
one mebx still-image-time sample, v0/v1 timing and simple rate-1 edit lists.
Strict mode rejects HEIC, B-frame ctts and fragmented MOV.
Apple diagnostic mode supports file-offset HEIF Exif and skips project video timing.
Nikon preservation requires a separately retained original JPEG and a trusted
pre-packaging SHA256. See docs/z8-live-photo.md. No assert-based validation.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

CONTENT = b'com.apple.quicktime.content.identifier'
STILL = b'com.apple.quicktime.still-image-time'
MAX_FILE = 512 * 1024 * 1024


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def number(data, offset=0, fmt='>I'):
    size = struct.calcsize(fmt)
    require(0 <= offset <= len(data) - size, 'truncated numeric field')
    return struct.unpack_from(fmt, data, offset)[0]


def boxes(data):
    result, pos = [], 0
    while pos < len(data):
        require(len(result) < 100000, 'too many boxes')
        size = number(data, pos)
        kind, header = data[pos + 4:pos + 8], 8
        if size == 1:
            size, header = number(data, pos + 8, '>Q'), 16
        require(size >= header and pos + size <= len(data), 'invalid box extent')
        result.append((kind, data[pos + header:pos + size], pos + header))
        pos += size
    return result


def one(items, kind):
    found = [payload for tag, payload, _ in items if tag == kind]
    require(len(found) == 1, 'expected one ' + repr(kind))
    return found[0]


def table(data, width):
    require(data[:4] == bytes(4), 'unsupported table version/flags')
    count = number(data, 4)
    require(count <= 100000 and len(data) == 8 + count * width, 'invalid table length')
    return [data[p:p + width] for p in range(8, len(data), width)]


def ifd(data, offset, endian):
    count = number(data, offset, endian + 'H')
    require(offset + 2 + 12 * count + 4 <= len(data), 'truncated IFD')
    result = {}
    for pos in range(offset + 2, offset + 2 + 12 * count, 12):
        tag, typ, n, value = struct.unpack_from(endian + 'HHII', data, pos)
        require(tag not in result, 'duplicate TIFF tag')
        result[tag] = (typ, n, value, pos + 8)
    return result


def tiff_value(data, entry):
    typ, count, offset, inline = entry
    size = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1}.get(typ)
    require(size is not None and count > 0, 'unsupported TIFF value')
    length = count * size
    start = inline if length <= 4 else offset
    require(start + length <= len(data), 'TIFF value outside data')
    return data[start:start + length]


def jpeg_note(data):
    require(data[:2] == b'\xff\xd8' and data[-2:] == b'\xff\xd9', 'not a complete JPEG envelope')
    exifs, pos = [], 2
    while pos < len(data) - 2:
        require(data[pos] == 255, 'invalid JPEG marker')
        marker = data[pos + 1]
        if marker in (0xda, 0xd9):
            break
        size = number(data, pos + 2, '>H')
        require(size >= 2 and pos + 2 + size <= len(data), 'invalid JPEG segment')
        payload = data[pos + 4:pos + 2 + size]
        if marker == 0xe1 and payload.startswith(b'Exif\0\0'):
            exifs.append(payload[6:])
        pos += size + 2
    require(len(exifs) == 1, 'expected one Exif APP1; duplicate Exif is not coexistence')
    return tiff_note(exifs[0])


def tiff_note(tiff):
    require(tiff[:2] in (b'II', b'MM'), 'invalid TIFF byte order')
    endian = '<' if tiff[:2] == b'II' else '>'
    require(number(tiff, 2, endian + 'H') == 42, 'invalid TIFF magic')
    root = ifd(tiff, number(tiff, 4, endian + 'I'), endian)
    require(0x8769 in root and root[0x8769][:2] == (4, 1), 'missing Exif IFD')
    exif = ifd(tiff, root[0x8769][2], endian)
    require(0x927c in exif and exif[0x927c][0] == 7, 'missing MakerNote')
    return tiff_value(tiff, exif[0x927c])


def heic_note(data):
    """Diagnostic HEIF Exif reader: file-offset extents only; never reads GPS IFD."""
    meta = one(boxes(data), b'meta')
    require(meta[:4] == bytes(4), 'unsupported HEIF meta')
    children = boxes(meta[4:])
    iinf = one(children, b'iinf')
    require(iinf[0] in (0, 1), 'unsupported iinf')
    infos = boxes(iinf[6 if iinf[0] == 0 else 8:])
    exifs = []
    for kind, entry, _ in infos:
        require(kind == b'infe' and entry[0] in (2, 3), 'unsupported infe')
        width = 2 if entry[0] == 2 else 4
        if entry[6 + width:10 + width] == b'Exif':
            exifs.append(int.from_bytes(entry[4:4 + width], 'big'))
    require(len(exifs) == 1, 'expected one HEIF Exif item')
    primary = one(children, b'pitm')
    require(primary[0] in (0, 1), 'unsupported pitm')
    primary_id = number(primary, 4, '>H' if primary[0] == 0 else '>I')
    refs = one(children, b'iref')
    require(refs[0] in (0, 1), 'unsupported iref')
    width = 2 if refs[0] == 0 else 4
    linked = False
    for kind, ref, _ in boxes(refs[4:]):
        if kind != b'cdsc':
            continue
        source = number(ref, 0, '>H' if width == 2 else '>I')
        count = number(ref, width, '>H')
        require(len(ref) == width + 2 + count * width, 'invalid HEIF cdsc')
        targets = [int.from_bytes(ref[i:i+width], 'big') for i in range(width+2, len(ref), width)]
        linked |= source == exifs[0] and primary_id in targets
    require(linked, 'Exif must describe primary HEIF item')
    loc = one(children, b'iloc')
    require(loc[0] in (0, 1, 2), 'unsupported iloc')
    osz, lsz, bsz = loc[4] >> 4, loc[4] & 15, loc[5] >> 4
    isz = loc[5] & 15 if loc[0] else 0
    require(all(n in (0, 4, 8) for n in (osz, lsz, bsz, isz)), 'unsupported iloc widths')
    cursor = 6
    def take(n):
        nonlocal cursor
        require(cursor + n <= len(loc), 'truncated iloc')
        value = int.from_bytes(loc[cursor:cursor+n], 'big')
        cursor += n
        return value
    count = take(4 if loc[0] == 2 else 2)
    found = []
    for _ in range(count):
        iid = take(4 if loc[0] == 2 else 2)
        method = take(2) if loc[0] else 0
        ref, base = take(2), take(bsz)
        extents = []
        for _ in range(take(2)):
            take(isz)
            offset, length = base + take(osz), take(lsz)
            if iid == exifs[0]:
                require(method == 0 and ref == 0 and length > 0 and offset + length <= len(data), 'unsupported Exif extent')
                extents.append(data[offset:offset+length])
        if iid == exifs[0]:
            found.append(b''.join(extents))
    require(len(found) == 1, 'missing/duplicate Exif location')
    exif = found[0]
    offset = 4 + number(exif)
    require(offset < len(exif), 'invalid Exif TIFF offset')
    return tiff_note(exif[offset:])


def apple_identifier(note):
    require(note[:10] == b'Apple iOS\0' and note[12:14] in (b'MM', b'II'), 'missing Apple MakerNote')
    endian = '>' if note[12:14] == b'MM' else '<'
    entries = ifd(note, 14, endian)
    require(17 in entries and entries[17][0] == 2, 'missing Apple ContentIdentifier 0x0011')
    value = tiff_value(note, entries[17])
    require(value.endswith(b'\0') and b'\0' not in value[:-1], 'invalid Apple identifier string')
    text = value[:-1].decode('utf-8')
    require(text and text.strip() == text, 'empty/whitespace identifier')
    return text


def timing(data):
    require(data[0:1] in (b'\0', b'\1'), 'unsupported time header')
    offset = 12 if data[0] == 0 else 20
    scale = number(data, offset)
    duration = number(data, offset + 4, '>I' if data[0] == 0 else '>Q')
    require(scale > 0 and duration > 0, 'invalid timescale/duration')
    return scale, duration


def edit_start(trak, movie_scale, media_scale, media_duration):
    edits = [p for k, p, _ in trak if k == b'edts']
    if not edits:
        return 0.0
    require(len(edits) == 1, 'duplicate edit list')
    data = one(boxes(edits[0]), b'elst')
    require(data[:4] in (bytes(4), b'\1\0\0\0'), 'unsupported edit version')
    width = 12 if data[0] == 0 else 20
    count = number(data, 4)
    require(count in (1, 2) and len(data) == 8 + count * width, 'unsupported edit list')
    delay = 0.0
    for i in range(count):
        fmt = '>IiHH' if data[0] == 0 else '>QqHH'
        duration, start, rate, fraction = struct.unpack_from(fmt, data, 8 + i * width)
        require(rate == 1 and fraction == 0 and duration > 0, 'unsupported edit rate/duration')
        if count == 2 and i == 0:
            require(start == -1, 'expected leading empty edit')
            delay = duration / movie_scale
        else:
            require(start == 0, 'trimmed media is not supported')
            require(abs(duration / movie_scale - media_duration / media_scale) <= 1 / movie_scale,
                    'edit duration does not match media')
    return delay


def samples(stbl, data, mdats):
    sizes = one(stbl, b'stsz')
    require(sizes[:4] == bytes(4), 'unsupported stsz')
    fixed, count = number(sizes, 4), number(sizes, 8)
    require(0 < count <= 100000, 'invalid sample count')
    require(len(sizes) == (12 if fixed else 12 + count * 4), 'invalid stsz length')
    lengths = [fixed] * count if fixed else [number(sizes, 12 + i * 4) for i in range(count)]
    offsets = [(k, p) for k, p, _ in stbl if k in (b'stco', b'co64')]
    require(len(offsets) == 1, 'missing/ambiguous chunk offsets')
    kind, payload = offsets[0]
    chunks = [number(v, 0, '>I' if kind == b'stco' else '>Q') for v in table(payload, 4 if kind == b'stco' else 8)]
    entries = [struct.unpack('>III', v) for v in table(one(stbl, b'stsc'), 12)]
    require(entries and entries[0][0] == 1, 'invalid first chunk')
    require(all(1 <= first <= len(chunks) and n > 0 and desc == 1 for first, n, desc in entries), 'unsupported chunk map')
    require(all(a[0] < b[0] for a, b in zip(entries, entries[1:])), 'unordered chunk map')
    result, index = [], 0
    for chunk_index, start in enumerate(chunks, 1):
        while index + 1 < len(entries) and entries[index + 1][0] <= chunk_index:
            index += 1
        for _ in range(entries[index][1]):
            require(len(result) < count, 'chunk map exceeds sample count')
            size = lengths[len(result)]
            require(size > 0 and any(lo <= start and start + size <= hi for lo, hi in mdats), 'sample outside mdat')
            result.append((start, size))
            start += size
    require(len(result) == count, 'incomplete chunk map')
    return result


def movie_metadata(data, *, diagnostic=False):
    top = boxes(data)
    require(not any(k == b'moof' for k, _, _ in top), 'fragmented MOV unsupported')
    brand = one(top, b'ftyp')
    require(brand[:4] == b'qt  ', 'expected QuickTime MOV brand')
    mdats = [(start, start + len(p)) for k, p, start in top if k == b'mdat']
    moov = boxes(one(top, b'moov'))
    require(not any(k == b'mvex' for k, _, _ in moov), 'fragmented MOV unsupported')
    movie_scale, movie_duration = timing(one(moov, b'mvhd'))
    meta = one(moov, b'meta')
    require(meta[:4] != bytes(4), 'moov/meta must be QuickTime, not ISO FullBox')
    meta = boxes(meta)
    require(one(meta, b'hdlr')[8:12] == b'mdta', 'wrong metadata handler')
    keys_data = one(meta, b'keys')
    require(keys_data[:4] == bytes(4), 'unsupported keys version')
    keys = boxes(keys_data[8:])
    require(number(keys_data, 4) == len(keys), 'keys count mismatch')
    matches = [i + 1 for i, (k, p, _) in enumerate(keys) if k == b'mdta' and p == CONTENT]
    require(len(matches) == 1, 'missing/duplicate MOV content identifier key')
    entry = one(boxes(one(meta, b'ilst')), struct.pack('>I', matches[0]))
    value = one(boxes(entry), b'data')
    require(value[:8] == struct.pack('>II', 1, 0), 'content identifier must be UTF-8')
    identifier = value[8:].decode('utf-8')
    require(identifier and '\0' not in identifier, 'invalid MOV identifier')
    videos, stills, extents = [], [], []
    video_ids, still_refs, track_ids = [], [], set()
    for kind, payload, _ in moov:
        if kind != b'trak':
            continue
        trak = boxes(payload)
        tkhd = one(trak, b'tkhd')
        require(tkhd[0] in (0, 1), 'unsupported tkhd')
        track_id = number(tkhd, 12 if tkhd[0] == 0 else 20)
        require(track_id > 0 and track_id not in track_ids, 'invalid/duplicate track ID')
        track_ids.add(track_id)
        mdia = boxes(one(trak, b'mdia'))
        handler = one(mdia, b'hdlr')[8:12]
        if handler not in (b'vide', b'meta'):
            continue  # Audio is explicitly outside this metadata gate.
        scale, duration = timing(one(mdia, b'mdhd'))
        minf = boxes(one(mdia, b'minf'))
        dref = one(boxes(one(minf, b'dinf')), b'dref')
        refs = boxes(dref[8:])
        require(dref[:8] == struct.pack('>II', 0, 1) and len(refs) == 1 and
                refs[0][0] in (b'url ', b'alis') and refs[0][1] == struct.pack('>I', 1),
                'external media reference unsupported')
        stbl = boxes(one(minf, b'stbl'))
        require(diagnostic or not any(k in (b'ctts', b'cslg') for k, _, _ in stbl), 'composition offsets unsupported')
        stsd = one(stbl, b'stsd')
        require(stsd[:8] == struct.pack('>II', 0, 1), 'expected one sample description')
        descriptions = boxes(stsd[8:])
        require(len(descriptions) == 1, 'ambiguous sample description')
        codec, description, _ = descriptions[0]
        require(description[:8] == bytes(6) + b'\0\1', 'unsupported sample data reference')
        if handler == b'meta':
            require(codec == b'mebx', 'unsupported metadata codec')
            key_boxes = boxes(one(boxes(description[8:]), b'keys'))
            matching = []
            for local_id, definition, _ in key_boxes:
                fields = boxes(definition)
                if one(fields, b'keyd') == b'mdta' + STILL:
                    require(one(fields, b'dtyp') == struct.pack('>II', 0, 65), 'still marker must have int8 type')
                    matching.append(local_id)
            if not matching:
                continue
        runs = [struct.unpack('>II', v) for v in table(one(stbl, b'stts'), 8)]
        require(runs and all(n > 0 and delta > 0 for n, delta in runs), 'empty timing table')
        locations = samples(stbl, data, mdats)
        extents.extend((s, s + n) for s, n in locations)
        require(sum(n for n, _ in runs) == len(locations) and
                sum(n * delta for n, delta in runs) == duration, 'sample timing/duration mismatch')
        start = 0.0 if diagnostic and handler == b'vide' else edit_start(trak, movie_scale, scale, duration)
        if handler == b'vide':
            require(codec in (b'avc1', b'hvc1') and len(description) >= 78, 'unsupported video description')
            require(diagnostic or (len({delta for _, delta in runs}) == 1 and start == 0), 'expected CFR video starting at zero')
            video_ids.append(track_id)
            videos.append({'duration_seconds': duration / scale, 'fps': len(locations) * scale / duration if diagnostic else scale / runs[0][1], 'frames': len(locations)})
        else:
            require(len(matching) == 1 and len(locations) == 1, 'expected one still marker sample')
            pos, size = locations[0]
            marker = one(boxes(data[pos:pos + size]), matching[0])
            require(marker == b'\xff', 'still marker must be int8 -1')
            refs = one(boxes(one(trak, b'tref')), b'cdsc')
            require(len(refs) > 0 and len(refs) % 4 == 0, 'invalid cdsc references')
            still_refs.extend(number(refs, i) for i in range(0, len(refs), 4))
            stills.append((start, start + duration / scale))
    require(len(videos) == 1 and len(stills) == 1, 'expected one video and one still-image-time track')
    require(still_refs and all(ref in video_ids for ref in still_refs), 'still cdsc must reference video track')
    ordered = sorted(extents)
    require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), 'overlapping media samples')
    result = videos[0]
    require(diagnostic or abs(movie_duration / movie_scale - result['duration_seconds']) <= 1 / movie_scale, 'movie/video duration mismatch')
    require(0 <= stills[0][0] < result['duration_seconds'] and
            stills[0][1] <= result['duration_seconds'] + 1e-9, 'still sample interval outside clip')
    result.update(identifier=identifier, still_time_seconds=stills[0][0],
                  moov_meta_format='quicktime', still_marker=-1, still_dtyp=65,
                  still_cdsc_video_track_ids=still_refs)
    return result


def read_media(path):
    require(path.is_file() and 0 < path.stat().st_size <= MAX_FILE, 'missing, empty or oversized file: ' + str(path))
    return path.read_bytes()


def dcf(path):
    require(path.parent.parent.name.upper() == 'DCIM' and
            re.fullmatch(r'[1-9][0-9]{2}[A-Z0-9_]{5}', path.parent.name) and
            re.fullmatch(r'[A-Z0-9_]{4}[0-9]{4}', path.stem), 'invalid project DCIM/DCF naming: ' + str(path))


def audit_pair(photo, mov, *, pre=1.5, post=1.5, fps=30.0,
               nikon_original=None, nikon_sha256=None, apple_structure_only=False):
    result = {'schema': 'z8-live-photo-pair-audit/v1', 'ok': False,
              'diagnostic_mode': 'apple_structure_only' if apple_structure_only else None,
              'z8_acceptance_eligible': False,
              'scope': 'metadata_structure_only', 'camera_verified': False,
              'photos_import_verified': False, 'media_decode_verified': False,
              'errors': [], 'parameters': {'pre': pre, 'post': post, 'fps': fps}}
    try:
        require(all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
                    for v in (pre, post, fps)), 'finite positive pre/post/fps required')
        photo, mov = Path(photo).resolve(), Path(mov).resolve()
        require(photo.suffix.upper() in (('.JPG', '.JPEG', '.HEIC') if apple_structure_only else ('.JPG', '.JPEG')) and mov.suffix.upper() == '.MOV', 'only JPEG/MOV profile supported; HEIC fails closed')
        if not apple_structure_only:
            dcf(photo)
            dcf(mov)
        require(photo.parent == mov.parent and photo.stem == mov.stem, 'pair names/directories differ')
        image, video = read_media(photo), read_media(mov)
        result['files'] = {str(p): hashlib.sha256(b).hexdigest() for p, b in ((photo, image), (mov, video))}
        result['image_identifier'] = apple_identifier(heic_note(image) if photo.suffix.upper() == '.HEIC' else jpeg_note(image))
        result['movie'] = info = movie_metadata(video, diagnostic=apple_structure_only)
        require(info['identifier'] == result['image_identifier'], 'pair identifiers differ')
        if not apple_structure_only:
            require(abs(info['duration_seconds'] - pre - post) <= 0.001 and
                    abs(info['still_time_seconds'] - pre) <= 0.001, 'pre/post window mismatch (1 ms tolerance)')
            require(abs(info['fps'] - fps) <= 0.001 and abs(info['frames'] - (pre + post) * fps) <= 0.001, 'frame rate/count mismatch')
            require(nikon_original is not None and nikon_sha256 is not None, 'Nikon preservation requires original JPEG and pre-packaging SHA256')
            original = Path(nikon_original).resolve()
            dcf(original)
            require(original != photo and original.parent == photo.parent, 'Nikon original must be a separate JPEG in the same card folder')
            require(original.suffix.upper() in ('.JPG', '.JPEG'), 'Nikon preservation profile requires JPEG')
            original_data = read_media(original)
            note = jpeg_note(original_data)
            require(note.startswith(b'Nikon\0\x02'), 'missing Nikon type-3 MakerNote')
            require(note[10:14] in (b'II*\0', b'MM\0*'), 'invalid Nikon TIFF header')
            endian = '<' if note[10:12] == b'II' else '>'
            ifd(note[10:], number(note, 14, endian + 'I'), endian)
            digest = hashlib.sha256(original_data).hexdigest()
            require(re.fullmatch('[0-9a-fA-F]{64}', nikon_sha256) and digest == nikon_sha256.lower(), 'Nikon original hash mismatch')
            result['nikon'] = {'location': 'separate_original', 'path': str(original),
                               'sha256': digest, 'makernote_sha256': hashlib.sha256(note).hexdigest(),
                               'baseline_source': 'caller_supplied_pre_packaging_hash', 'preserved': True}
        result['ok'] = True
    except (Invalid, OSError, ValueError, struct.error, TypeError) as exc:
        result['errors'].append(str(exc))
    if apple_structure_only:
        if 'image_identifier' in result:
            result['image_identifier'] = '<redacted>'
        if 'movie' in result:
            result['movie']['identifier'] = '<redacted>'
        result['skipped_checks'] = ['Nikon preservation', 'project DCF naming', 'project CFR/window', 'video composition/edit timing']
    # JSON must remain standards-compliant even for NaN/Infinity CLI arguments.
    result['parameters'] = {k: v if isinstance(v, (int, float)) and math.isfinite(v) else str(v)
                            for k, v in result['parameters'].items()}
    return result


def audit_card(root, manifest, *, pre=1.5, post=1.5, fps=30.0):
    """Audit every MOV, with a per-MOV retained-original/hash mapping."""
    root = Path(root).resolve()
    result = {'schema': 'z8-live-photo-card-audit/v1', 'ok': False, 'pairs': [],
              'errors': [], 'camera_verified': False, 'photos_import_verified': False}
    try:
        require(isinstance(manifest, dict), 'preservation manifest must be an object')
        movies = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.upper() == '.MOV')
        require(movies, 'no MOV resources on card')
        require(set(manifest) == {p.relative_to(root).as_posix() for p in movies}, 'manifest must cover every MOV exactly')
        identifiers = set()
        for mov in movies:
            require(mov.resolve().is_relative_to(root), 'MOV escapes card directory')
            entry = manifest[mov.relative_to(root).as_posix()]
            require(isinstance(entry, dict) and set(entry) == {'nikon_original', 'nikon_sha256'}, 'invalid preservation entry')
            require(isinstance(entry['nikon_original'], str) and isinstance(entry['nikon_sha256'], str), 'preservation values must be strings')
            original = (root / entry['nikon_original']).resolve()
            require(original.is_relative_to(root), 'original escapes card directory')
            photos = [p for p in mov.parent.iterdir() if p.stem == mov.stem and p.suffix.upper() in ('.JPG', '.JPEG', '.HEIC')]
            require(len(photos) == 1, 'missing/ambiguous paired still')
            require(photos[0].resolve().is_relative_to(root), 'still escapes card directory')
            pair = audit_pair(photos[0], mov, pre=pre, post=post, fps=fps,
                              nikon_original=original, nikon_sha256=entry['nikon_sha256'])
            result['pairs'].append(pair)
            if pair['ok']:
                identifier = pair['image_identifier']
                require(identifier not in identifiers, 'content identifier reused across captures')
                identifiers.add(identifier)
        result['ok'] = all(pair['ok'] for pair in result['pairs'])
        if not result['ok']:
            result['errors'].append('one or more pairs failed')
    except (Invalid, OSError, ValueError, TypeError) as exc:
        result['errors'].append(str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', help='one card directory or JPEG and MOV paths')
    parser.add_argument('--apple-structure-only', action='store_true', help='redacted Apple diagnostic; never Z8 acceptance')
    parser.add_argument('--pre', type=float, default=1.5)
    parser.add_argument('--post', type=float, default=1.5)
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--nikon-original')
    parser.add_argument('--nikon-sha256')
    parser.add_argument('--preservation-manifest', help='card-relative MOV to original/hash JSON mapping')
    args = parser.parse_args(argv)
    try:
        paths = [Path(p) for p in args.paths]
        require(not (args.apple_structure_only and args.preservation_manifest), 'diagnostic mode does not support preservation manifest')
        if args.preservation_manifest:
            require(len(paths) == 1 and paths[0].is_dir(), 'manifest mode requires one card directory')
            require(not args.nikon_original and not args.nikon_sha256, 'use manifest or single original options, not both')
            manifest = json.loads(Path(args.preservation_manifest).read_text(encoding='utf-8'))
            receipt = audit_card(paths[0], manifest, pre=args.pre, post=args.post, fps=args.fps)
            print(json.dumps(receipt, indent=2, ensure_ascii=True, allow_nan=False))
            return 0 if receipt['ok'] else 1
        if len(paths) == 1 and paths[0].is_dir():
            candidates = [p for p in paths[0].rglob('*') if p.suffix.upper() == '.MOV']
            require(len(candidates) == 1, 'directory profile requires exactly one MOV; use explicit pairs for multi-capture cards')
            mov = candidates[0]
            photos = [p for p in mov.parent.iterdir() if p.stem == mov.stem and p.suffix.upper() in ('.JPG', '.JPEG', '.HEIC')]
            require(len(photos) == 1, 'missing/ambiguous paired still')
            paths = [photos[0], mov]
        require(len(paths) == 2, 'expected a card directory or two files')
        receipt = audit_pair(*paths, pre=args.pre, post=args.post, fps=args.fps,
                             nikon_original=args.nikon_original, nikon_sha256=args.nikon_sha256,
                             apple_structure_only=args.apple_structure_only)
    except (Invalid, OSError, ValueError) as exc:
        receipt = {'schema': 'z8-live-photo-pair-audit/v1', 'ok': False, 'diagnostic_mode': 'apple_structure_only' if args.apple_structure_only else None,
                   'z8_acceptance_eligible': False, 'errors': [str(exc)]}
    print(json.dumps(receipt, indent=2, ensure_ascii=True, allow_nan=False))
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
