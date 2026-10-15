# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded standard-container search; no raw-DEFLATE or proprietary decoding.

Successful decoding requires end-of-stream (including the container checksum).
Decoded bytes stay in memory. Output records only hashes, extents and matching
resource-signature offsets, never a claim of recovered factory calibration.
"""
import bz2
import hashlib
import lzma
import re
import struct
import zlib

INPUT_LIMIT = 2 * 1024 * 1024
OUTPUT_LIMIT = 2 * 1024 * 1024
MAGICS = {
    'gzip': b'\x1f\x8b\x08',
    'bzip2': b'BZh',
    'xz': b'\xfd7zXZ\x00',
    # Recognized only; decoder is deliberately not required by this probe.
    'lz4-frame': b'\x04\x22\x4d\x18',
    'zstd-frame': b'\x28\xb5\x2f\xfd',
}


def offsets(data, needle):
    at = 0
    while True:
        at = data.find(needle, at)
        if at < 0:
            return
        yield at
        at += 1


def decode(data, offset, kind, *, input_limit=INPUT_LIMIT, output_limit=OUTPUT_LIMIT):
    window = memoryview(data)[offset:offset + input_limit]
    if kind == 'zlib':
        decoder = zlib.decompressobj()
    elif kind == 'gzip':
        decoder = zlib.decompressobj(31)
    elif kind == 'bzip2':
        decoder = bz2.BZ2Decompressor()
    elif kind == 'xz':
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
    else:
        return 'decoder_not_enabled', None, None
    try:
        decoded = decoder.decompress(window, output_limit + 1)
    except (zlib.error, OSError, EOFError, lzma.LZMAError):
        return 'invalid_or_unsupported', None, None
    if len(decoded) > output_limit:
        return 'output_bound', None, None
    if not decoder.eof:
        return 'incomplete_or_input_bound', None, None
    consumed = len(window) - len(decoder.unused_data)
    return 'complete', decoded, consumed


def scan(data, patterns):
    counts = {}
    complete = []
    bounded = []

    def attempt(at, kind):
        counter = counts.setdefault(kind, {})
        status, decoded, consumed = decode(data, at, kind)
        counter[status] = counter.get(status, 0) + 1
        if status == 'complete':
            png = False
            if at >= 8 and data[at-4:at] == b'IDAT':
                chunk_size = struct.unpack_from('>I', data, at-8)[0]
                end = at + chunk_size
                if consumed <= chunk_size and end + 4 <= len(data):
                    png = (zlib.crc32(memoryview(data)[at-4:end]) & 0xffffffff
                           == struct.unpack_from('>I', data, end)[0])
            complete.append(dict(format=kind, offset=hex(at), compressed_bytes=consumed,
                                 decoded_bytes=len(decoded), sha256=hashlib.sha256(decoded).hexdigest(),
                                 png_idat_chunk_crc_valid=png,
                                 matches={key: [hex(i) for i in offsets(decoded, needle)]
                                          for key, needle in patterns.items()}))
        elif status in ('output_bound', 'incomplete_or_input_bound', 'decoder_not_enabled'):
            bounded.append(dict(format=kind, offset=hex(at), reason=status))

    # All RFC1950 DEFLATE window-size headers, not just the common 78 9c pair.
    for match in re.finditer(b'[\x08\x18\x28\x38\x48\x58\x68\x78]', data):
        at = match.start()
        if at + 1 >= len(data):
            continue
        flags = data[at + 1]
        if ((data[at] << 8) | flags) % 31:
            continue
        if flags & 0x20:
            counter = counts.setdefault('zlib', {})
            counter['preset_dictionary_header'] = counter.get('preset_dictionary_header', 0) + 1
            continue
        attempt(at, 'zlib')
    for kind, magic in MAGICS.items():
        for at in offsets(data, magic):
            attempt(at, kind)
    return dict(input_bound=INPUT_LIMIT,output_bound=OUTPUT_LIMIT,
                counts=counts,complete_streams=complete,unresolved_candidates=bounded,
                scope='Single standard framed streams only; no recursive, raw, dictionary or proprietary decoding')


def inspect_cpio(data, patterns):
    """Parse newc/CRC cpio in memory; never create paths or follow symlinks."""
    entries = []
    at = 0
    while len(entries) < 100000:
        if at + 110 > len(data) or data[at:at+6] not in (b'070701', b'070702'):
            raise ValueError(f'Invalid cpio header at {at:#x}')
        fields = [int(data[at+6+i*8:at+14+i*8], 16) for i in range(13)]
        size, name_size = fields[6], fields[11]
        name_end = at + 110 + name_size
        if not 1 <= name_size <= 65536 or name_end > len(data):
            raise ValueError('Invalid cpio filename extent')
        name = data[at+110:name_end]
        if not name.endswith(b'\0'):
            raise ValueError('Unterminated cpio filename')
        name = name[:-1].decode('utf-8')
        body = (name_end + 3) & ~3
        end = body + size
        if end > len(data):
            raise ValueError('Truncated cpio entry')
        content = data[body:end]
        if data[at:at+6] == b'070702' and sum(content) & 0xffffffff != fields[12]:
            raise ValueError('cpio checksum mismatch')
        entries.append(dict(name=name,mode=oct(fields[1]),bytes=size,offset=hex(body),
                            sha256=hashlib.sha256(content).hexdigest(),
                            matches={key:[hex(i) for i in offsets(content,needle)]
                                     for key,needle in patterns.items()}))
        at = (end + 3) & ~3
        if name == 'TRAILER!!!':
            if any(data[at:]):
                raise ValueError('Unparsed nonzero cpio tail')
            return dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
                        records_including_trailer=len(entries),trailing_zero_bytes=len(data)-at,
                        entries=entries)
    raise ValueError('cpio entry-count bound reached')


def inspect_nisi(data, patterns):
    """Inspect the checked module's 2 KiB block directory and gzip/cpio part."""
    if len(data) < 0x20 or data[:16] != b'NISI' + bytes(12):
        raise ValueError('Invalid NISI header')
    count = struct.unpack_from('<I', data, 0x10)[0]
    if not 1 <= count <= 32 or 0x20 + 16 * count > 0x800:
        raise ValueError('Invalid NISI directory length')
    parts = []
    previous_end = 0x800
    for index in range(count):
        block, blocks, size, reserved = struct.unpack_from('<4I', data, 0x20+index*16)
        start, allocation = block*0x800, blocks*0x800
        if reserved or start < previous_end or not 0 < size <= allocation or start+allocation > len(data):
            raise ValueError('Invalid or overlapping NISI extent')
        previous_end = start + allocation
        payload = data[start:start+size]
        row = dict(index=index,offset=hex(start),allocated_bytes=allocation,bytes=size,
                   sha256=hashlib.sha256(payload).hexdigest())
        if len(payload) >= 0x3c and payload[0x38:0x3c] == b'ARMd':
            row['format'] = 'ARM64 Linux Image header'
        elif payload[:4] == b'\xd0\x0d\xfe\xed' and struct.unpack_from('>I',payload,4)[0] == size:
            row['format'] = 'Flattened Device Tree header and matching extent'
        elif payload[:3] == b'\x1f\x8b\x08':
            status, decoded, consumed = decode(payload,0,'gzip',input_limit=size,
                                               output_limit=128*1024*1024)
            row['format'] = 'gzip'
            row['decode_status'] = status
            if status != 'complete' or consumed != size:
                raise ValueError('NISI gzip not completely decoded within the declared bound')
            row['cpio'] = inspect_cpio(decoded,patterns)
        else:
            row['format'] = 'unclassified'
        parts.append(row)
    return dict(version=data[0x14:0x20].rstrip(b'\0').decode('ascii'),
                directory_records=count,block_bytes=0x800,parts=parts,
                scope='Declared NISI parts; gzip CRC and cpio extents validated; no filesystem extraction')
