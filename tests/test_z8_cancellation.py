# SPDX-License-Identifier: GPL-2.0-or-later
"""Cancel real wait loops without firmware or a subprocess."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from probe_adc_capture import at
from probe_tasks import settle


class WaitingGuest:
    def __init__(self, *, cancellable=True, paused=False):
        if cancellable:
            self.cancel_event = threading.Event()
        self.qmp = self
        self.data = {}
        self.calls = []
        self.breakpoints = {0x7770}
        self.waiting = threading.Event()
        self.closed = threading.Event()
        self.force_stopped = paused
        self.cancel_on_query = False
        self.register_reads = 0
        self.context_error = None

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        self.context_error = kind
        self.force_stopped = True
        self.closed.set()

    def command(self, name):
        self.calls.append(name)
        if name == 'query-status':
            self.waiting.set()
            if self.cancel_on_query:
                self.cancel_event.set()
                return {'running': False}
            return {'running': not self.force_stopped}
        return {}

    def rsp(self, command):
        self.calls.append(command)
        address = int(command.split(',')[1], 16)
        if command.startswith('Z1,'):
            self.breakpoints.add(address)
        elif command.startswith('z1,'):
            self.breakpoints.remove(address)

    def hmp(self, name, cpu):
        self.register_reads += 1
        pc = 0x1000 if cpu == 0 else 0x2000
        return f'PC={pc:016x} X00=0000000000000000 X19=0000000000000080 SP=0000000000003000'

    def log(self):
        return ''


class CancellationTests(unittest.TestCase):
    def cancelled_wait(self, operation):
        guest = WaitingGuest()
        errors = []

        def run():
            try:
                with guest:
                    operation(guest)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            self.assertTrue(guest.waiting.wait(1), 'wait loop did not begin')
            guest.cancel_event.set()
            self.assertTrue(guest.closed.wait(1), 'cancellation did not leave the guest context')
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], InterruptedError)
            self.assertIs(guest.context_error, InterruptedError)
            self.assertIn('stop', guest.calls)
        finally:
            # Keep a pre-fix failing test from leaving a background waiter.
            guest.force_stopped = True
            thread.join(timeout=1)
        return guest

    def test_at_cancellation_exits_owner_and_removes_only_its_breakpoint(self):
        guest = self.cancelled_wait(lambda g: at(g, 0x1000))
        self.assertEqual(guest.breakpoints, {0x7770})
        self.assertIn('z1,1000,4', guest.calls)
        self.assertNotIn('stops', guest.data)

    def test_settle_cancellation_exits_owner_and_stops_guest(self):
        guest = self.cancelled_wait(lambda g: settle(g, 30))
        self.assertEqual(guest.calls.count('stop'), 1)
        self.assertEqual(guest.breakpoints, {0x7770})

    def test_preexisting_cancel_does_not_resume_or_set_breakpoint(self):
        for operation in (lambda g: at(g, 0x1000), lambda g: settle(g, 30)):
            with self.subTest(operation=operation):
                guest = WaitingGuest(paused=True)
                guest.cancel_event.set()
                with self.assertRaises(InterruptedError):
                    operation(guest)
                self.assertEqual(guest.calls, [])

    def test_cancel_at_paused_status_is_not_reported_as_success(self):
        guest = WaitingGuest()
        guest.cancel_on_query = True
        with self.assertRaises(InterruptedError):
            at(guest, 0x1000)
        self.assertEqual(guest.register_reads, 0)
        self.assertEqual(guest.breakpoints, {0x7770})

    def test_settle_checks_cancel_before_resuming_an_observer_stop(self):
        guest = WaitingGuest(paused=True)

        class Observer:
            def handle(self):
                guest.cancel_event.set()
                return True

        guest.early_shutdown_observer = Observer()
        with self.assertRaises(InterruptedError):
            settle(guest, 1)
        self.assertEqual(guest.calls.count('cont'), 1)
        self.assertEqual(guest.calls.count('stop'), 1)

    def test_legacy_guest_without_event_keeps_normal_breakpoint_result(self):
        guest = WaitingGuest(cancellable=False, paused=True)
        cpu, registers = at(guest, 0x1000)
        self.assertEqual(cpu, 0)
        self.assertEqual(registers['PC'], 0x1000)
        self.assertEqual(guest.breakpoints, {0x7770})
        self.assertEqual(guest.calls, ['Z1,1000,4', 'cont', 'query-status', 'z1,1000,4'])

    def test_legacy_zero_settle_keeps_cont_stop_sequence(self):
        guest = WaitingGuest(cancellable=False)
        settle(guest, 0)
        self.assertEqual(guest.calls, ['cont', 'stop'])


if __name__ == '__main__':
    unittest.main()
