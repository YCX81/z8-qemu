# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded read-only observer contracts; synthetic RAM, no firmware execution."""
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_still_hevc_observer as obs
import z8_capture_probe as probe


class ObserverTests(unittest.TestCase):
    def test_each_site_rearms_after_natural_instruction_and_stops_at_eight(self):
        g,_=self.guest();reader=obs.Observer(g);pc=0x40c9700c
        regs=dict(PC=pc,SP=0xa00100080,X00=0xa00200000)
        with patch.object(reader,'read',side_effect=lambda a,n:bytes(n)):
            for i in range(8):
                self.assertTrue(reader.can_arm(pc))
                armed=reader.transition([(1,regs)],{pc})
                row=reader.observe(pc,1,regs);self.assertEqual(row['site_hit'],i+1)
                if i<7:
                    self.assertEqual(armed,[pc+4]);self.assertFalse(reader.can_arm(pc))
                    self.assertEqual(reader.transition([(1,dict(regs,PC=pc+4,SP=0xa00100000))],{pc+4}),[pc])
                else:self.assertEqual(armed,[])
        self.assertFalse(reader.can_arm(pc));self.assertEqual(reader.events,8)
        with self.assertRaises(ValueError):reader.observe(pc,1,regs)

    def test_rearm_rejects_wrong_stack_or_request_identity(self):
        for change in (dict(SP=0xa00100008),dict(X19=0xa00200008)):
            g,_=self.guest();reader=obs.Observer(g);pc=0x40c978b4
            regs=dict(PC=pc,SP=0xa00100000,X19=0xa00200000)
            self.assertEqual(reader.transition([(0,regs)],{pc}),[pc+4])
            with self.assertRaises(ValueError):reader.transition([(0,dict(regs,PC=pc+4,**change))],{pc+4})

    def test_fault_copies_use_current_native_state_without_unbounded_walk(self):
        g,calls=self.guest();reader=obs.Observer(g)
        row=reader.fault()
        self.assertEqual(row['memory']['R']['address'],'0xa03b671a0')
        self.assertEqual(row['memory']['S']['address'],'0xa02d41bb8')
        self.assertEqual(row['memory']['C_copy']['hex'],bytes(range(0x10,0x88)).hex())
        self.assertEqual(row['memory']['B_copy']['hex'],bytes(range(0xa8,0xf8)).hex())
        self.assertEqual(len(calls),2)
        reader.bytes_requested=obs.MAX_TOTAL
        row=reader.fault();self.assertIn('R',row['errors']);self.assertIn('S',row['errors'])
        self.assertEqual(len(calls),2)

    def test_cli_and_peer_points_default_off(self):
        argv=['z8_capture_probe','--firmware','main.bin','--qemu','qemu',
              '--raw','input.raw','--output','fresh-output']
        for extra, enabled in (([],False),(['--still-hevc-observer'],True)):
            with patch.object(sys,'argv',argv+extra),patch.object(probe,'run') as run:
                probe.main()
            self.assertIs(run.call_args.args[0].still_hevc_observer,enabled)
            self.assertFalse(run.call_args.args[0].capture_recovery_observer)
            g=SimpleNamespace(_still_hevc_observer_enabled=enabled)
            with patch.object(probe.Peer,'__init__',return_value=None), \
                 patch.object(probe,'load_bindings',return_value={}), \
                 patch.object(probe,'load_configuration',return_value={'boot':{'points':{'boot4':0}}}), \
                 patch.object(probe,'capture_bindings',return_value={}), \
                 patch.object(probe,'observation_points',return_value={}), \
                 patch.object(probe.z8_capture_resources,'observation_points',return_value={}):
                peer=probe.CapturePeer(g,probe.TraceJournal('fixture'))
            self.assertEqual(set(obs.POINTS)&peer.trace_points.keys(),set(obs.POINTS) if enabled else set())
            self.assertEqual(peer.still_hevc_observer is not None,enabled)

    def guest(self):
        calls = []
        def hmp(command, cpu=0):
            calls.append((command, cpu))
            match = re.fullmatch(r'xp /(\d+)bx (0x[0-9a-f]+)', command)
            if not match:
                raise AssertionError('Only physical byte reads permitted')
            size, address = int(match[1]), int(match[2], 16)
            return '\n'.join(f'{address+i:016x}: ' + ' '.join(
                f'0x{(i+j)&255:02x}' for j in range(min(8, size-i)))
                for i in range(0, size, 8))
        return SimpleNamespace(qmp=SimpleNamespace(hmp=hmp)), calls

    def test_hmp_read_bounds_and_exact_contiguous_response(self):
        g, calls = self.guest(); reader = obs.Observer(g)
        data = reader.read(0xa00001000, 0x150)
        self.assertEqual(data, bytes(range(256)) + bytes(range(80)))
        for address, size in ((0x16020000, 4), (0, 8), (0xa20000000-4, 8),
                              (0xa00001000, 513), (0xa00001000, 0), (-1, 4)):
            with self.subTest(address=address, size=size), self.assertRaises(ValueError):
                reader.read(address, size)
        self.assertEqual(len(calls), 1)
        g.qmp.hmp = lambda *a: '0000000a00001001: 0x00'
        with self.assertRaises(ValueError): reader.read(0xa00001000, 1)

    def test_budget_charged_before_failed_transport_and_no_more_reads(self):
        g, calls = self.guest(); reader = obs.Observer(g)
        reader.bytes_requested = obs.MAX_TOTAL - 4
        g.qmp.hmp = lambda *a: (_ for _ in ()).throw(RuntimeError('transport'))
        with self.assertRaises(RuntimeError): reader.read(0xa00001000, 4)
        with self.assertRaises(ValueError): reader.read(0xa00001000, 1)
        self.assertEqual(reader.bytes_requested, obs.MAX_TOTAL)

    def test_constructed_and_submit_stack_offsets_and_full_registers(self):
        g, calls = self.guest(); reader = obs.Observer(g)
        regs = {f'X{i:02}': 0 for i in range(31)}
        regs.update(SP=0xa00100000, X19=0xa00200000, PC=0x40c97808)
        row = reader.observe(regs['PC'], 2, regs)
        self.assertEqual(row['registers']['X30'], '0x0')
        for label, address, size in (('R', regs['X19'], 0x150),
                ('C', regs['SP']+0x78, 0x78), ('B', regs['SP']+0x28, 0x50),
                ('S', 0xa02d41bb8, 0x160)):
            self.assertEqual(row['memory'][label]['address'], hex(address))
            self.assertEqual(len(bytes.fromhex(row['memory'][label]['hex'])), size)
        self.assertNotIn('F', row['memory'])  # Not constructed at this PC.
        row = reader.observe(0x40c97864, 2, regs)
        self.assertEqual(row['memory']['F']['address'], hex(regs['SP']+0xf8))
        self.assertFalse(row['cpu_state_substituted'])
        self.assertLessEqual(max(int(re.search(r'/(\d+)', c)[1]) for c, _ in calls), 512)

    def test_bad_pointer_recorded_without_reading_mmio(self):
        g, calls = self.guest(); reader = obs.Observer(g)
        row = reader.observe(0x40c9700c, 0, dict(X00=0x16020000, PC=0x40c9700c))
        self.assertIn('R', row['errors'])
        self.assertEqual(len(calls), 1)  # S only
        reader.events = obs.MAX_EVENTS
        with self.assertRaises(ValueError): reader.observe(0x40c9700c, 0, {})
        self.assertEqual(len(calls), 1)

    def test_result_entry_and_nal_consumer_use_distinct_abi_arguments(self):
        g,_ = self.guest(); reader=obs.Observer(g)
        request=bytearray(0x150)
        struct_pack=struct.pack_into
        struct_pack('<Q',request,0x98,0xa00003000)
        struct_pack('<I',request,0x11c,4)
        struct_pack('<I',request,0x134,0x1000)
        struct_pack('<Q',request,0x138,0xa00004000)
        with patch.object(reader,'read',side_effect=lambda pa,size:bytes(request) if pa==0xa00001000 else bytes(size)) as read:
            row=reader.observe(0x40c99030,0,dict(X00=0xa00001000,X01=0xa00002000))
            self.assertEqual(row['memory']['D']['address'],'0xa00002000')
            self.assertEqual(row['memory']['O_before_consume']['size'],0x1e0)
            self.assertEqual(row['memory']['T3_first4']['address'],'0xa00007000')
            self.assertFalse(row['errors'])
            row=reader.observe(0x40c5cd4c,0,dict(X00=0xa00002000))
            self.assertEqual(row['memory']['D_before_result']['size'],0x20)
            self.assertNotIn('R',row['memory'])
            self.assertTrue(all(call.args[1]<=512 for call in read.call_args_list))

    def test_fault_registers_opt_in_independent_errors_and_first_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def hmp(command, cpu):
                calls.append((command, cpu))
                if cpu == 1: raise RuntimeError('offline')
                return f'PC=0000000040c5bb18 X00={cpu:016x}'
            g = SimpleNamespace(out=Path(folder), data={'capture_trace': {}},
                qmp=SimpleNamespace(hmp=hmp), _still_hevc_observer_enabled=True)
            with patch.object(probe, 'state', return_value={}):
                probe.capture_first_fault(g, RuntimeError('Unmodeled hardware access'))
                probe.capture_first_fault(g, RuntimeError('Unmodeled hardware access'))
            row=json.loads((g.out/'first-fault-state.json').read_text())
            self.assertEqual(calls, [('info registers', i) for i in range(4)])
            self.assertEqual(len(row['cpu_registers']), 3)
            self.assertIn('cpu1', row['errors'])
            self.assertIn('z8-still-hevc-state', row['device_states'])
            self.assertFalse(row['collection_complete'])


if __name__ == '__main__': unittest.main()
