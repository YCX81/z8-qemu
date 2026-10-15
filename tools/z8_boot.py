# SPDX-License-Identifier: GPL-2.0-or-later
"""Identity-bound Z8 boot through original serial, MCU and event paths.

Bindings are static candidates. Stages become accepted only after the native
checks complete; no task flags, CPU registers or guest code are replaced.
"""
import json
from pathlib import Path
import re
import struct
import time

from boot import key_body
from deliver_mcu_frame import deliver
from mcu_frames import frames, status_body
from probe import sha
from probe_adc_capture import at
from probe_tasks import settle
from receive_mcu_tx import receive

PROFILE = Path(__file__).resolve().parents[1] / 'profiles/z8-c311-bindings.json'


def load_configuration(g, profile_path=PROFILE):
    profile = json.loads(Path(profile_path).read_text())
    firmware = Path(g.firmware)
    if (profile.get('model') != 'Nikon Z8' or profile.get('version') != 'C3.11'
            or firmware.stat().st_size != profile['main_bytes']
            or sha(firmware) != profile['main_sha256']):
        raise ValueError('Z8 MCU bindings require the pinned original C3.11 MAIN')
    return profile


def load_bindings(g):
    return load_configuration(g)['bindings']


def strict_check(g):
    cancelled=getattr(g,'cancel_event',None)
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError('Z8 run cancelled')
    if 'Z9_UNKNOWN' in g.log():
        raise RuntimeError('Unmodeled hardware access during Z8 boot')


def observe(g, points, *, timeout=30):
    pending = set(points)
    for pc in pending:
        g.rsp(f'Z1,{pc:x},4')
    deadline = time.monotonic() + timeout
    try:
        strict_check(g)
        while pending and time.monotonic() < deadline:
            g.qmp.command('cont')
            while g.qmp.command('query-status')['running'] and time.monotonic() < deadline:
                strict_check(g)
                time.sleep(.01)
            g.qmp.command('stop')
            strict_check(g)
            for cpu in range(4):
                text = g.qmp.hmp('info registers', cpu)
                pc = int(re.search(r'\bPC=([0-9a-fA-F]+)', text)[1], 16)
                if pc in pending:
                    g.rsp(f'z1,{pc:x},4')
                    pending.remove(pc)
        if pending:
            raise TimeoutError('Original Z8 boot callbacks missing: ' +
                               ', '.join(hex(pc) for pc in sorted(pending)))
    finally:
        for pc in pending:
            g.rsp(f'z1,{pc:x},4')


