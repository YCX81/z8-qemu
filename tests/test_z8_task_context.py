# SPDX-License-Identifier: GPL-2.0-or-later
"""Contract tests for finding, never manufacturing, an RTOS task context."""
import struct
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_task_context as context
from boot import key_body


class FakeQMP:
    def __init__(self, guest): self.guest = guest
    def command(self, name, **kwargs):
        self.guest.actions.append(name)
        if name == 'query-status': return {'running': self.guest.running}
        if name == 'cont': self.guest.running = True
        if name == 'stop': self.guest.running = False
        return {}
    def hmp(self, name, cpu=0):
        self.guest.actions.append(('hmp', cpu))
        return f'PC={self.guest.pcs[cpu]:016x}'


class FakeGuest:
    def __init__(self):
        self.data = {'firmware_sha256': context.MAIN_SHA}
        self.actions = []; self.reads = []; self.running = False; self.fault = ''
        self.pcs = [0x40189a7c] * 4; self.pcs[2] = context.RX_RETIRED_PC
        self.cpu = 0; self.pstate = 0x60000004
        self.tpidr = 0xa04985000; self.tcb = 0xa04990000
        self.memory = {}; self.qmp = FakeQMP(self)
        self.put(self.tpidr, 0x54)
        self.put(self.tpidr, struct.pack('<Q', self.tcb))
        self.put(self.tpidr + 0x14, struct.pack('<I', 1))
        self.put(self.tcb, 0x20)
        self.put(self.tcb + 0x19, b'\x10')
        self.put(context.GLOBAL_SPINLOCK, bytes(4))
        self.put(context.MUTEX_COUNT, struct.pack('<I', 8))
        for index, address in enumerate(context.MUTEX_OBJECTS.values(), 1):
            self.put(address, struct.pack('<I', index))
            record = context.MUTEX_TABLE + (index - 1) * 0x28
            descriptor = 0xa04000000 + index * 0x10
            self.put(record, 0x28)
            self.put(record + 0x10, struct.pack('<Q', descriptor))
            self.put(descriptor, struct.pack('<II', 0, 0))
        self.xml = {
            'target.xml': '<target xmlns:xi="http://www.w3.org/2001/XInclude"><architecture>aarch64</architecture><xi:include href="core.xml"/><xi:include href="sys.xml"/></target>',
            'core.xml': '<feature><reg name="pc" bitsize="64" regnum="32"/><reg name="cpsr" bitsize="32"/></feature>',
            'sys.xml': '<feature><reg name="TPIDR_EL1" bitsize="64" regnum="117"/></feature>',
        }
    def put(self, address, value):
        if isinstance(value, int): value = bytes(value)
        self.memory.update({address + i: byte for i, byte in enumerate(value)})
    def read_bytes(self, address, size):
        self.reads.append((address, size))
        return bytes(self.memory[address + i] for i in range(size))
    def log(self): return self.fault
    def rsp(self, command, **kwargs):
        self.actions.append(command)
        if command.startswith('Hg'):
            self.cpu = int(command[2:], 16) - 1; return None
        if command.startswith('qXfer:'):
            name, span = command.split(':')[-2:]
            offset, requested = [int(x, 16) for x in span.split(',')]
            text = self.xml[name]; part = text[offset:offset + min(requested, 75)]
            return ('l' if offset + len(part) == len(text) else 'm') + part
        number = int(command[1:], 16)
        if number == 32: return self.pcs[self.cpu].to_bytes(8, 'little').hex()
        if number == 33: return self.pstate.to_bytes(4, 'little').hex()
        if number == 117: return self.tpidr.to_bytes(8, 'little').hex()
        raise AssertionError(command)


class FakePeer:
    def __init__(self):
        self.points = {context.RX_RETIRED_PC: 'rx_frame_retired'}
        self.rx_clear_pc = context.RX_RETIRED_PC
        self.record = {'events': [], 'rx': []}; self.sent = []; self.asserted = set()
    def enqueue_external(self, body, family): self.sent.append((body, family))


