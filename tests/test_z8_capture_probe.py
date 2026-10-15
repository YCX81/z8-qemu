# SPDX-License-Identifier: GPL-2.0-or-later
"""External contacts and causal trace fail-closed contracts; no firmware."""
from pathlib import Path
import json
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_capture_probe import shutter_body, expected_keys, TraceJournal, display_ready
import z8_capture_probe as probe


class CseqObservationContracts(unittest.TestCase):
    # A single instruction fixture, not a copied firmware function.
    instruction = bytes.fromhex('fc6fbaa9')

    def regs(self, pc=0x40f43a84, sp=0xa06c4efe0, event=8, lr=0x40c00294):
        return dict(PC=pc, SP=sp, X00=event, X30=lr)

    def enabled(self):
        observer = probe.CseqObserver()
        observer.enable(self.instruction, 0x40f43a84)
        return observer

    def test_menu_phase_is_not_repeated_and_requires_verified_instruction(self):
        observer = probe.CseqObserver()
        self.assertIsNone(observer.transition([(0, self.regs())]))
        for word, callback in ((bytes(4), 0x40f43a84), (self.instruction[:3], 0x40f43a84),
                               (self.instruction, 0x40000000)):
            with self.subTest(word=word, callback=callback), self.assertRaises(ValueError):
                probe.CseqObserver().enable(word, callback)
        self.assertFalse(observer.enabled)

    def test_natural_first_instruction_rearms_without_cpu_writes(self):
        observer = self.enabled()
        self.assertEqual(observer.transition([(0, self.regs())]), 0x40f43a88)
        # A different device may pause all CPUs before this task runs its STP.
        self.assertIsNone(observer.transition([(0, self.regs())]))
        self.assertEqual(observer.transition([(0, self.regs(0x40f43a88, 0xa06c4ef80))]), 0x40f43a84)
        self.assertEqual(observer.transition([(0, self.regs(event=0x200))]), 0x40f43a88)
        self.assertEqual(observer.snapshot()['entries'], 2)
        self.assertEqual(observer.snapshot()['natural_rearms'], 1)
        self.assertTrue(observer.snapshot()['pending_rearm'])
        self.assertFalse(observer.snapshot()['historical_trace_complete'])

    def test_rearm_rejects_changed_cpu_sp_event_and_return_address(self):
        bad = [(1, self.regs(0x40f43a88, 0xa06c4ef80)),
               (0, self.regs(0x40f43a88, 0xa06c4ef70)),
               (0, self.regs(0x40f43a88, 0xa06c4ef80, event=4)),
               (0, self.regs(0x40f43a88, 0xa06c4ef80, lr=0x40000000))]
        for hit in bad:
            with self.subTest(hit=hit):
                observer = self.enabled(); observer.transition([(0, self.regs())])
                with self.assertRaises(RuntimeError): observer.transition([hit])

    def test_entry_requires_one_serial_task_and_native_caller(self):
        for hits in ([(0, self.regs()), (1, self.regs())],
                     [(0, self.regs(lr=0x40000000))],
                     [(0, self.regs(sp=0x1000))], [(0, self.regs(sp=0xa06c4efe1))]):
            with self.subTest(hits=hits), self.assertRaises(RuntimeError):
                self.enabled().transition(hits)
        observer = self.enabled(); observer.transition([(0, self.regs())])
        with self.assertRaises(RuntimeError):
            observer.transition([(0, self.regs()), (1, self.regs(0x40f43a88, 0xa06c4ef80))])

    def test_later_entry_migration_and_reentrancy_are_not_silently_accepted(self):
        for cpu, regs in ((1, self.regs()), (0, self.regs(sp=0xa06c4ede0))):
            observer = self.enabled(); observer.transition([(0, self.regs())])
            observer.transition([(0, self.regs(0x40f43a88, 0xa06c4ef80))])
            with self.subTest(cpu=cpu, regs=regs), self.assertRaises(RuntimeError):
                observer.transition([(cpu, regs)])

    def test_repeated_task_has_bounded_observations(self):
        observer = self.enabled()
        observer.entries = 4096
        with self.assertRaisesRegex(RuntimeError, '4096'):
            observer.transition([(0, self.regs())])

    def test_native_callers_are_read_from_saved_stack_not_current_lr(self):
        calls = []
        def read(address, size):
            calls.append((address, size)); return struct.pack('<QQ', 0x208, 0x40f44dcc)
        fields = probe.command_caller(SimpleNamespace(read_bytes=read), dict(SP=0xa06c4ed30))
        self.assertEqual(calls, [(0xa06c4ed60, 16)])
        self.assertEqual(fields['caller_lr'], '0x40f44dcc')
        self.assertEqual(fields['caller_saved_x19'], '0x208')
        for sp in (0xa06c4ed31, 0x2000, 0xa40000000):
            with self.assertRaises(ValueError):
                probe.command_caller(SimpleNamespace(read_bytes=read), dict(SP=sp))
        with self.assertRaises(ValueError):
            probe.command_caller(SimpleNamespace(read_bytes=lambda a, n: bytes(8)), dict(SP=0xa06c4ed30))

    def test_wait_call_return_pairs_and_restored_lr_entry_are_explicit(self):
        for pc in (0x40f454f4, 0x40f454f8, 0x40f45ab4, 0x40f45ab8,
                   0x40f4fc18, 0x40f43a84):
            self.assertIn(pc, probe.POINTS)
        # Shared cb40 is armed only by the dedicated cb3c predecessor.
        self.assertNotIn(0x40f4cb40, probe.POINTS)


