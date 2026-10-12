# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded Z8 shutter tracing with original MCU inputs; never a NEF success oracle.

No task state, result, instruction or CPU register is substituted. Diagnostic
device properties are opt-in; all runtime RAM, pictures and traces stay local.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import time

from boot import key_body
from service_z9_mcu import Peer
from service_serial8 import WritePeer
from z8_boot import boot, load_bindings, load_configuration
from z8_properties import load_model_profile, make_boundary_peer
from z8_run import PreparedGuest, state
from z8_capture_bindings import load_bindings as capture_bindings, read_gpio_bindings, observation_points
from z8_capture_readiness import capture as capture_readiness

MENU_CONTACTS = {'right': (0x22, 0x80, 0), 'left': (0x23, 0x40, 0),
                 'up': (0x24, 0x20, 0), 'down': (0x25, 0x10, 0),
                 'menu': (0x43, 0, 0x8000), 'j': (0x4a, 0, 1)}


def shutter_body(half=False, full=False):
    if type(half) is not bool or type(full) is not bool or (full and not half):
        raise ValueError('Boolean half/full contacts; FULL requires HALF')
    data = bytearray(key_body(half=half)); data[8] = int(full)
    return bytes(data)


def expected_keys(half=False, full=False):
    shutter_body(half, full)
    return struct.pack('<11I', 0x200 | (int(half) << 31) | (int(full) << 30), *([0]*10))


def navigation_contact(action):
    if action not in MENU_CONTACTS:
        raise ValueError('Use menu/right/left/up/down/j; unverified ok is not accepted')
    offset, sw0, sw1 = MENU_CONTACTS[action]
    body = bytearray(key_body()); body[offset] = 1
    return bytes(body), struct.pack('<11I', 0x200 | sw0, sw1, *([0]*9))


def contact_observation(contact, keys):
    wanted = contact['expected']
    if not isinstance(wanted, bytes) or len(wanted) != 44 or len(keys) != 44:
        raise ValueError('Every contact requires an exact 44-byte decoded expectation')
    full = bool(struct.unpack_from('<I', keys)[0] & (1 << 30))
    return dict(label=contact['label'], keys_hex=keys.hex(), expected_hex=wanted.hex(),
                matched=keys == wanted, full_key_state=full, input_route='mcu_decoded_state',
                sole_trigger_verified=False,
                causal_scope='Original MCU state consumption only; a separate GPIO input may also dispatch capture')