class FakeSession:
    def __init__(self, guest):
        self.g = guest; self.peer = FakePeer(); self.handled = 0; self.states = []
    def advance(self, running):
        if running:
            if self.states:
                owner = self.states.pop(0)
                self.g.put(context.MUTEX_TABLE + 0x18, struct.pack('<Q', owner))
                self.g.pcs[2] = context.RX_RETIRED_PC; self.g.running = False
            return False
        self.handled += 1; self.g.pcs[2] = 0x40189a7c
        return True


class TaskContextTests(unittest.TestCase):
    def test_real_el1t_and_el1h_contexts_are_read_only_and_xml_cached(self):
        guest = FakeGuest()
        for mode in (4, 5):
            guest.pstate = 0x60000000 | mode
            result = context.read_current_context(guest, 2)
            self.assertTrue(result['callable'], result)
            self.assertEqual(result['current_tcb'], hex(guest.tcb))
            self.assertEqual(result['tpidr_el1'], hex(guest.tpidr))
        self.assertEqual(sum(a == 'qXfer:features:read:target.xml:0,1000' for a in guest.actions), 1)
        self.assertIn('p75', guest.actions)  # XML regnum 117, never a guessed system register number.
        self.assertFalse(any(isinstance(a, str) and a.startswith(('P', 'G', 'Z', 'z')) for a in guest.actions))
        self.assertNotIn('cont', guest.actions)

    def test_each_original_guard_refuses_an_unsafe_context(self):
        changes = [
            lambda g: setattr(g, 'pstate', 0x84),
            lambda g: setattr(g, 'pstate', 0),
            lambda g: g.put(g.tpidr + 0x50, struct.pack('<I', 1)),
            lambda g: g.put(g.tpidr + 0x14, bytes(4)),
            lambda g: g.put(g.tpidr, bytes(8)),
            lambda g: g.put(context.GLOBAL_SPINLOCK, struct.pack('<I', 1)),
            lambda g: g.put(context.MUTEX_TABLE + 0x18, struct.pack('<Q', g.tcb)),
            lambda g: g.put(context.MUTEX_TABLE + 0x18, struct.pack('<Q', g.tcb + 0x100)),
            lambda g: g.put(0xa04000010, struct.pack('<I', 0xffffffff)),
            lambda g: g.put(next(iter(context.MUTEX_OBJECTS.values())), bytes(4)),
            lambda g: g.put(next(iter(context.MUTEX_OBJECTS.values())), struct.pack('<I', 9)),
            lambda g: g.put(0xa04000010, struct.pack('<II', 3, 0x11)),
            lambda g: setattr(g, 'tpidr', 0x15080000),
        ]
        for change in changes:
            guest = FakeGuest(); change(guest)
            with self.subTest(change=change):
                result = context.read_current_context(guest, 2)
                self.assertFalse(result['callable']); self.assertTrue(result['reasons'])
                self.assertNotIn('cont', guest.actions)

    def test_wrong_cpu_identity_running_or_unknown_fail_before_reads(self):
        for kind in ('cpu', 'identity', 'running', 'unknown'):
            guest = FakeGuest(); cpu = 2
            if kind == 'cpu': cpu = True
            if kind == 'identity': guest.data['firmware_sha256'] = 'bad'
            if kind == 'running': guest.running = True
            if kind == 'unknown': guest.fault = 'Z9_UNKNOWN'
            with self.subTest(kind=kind), self.assertRaises((ValueError, RuntimeError)):
                context.read_current_context(guest, cpu)
            self.assertEqual(guest.reads, [])

    def test_missing_or_wrong_size_system_register_is_an_error(self):
        for xml in ('<feature/>', '<feature><reg name="TPIDR_EL1" bitsize="32" regnum="117"/></feature>'):
            guest = FakeGuest(); guest.xml['sys.xml'] = xml
            with self.assertRaises(RuntimeError): context.read_current_context(guest, 2)

    def test_xml_cycles_duplicates_malformed_and_bad_annex_are_rejected(self):
        cases = [
            '<feature xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="sys.xml"/></feature>',
            '<feature><reg name="pc" bitsize="64" regnum="32"/></feature>',
            '<feature><reg name="missing-bits"/></feature>',
            '<feature>',
            '<feature xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="../bad.xml"/></feature>',
        ]
        for xml in cases:
            guest = FakeGuest(); guest.xml['sys.xml'] = xml
            with self.subTest(xml=xml), self.assertRaises(RuntimeError):
                context.read_current_context(guest, 2)

    def test_actual_qemu_target_allows_only_its_unbound_xi_include_prefix(self):
        guest = FakeGuest()
        guest.xml['target.xml'] = ('<?xml version="1.0"?><!DOCTYPE target SYSTEM "gdb-target.dtd">'
                                  '<target><architecture>aarch64</architecture>'
                                  '<xi:include href="core.xml"/><xi:include href="sys.xml"/></target>')
        self.assertTrue(context.read_current_context(guest, 2)['callable'])
        for target in ('<target><other:include href="core.xml"/></target>',
                       '<target><xi:unexpected/></target>',
                       '<target><xi:include href="core.xml"></target>'):
            guest = FakeGuest(); guest.xml['target.xml'] = target
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                context.read_current_context(guest, 2)

    def test_actual_qemu_fpu_system_aliases_do_not_ambiguate_required_registers(self):
        guest = FakeGuest()
        guest.xml['core.xml'] = guest.xml['core.xml'].replace('</feature>',
            '<reg name="fpsr" bitsize="32" regnum="66"/>'
            '<reg name="fpcr" bitsize="32" regnum="67"/></feature>')
        guest.xml['sys.xml'] = guest.xml['sys.xml'].replace('</feature>',
            '<reg name="FPCR" bitsize="64" regnum="197" type="int" group="cp_regs"/>'
            '<reg name="FPSR" bitsize="64" regnum="198" type="int" group="cp_regs"/></feature>')
        self.assertTrue(context.read_current_context(guest, 2)['callable'])
        self.assertEqual(guest._z8_task_context_register_aliases['FPSR'], [(66, 32), (198, 64)])
        self.assertEqual(guest._z8_task_context_register_aliases['FPCR'], [(67, 32), (197, 64)])
        self.assertNotIn('p42', guest.actions); self.assertNotIn('pc6', guest.actions)
        # A duplicate required register or overlapping numeric register ID
        # remains an error, even when a spelling differs only by case.
        for tag in ('<reg name="tpidr_el1" bitsize="64" regnum="199"/>',
                    '<reg name="unrelated" bitsize="64" regnum="117"/>'):
            other = FakeGuest(); other.xml['sys.xml'] = other.xml['sys.xml'].replace('</feature>', tag + '</feature>')
            with self.assertRaises(RuntimeError): context.read_current_context(other, 2)

    def test_incomplete_xml_register_and_ram_reads_are_errors(self):
        for kind in ('xml', 'register', 'ram'):
            guest = FakeGuest(); original = guest.rsp
            if kind == 'xml': guest.rsp = lambda command, **kw: 'm' if command.startswith('qXfer') else original(command, **kw)
            if kind == 'register': guest.rsp = lambda command, **kw: 'E14' if command == 'p75' else original(command, **kw)
            if kind == 'ram': guest.read_bytes = lambda address, size: bytes(size - 1)
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                context.read_current_context(guest, 2)

    def test_invalid_table_count_descriptor_and_boundary_are_not_callable(self):
        for kind in ('count', 'descriptor', 'pc'):
            guest = FakeGuest()
            if kind == 'count': guest.put(context.MUTEX_COUNT, bytes(4))
            if kind == 'descriptor': guest.put(context.MUTEX_TABLE + 0x10, bytes(8))
            if kind == 'pc': guest.pcs[2] = 0x40189a7c
            with self.subTest(kind=kind):
                self.assertFalse(context.read_current_context(guest, 2)['callable'])

    def test_existing_good_peer_boundary_stays_unhandled_and_paused(self):
        guest = FakeGuest(); session = FakeSession(guest)
        cpu, evidence = context.wait_for_task_context(session)
        self.assertEqual(cpu, 2); self.assertTrue(evidence['context']['callable'])
        self.assertEqual(session.handled, 0); self.assertEqual(session.peer.sent, [])
        self.assertIn(context.RX_RETIRED_PC, session.peer.points)
        self.assertNotIn('cont', guest.actions); self.assertFalse(guest.running)

    def test_busy_owner_resumes_normal_peer_then_stops_at_later_boundary(self):
        guest = FakeGuest(); session = FakeSession(guest)
        guest.put(context.MUTEX_TABLE + 0x18, struct.pack('<Q', guest.tcb + 0x100))
        session.states = [0]
        with patch.object(context.time, 'sleep'):
            cpu, evidence = context.wait_for_task_context(session)
        self.assertEqual(cpu, 2); self.assertEqual(session.handled, 1)
        self.assertEqual(session.peer.sent, [(key_body(), 2)])
        self.assertEqual(len(evidence['attempts']), 2)
        self.assertFalse(guest.running)

    def test_idle_start_sends_only_neutral_key_release(self):
        guest = FakeGuest(); session = FakeSession(guest)
        guest.pcs[2] = 0x40189a7c; session.states = [0]
        with patch.object(context.time, 'sleep'):
            cpu, _ = context.wait_for_task_context(session)
        self.assertEqual(cpu, 2); self.assertEqual(session.peer.sent, [(key_body(), 2)])
        self.assertEqual(session.handled, 0)

    def test_timeout_and_peer_error_leave_the_guest_paused(self):
        for failure in ('timeout', 'peer'):
            guest = FakeGuest(); guest.pcs[2] = 0x40189a7c; session = FakeSession(guest)
            if failure == 'peer': session.advance = lambda running: (_ for _ in ()).throw(RuntimeError('peer failed'))
            with self.subTest(failure=failure), patch.object(context.time, 'sleep'):
                with self.assertRaises((TimeoutError, RuntimeError)):
                    context.wait_for_task_context(session, timeout=0.01)
            self.assertFalse(guest.running)

    def test_busy_lock_or_asserted_irq_cannot_exceed_frame_bound(self):
        for kind in ('lock', 'irq'):
            guest = FakeGuest(); session = FakeSession(guest)
            if kind == 'lock': guest.put(context.GLOBAL_SPINLOCK, struct.pack('<I', 1))
            if kind == 'irq': session.peer.asserted.add(26)
            session.states = [0]
            with self.subTest(kind=kind), patch.object(context.time, 'sleep'):
                with self.assertRaisesRegex(TimeoutError, 'frame bound'):
                    context.wait_for_task_context(session, max_frames=1)
            self.assertFalse(guest.running)
            self.assertEqual(len(session.peer.sent), 1)

    def test_unexpected_stop_unknown_mmio_and_bad_cpu_snapshot_are_errors(self):
        for kind in ('stop', 'unknown', 'snapshot'):
            guest = FakeGuest(); session = FakeSession(guest); guest.pcs[2] = 0x40189a7c
            def advance(running):
                if running:
                    guest.running = False
                    if kind == 'unknown': guest.fault = 'Z9_UNKNOWN'
                return False
            session.advance = advance
            if kind == 'snapshot': guest.qmp.hmp = lambda *args: 'missing PC'
            with self.subTest(kind=kind), patch.object(context.time, 'sleep'):
                with self.assertRaises(RuntimeError): context.wait_for_task_context(session)
            self.assertFalse(guest.running)

    def test_only_owned_retirement_breakpoints_are_eligible(self):
        guest = FakeGuest(); session = FakeSession(guest)
        session.peer.points[context.RX_RETIRED_PC] = 'wrong_label'
        with patch.object(context.time, 'sleep'), self.assertRaises(TimeoutError):
            context.wait_for_task_context(session, timeout=0.01)
        self.assertEqual(session.handled, 0)

    def test_wrong_peer_binding_or_invalid_limits_fail_before_queueing(self):
        for options in ({'timeout': 0}, {'timeout': 31}, {'max_frames': 0}, {'max_frames': True}):
            guest = FakeGuest(); session = FakeSession(guest)
            with self.assertRaises(ValueError): context.wait_for_task_context(session, **options)
            self.assertEqual(session.peer.sent, [])
        guest = FakeGuest(); session = FakeSession(guest); session.peer.rx_clear_pc += 4
        with self.assertRaises(ValueError): context.wait_for_task_context(session)


if __name__ == '__main__': unittest.main()
