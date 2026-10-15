# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only complete NEF export from one stopped 8-GiB virtual-card run.

Usage: python tools/z8_export_nefs.py --run RUN --output FRESH_DIR --firmware KERNEL
KERNEL is the actual executed input file. An explicit executed_program_sha256
can identify an authorized derived kernel separately from the source MAIN hash.
No firmware bytes, directory names or NEF contents are manufactured here.

All new files stay in FRESH_DIR. Old audit receipts/volumes are read-only and
rechecked, never overwritten. Without one, a verified sparse copy in FRESH_DIR
is passed to the shared card auditor. The full original catalog is retained.
Export IDs/output_index describe directory observations, NOT native writer/job
order. Bind them to actual writer evidence before using z8_precapture_audit.
native_accepted is always false; this tool neither decodes RAW nor proves capture.
"""
import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess

from virtual_recording_card import audit as audit_card, extents as sparse_extents
from inspect_virtual_exfat import catalog
from extract_virtual_exfat import extract as extract_file

CARD_BYTES = 8 * 1024 ** 3
MAX_NEF_BYTES = 256 * 1024 ** 2
MAX_CLUSTERS = 1024 * 1024
CHUNK = 1024 * 1024


class ExportError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise ExportError(code, message)


def _integer(value, name, minimum=0, maximum=(1 << 64) - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail('schema', 'Invalid integer: ' + name)
    return value


def _regular(path):
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        _fail('input_file', 'Regular evidence file required: ' + str(path))
    return path


def _identity(path):
    row = _regular(path).stat()
    return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(path):
    _regular(path)
    if path.stat().st_size > 32 * CHUNK:
        _fail('schema', 'Evidence JSON exceeds limit')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        _fail('schema', 'Evidence JSON must be an object')
    return value


def _read(stream, offset, length):
    if offset < 0 or length < 0 or offset + length > os.fstat(stream.fileno()).st_size:
        _fail('bounds', 'Evidence read exceeds file')
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        _fail('short_read', 'Truncated evidence read')
    return data


def _partition(card):
    if _regular(card).stat().st_size != CARD_BYTES:
        _fail('card_size', 'Expected the complete 8-GiB regular virtual card')
    with card.open('rb') as stream:
        mbr = _read(stream, 0, 512)
    if mbr[510:] != b'\x55\xaa' or mbr[450] != 7 or any(mbr[462:510]):
        _fail('partition', 'Expected one generated exFAT MBR partition')
    start, sectors = struct.unpack_from('<II', mbr, 454)
    offset, length = start * 512, sectors * 512
    if offset < 512 or not length or offset + length != CARD_BYTES:
        _fail('partition', 'Partition must fit the full generated card exactly')
    return offset, length


def _ranges(stream, offset, length):
    try:
        result = [(max(a, offset) - offset, min(b, offset + length) - offset)
                  for a, b in sparse_extents(stream, os.fstat(stream.fileno()).st_size)
                  if max(a, offset) < min(b, offset + length)]
        if len(result) > MAX_CLUSTERS:
            _fail('bounds', 'Too many sparse file extents')
        return result
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS):
            raise
        return [(0, length)]


def _same_region(source, other, offset, length):
    """Compare the union of allocated ranges; holes in both files are zeros."""
    if other.stat().st_size != length:
        _fail('partition_mismatch', 'Derived volume size differs from actual card')
    identities = (_identity(source), _identity(other))
    with source.open('rb') as left, other.open('rb') as right:
        ranges = sorted(_ranges(left, offset, length) + _ranges(right, 0, length))
        merged = []
        for first, last in ranges:
            if merged and first <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        for first, last in merged:
            for position in range(first, last, CHUNK):
                size = min(CHUNK, last - position)
                if _read(left, offset + position, size) != _read(right, position, size):
                    _fail('partition_mismatch', 'Actual card differs from the audited derived volume')
    if identities != (_identity(source), _identity(other)):
        _fail('input_changed', 'Evidence changed during partition verification')


def _sparse_copy(source, target):
    before = _identity(source)
    with source.open('rb') as src, target.open('xb') as dst:
        dst.truncate(before[2])
        for first, last in _ranges(src, 0, before[2]):
            for position in range(first, last, CHUNK):
                data = _read(src, position, min(CHUNK, last - position))
                if data.strip(b'\0'):
                    dst.seek(position)
                    dst.write(data)
        dst.flush()
    if before != _identity(source):
        _fail('input_changed', 'Card changed while making the private audit copy')
    _same_region(source, target, 0, before[2])


def _fsck(volume):
    result = subprocess.run(['fsck.exfat', '-n', str(volume)], capture_output=True,
                            text=True, timeout=60, check=False)
    return dict(exit_code=result.returncode, output=result.stdout + result.stderr)


def _volume(card, output, partition):
    old = card.with_suffix('.audit.json')
    if old.exists():
        receipt = _json(old)
        if Path(receipt.get('source_card', '')).resolve() != card:
            _fail('audit_receipt', 'Old audit identifies another source card')
        volume = card.with_name(card.stem + '-after-volume.raw')
        if Path(receipt.get('derived_volume', '')).resolve() != volume:
            _fail('audit_receipt', 'Old audit does not identify its expected derived volume')
        origin = 'existing_receipt_rechecked_read_only'
    else:
        copied = output / 'audit-input-card.raw'
        _sparse_copy(card, copied)
        receipt = audit_card(copied)
        volume = copied.with_name(copied.stem + '-after-volume.raw')
        if (Path(receipt.get('source_card', '')).resolve() != copied
                or Path(receipt.get('derived_volume', '')).resolve() != volume):
            _fail('audit_receipt', 'Shared auditor returned unexpected artifact paths')
        origin = 'fresh_verified_copy_audited_in_output_only'
    if type(receipt.get('fsck_exit')) is not int or receipt['fsck_exit'] != 0 or receipt.get('source_unchanged') is not True:
        _fail('audit_receipt', 'Card audit did not establish an unchanged clean filesystem')
    _regular(volume)
    _same_region(card, volume, *partition)
    checked = _fsck(volume)
    if type(checked.get('exit_code')) is not int or checked['exit_code'] != 0:
        _fail('filesystem', 'Read-only filesystem recheck failed')
    return volume, dict(origin=origin, prior_receipt=receipt, fresh_fsck=checked,
                        partition_identical=True, comparison='exact union of allocated ranges; common holes are zero')


def _geometry(volume, partition):
    with volume.open('rb') as stream:
        boot = _read(stream, 0, 512)
    if boot[3:11] != b'EXFAT   ' or boot[510:] != b'\x55\xaa' or not 9 <= boot[108] <= 12 or boot[109] > 16 or boot[110] != 1:
        _fail('geometry', 'Only a bounded single-FAT exFAT volume is supported')
    sector, cluster = 1 << boot[108], (1 << boot[108]) << boot[109]
    fat, fat_length, heap, count, root = struct.unpack_from('<IIIII', boot, 80)
    base, sectors = struct.unpack_from('<QQ', boot, 64)
    if (base * sector != partition[0] or sectors * sector != partition[1]
            or not 1 <= count <= MAX_CLUSTERS or not 2 <= root < count + 2
            or fat < 24 or not fat_length or heap < fat + fat_length
            or (count + 2) * 4 > fat_length * sector
            or heap * sector + count * cluster > partition[1]):
        _fail('geometry', 'Invalid FAT, heap, cluster or volume bounds')
    return dict(sector=sector, cluster=cluster, fat=fat * sector,
                heap=heap * sector, count=count, root=root)


def _chain(stream, geometry, first, length, contiguous=False, *, directory=False):
    count, cluster = geometry['count'], geometry['cluster']
    expected = None if length is None else math.ceil(length / cluster)
    if expected == 0:
        if first:
            _fail('cluster_chain', 'Empty entry unexpectedly owns a cluster')
        return []
    if expected is not None and expected > count:
        _fail('cluster_chain', 'Declared length exceeds volume cluster count')
    result, seen, current = [], set(), first
    limit = min(count, (16 * CHUNK) // cluster) if directory else count
    while True:
        if not 2 <= current < count + 2 or current in seen or len(result) >= limit:
            _fail('cluster_chain', 'Invalid, cyclic or excessive complete cluster chain')
        result.append(current); seen.add(current)
        if contiguous:
            if len(result) == expected:
                return result
            current += 1
            continue
        following = struct.unpack('<I', _read(stream, geometry['fat'] + current * 4, 4))[0]
        if following >= 0xfffffff8:
            if expected is not None and len(result) != expected:
                _fail('cluster_chain', 'FAT chain terminates before the declared data length')
            return result
        if expected is not None and len(result) == expected:
            _fail('cluster_chain', 'FAT chain continues beyond the declared data length')
        current = following


def _card_path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or any(c in value for c in ('\\', '\0', ':'))
            or any(p in ('', '.', '..') for p in value.split('/'))):
        _fail('directory_name', 'Invalid or traversal-like card path')
    return value


def _validate_catalog(volume, geometry, original, partition_offset):
    """Independently reject skipped active entries, incomplete chains and aliases."""
    rows = original.get('files')
    if not isinstance(rows, list) or len(rows) > 1024:
        _fail('catalog', 'Expected the complete bounded directory catalog')
    lookup, normalized = {}, set()
    for row in rows:
        name = _card_path(row.get('path'))
        if name.casefold() in normalized:
            _fail('duplicate_entry', 'Duplicate case-insensitive logical path')
        normalized.add(name.casefold()); lookup[name] = row
        if type(row.get('directory')) is not bool or type(row.get('contiguous')) is not bool:
            _fail('catalog', 'Directory and contiguous flags must be booleans')
        length = _integer(row.get('bytes'), 'entry bytes', maximum=volume.stat().st_size)
        if _integer(row.get('valid_bytes'), 'valid bytes') != length:
            _fail('uninitialized_entry', 'Entry contains uninitialized data bytes')
        _integer(row.get('first_cluster'), 'first cluster', maximum=0xffffffff)
        if not row['directory'] and name.lower().endswith('.nef') and not 0 < length <= MAX_NEF_BYTES:
            _fail('nef_length', 'NEF length exceeds the bounded shared extractor')
    owners, found, chains = {}, set(), {}
    with volume.open('rb') as stream:
        def own(clusters, owner):
            for number in clusters:
                if number in owners:
                    _fail('cluster_overlap', 'A cluster belongs to multiple files/directories')
                owners[number] = owner

        def walk(first, prefix='', length=None, contiguous=False, depth=0):
            if depth > 6:
                _fail('directory', 'Directory depth bound reached')
            clusters = _chain(stream, geometry, first, length, contiguous, directory=True)
            own(clusters, prefix or '<root>')
            data = b''.join(_read(stream, geometry['heap'] + (c - 2) * geometry['cluster'], geometry['cluster']) for c in clusters)
            at = 0
            while at + 32 <= len(data):
                kind = data[at]
                if kind == 0:
                    return
                if not kind & 0x80:
                    at += 32; continue
                if kind in (0x81, 0x82, 0x83) and not prefix:
                    if kind != 0x83:
                        start, size = struct.unpack_from('<IQ', data, at + 20)
                        own(_chain(stream, geometry, start, size), f'<system-{kind:x}>')
                    at += 32; continue
                if kind != 0x85:
                    _fail('directory', 'Unknown active directory entry prevents a complete catalog')
                secondary = data[at + 1]
                end = at + 32 * (secondary + 1)
                if not 2 <= secondary <= 18 or end > len(data):
                    _fail('directory', 'Truncated directory entry set')
                group = data[at:end]
                if group[32] != 0xc0 or any(group[i] != 0xc1 for i in range(64, len(group), 32)):
                    _fail('directory', 'Duplicate/unknown stream or filename entry')
                name_length = group[35]
                if not 1 <= name_length <= 255 or secondary != 1 + math.ceil(name_length / 15):
                    _fail('directory', 'Filename entries do not match the declared name')
                name = b''.join(group[i + 2:i + 32] for i in range(64, len(group), 32))[:name_length * 2].decode('utf-16le')
                name = _card_path(prefix + name)
                attributes = struct.unpack_from('<H', group, 4)[0]
                valid, start, size = struct.unpack_from('<Q', group, 40)[0], struct.unpack_from('<I', group, 52)[0], struct.unpack_from('<Q', group, 56)[0]
                observed = dict(path=name, directory=bool(attributes & 0x10), valid_bytes=valid,
                                first_cluster=start, bytes=size, contiguous=bool(group[33] & 2))
                if observed != lookup.get(name) or name in found:
                    _fail('catalog', 'Shared catalog differs from complete original directory entries')
                found.add(name)
                if observed['directory']:
                    walk(start, name + '/', size, observed['contiguous'], depth + 1)
                else:
                    chain = _chain(stream, geometry, start, size, observed['contiguous'])
                    own(chain, name); chains[name] = chain
                at = end
        walk(geometry['root'])
    if found != set(lookup):
        _fail('catalog', 'Catalog does not cover the actual directory tree exactly')
    result = {}
    for name, chain in chains.items():
        remaining, extents = lookup[name]['bytes'], []
        for cluster in chain:
            offset = partition_offset + geometry['heap'] + (cluster - 2) * geometry['cluster']
            amount = min(remaining, geometry['cluster'])
            if extents and extents[-1]['offset'] + extents[-1]['length'] == offset:
                extents[-1]['length'] += amount
            else:
                extents.append(dict(offset=offset, length=amount))
            remaining -= amount
        result[name] = extents
    return result


def _compare_export(card, path, extents, length, expected_sha):
    if path.stat().st_size != length or _sha(path) != expected_sha:
        _fail('export_identity', 'Shared extraction receipt does not match exported bytes')
    consumed = 0
    with card.open('rb') as source, path.open('rb') as exported:
        for extent in extents:
            for offset in range(0, extent['length'], CHUNK):
                size = min(CHUNK, extent['length'] - offset)
                if _read(source, extent['offset'] + offset, size) != _read(exported, consumed, size):
                    _fail('card_bytes', 'Export differs from original card extents')
                consumed += size
    if consumed != length:
        _fail('card_bytes', 'Card extents do not cover the entire exported file')


def export_run(run_dir, output_dir, *, firmware):
    """Export observed files; never assign native jobs or pre/post-trigger roles."""
    run = Path(run_dir).resolve()
    output_arg = Path(output_dir)
    if output_arg.exists() or output_arg.is_symlink():
        _fail('output_exists', 'Use a fresh output directory; existing evidence is preserved')
    output, program = output_arg.resolve(), Path(firmware).resolve()
    record_path = run / 'result.json'
    card = run / 'capture-card.raw'
    # Establish the same baseline before ANY preflight content read. Taking
    # it after hashing would miss a change between that hash and this stat.
    watched = {p: _identity(p) for p in (record_path, program, card)}
    native = _json(record_path)
    if (native.get('owned_qemu_stopped') is not True or native.get('error') is not None
            or native.get('cpu_register_redirection') is not False or native.get('ram_patches') != []
            or ('input_unchanged' in native and native['input_unchanged'] is not True)):
        _fail('run_state', 'Require a stopped fault-free run without runtime code/result replacement')
    source_sha = native.get('firmware_sha256')
    executed_sha = native.get('executed_program_sha256', source_sha)
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value) for value in (source_sha, executed_sha)):
        _fail('program_identity', 'Source and executed program identities must be explicit SHA256 values')
    if _sha(_regular(program)) != executed_sha:
        _fail('program_identity', 'Executed input program differs from its recorded identity')
    declared = native.get('private_virtual_card', {})
    if (not isinstance(declared, dict) or Path(declared.get('path', '')).resolve() != card
            or type(declared.get('bytes')) is not int or declared['bytes'] != CARD_BYTES
            or declared.get('format') != 'raw' or declared.get('camera_files_supplied') is not False):
        _fail('card_identity', 'Run does not identify this fresh regular-file virtual card')
    partition = _partition(card)
    base = Path(os.path.commonpath((run, output)))
    if base == Path(base.anchor):
        _fail('output_scope', 'Run and export need a shared private evidence parent')
    if any(_identity(p) != old for p, old in watched.items()):
        _fail('input_changed', 'Run input changed during preflight validation')
    for p in (card.with_suffix('.audit.json'), card.with_name(card.stem + '-after-volume.raw')):
        if p.exists():
            watched[p] = _identity(p)
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema='z8-nef-export-v1', status='failed', native_accepted=False,
                  writer_mapping_verified=False, output_order_basis='directory_observation_not_writer',
                  run=str(run), output=str(output), audit_base_dir=str(base),
                  firmware_rechecked=True, executed_program_rechecked=True,
                  source_main_sha256=source_sha, executed_program_sha256=executed_sha,
                  derived_program=executed_sha != source_sha,
                  program_provenance={k: native[k] for k in ('patch_provenance', 'program_provenance') if k in native},
                  original_catalog=None, issues=[],
                  storage=dict(kind='guest_nvme_export', card_path=card.relative_to(base).as_posix(),
                               sector_size=512, catalog_complete=False, nef_files=[]))
    try:
        volume, checked = _volume(card, output, partition)
        report['filesystem_audit'] = checked
        geometry = _geometry(volume, partition)
        report['storage']['sector_size'] = geometry['sector']
        original = catalog(volume)
        report['original_catalog'] = original
        (output / 'original-catalog.json').write_text(json.dumps(original, indent=2) + '\n')
        for index, entry in enumerate(original['files']):
            if not entry.get('directory') and str(entry.get('path', '')).lower().endswith('.nef'):
                ordinal = len(report['storage']['nef_files'])
                target = output / f'file-{ordinal + 1:06}.NEF'
                report['storage']['nef_files'].append(dict(file_id=f'export-{ordinal + 1:06}',
                    path=target.relative_to(base).as_posix(), bytes=entry.get('bytes'),
                    output_index=ordinal, directory_entry_index=index, card_path=entry.get('path'),
                    export_status='not_exported', writer_mapping_verified=False))
        spans = _validate_catalog(volume, geometry, original, partition[0])
        for row in report['storage']['nef_files']:
            target = base / row['path']
            row['extents'] = spans[row['card_path']]
            receipt = extract_file(volume, row['card_path'], target)
            if receipt['bytes'] != row['bytes']:
                _fail('export_identity', 'Extractor length differs from the original catalog')
            _compare_export(card, target, row['extents'], row['bytes'], receipt['sha256'])
            row.update(sha256=receipt['sha256'], export_status='exported')
        # Revalidate the entire card/volume, not just exported data, so a stale
        # directory cannot silently hide a newly added or changed NEF entry.
        _same_region(card, volume, *partition)
        report['storage']['catalog_complete'] = True
        report['status'] = 'exported' if report['storage']['nef_files'] else 'incomplete'
        if not report['storage']['nef_files']:
            report['issues'].append(dict(code='no_nef', message='Complete catalog contains no NEF files'))
    except (ExportError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report['status'] = 'failed'
        report['storage']['catalog_complete'] = False
        report['issues'].append(dict(code=getattr(error, 'code', 'invalid_evidence'), message=str(error)))
    try:
        changed = any(_identity(p) != old for p, old in watched.items())
    except (OSError, ExportError):
        changed = True
    if changed:
        report['status'] = 'failed'; report['storage']['catalog_complete'] = False
        report['issues'].append(dict(code='input_changed', message='Original run artifact changed during export'))
    report['exporter_sha256'] = _sha(Path(__file__))
    with (output / 'export-manifest.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--firmware', type=Path, required=True, help='Actual executed input program, original or explicitly fingerprinted derivative')
    args = parser.parse_args(argv)
    try:
        report = export_run(args.run, args.output, firmware=args.firmware)
    except (ExportError, OSError, ValueError) as error:
        report = dict(status='failed', native_accepted=False,
                      issues=[dict(code=getattr(error, 'code', 'invalid_evidence'), message=str(error))])
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'exported' else 2 if report['status'] == 'incomplete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