class PeerObservationIntegration(unittest.TestCase):
    def setUp(self):
        self.registers = [self.regs(0x40001000 + 4 * cpu) for cpu in range(4)]
        self.commands = []
        self.memory = {0xA03BBA490: struct.pack('<Q', 0x40f43a84),
                       0x40f43a84: bytes.fromhex('fc6fbaa9')}
        def hmp(command, cpu=0):
            self.assertEqual(command, 'info registers')
            return ' '.join(f'{key}={value:x}' for key, value in self.registers[cpu].items())
        def read(address, size):
            data = self.memory.get(address, bytes(size))
            self.assertEqual(len(data), size)
            return data
        self.peer = probe.CapturePeer.__new__(probe.CapturePeer)
        self.peer.g = SimpleNamespace(data={'capture_trace': {}}, rsp=self.commands.append,
            qmp=SimpleNamespace(hmp=hmp), read_bytes=read, rd=lambda a: 2)
        self.peer.cseq = probe.CseqObserver()
        self.peer.flag8_predecessor = None
        self.peer.points = {}
        self.peer.trace_points = dict(probe.POINTS)
        self.peer.record = {'events': []}
        self.peer.capture_profile = {'ccomp': {'gpio_state': 0xa00001000}}
        self.peer.journal = TraceJournal('mock-native-boundaries')
        self.peer.observation_count = 0
        self.peer.now = lambda: 100
        self.peer.consumer = 0x40003000
        self.peer.active_contact = None
        self.peer.enqueue_external = lambda body, family: None
        self.state = patch.object(probe, 'state', return_value={})
        self.state.start(); self.addCleanup(self.state.stop)

    @staticmethod
    def regs(pc, sp=0xa06c4efe0, event=8):
        row = {f'X{i:02}': 0 for i in range(31)}
        row.update(PC=pc, SP=sp, X00=event, X30=0x40c00294)
        return row

    def test_real_peer_removes_then_rearms_only_the_next_boundary(self):
        self.peer.start_shutter_observation()
        self.registers[0] = self.regs(0x40f43a84)
        self.assertTrue(self.peer.handle())
        self.assertEqual(self.commands, ['Z1,40f43a84,4', 'z1,40f43a84,4', 'Z1,40f43a88,4'])
        self.assertNotIn(0x40f43a84, self.peer.points)
        self.registers[0] = self.regs(0x40f43a88, sp=0xa06c4ef80)
        self.assertTrue(self.peer.handle())
        self.assertEqual(self.commands[-2:], ['z1,40f43a88,4', 'Z1,40f43a84,4'])
        self.assertNotIn(0x40f43a88, self.peer.points)
        rows = self.peer.journal.events
        self.assertEqual([row['kind'] for row in rows],
                         ['cseq_repeated_observation_enabled', 'cseq_dispatch', 'cseq_rearm'])
        self.assertEqual(rows[1]['task_event_mask'], '0x8')
        self.assertEqual(rows[1]['context_words']['0x34'], 0)
        self.assertTrue(all(command.startswith(('Z1,', 'z1,')) for command in self.commands))

    def test_new_contact_cannot_rearm_entry_during_natural_instruction_transition(self):
        self.peer.start_shutter_observation()
        self.registers[0] = self.regs(0x40f43a84); self.peer.handle()
        self.peer.begin('half', shutter_body(True), expected_keys(True))
        self.assertNotIn(0x40f43a84, self.peer.points)
        self.assertIn(0x40f43a88, self.peer.points)

    def test_menu_dispatch_is_still_one_shot(self):
        self.peer.begin('wake', shutter_body(), expected_keys())
        self.registers[0] = self.regs(0x40f43a84); self.peer.handle()
        self.assertNotIn(0x40f43a84, self.peer.points)
        self.assertNotIn(0x40f43a88, self.peer.points)
        self.assertFalse(self.peer.cseq.enabled)

    def test_global_cap_and_identity_failure_happen_before_breakpoint_changes(self):
        self.peer.start_shutter_observation()
        self.registers[0] = self.regs(0x40f43a84)
        self.peer.observation_count = 4096
        before = list(self.commands)
        with self.assertRaisesRegex(RuntimeError, '4096'): self.peer.handle()
        self.assertEqual(before, self.commands)
        self.peer.observation_count = 0
        self.registers[1] = self.regs(0x40f43a84)
        with self.assertRaisesRegex(RuntimeError, 'serial'): self.peer.handle()
        self.assertEqual(before, self.commands)
        self.assertIn('cseq_identity_failure', self.peer.g.data['capture_trace'])

    def test_saved_command_caller_is_in_actual_observation(self):
        self.peer.arm(0x40f4fbc0, 'capture_ccomp_command_wait_return')
        self.registers[0] = self.regs(0x40f4fbc0, sp=0xa06c4ed30, event=1)
        self.memory[0xa06c4ed60] = struct.pack('<QQ', 0x208, 0x40f44dcc)
        self.peer.handle()
        self.assertEqual(self.peer.journal.events[-1]['command_caller']['caller_lr'], '0x40f44dcc')

    def test_flag8_arguments_require_actual_dedicated_predecessor(self):
        self.peer.arm(0x40f4cb3c, 'capture_ccomp_completion_flag8_prepare')
        self.registers[0] = self.regs(0x40f4cb3c, event=0xa03bba4f8)
        self.peer.handle()
        self.assertIn(0x40f4cb40, self.peer.points)
        self.registers[0]['PC'] = 0x40f4cb40
        self.registers[0]['X01'] = 8
        self.peer.handle()
        row = self.peer.journal.events[-1]
        self.assertTrue(row['predecessor_verified'])
        self.assertEqual(row['flag_mask'], 8)
        self.assertFalse(row['flag_set_completed'])
        self.assertIsNone(self.peer.flag8_predecessor)

    def test_shared_flag16_path_cannot_masquerade_as_flag8(self):
        self.peer.arm(0x40f4cb3c, 'capture_ccomp_completion_flag8_prepare')
        self.registers[0] = self.regs(0x40f4cb3c, event=0xa03bba4f8); self.peer.handle()
        self.registers[0]['PC'] = 0x40f4cb40
        self.registers[0]['X01'] = 16
        with self.assertRaisesRegex(RuntimeError, 'flag8 argument'): self.peer.handle()
        self.assertFalse(any(row['kind'] == 'ccomp_completion_flag8_arguments'
                             for row in self.peer.journal.events))

    def test_contact_change_does_not_rearm_pending_flag8_predecessor(self):
        self.peer.arm(0x40f4cb3c, 'capture_ccomp_completion_flag8_prepare')
        self.registers[0] = self.regs(0x40f4cb3c, event=0xa03bba4f8); self.peer.handle()
        self.peer.begin('full-release', shutter_body(True), expected_keys(True))
        self.assertNotIn(0x40f4cb3c, self.peer.points)
        self.assertIn(0x40f4cb40, self.peer.points)
        self.registers[0]['PC'] = 0x40f4cb40
        self.registers[0]['X01'] = 8
        self.peer.handle()
        self.assertTrue(self.peer.journal.events[-1]['predecessor_verified'])

    def test_skip_repeat_still_validates_binding_and_leaves_one_shot_entry(self):
        self.peer.start_shutter_observation(repeat=False)
        self.assertFalse(self.peer.cseq.enabled)
        self.registers[0] = self.regs(0x40f43a84)
        self.peer.handle()
        self.assertNotIn(0x40f43a88, self.peer.points)
        self.assertNotIn(0x40f43a84, self.peer.points)
        self.assertEqual(self.peer.journal.events[0]['kind'], 'cseq_repeated_observation_disabled')
        self.assertTrue(self.peer.journal.events[0]['binding_verified'])
        self.memory[0xA03BBA490] = struct.pack('<Q', 0x40000000)
        with self.assertRaises(ValueError): self.peer.start_shutter_observation(repeat=False)

    def test_secondary_scalar_observation_is_integrated_and_keeps_x22(self):
        self.peer.arm(0x40f6a13c, 'capture_schedule_secondary_advance')
        self.registers[0] = self.regs(0x40f6a13c, event=5)
        self.registers[0].update(X19=419, X20=418, X21=91000000, X22=91050000)
        self.peer.handle()
        row = self.peer.journal.events[-1]
        self.assertEqual(row['registers']['X22'], hex(91050000))
        self.assertEqual(row['scheduling']['secondary_advance']['new_ordinal'], 419)
        self.assertFalse(row['scheduling']['event_history_complete'])