def boot(g, progress=print, *, status68=None):
    profile = load_configuration(g)
    power_body = status_body({0x1c: 2})
    if status68 is not None:
        from z8_video_mcu import status68_input
        power_body = status68_input(power_body, status68)
    bindings, config = profile['bindings'], profile['boot']
    points = config['points']
    record = dict(profile_sha256=sha(PROFILE), classification='native_Z8_boot_with_synthetic_MCU_inputs',
                  stages=[], complete=False, menu_verified=False, liveview_verified=False,
                  physical_protocol_verified=False,
                  rx_retirement_semantics=profile['rx_retirement_semantics'])
    g.data['z8_boot'] = record
    record.update(status68_input=status68, power_status_input_body_hex=power_body.hex())

    def accepted(stage):
        strict_check(g)
        record['stages'].append(stage)

    progress('Booting original Z8 C3.11 firmware')
    strict_check(g)
    g.rsp('z1,40160000,4')
    _, regs = at(g, points['rx_start_ready'])
    strict_check(g)
    handle = struct.unpack('<Q', g.read_bytes(0xa02e80520, 8))[0]
    descriptor = struct.unpack('<Q', g.read_bytes(0xa02809148 + 0x20, 8))[0]
    record['rx_start'] = dict(pc=hex(points['rx_start_ready']), flags=hex(regs['X19']),
                              gpio_handle=hex(handle), irq_descriptor=hex(descriptor),
                              gic_enable_word=hex(g.rd(0x0d000104)),
                              gic_enable_read_scope='CPU-less QTest view; recorded without acceptance assertion',
                              protocol_callback=hex(struct.unpack('<Q', g.read_bytes(0xa02e804f0, 8))[0]))
    g.check('original Z8 RX task received startup notification', bool(regs['X19'] & 1))
    g.check('original Z8 RX GPIO handle is model-specific pin object', handle == 0xa02809148)
    g.check('original Z8 RX GPIO uses IRQ58 descriptor', descriptor == 0xa0280c1e0)
    accepted('rx_task_irq_initialized')
    deliver(g, frames(power_body)[0], prefix='z8-power-', bindings=bindings)
    _, regs = at(g, points['power_status'])
    g.check('original Z8 power status accepted', regs['X01'] == int(config['expected_power_status_event'], 0))
    _, regs = at(g, points['system_power_event'])
    g.check('original Z8 system power event', regs['X00'] == int(config['expected_system_power_event'], 0))
    address = regs['X01']
    ram_end = 0xa00000000 + g.ram_mib * 1024 * 1024
    event = dict(event=hex(regs['X00']), payload_address=hex(address),
                 semantic_scope='original status notification; event ID alone does not identify power on or off')
    if 0xa00000000 <= address and address + 12 <= ram_end:
        payload = g.read_bytes(address, 12)
        event.update(payload_hex=payload.hex(), payload_words=list(struct.unpack('<3I', payload)))
    else:
        event['payload_not_read'] = 'address outside the declared guest RAM range'
    record['system_power_event'] = event
    accepted('power_status_and_system_event')
    settle(g, 1)
    strict_check(g)
    body = bytearray(112)
    body[0] = 3
    record['battery_status_input_body_hex'] = bytes(body).hex()
    deliver(g, frames(body, family=4)[0], prefix='z8-battery-', bindings=bindings)
    # This is the original lens service send-callback return reached after
    # the synthetic battery frame. It does not establish battery health.
    at(g, points['lens_request_callback_return'])
    accepted('lens_request_callback_return_after_battery_input')
    for index in range(32):
        strict_check(g)
        wire = receive(g, prefix=f'z8-boot-out-{index:02d}-', bindings=bindings)
        if wire[0] == 13 and wire[8] in (1, 2, 3, 4):
            body = bytearray(16)
            body[0], body[4] = 0x80 | wire[8], wire[12]
            deliver(g, frames(body, family=13)[0], prefix=f'z8-boot-lens-{index:02d}-', bindings=bindings)
            _, regs = at(g, points['lens_accept'])
            payload = g.read_bytes(regs['X01'], 8)
            g.check('original Z8 lens controller accepts reply', struct.unpack_from('<I', payload)[0] == 0)
            if payload[4] == 0:
                break
    else:
        raise RuntimeError('Z8 lens controller startup did not complete')
    accepted('lens_controller_startup')
    observe(g, config['observe_after_lens'])
    accepted('post_lens_callbacks')
    deliver(g, frames(key_body(half=True), family=2)[0], prefix='z8-boot-key-', bindings=bindings)
    observe(g, config['observe_after_half_key'])
    accepted('half_press_callbacks')
    body = bytearray(16)
    body[0] = 1
    deliver(g, frames(body, family=9)[0], prefix='z8-boot-ccomp-', bindings=bindings)
    accepted('capture_composition_transport')
    # deliver() stops before the accepted frame clears RX state and writes
    # its GPIO ACK. Let those original instructions retire before a peer
    # takes over, so the manual frame cannot overlap a continuous transfer.
    at(g, bindings['rx_after_flag_clear'])
    g.check('original Z8 final RX state retires before peer handoff', g.rd(0xa02e804f8) == 0)
    accepted('capture_composition_rx_retired')
    record['complete'] = True
    progress('Original Z8 boot checkpoints accepted; display and sensor acceptance remain separate')
    return record