class HostBudget:
    def __init__(self, seconds, *, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.clock(); self.seconds = seconds
        self.deadline = self.started + seconds

    def check(self, phase):
        if self.clock() >= self.deadline:
            raise TimeoutError('Total host deadline exceeded during ' + phase + ' (preparation, boot and waits included)')

    def report(self):
        elapsed = self.clock() - self.started
        return dict(limit_host_seconds=self.seconds, elapsed_host_seconds=elapsed,
                    deadline_exceeded=elapsed >= self.seconds, startup_included=True,
                    scope='Cooperative host wall-clock budget; blocking transport calls and cleanup can overrun it')


def clock_devices(peer, spi8, spi9, channels):
    if peer.clock_if_ready(channels):
        return True
    stopped8 = spi8.clock_if_ready(channels[8], resume=False)
    stopped9 = spi9.clock_if_ready(channels[9], resume=False)
    # The caller handles all observers at this pause before its only cont.
    return stopped8 or stopped9


def ordinary_work_idle(raw, jpeg, events, baseline):
    returned = [e for e in events if e['seq'] >= baseline['event_start']
                and e['kind'] == 'file_writer_return']
    return bool(raw.get('jobs', 0) > baseline['raw_jobs'] and not raw.get('pending', True)
        and raw.get('acks') == raw.get('jobs') and jpeg.get('jobs', 0) >= baseline['jpeg_jobs'] + 3
        and not jpeg.get('requested', True) and not jpeg.get('pending', 1)
        and not jpeg.get('core', 1) and jpeg.get('acks') == jpeg.get('core_acks') == jpeg.get('jobs')
        and returned and all(int(e['registers']['X00'], 16) & 0xffffffff == 0 for e in returned))


def finalize_trace(g, peer, budget, failure=None):
    trace = g.data['capture_trace']
    errors = trace.setdefault('cleanup_errors', [])
    if failure is not None:
        trace['failure'] = repr(failure)
    try:
        g.qmp.command('stop')
    except Exception as error:
        errors.append(dict(stage='stop', error=repr(error)))
    names = ['runtime-clock-state', 'raw-pipeline-state', 'engine1548-scanout-state']
    if g.data.get('capture_device_hypotheses'):
        names += ['primary-d980-state', 'engine151-raw-output-state', 'engine15e2-jpeg-state', 'capture-gpio-state',
                  'ccomp-gpio-state', 'primary-d980-single-frame-state', 'primary-d980-source-notify-state',
                  'primary-d980-prepared-start-state', 'primary-d980-channel-boundary-state',
                  'raw-source-boundary-state', 'engine150c-state', 'engine151-control-state']
    diagnostic = trace.setdefault('final_device_states', {})
    for name in names:
        try:
            diagnostic[name] = state(g, name)
        except Exception as error:
            errors.append(dict(stage=name, error=repr(error)))
    try:
        if peer is not None:
            if hasattr(peer, 'cseq'):
                trace['cseq_observer'] = peer.cseq.snapshot()
                trace['observations_recorded'] = peer.observation_count
                trace['pending_flag8_argument_observation'] = peer.flag8_predecessor is not None
            peer.close()
    except Exception as error:
        errors.append(dict(stage='peer.close', error=repr(error)))
    trace['host_budget'] = budget.report()
    with (g.out/'capture-trace.json').open('w', encoding='utf-8') as stream:
        json.dump(trace, stream, indent=2); stream.write('\n')
    if errors and failure is None:
        raise RuntimeError('Probe cleanup or diagnostic collection was incomplete; see capture-trace.json')


def contact_sequence(args):
    contacts = []
    for label, half in (('wake-half', True), ('wake-release', False)):
        contacts.append(dict(label=label, body=shutter_body(half, False),
            expected=expected_keys(half, False), gap=1_000_000_000, half=half, full=False))
    actions = args.menu_actions.split(',') if args.menu_actions else []
    if len(actions) > 64 or (args.menu_only and args.wait_capture_work):
        raise ValueError('Use at most 64 menu actions; image-work waiting requires a shutter sequence')
    for action in actions:
        body, expected = navigation_contact(action)
        contacts.append(dict(label='menu-'+action, body=body, expected=expected,
            gap=args.navigation_press_ms*1_000_000, half=False, full=False))
        contacts.append(dict(label='menu-release', body=key_body(), expected=expected_keys(),
            gap=max(args.navigation_release_ms, 10000 if action == 'menu' else 0)*1_000_000,
            half=False, full=False))
    if actions and not args.menu_only:
        for label, half in (('leave-menu-half', True), ('leave-menu-release', False)):
            contacts.append(dict(label=label, body=shutter_body(half, False),
                expected=expected_keys(half, False), gap=1_000_000_000, half=half, full=False))
    shutter_start = len(contacts)
    if not args.menu_only:
        for label, half, full in [('release', False, False), ('half', True, False), ('full', True, True),
                                  ('full-release', True, False), ('half-release', False, False)]:
            gap = args.half_seconds if label == 'half' else args.settle
            contacts.append(dict(label=label, body=shutter_body(half, full),
                expected=expected_keys(half, full), gap=gap*1_000_000_000, half=half, full=full))
    return contacts, shutter_start


def display_ready(lcd):
    return bool(lcd.get('active') and lcd.get('frames',0)>0 and
                (lcd.get('width'),lcd.get('height'))==(1024,682))


class TraceJournal:
    def __init__(self, run_id):
        self.clock_id = run_id + ':qemu_virtual'
        self.events = []

    def add(self, kind, t_ns, **fields):
        if (type(t_ns) is not int or t_ns < 0 or
                (self.events and t_ns < self.events[-1]['t_ns'])):
            raise ValueError('One nondecreasing QEMU virtual clock is required')
        seq = len(self.events)
        row = dict(seq=seq, event_id=f'e{seq}', clock_id=self.clock_id,
                   t_ns=t_ns, kind=kind, **fields)
        self.events.append(row)
        return row


class CseqObserver:
    """Observe one serial native task by alternating entry/next-instruction traps.

    The original STP runs in the guest. No stepping packet, PC write, emulated
    instruction, DAIF change or OS event injection is used by this observer.
    CPU/SP/LR checks bound the task-identity assumption; migration fails closed.
    """
    ENTRY = 0x40F43A84
    REARM = ENTRY + 4
    TASK = 0xA03BBA448
    CALLER = 0x40C00294

    def __init__(self):
        self.enabled = False
        self.identity = None
        self.pending = None
        self.entries = 0
        self.rearms = 0

    def enable(self, instruction, callback):
        # A64 STP X28,X27,[SP,#-0x60]!; encode its operands rather than embed
        # a firmware function. This store cannot branch and only subtracts 96
        # from SP, leaving W0 and LR unchanged at the next instruction.
        first = 0xA9800000 | ((-0x60 // 8) & 0x7f) << 15 | 27 << 10 | 31 << 5 | 28
        if (self.enabled or instruction != struct.pack('<I', first)
                or callback != self.ENTRY):
            raise ValueError('CSEQ registration/first instruction must match the original serial callback')
        self.enabled = True

    def transition(self, cpu_registers):
        if not self.enabled:
            return None
        hits = [(cpu, regs) for cpu, regs in cpu_registers
                if regs['PC'] in (self.ENTRY, self.REARM)]
        if not hits:
            return None
        if len(hits) != 1:
            raise RuntimeError('CSEQ is not a single serial task: simultaneous CPU boundaries')
        cpu, regs = hits[0]
        sp, lr, event = regs['SP'], regs['X30'], regs['X00']
        if self.pending is not None:
            old_cpu, old_sp, old_lr, old_event = self.pending
            # Another device can halt the machine before this task executes
            # the first instruction. Keep waiting; do not count it twice.
            if regs['PC'] == self.ENTRY and (cpu, sp, lr, event) == self.pending:
                return None
            if (regs['PC'] != self.REARM or
                    (cpu, sp, lr, event) != (old_cpu, old_sp - 0x60, old_lr, old_event)):
                raise RuntimeError('CSEQ rearm identity/first-instruction transition changed')
            self.pending = None
            self.rearms += 1
            return self.ENTRY
        if regs['PC'] == self.REARM:
            return None  # This unarmed PC may be sampled by an unrelated stop.
        if (lr != self.CALLER or sp % 16 or
                not 0xA00000270 <= sp < 0xA40000000 or
                (self.identity is not None and self.identity != (cpu, sp))):
            raise RuntimeError('CSEQ task CPU/SP/native caller identity changed')
        if self.entries >= 4096:
            raise RuntimeError('CSEQ 4096-entry observation limit')
        self.identity = (cpu, sp)
        self.pending = (cpu, sp, lr, event)
        self.entries += 1
        return self.REARM

    def snapshot(self):
        return dict(enabled=self.enabled, entries=self.entries, natural_rearms=self.rearms,
                    pending_rearm=self.pending is not None,
                    cpu=None if self.identity is None else self.identity[0],
                    entry_sp=None if self.identity is None else hex(self.identity[1]),
                    limit_total_observations=4096, historical_trace_complete=False,
                    scope='Single registered callback, fixed CPU/SP and native loop LR; no OS TCB identity claim')


def command_caller(guest, regs):
    sp = regs['SP']
    if sp % 16 or not 0xA00000000 <= sp <= 0xA40000000 - 0x40:
        raise ValueError('Command helper stack is outside bounded aligned Z8 RAM')
    saved = guest.read_bytes(sp + 0x30, 16)
    if len(saved) != 16:
        raise ValueError('Short command helper saved-register read')
    x19, lr = struct.unpack('<QQ', saved)
    return dict(caller_saved_x19=hex(x19), caller_lr=hex(lr),
                saved_register_address=hex(sp + 0x30),
                scope='Original saved caller registers, not current wait-return X30')


SCHEDULING_POINTS = {
    0x40F4A89C: 'schedule_counter_setter', 0x40F52014: 'schedule_settings_gate',
    0x40F4A9F8: 'schedule_before_counter_getter', 0x40F4A9FC: 'schedule_counter_result',
    0x40F4AA24: 'schedule_zero_count_post', 0x40F4BE80: 'schedule_event149_payload',
    0x40F4AF64: 'schedule_secondary_call', 0x40F6A13C: 'schedule_secondary_advance',
    0x40F468F8: 'schedule_before_delay_bound', 0x40F468FC: 'schedule_after_delay_bound',
    0x40F46A94: 'schedule_before_alignment', 0x40F52FC4: 'schedule_selected_max_delay',
}


def scheduling_observation(guest, pc, regs):
    """Read the actual native scheduler ABI; never change counts or time.

    Each point is sampled once per external contact. These sparse observations
    cannot establish event149 continuity, FIFO completeness or absent events.
    """
    if pc not in SCHEDULING_POINTS:
        raise ValueError('Unknown scheduling observation point')
    def read(address, size, alignment=8):
        if (type(address) is not int or address % alignment or
                not 0xA00000000 <= address <= 0xA40000000 - size):
            raise ValueError('Native scheduling pointer outside aligned Z8 RAM')
        data = guest.read_bytes(address, size)
        if len(data) != size:
            raise ValueError('Short native scheduling memory read')
        return data
    phase0, phase1, previous, timestamp, remaining = struct.unpack('<IIQQQ', read(0xA03BBC428, 32))
    ordinal, source_time = struct.unpack('<QQ', read(0xA03BBB330, 16))
    context = read(0xA03BBA948, 0x174)
    row = dict(event_history_complete=False, scope='Partial first-hit-per-contact scheduling observation',
        snapshot=dict(phases=[phase0, phase1], previous_ordinal=previous,
                      stored_timestamp=timestamp, remaining_count=remaining,
                      source_ordinal=ordinal, source_timestamp=source_time,
                      native_state=struct.unpack('<I', read(0xA03BBB43C, 4, 4))[0],
                      scheduled_time=struct.unpack('<Q', read(0xA03BBA878, 8))[0],
                      command_time=struct.unpack('<Q', read(0xA03BBA830, 8))[0]),
        context_words={hex(off):struct.unpack_from('<I', context, off)[0]
                       for off in (0x1c, 0x2c, 0x30, 0x34, 0x58, 0x60, 0x80, 0x88, 0x8c, 0xfc, 0x100, 0x170)},
        context_byte_5c=context[0x5c])
    if pc == 0x40F4BE80:
        pair = struct.unpack('<QQ', read(regs['X19'], 16))
        row['event149'] = dict(pointer=hex(regs['X19']), ordinal=pair[0], timestamp=pair[1])
    elif pc == 0x40F4AF64:
        if regs['X00'] != regs['SP']:
            raise ValueError('Secondary native argument must be the original stack packet')
        pointer, native_state, _ = struct.unpack('<QII', read(regs['X00'], 16, 16))
        if pointer != 0xA03BBB330:
            raise ValueError('Secondary native packet does not reference the original ordinal/time pair')
        pair = struct.unpack('<QQ', read(pointer, 16))
        row['secondary_call'] = dict(argument_pointer=hex(regs['X00']), source_pointer=hex(pointer),
                                     state=native_state, ordinal=pair[0], timestamp=pair[1])
    elif pc == 0x40F6A13C:
        # X19 is a scalar ordinal after a11c's LDP, never a payload pointer.
        row['secondary_advance'] = dict(remaining_getter=regs['X00'], new_ordinal=regs['X19'],
            new_timestamp=regs['X22'], previous_ordinal=regs['X20'], stored_timestamp=regs['X21'])
    elif pc == 0x40F4A9FC:
        row['remaining_getter'] = regs['X00']
    elif pc == 0x40F4A9F8:
        row['counter_lock_address'] = '0xa03bbc3b0'
        row['counter_lock_observed_hex'] = read(0xA03BBC3B0, 16).hex()
    elif pc == 0x40F4A89C:
        row['counter_setter'] = dict(value=regs['X00'], computed_count=regs['X24'])
    elif pc == 0x40F52014:
        w8, w9, w10 = (regs[name] & 0xffffffff for name in ('X08', 'X09', 'X10'))
        row['settings_gate'] = dict(state_argument=regs['X00'] & 0xffffffff,
            w8=w8, w9=w9, w10=w10, substantive_branch_predicate=w8 != 0 and w9 == 1 and w10 >= 2)
    elif pc == 0x40F4AA24:
        row['task_post_arguments'] = dict(task=hex(regs['X00']), mask=regs['X01'] & 0xffffffff,
                                         post_completed=False)
    main_frame = pc in (0x40F468F8, 0x40F468FC, 0x40F46A94, 0x40F4A89C)
    if main_frame or pc == 0x40F52FC4:
        sp = regs['SP']
        if (type(sp) is not int or sp % 16 or
                not 0xA00000000 <= sp <= 0xA40000000 - 0x90):
            raise ValueError('Scheduling stack frame outside aligned Z8 RAM')
        cache = read(0xA03BBA810, 0x70)
        pair = struct.unpack('<QQ', read(0xA03BBB490, 16))
        row['bound_inputs'] = dict(
            time_cache_u64={hex(off):struct.unpack_from('<Q', cache, off)[0]
                            for off in (0, 0x20, 0x48, 0x60, 0x68)},
            native_delay_packet_u32=list(struct.unpack('<III', read(0xA03BBB1F4, 12, 4))),
            time_b490_u64=pair[0], time_b498_u64=pair[1],
            parity_acb0_u32=struct.unpack('<I', read(0xA03BBACB0, 4, 4))[0],
            source_period_b980_u32=struct.unpack('<I', read(0xA03BBB980, 4, 4))[0],
            context_304_u32=struct.unpack('<I', read(0xA03BBAC4C, 4, 4))[0])
        if main_frame:
            if pc == 0x40F468F8 and regs['X00'] != sp + 0x88:
                raise ValueError('Original lower-bound call does not reference SP+0x88')
            row['main_frame'] = dict(sp=hex(sp), bound_slot_u64=struct.unpack('<Q', read(sp + 0x88, 8))[0])
            # These slots are written only at46a8c/90. Earlier reads would
            # mislabel unrelated or uninitialized stack bytes as P/T/O.
            if pc in (0x40F46A94, 0x40F4A89C):
                period, base_time, offset = struct.unpack('<IQQ', read(sp + 0x24, 20, 4))
                row['main_frame']['alignment'] = dict(period_u32=period, base_time_u64=base_time,
                    offset_u64_observed=offset, offset_u32_used=offset & 0xffffffff,
                    stack08_u64=struct.unpack('<Q', read(sp + 8, 8))[0])
        else:
            #52d98 owns a separate0x90-byte frame. X19 is its input time
            #pointer; SP+88 is the saved native LR, not the caller's bound.
            row['delay_helper'] = dict(max_delay_w20=regs['X20'] & 0xffffffff,
                input_pointer=hex(regs['X19']), input_bound_u64=struct.unpack('<Q', read(regs['X19'], 8))[0],
                caller_lr=hex(struct.unpack('<Q', read(sp + 0x88, 8))[0]))
    return row


# Interface observations from C3.11; the capture profile carries fuller static
# provenance. One-shot observations identify the first missing link per contact.
POINTS = {0x40D51948:'full_application', 0x40FB875C:'full_request',
    0x40FD9E64:'full_backend', 0x40FD9F30:'full_worker',
    0x40EDEC80:'format_gate_result', 0x40F6C060:'hsfc_construct',
    0x40F6F968:'history_metadata', 0x40F6FFF0:'history_enqueue',
    0x40F6FE90:'history_submit', 0x40F93098:'backend_submit',
    0x40EABD10:'raw_step', 0x40EB17B0:'nef_tag', 0x40EA3638:'file_writer',
    0x406CE108:'file_write_bytes', 0x40EA363C:'file_writer_return',
    0x4117B660:'ccomp_gpio_completion_dispatch', 0x4117AD90:'ccomp_task_notify',
    0x4117A920:'ccomp_task_event', 0x4117AA18:'ccomp_listener_dispatch',
    0x40F4BF84:'ccomp_capture_listener', 0x40F4CB18:'ccomp_capture_prepare_call',
    0x40F4CB1C:'ccomp_capture_prepare_return', 0x40F4C8F0:'ccomp_capture_flag_set',
    0x40F46B0C:'ccomp_wait_policy', 0x40F46B2C:'ccomp_wait_condition',
    0x40F46B50:'ccomp_completion_wait_call', 0x40F46B60:'ccomp_completion_wait_join',
    0x40F4A068:'ccomp_completion_wait_skip', 0x40F4CAB0:'ccomp_completion_handler',
    0x40F4CB3C:'ccomp_completion_flag8_prepare', 0x40F61C18:'ccomp_sensor_event',
    0x40F5A288:'ccomp_sensor_event_return', 0x40F606A0:'ccomp_sync_notify',
    0x40F46B80:'ccomp_wait1_call', 0x40F46B84:'ccomp_wait1_return',
    0x40F46BB8:'ccomp_aux_wait1_call', 0x40F46BBC:'ccomp_aux_wait1_return',
    0x40F4FC18:'ccomp_command_helper_return', 0x40F43A84:'cseq_dispatch',
    0x40F454F4:'ccomp_bit20_wait8_call', 0x40F454F8:'ccomp_bit20_wait8_return',
    0x40F45AB4:'ccomp_alternate_wait8_call', 0x40F45AB8:'ccomp_alternate_wait8_return'}
POINTS.update(SCHEDULING_POINTS)


class CapturePeer(Peer):
    def __init__(self, guest, journal):
        super().__init__(guest, bindings=load_bindings(guest), limit_frames=1024,
                         config_reply=True, all_mode_replies=True)
        self.consumer = load_configuration(guest)['boot']['points']['boot4']
        self.capture_profile = capture_bindings(guest)
        self.trace_points = dict(POINTS)
        for pc,name in observation_points(self.capture_profile).items():
            if pc != self.consumer:
                self.trace_points.setdefault(pc,name)
        self.journal = journal
        self.active_contact = None
        self.return_contact = None
        self.observation_count = 0
        self.last_pixel_frame = 0
        self.last_raw_job = 0
        self.cseq = CseqObserver()
        self.flag8_predecessor = None

    def queue_reply(self, wire):
        from ccomp_command_peer import queue_reply
        if queue_reply(self, wire):
            return
        super().queue_reply(wire)

    def now(self):
        return state(self.g, 'runtime-clock-state')['virtual_ns']

    def start_shutter_observation(self, *, repeat=True):
        callback = struct.unpack('<Q', self.g.read_bytes(self.cseq.TASK + 0x48, 8))[0]
        if type(repeat) is not bool or self.cseq.enabled:
            raise ValueError('Choose the CSEQ observation policy once at shutter_start')
        # Validate the same original registration/instruction even when the
        # repeated observer is disabled. One-shot contacts retain their scope.
        validated = self.cseq if repeat else CseqObserver()
        validated.enable(self.g.read_bytes(self.cseq.ENTRY, 4), callback)
        self.arm(self.cseq.ENTRY, 'capture_cseq_dispatch')
        self.journal.add('cseq_repeated_observation_enabled' if repeat else
                         'cseq_repeated_observation_disabled', self.now(),
                         binding_verified=True, **self.cseq.snapshot())

    def begin(self, label, body, expected):
        if not isinstance(expected, bytes) or len(expected) != 44:
            raise ValueError('Every external contact needs an exact decoded-key expectation')
        if self.active_contact is not None:
            raise RuntimeError('Previous key consumer has not returned')
        self.active_contact = dict(label=label, expected=expected, entered=False)
        self.arm(self.consumer, 'capture_key_consumer')
        for pc, name in self.trace_points.items():
            if pc == self.cseq.ENTRY and self.cseq.enabled:
                continue  # Entry/next-instruction alternation owns this point.
            if pc == 0x40F4CB3C and self.flag8_predecessor is not None:
                continue  # Let the pending original MOV reach its argument observer.
            self.arm(pc, 'capture_' + name)
        self.journal.add('input_queued', self.now(), label=label, body_hex=body.hex(),
                         input_route='mcu_frame', causal_scope='Queued external input, not original FULL consumption')
        if self.g.data.get('capture_device_hypotheses'):
            self.g.data.setdefault('capture_readiness',[]).append(dict(label=label,
                t_ns=self.now(),state=capture_readiness(self.g,primary_mmio=True)))
            self.g.data['capture_trace'].setdefault('checkpoints',[]).append(dict(
                label=label,t_ns=self.now(),
                primary={hex(a):self.g.rd(a) for a in
                         (0x150c0200,0x150c0204,0x150c020c,0x150c0210,0x150c0218,0x150c021c)},
                int262_enable_word=self.g.rd(0x0d000120),
                int262_pending_word=self.g.rd(0x0d000220),
                d980=state(self.g,'primary-d980-state'),
                gpio=state(self.g,'capture-gpio-state'),
                lcd=state(self.g,'engine1548-scanout-state')))
        self.enqueue_external(body, 2)

    def handle(self):
        if self.g.data.get('capture_pixels_enabled'):
            from z8_capture_pixels import capture_d980, capture_raw_output
            d980=state(self.g,'primary-d980-state')
            if d980.get('frames',0)>self.last_pixel_frame:
                receipt=capture_d980(self.g,f'd980-{d980["frames"]:05}')
                self.g.data.setdefault('producer_snapshots',[]).append(receipt)
                self.last_pixel_frame=d980['frames']
                print('Saved actual D980 producer',self.last_pixel_frame,flush=True)
            raw=state(self.g,'engine151-raw-output-state')
            if raw.get('jobs',0)>self.last_raw_job:
                receipt=capture_raw_output(self.g,f'raw-{raw["jobs"]:05}')
                self.g.data.setdefault('raw_output_snapshots',[]).append(receipt)
                self.last_raw_job=raw['jobs']
                print('Saved actual RAW output',self.last_raw_job,flush=True)
        observations = []
        seen = set()
        cpu_registers = []
        for cpu in range(4):
            registers = self.g.qmp.hmp('info registers', cpu)
            regs = {k:int(v,16) for k,v in re.findall(r'\b(X\d\d|PC|SP)=([0-9a-fA-F]+)', registers)}
            cpu_registers.append((cpu, regs))
            pc = regs['PC']; label = self.points.get(pc, '')
            if label.startswith('capture_') and pc not in seen:
                observations.append((pc,label,cpu,regs)); seen.add(pc)
        if self.observation_count + len(observations) > 4096:
            raise RuntimeError('Bounded 4096 total capture observation limit')
        self.observation_count += len(observations)
        try:
            rearm = self.cseq.transition(cpu_registers)
        except RuntimeError as error:
            self.g.data['capture_trace']['cseq_identity_failure'] = dict(error=str(error),
                cpus=[dict(cpu=cpu, pc=hex(regs['PC']), sp=hex(regs['SP']),
                           lr=hex(regs['X30']), x0=hex(regs['X00'])) for cpu, regs in cpu_registers])
            raise
        handled = super().handle()
        if rearm is not None:
            self.arm(rearm, 'capture_cseq_dispatch' if rearm == self.cseq.ENTRY else 'capture_cseq_rearm')
        for pc,label,cpu,regs in observations:
            fields = dict(pc=hex(pc), cpu=cpu,
                registers={k:hex(regs[k]) for k in
                    ('X00','X01','X02','X03','X08','X09','X10','X12','X19','X20','X21','X22','X24','X25','X26','X27','X30','SP')})
            if pc in SCHEDULING_POINTS:
                fields['scheduling'] = scheduling_observation(self.g, pc, regs)
            if pc == 0x40F4FBC0:
                fields['command_caller'] = command_caller(self.g, regs)
            if label == 'capture_cseq_dispatch':
                context = self.g.read_bytes(0xA03BBA948, 0x174)
                fields['task_event_mask'] = hex(regs['X00'] & 0xffffffff)
                fields['context_words'] = {hex(off):struct.unpack_from('<I', context, off)[0]
                    for off in (0x2c, 0x30, 0x34, 0xfc, 0x100, 0x170)}
                fields['native_state_word'] = self.g.rd(0xA03BBB43C)
                fields['timing_slots'] = {hex(a):hex(struct.unpack('<Q', self.g.read_bytes(a, 8))[0])
                    for a in (0xA03BBA8A0, 0xA03BBA8B0, 0xA03BBA830)}
                fields['repeated_task_observation'] = self.cseq.snapshot()
            if label == 'capture_cseq_rearm':
                fields['repeated_task_observation'] = self.cseq.snapshot()
            if label == 'capture_ccomp_completion_flag8_prepare':
                if self.flag8_predecessor is not None:
                    raise RuntimeError('Overlapping dedicated flag8 predecessor')
                self.flag8_predecessor = (cpu, regs['SP'])
                self.arm(0x40F4CB40, 'capture_ccomp_completion_flag8_arguments')
            if label == 'capture_ccomp_completion_flag8_arguments':
                if (sum(reg['PC'] == pc for _, reg in cpu_registers) != 1 or
                        self.flag8_predecessor != (cpu, regs['SP']) or
                        regs['X00'] != 0xA03BBA4F8 or regs['X01'] != 8):
                    raise RuntimeError('Dedicated flag8 argument path changed or was intercepted by another task')
                self.flag8_predecessor = None
                fields.update(flag_object=hex(regs['X00']), flag_mask=8,
                              predecessor_verified=True, flag_set_completed=False,
                              scope='Original mov W1,#8 executed; setter still pending')
            if 'ccomp_' in label:
                fields['ccomp_state']=dict(
                    request=self.g.rd(0x0d15100c),
                    native_state=self.g.rd(self.capture_profile['ccomp']['gpio_state']),
                    device=state(self.g,'ccomp-gpio-state'))
                fields['native_event_objects']={hex(a):self.g.read_bytes(a,16).hex()
                    for a in (0xa04975cb0,0xa03bba4f8,0xa03bba504)}
            if label == 'capture_ccomp_wait_policy':
                pointer=regs['X21']
                if not 0xa00000000 <= pointer <= 0xa40000000-0x30:
                    raise ValueError('CCOMP wait policy pointer outside Z8 RAM')
                fields['mode_word']=self.g.rd(pointer+0x2c)
            if label == 'capture_ccomp_wait_condition':
                fields['pending_pointer']=hex(struct.unpack('<Q',self.g.read_bytes(0xa03bba830,8))[0])
            if label == 'capture_key_consumer':
                if self.active_contact is None:
                    raise RuntimeError('Key observer without an active external contact')
                pointer=regs['X00']
                if not 0xA00000000 <= pointer <= 0xA40000000-44:
                    raise ValueError('Key payload outside declared Z8 RAM')
                keys = self.g.read_bytes(pointer,44)
                observation = contact_observation(self.active_contact, keys)
                matched = observation['matched']
                self.active_contact['entered'] = matched
                fields.update(observation)
                self.return_contact = self.active_contact
                self.arm(regs['X30'], 'capture_key_return')
            elif label == 'capture_key_return':
                contact=self.return_contact
                if contact is None:
                    raise RuntimeError('Key return has no matching original entry')
                fields.update(label=contact['label'], matched=contact['entered'])
                if contact['entered']:
                    self.active_contact = None
                else:
                    self.arm(self.consumer, 'capture_key_consumer')
                self.return_contact = None
            elif label == 'capture_format_gate_result':
                fields.update(candidate=regs['X21']&0xffffffff,
                              effective_gate=regs['X12']&0xffffffff,
                              result=regs['X19']&0xffffffff)
            self.journal.add(label.removeprefix('capture_'), self.now(), **fields)
        return handled or bool(observations)


def run(args):
    budget = HostBudget(args.seconds)
    contacts, shutter_start = contact_sequence(args)
    import guest_transport
    from private_bounded_qmp import QMP
    guest_transport.QMP = QMP
    journal = TraceJournal(args.output.name)
    guest = PreparedGuest(args.firmware,args.qemu,args.raw,args.output,
                          properties=load_model_profile()['properties'].copy())
    # Additional peripheral model opt-ins are recorded separately from the
    # established assisted-menu profile. No implicit Z9 firmware binding.
    if args.capture_properties:
        extra=json.loads(args.capture_properties.read_text())
        if (extra.get('main_sha256') != guest.data['firmware_sha256'] or
                extra.get('model') != 'Nikon Z8'):
            raise ValueError('Capture properties require the exact Z8 identity')
        properties=extra['properties']
        if any(not re.fullmatch(r'[a-z0-9-]+=(?:on|off|[0-9]+)', p) for p in properties):
            raise ValueError('Only explicit bounded peripheral properties are admitted')
        guest.command[guest.command.index('-M')+1] += ',' + ','.join(properties)
        guest.data['capture_device_hypotheses'] = extra
    if args.card:
        from virtual_recording_card import create
        card=guest.out/'capture-card.raw'
        guest.data['private_virtual_card']=create(card,'exfat')
        guest.command[guest.command.index('-M')+1] += ',pcie-link-diagnostic-mask=4,pcie-dbi-model=on,pcie-ram-dma-input=on,pcie-image-ram-dma-input=on,pcie-intx-model=on'
        guest.command += ['-drive',f'file={card},if=none,id=card,format=raw',
                          '-device','nvme,drive=card,serial=Z8-LAB-CAPTURE,bus=dw-pcie,addr=0']
    if args.tagged_input:
        guest.command[guest.command.index('-M')+1] += ',sensor-frame-tag-model=on'
    if args.capture_selector:
        # C3.11 resource 0x5c -> bank38 base47e80000, original lookup
        # 41170d24 reads base+selector+daf40. This is missing ROM input,
        # not a recovered coefficient table or a write to live guest state.
        selector=guest.out/'z8-capture-selector2.bin'
        with selector.open('xb') as stream:
            stream.write(bytes((1,)))
        guest.command += ['-device',f'loader,file={selector},addr=0x47f5af42,force-raw=on']
        guest.data['capture_selector_input']=dict(address='0x47f5af42',selector=2,record=1,
            scope='explicit missing ROM placeholder; coefficients and physical geometry unverified')
    guest.data['raw_precapture_verified']=False
    guest.data['capture_pixels_enabled']=bool(args.capture_pixels)
    guest.data['probe_arguments']={key:str(value) if isinstance(value,Path) else value
                                  for key,value in vars(args).items()}
    source_paths=[Path(__file__), Path(__file__).with_name('service_z9_mcu.py'),
                  Path(__file__).with_name('service_serial8.py'),
                  Path(__file__).with_name('z8_capture_bindings.py'),
                  Path(__file__).with_name('z8_capture_readiness.py')]
    if args.capture_pixels:source_paths.append(Path(__file__).with_name('z8_capture_pixels.py'))
    if args.capture_properties:source_paths.append(args.capture_properties)
    guest.data['probe_sources']={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in source_paths}
    guest.data['capture_trace']=dict(scope='bounded diagnostic prototype: first hit per contact, optional serial CSEQ repetition after shutter_start',
        cseq_repeat_requested=not getattr(args, 'skip_cseq_repeat', False),
        events=journal.events,complete=False,historical_trace_complete=False,
        complete_meaning='contact sequence and optional observed idle gate only; no file/NEF acceptance',
        input_routes=['mcu_frame','gpio'] if args.gpio else ['mcu_frame'],sole_full_trigger_verified=False)
    with guest as g:
        peer=None; failure=None; original_log=g.log
        def budgeted_log():
            budget.check('boot or guest service')
            return original_log()
        try:
            g.log=budgeted_log
            budget.check('startup')
            boot(g)
            peer=CapturePeer(g,journal); clock=make_boundary_peer(g)
            if args.gpio:
                g.data['z8_capture_gpio_bindings']=read_gpio_bindings(g)
            if args.rtc_status:
                from rtc_mcu_input import diagnostic_status
                from mcu_frames import frames
                body=diagnostic_status()
                peer.pending.append((7,body,frames(body,family=7)[0]))
                peer.start_rx()
            spi8=WritePeer(g); spi9=WritePeer(g,channel=9,wire_bits=9,allow_dummy_rx=True)
            ready_at=peer.now()+args.settle*1_000_000_000
            index=0; explicit_stop=False; snapshots=0; next_snapshot=0
            work_baseline=None; idle_since=None
            g.qmp.command('cont')
            while time.monotonic()<budget.deadline:
                if (g.out/'stop.request').exists():
                    raise InterruptedError('Owned probe stop requested')
                if 'Z9_UNKNOWN' in g.log():
                    raise RuntimeError('Unmodeled hardware access; inspect the captured first fault')
                running=g.qmp.command('query-status')['running']
                if not running:
                    handled=peer.handle()
                    if not handled and not explicit_stop:
                        raise RuntimeError('Guest stopped outside registered input/trace boundaries')
                    explicit_stop=False; g.qmp.command('cont'); continue
                clock.tick()
                now=peer.now()
                eligible=peer.active_contact is None and now>=ready_at
                if eligible and (index==0 or (args.wait_liveview and index==shutter_start and not args.menu_only)):
                    lcd=state(g,'engine1548-scanout-state')
                    eligible=display_ready(lcd)
                    if args.wait_liveview and index==shutter_start and not args.menu_only:
                        eligible=eligible and lcd.get('last_frame_has_video') and lcd.get('video_frames',0)>=8
                    if eligible and index==0:
                        g.data['capture_trace']['initial_display_ready']=dict(t_ns=now,state=lcd)
                if eligible:
                    if index==len(contacts):
                        if args.wait_capture_work:
                            raw=state(g,'engine151-raw-output-state')
                            jpeg=state(g,'engine15e2-jpeg-state')
                            ready=work_baseline is not None and ordinary_work_idle(raw,jpeg,journal.events,work_baseline)
                            idle_since=(now if idle_since is None else idle_since) if ready else None
                            ready=ready and now-idle_since>=2_000_000_000
                            if not ready:
                                eligible=False
                            else:
                                g.data['ordinary_raw_work_quiescent']=True
                                g.data['ordinary_raw_work_scope']='New counters and observed successful writer return idle for two virtual seconds; card export, NEF decoding and NVMe durability remain unverified'
                        if not eligible:
                            # Continue normal device/peer service below while image work runs.
                            ready_at=now+100_000_000
                        else:
                            g.data['capture_trace']['complete']=True
                            break
                    if not eligible:
                        channels=state(g,'serial-state')
                        if clock_devices(peer,spi8,spi9,channels):
                            explicit_stop=True; continue
                        time.sleep(.005);continue
                    g.qmp.command('stop'); peer.handle()
                    item=contacts[index]; label=item['label']; half=item['half']; full=item['full']
                    if index==shutter_start and not args.menu_only:
                        peer.start_shutter_observation(repeat=not getattr(args, 'skip_cseq_repeat', False))
                        work_baseline=dict(raw_jobs=state(g,'engine151-raw-output-state').get('jobs',0),
                            jpeg_jobs=state(g,'engine15e2-jpeg-state').get('jobs',0),event_start=len(journal.events))
                        g.data['capture_work_baseline']=work_baseline
                    g.qmp.command('screendump',filename=str(g.out/f'step-{index:03}-{label}.ppm'))
                    if args.gpio:
                        g.qmp.command('qom-set',path='/machine',property='capture-gpio-input',value=str(int(half)|(int(full)<<1)))
                        journal.add('gpio_input_set',peer.now(),label=label,levels=int(half)|(int(full)<<1),
                                    input_route='gpio',causal_scope='External GPIO levels only; not original MCU FULL consumption')
                    peer.begin(label,item['body'],item['expected'])
                    print('Queued Z8 contact',label,flush=True)
                    index+=1
                    gap=max(item['gap'],args.settle*1_000_000_000) if index==len(contacts) else item['gap']
                    ready_at=now+gap
                    g.qmp.command('cont')
                if time.monotonic()>=next_snapshot:
                    g.qmp.command('stop'); peer.handle()
                    g.qmp.command('screendump',filename=str(g.out/f'probe-{snapshots:03}.ppm'))
                    snapshots+=1; next_snapshot=time.monotonic()+15
                    g.qmp.command('cont')
                channels=state(g,'serial-state')
                if clock_devices(peer,spi8,spi9,channels):
                    explicit_stop=True; continue
                time.sleep(.005)
            g.qmp.command('stop'); peer.handle()
            g.data['capture_trace']['device_states']={p:state(g,p) for p in
                ('runtime-clock-state','raw-pipeline-state','engine1548-scanout-state')}
            if args.capture_properties:
                g.data['capture_trace']['capture_devices']={p:state(g,p) for p in
                    ('primary-d980-state','engine151-raw-output-state','engine15e2-jpeg-state','capture-gpio-state')}
            if not g.data['capture_trace']['complete']:
                budget.check('contact/image work wait')
                raise TimeoutError('Shutter contact trace did not complete')
        except BaseException as error:
            failure=error
            raise
        finally:
            g.log=original_log
            finalize_trace(g,peer,budget,failure)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('firmware','qemu','raw','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=180,choices=range(15,601))
    p.add_argument('--settle',type=int,default=2,choices=range(1,31))
    p.add_argument('--half-seconds',type=int,default=2,choices=range(1,6))
    p.add_argument('--capture-properties',type=Path)
    p.add_argument('--card',action='store_true')
    p.add_argument('--gpio',action='store_true')
    p.add_argument('--rtc-status',action='store_true')
    p.add_argument('--tagged-input',action='store_true')
    p.add_argument('--capture-selector',action='store_true')
    p.add_argument('--menu-actions',default='')
    p.add_argument('--menu-only',action='store_true')
    p.add_argument('--wait-liveview',action='store_true')
    p.add_argument('--navigation-press-ms',type=int,default=400,choices=range(20,1001))
    p.add_argument('--navigation-release-ms',type=int,default=2000,choices=range(500,10001))
    p.add_argument('--capture-pixels',action='store_true')
    p.add_argument('--wait-capture-work',action='store_true')
    p.add_argument('--skip-cseq-repeat',action='store_true',
                   help='Use first-hit-per-contact CSEQ sampling; keep identity guards and the 4096 total bound')
    run(p.parse_args())


if __name__=='__main__':
    main()
