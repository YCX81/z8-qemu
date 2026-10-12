# SPDX-License-Identifier: GPL-2.0-or-later
"""Contract tests for explicitly labelled, bounded lab function invocation."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_native_call import invoke, MAIN_SHA


class FakeQMP:
    def __init__(self, owner): self.owner = owner
    def command(self, operation, **kwargs):
        self.owner.actions.append(operation)
        if operation == 'query-status': return {'running': self.owner.running}
        if operation == 'stop': self.owner.running = False
        if operation == 'cont':
            self.owner.vectors = {k: 'ff' * (len(v) // 2) for k, v in self.owner.vectors.items()}
            self.owner.resume()
        return {}
    def hmp(self, operation, cpu=0):
        return (f'PC={self.owner.pc:016x} SP=0000000a3000fff0 '
                f'X00={self.owner.x0:016x} X01=0000000a30001000')


class FakeGuest:
    def __init__(self):
        self.data = {'firmware_sha256': MAIN_SHA}
        self.actions = []; self.qmp = FakeQMP(self)
        self.pc = 0x40160000; self.fault = ''; self.saved = '00' * 268
        self.x0 = 0x1234; self.status = 0; self.stops = []; self.running = False
        self.gdb = self
        self.vectors = {i: 'ab' * (16 if i < 66 else 4) for i in range(34, 68)}
    def rsp(self, command, **kwargs):
        self.actions.append(command)
        if command.startswith('qXfer:features:read:target.xml'):
            return 'l<target><xi:include href="aarch64-fpu.xml"/></target>'
        if command == 'p21': return self.status.to_bytes(4, 'little').hex()
        if command.startswith('p'): return self.vectors[int(command[1:], 16)]
        if command.startswith('P'):
            number, value = command[1:].split('=')
            if int(number, 16) in self.vectors: self.vectors[int(number, 16)] = value
            if int(number, 16) == 33: self.status = int.from_bytes(bytes.fromhex(value), 'little')
        return self.saved if command == 'g' else None
    def log(self): return self.fault
    def resume(self):
        if self.stops:
            self.pc, self.x0, self.status = self.stops.pop(0)
    def sendall(self, data):
        self.actions.append(data.decode())
        if data.startswith(b'$vCont;'): self.resume()
    def recv(self, size): return b'+'


class NativeCallTests(unittest.TestCase):
    def test_task_policy_preserves_callee_irq_state_on_observer_resume(self):
        guest = FakeGuest(); guest.saved = '00' * 264 + '04000000'; guest.status = 4
        guest.stops = [(0x40F25960, 0x04300001, 0x60000385), (0x40160000, 1, 5)]
        result = invoke(guest, 0x410a95e8, [], cpu=1, stack_top=0xa3000fff0,
                        isolated=True, interrupt_policy='preserve',
                        observers={0x40F25960: lambda g, r: {'seen': True}})
        self.assertFalse(any(a.startswith('P21=') for a in guest.actions))
        self.assertEqual(sum(a.startswith('$vCont;c:2#') for a in guest.actions), 2)
        self.assertFalse(result['interrupt_mask_override'])
        self.assertTrue(result['context_restored'])

    def test_task_policy_rejects_irq_masked_or_wrong_el_entry_without_register_writes(self):
        for status in (0x85, 0, 0xD):
            guest = FakeGuest(); guest.saved = '00' * 264 + status.to_bytes(4, 'little').hex()
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                       isolated=True, interrupt_policy='preserve')
            self.assertFalse(any(a.startswith('P') for a in guest.actions))

    def notification_guest(self):
        guest = FakeGuest()
        guest.stops = [(0x40f25960, 0x04300001, 0x60000004),
                       (0x40160000, 0x1234, 0x600003c4)]
        return guest

    def test_observer_records_notification_then_same_isolated_cpu_returns(self):
        guest = self.notification_guest(); called = []
        def notification(owner, registers):
            self.assertIs(owner, guest)
            self.assertEqual(registers['X00'], 0x04300001)
            called.append(registers['PC'])
            return dict(event=hex(registers['X00']), payload_address=hex(registers['X01']))
        result = invoke(guest, 0x410a95e8, [], cpu=2, stack_top=0xa3000fff0,
                        isolated=True, observers={0x40f25960: notification})
        self.assertEqual(called, [0x40f25960])
        self.assertTrue(result['returned'])
        self.assertTrue(result['context_restored'])
        self.assertEqual(result['observations'][0]['data']['event'], '0x4300001')
        self.assertEqual(result['observations'][0]['cpu'], 2)
        self.assertEqual(sum(a.startswith('$vCont;c:3#') for a in guest.actions), 2)
        self.assertNotIn('cont', guest.actions)
        self.assertIn('P21=c4030060', guest.actions)  # preserve current NZCV
        self.assertIn('z1,40f25960,4', guest.actions)
        self.assertIn('G' + guest.saved, guest.actions)

    def test_observer_failure_cleans_every_breakpoint_and_register(self):
        guest = self.notification_guest(); before = dict(guest.vectors)
        def failing(owner, registers): raise LookupError('payload unreadable')
        with self.assertRaisesRegex(LookupError, 'payload unreadable'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: failing, 0x40f25000: lambda g, r: None})
        for pc in (0x40160000, 0x40f25960, 0x40f25000):
            self.assertIn(f'z1,{pc:x},4', guest.actions)
        self.assertEqual(guest.vectors, before)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertEqual(sum(a.startswith('$vCont;') for a in guest.actions), 1)
        self.assertFalse(guest.data['native_calls'][0]['returned'])
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])

    def test_observer_addresses_and_callbacks_validate_before_guest_access(self):
        invalid = ({0x40f25961: lambda g, r: None}, {0x40160000: lambda g, r: None},
                   {0x48000000: lambda g, r: None}, {-4: lambda g, r: None},
                   {'0x40f25960': lambda g, r: None}, {0x40f25960: None}, [], {True: lambda g, r: None})
        for observers in invalid:
            with self.subTest(observers=observers):
                guest = FakeGuest()
                with self.assertRaises(ValueError):
                    invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0, observers=observers)
                self.assertEqual(guest.actions, [])

    def test_missing_observer_is_not_notification_acceptance(self):
        guest = FakeGuest()
        with self.assertRaisesRegex(RuntimeError, 'not reached'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   observers={0x40f25960: lambda g, r: None})
        self.assertIn('z1,40f25960,4', guest.actions)
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])

    def test_non_json_observer_result_fails_before_resuming(self):
        guest = self.notification_guest()
        with self.assertRaisesRegex((TypeError, ValueError), 'JSON'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: lambda g, r: b'not JSON'})
        self.assertEqual(sum(a.startswith('$vCont;') for a in guest.actions), 1)
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])

    def test_observer_introducing_unknown_mmio_never_resumes(self):
        guest = self.notification_guest()
        def unknown(owner, registers): owner.fault = 'Z9_UNKNOWN'; return {}
        with self.assertRaisesRegex(RuntimeError, 'MMIO'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: unknown})
        self.assertEqual(sum(a.startswith('$vCont;') for a in guest.actions), 1)

    def test_observer_register_mutation_is_rejected_then_restored(self):
        guest = self.notification_guest(); before = dict(guest.vectors)
        def changes_fp(owner, registers): owner.vectors[34] = '11' * 16; return {}
        with self.assertRaisesRegex(RuntimeError, 'changed.*register'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: changes_fp})
        self.assertEqual(guest.vectors, before)
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])
        self.assertEqual(sum(a.startswith('$vCont;') for a in guest.actions), 1)

    def test_observer_must_leave_guest_paused(self):
        guest = self.notification_guest()
        def starts_running(owner, registers): owner.running = True; return {}
        with self.assertRaisesRegex(RuntimeError, 'observer resumed'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: starts_running})
        self.assertFalse(guest.running)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertEqual(sum(a.startswith('$vCont;') for a in guest.actions), 1)

    def test_partial_observer_install_failure_attempts_cleanup_of_all_attempted_pcs(self):
        guest = FakeGuest(); original = guest.rsp
        def install_failure(command, **kwargs):
            if command == 'Z1,40f25964,4': raise RuntimeError('observer install failed')
            return original(command, **kwargs)
        guest.rsp = install_failure
        with self.assertRaisesRegex(RuntimeError, 'observer install failed'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: lambda g, r: {}, 0x40f25964: lambda g, r: {}})
        for pc in (0x40160000, 0x40f25960, 0x40f25964):
            self.assertIn(f'z1,{pc:x},4', guest.actions)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertFalse(any(a.startswith('$vCont;') for a in guest.actions))

    def test_nonisolated_observer_continues_without_using_single_cpu_protocol(self):
        guest = self.notification_guest()
        result = invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                        observers={0x40f25960: lambda g, r: {'notified': True}})
        self.assertTrue(result['returned'])
        self.assertEqual(guest.actions.count('cont'), 2)
        self.assertFalse(any(a.startswith('$vCont;') for a in guest.actions))

    def test_observer_breakpoint_removal_failure_still_restores_context(self):
        guest = self.notification_guest(); original = guest.rsp
        def failed_remove(command, **kwargs):
            if command == 'z1,40f25960,4': raise RuntimeError('observer removal failed')
            return original(command, **kwargs)
        guest.rsp = failed_remove
        with self.assertRaisesRegex(RuntimeError, 'observer removal failed'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, observers={0x40f25960: lambda g, r: {}})
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertIn('P43=' + guest.vectors[67], guest.actions)
        self.assertFalse(guest.data['native_calls'][0]['context_restored'])

    def test_isolated_call_resumes_only_one_cpu_with_interrupts_masked(self):
        guest = FakeGuest()
        invoke(guest, 0x410a95e8, [], cpu=2, stack_top=0xa3000fff0, isolated=True)
        self.assertTrue(any(x.startswith('$vCont;c:3#') for x in guest.actions))
        self.assertIn('P21=c0030000', guest.actions)
        self.assertNotIn('cont', guest.actions)
        self.assertIn('G' + guest.saved, guest.actions)

    def test_isolated_call_cannot_service_other_cpus(self):
        guest = FakeGuest()
        with self.assertRaises(ValueError):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0,
                   isolated=True, advance=lambda running: True)
        self.assertEqual(guest.actions, [])

    def test_return_is_observed_and_full_context_restored(self):
        guest = FakeGuest()
        result = invoke(guest, 0x410a95e8, [31], cpu=0, stack_top=0xa3000fff0)
        self.assertEqual(result['result_x0'], 0x1234)
        self.assertTrue(result['returned'])
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertIn('z1,40160000,4', guest.actions)
        self.assertTrue(guest.data['cpu_register_redirection'])

    def test_unexpected_stop_is_failure_not_function_success(self):
        guest = FakeGuest(); guest.pc = 0x410a0000
        with self.assertRaisesRegex(RuntimeError, 'unexpected'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertFalse(guest.data['native_calls'][0]['returned'])

    def test_vector_and_fp_control_context_is_also_restored(self):
        guest = FakeGuest(); before = dict(guest.vectors)
        invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0)
        self.assertEqual(guest.vectors, before)

    def test_unknown_mmio_is_never_accepted(self):
        guest = FakeGuest(); guest.fault = 'Z9_UNKNOWN'
        with self.assertRaisesRegex(RuntimeError, 'MMIO'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0)

    def test_invalid_identity_or_stack_does_not_write_registers(self):
        for change in ('identity', 'stack'):
            with self.subTest(change=change):
                guest = FakeGuest()
                if change == 'identity': guest.data['firmware_sha256'] = 'wrong'
                with self.assertRaises(ValueError):
                    invoke(guest, 0x410a95e8, [], cpu=0,
                           stack_top=0xa3000fff1 if change == 'stack' else 0xa3000fff0)
                self.assertFalse(any(x.startswith('P') for x in guest.actions))

    def test_failed_breakpoint_removal_still_attempts_all_register_restoration(self):
        guest = FakeGuest()
        original = guest.rsp
        def fail_remove(command, **kwargs):
            if command.startswith('z1,'):
                raise RuntimeError('breakpoint removal failed')
            return original(command, **kwargs)
        guest.rsp = fail_remove
        with self.assertRaisesRegex(RuntimeError, 'restor'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertIn('P43=' + guest.vectors[67], guest.actions)
        self.assertFalse(guest.data['native_calls'][0]['context_restored'])

    def test_original_failure_survives_cleanup_error_and_remaining_fp_registers_restore(self):
        guest = FakeGuest(); guest.pc = 0x410a0000
        original = guest.rsp
        def fail_one_vector(command, **kwargs):
            if command.startswith('P22='):
                raise RuntimeError('one vector restore failed')
            return original(command, **kwargs)
        guest.rsp = fail_one_vector
        with self.assertRaisesRegex(RuntimeError, 'unexpected'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0)
        self.assertIn('P43=' + guest.vectors[67], guest.actions)
        row = guest.data['native_calls'][0]
        self.assertFalse(row['context_restored'])
        self.assertIn('vector restore failed', str(row['restore_errors']))

    def test_timeout_restores_context_without_claiming_return(self):
        guest = FakeGuest()
        with patch('z8_native_call.time.monotonic', side_effect=(0.0, 2.0)):
            with self.assertRaises(TimeoutError):
                invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0, timeout=1)
        self.assertIn('G' + guest.saved, guest.actions)
        self.assertFalse(guest.data['native_calls'][0]['returned'])
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])

    def test_isolated_ack_failure_restores_context(self):
        guest = FakeGuest(); guest.recv = lambda size: b'-'
        with self.assertRaisesRegex(RuntimeError, 'acknowledge'):
            invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0, isolated=True)
        self.assertTrue(guest.data['native_calls'][0]['context_restored'])

    def test_partial_core_packet_is_rejected_before_register_writes_in_all_modes(self):
        for isolated in (False, True):
            with self.subTest(isolated=isolated):
                guest = FakeGuest(); guest.saved = '00' * 267
                with self.assertRaises(RuntimeError):
                    invoke(guest, 0x410a95e8, [], cpu=0, stack_top=0xa3000fff0, isolated=isolated)
                self.assertFalse(any(x.startswith('P') for x in guest.actions))


if __name__ == '__main__': unittest.main()
