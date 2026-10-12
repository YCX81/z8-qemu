# SPDX-License-Identifier: GPL-2.0-or-later
"""Construct a guarded, simulation-only MAIN and explicit loader regions.

This emits no Nikon update container. The original input is never overwritten.
NOR is populated by QEMU's initial loader, not by ordinary guest/debug writes.
"""
import hashlib
import json
from pathlib import Path

from z8_picture_control import build_plan, _assemble, BASE, DATA_BASE
from z8_picture_control_menu import build_menu_plan
from z8_picture_control_labels import build_label_plan
from z8_picture_control_list import build_list_plan
from z8_picture_control_main_menu import build_main_menu_plan
from z8_c10_media import build_media_code, SAVE, LOAD
from z8_c10_memory import reservation_patch


def _sha(data): return hashlib.sha256(data).hexdigest()


def source_offset(address, length, source_size):
    if any(type(value) is not int for value in (address, length, source_size)) or length <= 0 or source_size < 0:
        raise ValueError('Source mapping requires a nonempty integer range')
    if BASE <= address and address + length <= BASE + source_size:
        return address - BASE
    if 0xA02808000 <= address and address + length <= 0xA02AB5000:
        offset = 0x4B13000 + address - 0xA02808000
        if offset + length <= source_size:
            return offset
    raise ValueError('Patch has no verified MAIN/scatter source mapping')


def compose(source):
    plans = dict(core=build_plan(source), menu=build_menu_plan(source),
                 labels=build_label_plan(source))
    plans['list'] = build_list_plan(source, core_plan=plans['core'])
    plans['main_menu'] = build_main_menu_plan(source, core_plan=plans['core'])
    memory = reservation_patch(source)
    patches = [p for plan in plans.values() for p in plan['patches']] + [memory['patch']]
    regions = []
    tail = plans['list']['core_tail_reservation']
    overlay = plans['main_menu']['core_data_overlay']
    overlay_count = 0
    for name, plan in plans.items():
        for region in plan['regions']:
            data = bytearray(region['data'])
            if name == 'core' and region['permissions'] == 'rx':
                start = tail['address'] - region['address']
                if start <= 0 or start + tail['bytes'] != len(data) or any(data[start:]):
                    raise ValueError('List code cannot replace a nonempty or partial core tail')
                data = data[:start]
            if name == 'core' and region['permissions'] == 'rw':
                start = overlay['address'] - region['address']
                old, new = overlay['expected'], overlay['data']
                if (not old or len(old) != len(new) or not 0 <= start <= len(data) - len(old) or
                        any(old) or data[start:start + len(old)] != old):
                    raise ValueError('Main-menu data overlay is outside its verified empty reservation')
                data[start:start + len(old)] = new
                overlay_count += 1
            regions.append(dict(region, data=data))
    if overlay_count != 1:
        raise ValueError('Main-menu data overlay must have exactly one owned region')
    media = build_media_code()
    regions.append(dict(address=media['address'], data=bytearray(media['data']), permissions='rx'))
    offsets = []
    for patch in patches:
        old, new = patch['expected'], patch['replacement']
        if not old or len(old) != len(new):
            raise ValueError('Patch length must remain unchanged')
        offset = source_offset(patch['address'], len(old), len(source))
        if patch.get('source_offset', offset) != offset:
            raise ValueError('Declared source offset disagrees with verified mapping')
        if source[offset:offset + len(old)] != old:
            raise ValueError('Original patch bytes do not match')
        offsets.append((offset, offset + len(old), patch))
    offsets.sort(key=lambda item: item[0])
    if any(a[1] > b[0] for a, b in zip(offsets, offsets[1:])):
        raise ValueError('Overlapping source patches')
    regions.sort(key=lambda r: r['address'])
    spans = [(r['address'], r['address'] + len(r['data'])) for r in regions]
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('Overlapping loader regions')
    for region, (start, end) in zip(regions, spans):
        allowed = ((0x45000000 <= start < end <= 0x45010000 and region['permissions'] == 'rx') or
                   (DATA_BASE <= start < end <= DATA_BASE + 0x38000 and region['permissions'] == 'rw'))
        if not allowed:
            raise ValueError('Loader region exceeds the simulation reservation')
        if start < BASE + len(source) and end > BASE:
            raise ValueError('Loader region overlaps the MAIN image')
    for name, target in (('save_callback', SAVE), ('load_callback', LOAD)):
        address = plans['core']['entries'][name]
        owner = next((r for r in regions if r['permissions'] == 'rx' and
                      r['address'] <= address and address + 4 <= r['address'] + len(r['data'])), None)
        if address % 4 or owner is None:
            raise ValueError('Media callback must fit an aligned executable loader region')
        if target % 4 or not media['address'] <= target <= media['address'] + len(media['data']) - 4:
            raise ValueError('Media entry is outside the executable media region')
        offset = address - owner['address']
        owner['data'][offset:offset + 4] = _assemble(f'b #{target}', address)
    derived = bytearray(source)
    for start, end, patch in offsets:
        derived[start:end] = patch['replacement']
    return dict(plans=plans, patches=patches, regions=regions, image=bytes(derived),
                media=media, source_sha256=_sha(source), memory_reservation=memory['metadata'])


def build_experiment(source, output):
    """Validate everything before creating a new, non-reusable output directory."""
    composed = compose(source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    image = output / 'simulation-main-not-an-update.bin'
    image.write_bytes(composed['image'])
    loader_regions = []
    for index, region in enumerate(composed['regions']):
        path = output / f'owned-region-{index}.bin'
        path.write_bytes(region['data'])
        loader_regions.append(dict(address=region['address'], path=path,
                                   bytes=len(region['data']), sha256=_sha(region['data'])))
    metadata = dict(simulation_only=True, camera_update_container=False,
        memory_reservation=composed['memory_reservation'],
        original_sha256=composed['source_sha256'], derived_sha256=_sha(composed['image']),
        source_patches=[dict(address=hex(p['address']), name=p['name'],
                            source_offset=hex(source_offset(p['address'], len(p['expected']), len(source))),
                            expected=p['expected'].hex(), replacement=p['replacement'].hex())
                        for p in composed['patches']],
        loader_regions=[dict(r, address=hex(r['address']), path=r['path'].name) for r in loader_regions],
        state_scope='fresh isolated laboratory NAND; populated camera settings migration is unverified')
    (output / 'experiment.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return dict(composed, derived_main=image, loader_regions=loader_regions, metadata=metadata)