class SchedulingObservationContracts(unittest.TestCase):
    def setUp(self):
        self.reads = []
        self.memory = {
            0xa03bbc428: struct.pack('<IIQQQ', 4, 0, 418, 91000000, 5),
            0xa03bbb330: struct.pack('<QQ', 419, 91050000),
            0xa03bbb43c: struct.pack('<I', 1),
            0xa03bba878: struct.pack('<Q', 546056087),
            0xa03bba830: struct.pack('<Q', 91053047),
            0xa03bbc3b0: bytes(range(16)),
            0xa03bba948: bytes(0x174),
            0xa06c40000: struct.pack('<QQ', 420, 91080000),
            0xa06c40100: struct.pack('<QII', 0xa03bbb330, 1, 0),
            0xa06c40108: struct.pack('<Q', 91000000),
            0xa06c40124: struct.pack('<IQQ', 8366, 91035667, 0),
            0xa06c40188: struct.pack('<Q', 546054041),
            0xa03bba810: bytes(0x70),
            0xa03bbb1f4: struct.pack('<III', 0, 55, 0),
            0xa03bbb490: struct.pack('<QQ', 90000000, 91000000),
            0xa03bbacb0: struct.pack('<I', 0),
            0xa03bbb980: struct.pack('<I', 8366),
            0xa03bbac4c: struct.pack('<I', 0),
        }
        def read(address, size):
            self.reads.append((address, size))
            return self.memory.get(address, b'')
        self.g = SimpleNamespace(read_bytes=read)
        self.regs = {f'X{i:02}': 0 for i in range(31)}
        self.regs['SP'] = 0xa06c40100

    def observe(self, pc, **registers):
        return probe.scheduling_observation(self.g, pc, dict(self.regs, **registers))

    def test_event149_reads_actual_payload_before_global_copy(self):
        row = self.observe(0x40f4be80, X19=0xa06c40000)
        self.assertEqual(row['event149']['ordinal'], 420)
        self.assertEqual(row['event149']['timestamp'], 91080000)
        self.assertEqual(row['snapshot']['source_ordinal'], 419)
        self.assertFalse(row['event_history_complete'])
        self.assertIn((0xa06c40000, 16), self.reads)

    def test_secondary_call_requires_original_stack_and_known_pair_pointer(self):
        row = self.observe(0x40f4af64, X00=0xa06c40100)
        self.assertEqual(row['secondary_call']['state'], 1)
        self.assertEqual(row['secondary_call']['ordinal'], 419)
        for pointer in (0x1000, 0xa40000000, 0xa06c40101):
            with self.assertRaises(ValueError): self.observe(0x40f4af64, X00=pointer)
        self.memory[0xa06c40100] = struct.pack('<QII', 0xa03bbb340, 1, 0)
        with self.assertRaises(ValueError): self.observe(0x40f4af64, X00=0xa06c40100)

    def test_advance_point_uses_scalar_x19_and_records_x22(self):
        row = self.observe(0x40f6a13c, X00=5, X19=419, X20=418, X21=91000000, X22=91050000)
        self.assertEqual(row['secondary_advance']['new_ordinal'], 419)
        self.assertEqual(row['secondary_advance']['new_timestamp'], 91050000)
        self.assertEqual(row['secondary_advance']['remaining_getter'], 5)
        self.assertFalse(any(address == 419 for address, _ in self.reads))

    def test_integer_count_and_gate_registers_are_logged_without_predicted_success(self):
        row = self.observe(0x40f4a9fc, X00=7)
        self.assertEqual(row['remaining_getter'], 7)
        self.assertEqual(row['snapshot']['remaining_count'], 5)
        row = self.observe(0x40f52014, X00=1, X08=1, X09=0, X10=0)
        self.assertFalse(row['settings_gate']['substantive_branch_predicate'])
        row = self.observe(0x40f4a89c, X00=8, X24=9)
        self.assertEqual(row['counter_setter'], {'value': 8, 'computed_count': 9})
        row = self.observe(0x40f4aa24, X00=0xa03bba448, X01=0x100000)
        self.assertEqual(row['task_post_arguments']['mask'], 0x100000)
        self.assertFalse(row['task_post_arguments']['post_completed'])
        row = self.observe(0x40f4a9f8)
        self.assertEqual(row['counter_lock_observed_hex'], bytes(range(16)).hex())

    def test_wrong_pointer_short_read_and_unregistered_point_fail_closed(self):
        for pointer in (0x8000, 0xa40000000 - 8, 0xa06c40001):
            with self.assertRaises(ValueError): self.observe(0x40f4be80, X19=pointer)
        self.memory[0xa06c40000] = bytes(8)
        with self.assertRaises(ValueError): self.observe(0x40f4be80, X19=0xa06c40000)
        with self.assertRaises(ValueError): self.observe(0x40000000)

    def test_all_targeted_points_are_individually_armed(self):
        expected = {0x40f4a89c, 0x40f52014, 0x40f4a9f8, 0x40f4a9fc,
                    0x40f4aa24, 0x40f4be80, 0x40f4af64, 0x40f6a13c,
                    0x40f468f8, 0x40f468fc, 0x40f46a94, 0x40f52fc4}
        self.assertEqual(set(probe.SCHEDULING_POINTS), expected)
        self.assertTrue(expected <= set(probe.POINTS))

    def test_pre_helper_does_not_read_uninitialized_alignment_slots(self):
        for pc in (0x40f468f8, 0x40f468fc):
            self.reads.clear()
            row = self.observe(pc, X00=0xa06c40188)
            self.assertEqual(row['main_frame']['bound_slot_u64'], 546054041)
            self.assertNotIn('alignment', row['main_frame'])
            self.assertNotIn((0xa06c40124, 20), self.reads)
        with self.assertRaises(ValueError): self.observe(0x40f468f8, X00=0xa06c40180)

    def test_alignment_tuple_is_typed_and_uses_original_u32_offset(self):
        self.memory[0xa06c40124] = struct.pack('<IQQ', 8366, 91035667, (7 << 32) | 9)
        for pc in (0x40f46a94, 0x40f4a89c):
            row = self.observe(pc, X00=54388, X24=54389)
            values = row['main_frame']['alignment']
            self.assertEqual(values['period_u32'], 8366)
            self.assertEqual(values['base_time_u64'], 91035667)
            self.assertEqual(values['offset_u64_observed'], (7 << 32) | 9)
            self.assertEqual(values['offset_u32_used'], 9)

    def test_main_frame_checks_sp_alignment_range_and_exact_read_length(self):
        for sp in (0x1000, 0xa40000000 - 0x80, 0xa06c40108):
            with self.assertRaises(ValueError): self.observe(0x40f46a94, SP=sp)
        self.memory[0xa06c40124] = bytes(16)
        with self.assertRaises(ValueError): self.observe(0x40f46a94)

    def test_max_delay_helper_has_its_own_frame_and_explicit_caller(self):
        self.memory[0xa06c40000] = struct.pack('<Q', (1 << 32) + 5)
        self.memory[0xa06c40188] = struct.pack('<Q', 0x40f468fc)
        row = self.observe(0x40f52fc4, X19=0xa06c40000, X20=(1 << 32) + 43)
        self.assertEqual(row['delay_helper']['input_bound_u64'], (1 << 32) + 5)
        self.assertEqual(row['delay_helper']['max_delay_w20'], 43)
        self.assertEqual(row['delay_helper']['caller_lr'], '0x40f468fc')
        self.assertNotIn('main_frame', row)
        self.assertNotIn((0xa06c40124, 20), self.reads)
        with self.assertRaises(ValueError): self.observe(0x40f52fc4, X19=43)


