# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned full-QEMU C10 service/media experiment; never a camera updater.

The original MAIN is input-only. Version-guarded guest patches and reserved
RAM are explicit. NP3 arrives through a scoped simulated file-input service;
the original A64 parser and authored guest media code perform the operations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time

from z8_run import PreparedGuest
from z8_guest import ROOT
from z8_boot import boot, load_bindings
from probe_adc_capture import at
from service_z9_mcu import Peer
from service_serial8 import WritePeer
from z8_properties import load_model_profile, make_boundary_peer
from z8_native_call import invoke
from z8_picture_control import (make_np3, make_flexible_np3, DATA_BASE, INPUT, RECORDS,
                               _record, _keys)
from z8_c10_image import build_experiment
from z8_c10_memory import (NATIVE_HEAP_RECORDS, GENERAL_BASE, GENERAL_USABLE_BYTES,
                           SECOND_HEAP_BASE, SECOND_HEAP_BYTES)
from z8_task_context import wait_for_task_context, read_current_context


def digest(data): return hashlib.sha256(data).hexdigest()


def prepare_code_mapping(guest):
    """Register RX code and RW/NX data after the original gap unmap.

    The runtime mapper owns its page-table bitmap and performs native TLB
    maintenance. Mapping alone does not reserve the data from other heaps.
    """
    guest.rsp('z1,40160000,4')
    cpu, registers = at(guest, 0x4016e6a8)
    context = 0xa04b58d00
    before = guest.read_bytes(context, 0x40)
    root, pool, pages = struct.unpack_from('<QQI', before)
    with guest.firmware.open('rb') as stream:
        stream.seek(0x8000)
        descriptor = stream.read(0x160)
    root_bytes = (struct.unpack_from('<Q', descriptor, 0x100)[0] + 4095) & ~4095
    pool_bytes = (struct.unpack_from('<Q', descriptor, 0x118)[0] + 4095) & ~4095
    if (cpu != 0 or before[0x38] != 0 or
            root != struct.unpack_from('<Q', descriptor, 0x108)[0] or
            pool != root + root_bytes or pages != pool_bytes // 4096):
        raise RuntimeError('Original runtime MMU context does not match the pinned layout')
    # These 128 bitmap bits are explicitly initialized by 40172110/40172118.
    free = [n for n in range(min(pages, 128))
            if not (before[0x14 + n // 8] & (1 << (n % 8)))]
    if len(free) < 4:
        raise RuntimeError('Original runtime page-table pool has insufficient free pages')
    stack_base, stack_size = struct.unpack_from('<QQ', descriptor, 8)
    if not stack_base + 0x1000 <= registers['SP'] <= stack_base + stack_size:
        raise RuntimeError('Runtime mapping requires the verified CPU0 startup stack')

    def leaf(va):
        table = root
        for level, shift in ((1, 30), (2, 21), (3, 12)):
            if not (root <= table < root + root_bytes or pool <= table < pool + pool_bytes):
                raise RuntimeError('Page walk escaped the original table allocation')
            address = table + ((va >> shift) & 511) * 8
            value = struct.unpack('<Q', guest.read_bytes(address, 8))[0]
            if not value & 1 or level == 3 or value & 3 != 3:
                return dict(level=level, address=hex(address), value=hex(value))
            table = value & 0xffffff000

    ranges = ((0x45000000, 0x10000, 0xf, 0x7cf),
              (DATA_BASE, 0x40000, 0xc, 0x6000000000074f))
    preserved = {hex(va): leaf(va) for va in (0x40168000, 0x41647000)}
    before_walks = {}
    for va, size, _, _ in ranges:
        for page in range(va, va + size, 4096):
            row = leaf(page)
            if int(row['value'], 16) & 1:
                raise RuntimeError('C10 simulation range already has a valid mapping')
            before_walks[hex(page)] = row
    for va, size, flags, _ in ranges:
        call = invoke(guest, 0x40172b88,
                      [context, va, size, va, flags, 1, 0xffffffff],
                      cpu=cpu, stack_top=registers['SP'], isolated=True)
        if call['result_x0'] != 0:
            raise RuntimeError('Original runtime mapper rejected the C10 mapping')
    after_walks = {}
    for va, size, _, attributes in ranges:
        for page in range(va, va + size, 4096):
            row = leaf(page)
            if row['level'] != 3 or int(row['value'], 16) != page | attributes:
                raise RuntimeError('C10 PTE physical address or RX/RW-NX attributes differ')
            after_walks[hex(page)] = row
    if any(leaf(int(va, 16)) != old for va, old in preserved.items()):
        raise RuntimeError('C10 mapping changed original text or rodata permissions')
    guest.data['c10_code_mapping'] = dict(native_mapper='0x40172b88',
        after_native_gap_unmap_pc='0x4016e6a8', context=hex(context),
        context_before=before.hex(), context_after=guest.read_bytes(context, 0x40).hex(),
        checked_pool_bytes=pool_bytes, free_pages_before=len(free),
        mappings=[dict(address=hex(va), physical=hex(va), bytes=size, flags=flags)
                  for va, size, flags, _ in ranges],
        ptes_before=before_walks, ptes_after=after_walks,
        original_permissions_preserved=preserved,
        physical_camera_region_verified=False, native_data_allocator_reservation_verified=False)
    # Stock boot's transport entry cleanup expects this breakpoint to exist.
    guest.rsp('Z1,40160000,4')


def write_bytes(guest, address, data):
    """Write only owned guest RAM. NOR is populated exclusively at startup."""
    if not DATA_BASE <= address <= address + len(data) <= DATA_BASE + 0x40000:
        raise ValueError('Runtime writes must stay in owned simulation RAM')
    for offset in range(0, len(data), 4096):
        part = data[offset:offset + 4096]
        guest.qt_command(f'write {address + offset:#x} {len(part):#x} 0x{part.hex()}')
        if guest.read_bytes(address + offset, len(part)) != part:
            raise RuntimeError('Guest write did not match physical readback')
        if 'Z9_UNKNOWN' in guest.log():
            raise RuntimeError('Guest input reached an unknown device')


class Session:
    def __init__(self, guest):
        self.g = guest
        self.call_cpu = None
        self.explicit_stop = False
        self.peer = Peer(guest, bindings=load_bindings(guest), limit_frames=1024,
                         config_reply=True, all_mode_replies=True)
        self.clock = make_boundary_peer(guest)
        self.spi8 = WritePeer(guest)
        self.spi9 = WritePeer(guest, channel=9, wire_bits=9, allow_dummy_rx=True)

    def advance(self, running):
        g = self.g
        if not running:
            handled = self.peer.handle() or self.explicit_stop
            self.explicit_stop = False
            return handled
        self.clock.tick()
        channels = json.loads(g.qmp.command('qom-get', path='/machine', property='serial-state'))
        if self.peer.clock_if_ready(channels):
            self.explicit_stop = True
            return True
        self.spi8.clock_if_ready(channels[8]); self.spi9.clock_if_ready(channels[9])
        return False

    def run(self, seconds):
        self.call_cpu = None
        g = self.g; deadline = time.monotonic() + seconds
        g.qmp.command('cont')
        while time.monotonic() < deadline:
            if 'Z9_UNKNOWN' in g.log(): raise RuntimeError('Unmodeled hardware access')
            running = g.qmp.command('query-status')['running']
            handled = self.advance(running)
            if not running:
                if not handled: raise RuntimeError('Unexpected guest stop')
                g.qmp.command('cont')
            time.sleep(.002)
        g.qmp.command('stop'); self.peer.handle()

    def call(self, address, *args, timeout=10, observers=None):
        if self.call_cpu is None or not read_current_context(self.g, self.call_cpu)['callable']:
            self.call_cpu, _ = wait_for_task_context(self)
        return invoke(self.g, address, args, cpu=self.call_cpu, stack_top=DATA_BASE + 0x3fff0,
                      timeout=timeout, isolated=True, interrupt_policy='preserve',
                      observers=observers)['result_x0']


def verify_loaded(session, experiment):
    g = session.g
    for patch in experiment['patches']:
        if g.read_bytes(patch['address'], len(patch['replacement'])) != patch['replacement']:
            raise ValueError(f'Loaded patch mismatch: {patch["address"]:#x}')
    for region in experiment['regions']:
        # Mutable data can be initialized by native boot. Code must be exact.
        if region['permissions'] == 'rx' and g.read_bytes(region['address'], len(region['data'])) != region['data']:
            raise ValueError('Loaded executable region differs from its guarded plan')
    verify_memory_reservation(session)
    g.data['c10_media'] = dict(driver='authored_guest_a64_nand', blocks=experiment['media']['nand_blocks'],
        reservation='isolated simulation image only; not physical camera allocation')


def verify_memory_reservation(session):
    """Check what the original constructors registered, not just the patch file."""
    g = session.g
    content = g.read_bytes(NATIVE_HEAP_RECORDS, 0x40)
    if len(content) != 0x40:
        raise RuntimeError('Incomplete original heap records')
    rows = []
    for index, (base, size) in enumerate(((GENERAL_BASE, GENERAL_USABLE_BYTES),
                                        (SECOND_HEAP_BASE, SECOND_HEAP_BYTES))):
        handle, actual_size, flags, actual_base = struct.unpack_from('<QIIQ', content, index * 0x20)
        g.check(f'original heap constructor preserves C10 reservation {index}',
                handle != 0 and actual_size == size and actual_base == base)
        rows.append(dict(handle=hex(handle), address=hex(actual_base), bytes=actual_size, flags=flags))
    g.data['c10_native_heaps'] = rows
    g.data['c10_ram_reservation_verified'] = True


def current_selection(session, mode):
    if mode not in (0, 1):
        raise ValueError('Current Picture Control mode must be 0 or 1')
    selector, params = DATA_BASE + 0x3c000, DATA_BASE + 0x3c010
    getter = 0x410a7b58 if mode == 0 else 0x410a7d10
    if session.call(getter, selector, params) != 1:
        raise RuntimeError('Original current Picture Control getter failed')
    content = session.g.read_bytes(params, 0x158)
    if len(content) != 0x158:
        raise RuntimeError('Incomplete original current Picture Control object')
    return session.g.rd(selector), content


def record_bytes(g, slot, mode):
    content = g.read_bytes(_record(slot, mode), 0x400)
    if len(content) != 0x400:
        raise RuntimeError('Incomplete Picture Control record')
    return content


def capture_selection_notification(guest, registers):
    """Read the native callback payload; never substitute a callback result."""
    pointer = registers['X01']
    if registers['X00'] != 0x04300001 or not 0xa00000000 <= pointer <= 0xa40000000 - 8:
        raise RuntimeError('Unexpected native selection notification')
    payload = struct.unpack('<II', guest.read_bytes(pointer, 8))
    if payload != (1, 0x40000410):
        raise RuntimeError('Native selection notification has a different ID list')
    return dict(event='0x04300001', count=payload[0], ids=[hex(payload[1])],
                callback=hex(struct.unpack('<Q', guest.read_bytes(0xa03b9d858, 8))[0]))


def expected_flexible_extra(seed):
    """Independent semantic oracle for the authored 0310 fixture's tag values."""
    grading = b''.join(struct.pack('<HBB', 0x8000 + seed + i * 17,
                                  128 + seed % 16 + i, 112 + i) for i in range(3))
    return (bytes([1] * 5 + [144 + seed % 16]) + bytes(range(96 + seed % 16, 120 + seed % 16)) +
            grading + bytes((128 + seed % 16, 112 + seed % 16)))


def import_fixture(session, entry, slot, mode, *, flexible=False):
    """Import one authored NP3 and check every other independent record."""
    _record(slot, mode)  # Validate before any guest input is written.
    g = session.g
    before = {(s, m): record_bytes(g, s, m) for m in (0, 1) for s in range(1, 11)
              if (s, m) != (slot, mode)}
    name = f'C{slot} LAB M{mode}'
    seed = slot + mode * 31
    blob = (make_flexible_np3 if flexible else make_np3)(name, seed)
    write_bytes(g, INPUT, blob)
    ok = session.call(entry, INPUT, len(blob), slot, mode)
    g.check(f'original NP3 parser and native setter slot {mode}:{slot}', ok == 1)
    record = record_bytes(g, slot, mode)
    g.check(f'imported record identity and native fields {mode}:{slot}',
            struct.unpack_from('<III', record) == (1, *_keys(slot, mode)) and
            record[0x10:0x24] == name.encode().ljust(20, b'\0') and record[0x24] == 1 and
            struct.unpack_from('<H', record, 0x120)[0] == (32 if flexible else 1))
    if flexible:
        g.check(f'original Flexible Color extra conversion {mode}:{slot}',
                record[0x370:0x39c] == expected_flexible_extra(seed))
    g.check(f'all other records survive import {mode}:{slot}',
            all(record_bytes(g, s, m) == value for (s, m), value in before.items()))


def select_control(session, entry, slot, mode):
    """Require both the native selector and native parameter object to agree."""
    address = _record(slot, mode)
    g = session.g
    before = {(s, m): record_bytes(g, s, m) for m in (0, 1) for s in range(1, 11)}
    wanted = 30 + slot + mode * 50
    if entry == 0x403f96a8:
        # The original GUI wrapper takes an INTERNAL selector, not a display
        # code or a mode argument. Partial failure must never count as success.
        observers = {0x40f25960: capture_selection_notification}
        g.check(f'native high selection C{slot} mode {mode}',
                session.call(entry, wanted, timeout=30, observers=observers) == 1)
        setting = 0x07000633 if mode == 0 else 0x0700122b
        code = 40 if slot == 10 else slot + 27
        g.check(f'native selection setting updated mode {mode}', session.call(0x410df224, setting) == code)
    else:
        g.check(f'C{slot} selection mode {mode}', session.call(entry, slot, mode) == address)
    selector, params = current_selection(session, mode)
    g.check(f'original current getter sees C{slot} mode {mode}',
            selector == wanted and params[:0x114] == before[slot, mode][0x10:0x124])
    if struct.unpack_from('<H', before[slot, mode], 0x120)[0] == 32:
        g.check(f'native Flexible Color current extra {mode}:{slot}',
                params[0x12c:0x158] == before[slot, mode][0x370:0x39c])
    g.check(f'all records survive selection {mode}:{slot}',
            all(record_bytes(g, s, m) == value for (s, m), value in before.items()))


def selection_snapshot(session):
    """Compare each target current object with its persisted source record."""
    g = session.g
    values = struct.unpack('<4I', g.read_bytes(RECORDS + 48, 16))
    slots, sources = values[:2], values[2:]
    if any(not 1 <= s <= 10 for s in slots) or sources[0] != 0 or sources[1] not in (0, 1):
        raise RuntimeError('This acceptance requires two valid selected custom records')
    selectors, hashes, extra_hashes = [], [], []
    for target, (slot, source) in enumerate(zip(slots, sources)):
        selector, params = current_selection(session, target)
        g.check(f'current target {target} matches persisted source {source}',
                selector == 30 + slot + source * 50 and
                params[:0x114] == record_bytes(g, slot, source)[0x10:0x124])
        if struct.unpack_from('<H', params, 0x110)[0] == 32:
            g.check(f'Flexible Color current extra survives target {target}',
                    params[0x12c:] == record_bytes(g, slot, source)[0x370:0x39c])
        selectors.append(selector); hashes.append(digest(params[:0x114]))
        extra_hashes.append(digest(params[0x12c:]))
    return dict(schema=2, slots=list(slots), sources=list(sources), selectors=selectors,
                primary_sha256=hashes, extra_sha256=extra_hashes)


def verify_settled_selection(session, expected, phase):
    """Recheck both original current objects after queued work can execute.

    A correct DSID and immediate setter return do not prove that the original
    refresh task accepts C10. These two host seconds are a scheduling window,
    not a guest timing or physical-camera latency measurement.
    """
    session.run(2.0)
    actual = selection_snapshot(session)
    session.g.check(f'complete C10 selection survives scheduled refresh: {phase}',
                    actual == expected)
    session.g.data.setdefault('c10_settled_selection', []).append(
        dict(phase=phase, wall_pacing_seconds=2.0, selection=actual))


def set_native_integer(session, key, value):
    # 41015d6c writes its changed flag through mandatory x2, including on
    # unchanged values. Leaving it in the interrupted context is invalid.
    changed = DATA_BASE + 0x3c200
    session.g.check(f'native integer setting {key:#x} accepted',
                    session.call(0x41015d6c, key, value, changed) == 1)
    session.g.check(f'native integer setting {key:#x} readback',
                    session.call(0x410df224, key) == value)


def apply_restored_native_selection(session):
    """Lab startup application uses only metadata read by the guest from NAND.

    Lower-level LOAD is verified first. This separately replays original GUI
    setting/notification APIs so their DSIDs agree with the restored current
    objects. Expected host hashes never determine what gets applied.
    """
    state = selection_snapshot(session)
    slots, sources = state['slots'], state['sources']
    if sources[1] == 0 and slots[1] != slots[0]:
        raise RuntimeError('Native follow-photo UI cannot apply different source slots')
    follow = int(sources[1] == 0)
    set_native_integer(session, 0x0300122c, follow)
    select_control(session, 0x403f96a8, slots[0], 0)
    if sources[1] == 1:
        select_control(session, 0x403f96a8, slots[1], 1)
    session.g.check('native UI application preserves restored source relationship',
                    selection_snapshot(session) == state)
    session.g.data['c10_native_selection_applied_after_restore'] = True


def run(args, *, after_phase=None):
    """Run media acceptance, optionally continuing a separately recorded UI probe."""
    props = load_model_profile()['properties']
    guest = PreparedGuest(args.firmware, args.qemu, args.raw, args.output, properties=props,
                          nand_image=args.media, create_nand_image=args.phase == 'create')
    try:
        experiment = build_experiment(args.firmware.read_bytes(), guest.out / 'simulation-image')
        guest.command[guest.command.index('-kernel') + 1] = str(experiment['derived_main'])
        for region in experiment['loader_regions']:
            guest.command += ['-device', f'loader,file={region["path"]},addr={region["address"]:#x},force-raw=on']
        guest.data['classification'] = 'z8_c10_guest_extension_experiment'
        guest.data['c10_image'] = experiment['metadata']
    except BaseException as exc:
        guest.__exit__(type(exc), exc, None)
        raise
    plan = experiment['plans']['core']
    with guest as g:
        g.data['c10_phase'] = args.phase
        g.data['acceptance_scope'] = dict(native_parser=True, native_setting_functions=True,
            whole_camera_menu_clicks=False, real_sd_import=False, physical_hardware=False)
        prepare_code_mapping(g)
        boot(g, lambda text: print(text, flush=True))
        session = Session(g)
        try:
            session.run(args.settle_seconds)
            g.data['native_label_callbacks'] = {hex(address): hex(struct.unpack('<Q', g.read_bytes(address, 8))[0])
                for address in (0xa02f2a598, 0xa02f2a5a0)}
            print('Native label callbacks', g.data['native_label_callbacks'], flush=True)
            print('Verifying startup-loaded guest extension', flush=True)
            verify_loaded(session, experiment)
            entry = plan['entries']
            if args.phase == 'create':
                g.check('blank C10 media does not report restoration', session.call(entry['load']) == 0)
                for mode in (0, 1):
                    for slot in range(1, 11):
                        import_fixture(session, entry['import_np3'], slot, mode, flexible=slot >= 9)
                    select_control(session, 0x403f96a8, 10, mode)
                    address = session.call(entry['list'], mode)
                    codes = struct.unpack('<10I', g.read_bytes(address + 0x7c, 40))
                    g.check(f'ten independent custom choices mode {mode}', len(set(codes)) == 10 and codes[-1] == 40)
                g.data['c10_follow_photo_before_test'] = session.call(0x410df224, 0x0300122c)
                g.data['c10_native_capture_mode'] = session.call(0x410d9784)
                g.check('native mode permits follow-photo selection', g.data['c10_native_capture_mode'] not in (1, 2))
                set_native_integer(session, 0x0300122c, 1)
                # Exercise a real change through C9 and back to C10, including
                # the original higher setting write and notification path.
                select_control(session, 0x403f96a8, 9, 0)
                select_control(session, 0x403f96a8, 10, 0)
                saved = session.call(entry['save'], timeout=30)
                g.check('guest NAND save committed', saved == 1)
                selected = selection_snapshot(session)
                g.check('C10 mirror source is included in saved state', selected['selectors'] == [40, 40])
                verify_settled_selection(session, selected, 'create_native_selection')
                expected = {f'{mode}:{slot}': digest(record_bytes(g, slot, mode))
                            for mode in (0, 1) for slot in range(1, 11)}
                (g.out / 'expected-records.json').write_text(json.dumps(expected, indent=2) + '\n')
                (g.out / 'expected-selection.json').write_text(json.dumps(selected, indent=2) + '\n')
            else:
                if args.expected is None: raise ValueError('Cold restore needs the prior read-only expected hashes')
                expected = json.loads(args.expected.read_text())
                selection_reference = args.expected.parent / 'expected-selection.json'
                g.check('new QEMU process restores by guest NAND reads', session.call(entry['load'], timeout=30) == 1)
                for mode in (0, 1):
                    for slot in range(1, 11):
                        g.check(f'cold content {mode}:{slot}', digest(record_bytes(g, slot, mode)) == expected[f'{mode}:{slot}'])
                    if selection_reference.exists():
                        listing = session.call(entry['list'], mode)
                        codes = struct.unpack('<10I', g.read_bytes(listing + 0x7c, 40))
                        g.check(f'cold native list publishes ten custom choices {mode}',
                                codes == (*range(28, 37), 40))
                    if not selection_reference.exists():
                        # Read-only compatibility for pinned historical schema1
                        # GUI probes; normal current experiments write schema2.
                        selector, params = current_selection(session, mode)
                        g.check(f'cold native C10 selection mode {mode}',
                                selector == 40 + mode * 50 and params[:0x114] == record_bytes(g, 10, mode)[0x10:0x124])
                if selection_reference.exists():
                    selected = json.loads(selection_reference.read_text())
                    g.check('guest restored source groups and current parameters', selection_snapshot(session) == selected)
                    apply_restored_native_selection(session)
                    verify_settled_selection(session, selected, 'cold_native_selection')
                g.data['cold_restore_verified'] = True
            g.data['c10_media_state'] = json.loads(g.qmp.command('qom-get', path='/machine', property='z8-nand-image-state'))
            g.data['c10_service_media_passed'] = True
            print('C10 guest service/media phase passed', flush=True)
            if getattr(args, 'gui_test', False):
                from z8_run import menu_profile
                from z8_c10_menu_acceptance import run_menu_acceptance
                gui_reference = selection_snapshot(session)
                g.data['c10_main_menu_acceptance'] = run_menu_acceptance(
                    session, expected_menu_rgb_sha256=menu_profile(g)['menu_rgb_sha256'],
                    output=g.out / 'main-menu-gui')
                g.check('guest saves actual MCU C10 selection', session.call(entry['save'], timeout=30) == 1)
                verify_settled_selection(session, gui_reference, 'MCU_selection_and_save')
                g.data['acceptance_scope']['whole_camera_menu_clicks'] = True
                g.data['c10_media_state_after_gui'] = json.loads(g.qmp.command(
                    'qom-get', path='/machine', property='z8-nand-image-state'))
            if after_phase is not None:
                after_phase(session)
        finally:
            g.qmp.command('stop'); session.peer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--raw', type=Path, default=ROOT / '.local/input.raw')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--media', type=Path, required=True)
    parser.add_argument('--phase', choices=('create', 'restore'), required=True)
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--settle-seconds', type=float, default=20)
    parser.add_argument('--gui-test', action='store_true',
                        help='Exercise original MCU main-menu C9/C10 selection and capture native frames')
    args = parser.parse_args()
    if not 0 <= args.settle_seconds <= 120: parser.error('Invalid settle duration')
    run(args)


if __name__ == '__main__': main()