class ContactContracts(unittest.TestCase):
    def test_every_supported_navigation_key_has_exact_expected_state(self):
        for action in ('menu', 'right', 'left', 'up', 'down', 'j'):
            body, expected = probe.navigation_contact(action)
            self.assertEqual(len(body), 124)
            self.assertEqual(len(expected), 44)
            self.assertEqual(sum(value != 0 for value in body), 3)
        with self.assertRaises(ValueError): probe.navigation_contact('ok')

    def test_missing_expected_state_is_never_an_automatic_match(self):
        peer = probe.CapturePeer.__new__(probe.CapturePeer)
        peer.active_contact = None
        with self.assertRaises(ValueError): peer.begin('unknown', bytes(124), None)

    def test_full_mcu_observation_does_not_claim_gpio_or_sole_trigger_causality(self):
        contact = dict(label='full', expected=expected_keys(True, True))
        row = probe.contact_observation(contact, expected_keys(True, True))
        self.assertTrue(row['matched'])
        self.assertTrue(row['full_key_state'])
        self.assertEqual(row['input_route'], 'mcu_decoded_state')
        self.assertFalse(row['sole_trigger_verified'])

    def test_host_budget_includes_startup_and_host_wait(self):
        now = [10.0]
        budget = probe.HostBudget(5, clock=lambda: now[0])
        now[0] = 14.0
        budget.check('boot')
        now[0] = 15.1
        with self.assertRaises(TimeoutError): budget.check('host wait')
        self.assertTrue(budget.report()['deadline_exceeded'])
        self.assertGreater(budget.report()['elapsed_host_seconds'], 5)

    def test_spi_peers_leave_halt_for_shared_observer_dispatch(self):
        calls = []
        peer = SimpleNamespace(clock_if_ready=lambda channels: False)
        def spi(number, result):
            def clock(sample, *, resume=True):
                calls.append((number, resume)); return result
            return SimpleNamespace(clock_if_ready=clock)
        self.assertTrue(probe.clock_devices(peer, spi(8, True), spi(9, False), list(range(10))))
        self.assertEqual(calls, [(8, False), (9, False)])

    def test_wait_work_needs_new_jobs_and_actual_successful_writer_return(self):
        raw = dict(jobs=1, acks=1, pending=False)
        jpeg = dict(jobs=3, acks=3, core_acks=3, requested=False, pending=0, core=0)
        baseline = dict(raw_jobs=0, jpeg_jobs=0, event_start=1)
        entry = dict(seq=1, kind='file_writer', registers={'X00': '0x0'})
        returned = dict(seq=2, kind='file_writer_return', registers={'X00': '0x0'})
        self.assertFalse(probe.ordinary_work_idle(raw, jpeg, [entry], baseline))
        self.assertTrue(probe.ordinary_work_idle(raw, jpeg, [entry, returned], baseline))
        self.assertTrue(probe.ordinary_work_idle(dict(raw,acks=4), jpeg, [entry,returned], baseline))
        self.assertFalse(probe.ordinary_work_idle(dict(raw,acks=0), jpeg, [entry,returned], baseline))
        self.assertFalse(probe.ordinary_work_idle(dict(raw,acks=True), jpeg, [entry,returned], baseline))
        self.assertFalse(probe.ordinary_work_idle(raw, jpeg, [returned], dict(baseline, raw_jobs=1)))
        self.assertFalse(probe.ordinary_work_idle(raw, jpeg, [dict(returned, registers={'X00': '0x3'})], baseline))

    def test_cleanup_errors_do_not_erase_first_failure_or_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = TraceJournal('failure')
            journal.add('key_consumer', 1)
            def broken(*args, **kwargs): raise RuntimeError('cleanup transport failed')
            g = SimpleNamespace(out=Path(directory), data={'capture_trace':dict(events=journal.events, complete=False)},
                                qmp=SimpleNamespace(command=broken))
            original = RuntimeError('first device fault')
            with patch.object(probe, 'state', side_effect=RuntimeError('state unavailable')):
                probe.finalize_trace(g, SimpleNamespace(close=broken), probe.HostBudget(5), original)
            result = json.loads((Path(directory)/'capture-trace.json').read_text())
            self.assertEqual(result['failure'], repr(original))
            self.assertEqual(len(result['events']), 1)
            self.assertTrue(result['cleanup_errors'])

    def test_placeholder_and_inactive_lcd_cannot_accept_navigation(self):
        self.assertFalse(display_ready(dict(active=False,width=0,height=0,frames=0)))
        self.assertFalse(display_ready(dict(active=True,width=1,height=1,frames=1)))
        self.assertFalse(display_ready(dict(active=True,width=1024,height=682,frames=0)))
        self.assertTrue(display_ready(dict(active=True,width=1024,height=682,frames=9)))
    def test_full_contact_is_distinct_and_does_not_change_other_keys(self):
        half = shutter_body(True, False)
        full = shutter_body(True, True)
        self.assertEqual(len(full), 124)
        self.assertEqual([i for i in range(124) if half[i] != full[i]], [8])
        self.assertEqual(full[7:10], bytes((1, 1, 0)))
        self.assertEqual(expected_keys(True, True), struct.pack('<11I', 0xC0000200, *([0]*10)))

    def test_invalid_and_half_released_full_contacts_are_rejected(self):
        for half, full in ((0, True), (True, 1), (False, True), ('yes', False)):
            with self.assertRaises(ValueError):
                shutter_body(half, full)

    def test_journal_uses_one_monotonic_virtual_clock_and_unique_ids(self):
        j = TraceJournal('probe-1')
        a = j.add('queued', 10, phase='half')
        b = j.add('full_press_consumed', 10, phase='full', pc='0x40d271e4')
        self.assertEqual((a['seq'], b['seq']), (0, 1))
        self.assertNotEqual(a['event_id'], b['event_id'])
        self.assertEqual(a['clock_id'], b['clock_id'])
        for t in (9, -1, 2.5, True):
            with self.assertRaises(ValueError):
                j.add('bad_time', t)
        self.assertEqual(len(j.events), 2)


if __name__ == '__main__':
    unittest.main()
