# SPDX-License-Identifier: GPL-2.0-or-later
"""Authored resource inputs and passive native lookup guards; no firmware."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_capture_resources as resources
import z8_capture_probe as probe
from z8_resources import plan as boot_resource_plan


class ResourceInputs(unittest.TestCase):
    def guest(self, folder):
        return SimpleNamespace(out=folder, firmware=folder/'original-main', proc=None,
            command=['qemu', '-kernel', str(folder/'original-main')],
            data={'firmware_sha256': resources.MAIN_SHA})

    def options(self, timing=True, processing=True):
        return SimpleNamespace(capture_timing_input=timing, hsfc_processing_input=processing)

    def validated(self):
        return patch.object(resources, 'validate_firmware', return_value=dict(bytes=81807709, sha256=resources.MAIN_SHA))

    def test_omitted_options_have_no_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            g=self.guest(Path(tmp)); original=list(g.command)
            self.assertIsNone(resources.attach(g, SimpleNamespace()))
            self.assertEqual(g.command, original)
            self.assertEqual(list(g.out.iterdir()), [])
            self.assertEqual(resources.observation_points(g), {})

    def test_exact_fixed_inputs_identity_hash_and_unverified_initial_status(self):
        with tempfile.TemporaryDirectory() as tmp, self.validated() as validate:
            g=self.guest(Path(tmp)); record=resources.attach(g, self.options())
            validate.assert_called_once()
            self.assertEqual(record['source_main']['sha256'], resources.MAIN_SHA)
            targets={r['address']:r for r in record['inputs'].values()}
            self.assertEqual(set(targets), {'0x47f63022','0x47f5af4f'})
            for address,value in (('0x47f63022',struct.pack('<H',1200)),('0x47f5af4f',bytes([1]))):
                row=targets[address]; path=g.out/row['file']
                self.assertEqual(path.read_bytes(), value)
                self.assertEqual(row['sha256'], hashlib.sha256(value).hexdigest())
                self.assertTrue(row['supplied'])
                self.assertFalse(row['actual_lookup_verified'])
                self.assertFalse(row['hardware_calibration'])
                self.assertIn(f'loader,file={path},addr={address},force-raw=on',g.command)
            self.assertEqual(len(resources.observation_points(g)),6)
            self.assertFalse(record['original_main_modified'])
            self.assertFalse(record['nef_accepted'])

    def test_new_cells_do_not_overlap_original_main_or_other_boot_inputs(self):
        existing=boot_resource_plan(native_sampling=True,battery_limits=True)
        existing.append(dict(address=0x47f5af42,data=bytes([1])))
        for spec in resources.SPECS.values():
            first,last=spec['address'],spec['address']+spec['size']
            self.assertGreaterEqual(first,0x47e80000)
            self.assertLessEqual(last,0x47f80000)
            self.assertGreater(first,0x40160000+81807709)
            for entry in existing:
                self.assertFalse(first<entry['address']+len(entry['data']) and last>entry['address'])

    def test_reject_identity_kernel_live_install_and_nonboolean_flags(self):
        for kind in ('hash','kernel','running','badflag','validator'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp, self.validated() as validate:
                g=self.guest(Path(tmp)); args=self.options()
                if kind=='hash':g.data['firmware_sha256']='0'*64
                if kind=='kernel':g.command[-1]=str(g.out/'derived-main')
                if kind=='running':g.proc=object()
                if kind=='badflag':args.capture_timing_input=1
                if kind=='validator':validate.side_effect=ValueError('wrong original MAIN bytes')
                with self.assertRaises(ValueError):resources.attach(g,args)
                self.assertEqual(list(g.out.iterdir()),[])

    def test_all_outputs_are_preflighted_before_installing_any_cell(self):
        with tempfile.TemporaryDirectory() as tmp, self.validated():
            g=self.guest(Path(tmp)); resources.attach(g,self.options(False,True))
            old={p.name:p.read_bytes() for p in g.out.iterdir()}; command=list(g.command)
            with self.assertRaises(FileExistsError): resources.attach(g,self.options())
            self.assertEqual({p.name:p.read_bytes() for p in g.out.iterdir()},old)
            self.assertEqual(g.command,command)

    def test_probe_rejects_invalid_flag_before_preparing_guest(self):
        with patch.object(probe,'PreparedGuest') as prepare:
            with self.assertRaises(ValueError):
                probe.run(SimpleNamespace(capture_timing_input='on'))
            prepare.assert_not_called()

    def test_probe_attaches_only_explicit_inputs_before_start_and_records_source(self):
        class BeforeStart(Exception): pass
        class Prepared:
            def __init__(self, folder):
                self.out=folder;self.firmware=folder/'original-main';self.proc=None
                self.command=['qemu','-M','nikon-z8-bringup','-kernel',str(self.firmware)]
                self.data={'firmware_sha256':resources.MAIN_SHA}
            def __enter__(self):raise BeforeStart()
            def __exit__(self,*args):raise AssertionError('QEMU must not start')
        with tempfile.TemporaryDirectory() as tmp,self.validated():
            g=Prepared(Path(tmp))
            args=SimpleNamespace(capture_timing_input=True,hsfc_processing_input=False,
                seconds=15,output=g.out,firmware=g.firmware,qemu=g.out/'unused-qemu',raw=g.out/'unused-raw',
                capture_properties=None,card=False,tagged_input=False,capture_selector=False,
                capture_pixels=False,gpio=False)
            with patch.object(probe,'PreparedGuest',return_value=g), \
                 patch.object(probe,'contact_sequence',return_value=([],0)),self.assertRaises(BeforeStart):
                probe.run(args)
            self.assertEqual(set(g.data['capture_resource_inputs']['inputs']),{'timing'})
            self.assertTrue(any(Path(p).name=='z8_capture_resources.py' for p in g.data['probe_sources']))


class NativeLookupGuards(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.g=ResourceInputs().guest(Path(self.tmp.name))
        with ResourceInputs().validated():resources.attach(self.g,ResourceInputs().options())
        self.base=0xa155d78c0;self.obj=0xa00010000
        self.input=bytearray(0x1a8)
        struct.pack_into('<I',self.input,0xe0,3)
        struct.pack_into('<I',self.input,0x174,1)
        self.memory={self.obj:bytes(self.input),0xa0496f6a0:struct.pack('<Q',self.base),
            0xa0291da98:struct.pack('<Q',self.base),0xa02aa880c:struct.pack('<I',15),
            0x47f63022:struct.pack('<H',1200),self.base+0xe3022:struct.pack('<H',1200),
            0x47f5af4f:bytes([1]),self.base+0xdaf4f:bytes([1])}
        self.reads=[]
        def read(address,size):
            self.reads.append((address,size))
            return self.memory.get(address,b'')[:size]
        self.g.read_bytes=read
        self.g.qmp=SimpleNamespace(command=lambda command:dict(running=False,status='paused'))
        self.regs=dict(PC=0x41172254,X00=self.obj,X19=self.obj,X10=1,X21=self.base+0xe3022,
                       X08=15,X09=0xa0496f6a0,X13=self.base,X14=self.base+15,X15=0xdaf66,X20=1200)

    def observe(self,pc,**changes):
        return resources.observe(self.g,pc,dict(self.regs,PC=pc,**changes),cpu=0,t_ns=100)

    def test_timing_preload_is_not_native_load_completion(self):
        row=self.observe(0x41172254)
        self.assertTrue(row['guard_passed']);self.assertFalse(row['native_load_completed'])
        self.assertFalse(self.g.data['capture_resource_inputs']['inputs']['timing']['actual_lookup_verified'])
        row=self.observe(0x41172258)
        self.assertTrue(row['native_load_completed'])
        self.assertTrue(self.g.data['capture_resource_inputs']['inputs']['timing']['actual_lookup_verified'])
        self.assertEqual(row['ram_address'],hex(self.base+0xe3022))

    def test_timing_target_rejects_changed_selector_address_value_and_fallback(self):
        for pc,change in ((0x41172254,dict(X10=0)),(0x41172254,dict(X21=0x15485800)),
                          (0x41172258,dict(X20=0)),(0x4117239c,{})):
            with self.subTest(pc=pc,change=change),self.assertRaises(ValueError):self.observe(pc,**change)
        self.assertNotIn((0x15485800,2),self.reads)
        self.memory[self.base+0xe3022]=bytes(2)
        with self.assertRaises(ValueError):self.observe(0x41172254)
        self.assertTrue(self.g.data['capture_resource_inputs']['observations'])

    def test_other_timing_mode_is_not_applicable_and_never_verified(self):
        for offset,value in ((0,13),(0xe0,2),(0x174,0)):
            data=bytearray(self.input);struct.pack_into('<I',data,offset,value)
            self.memory[self.obj]=bytes(data)
            row=self.observe(0x41172254)
            self.assertFalse(row['applicable']);self.assertEqual(row['status'],'not_applicable')
        self.assertFalse(self.g.data['capture_resource_inputs']['inputs']['timing']['actual_lookup_verified'])

    def processing(self):
        self.memory[self.obj]=struct.pack('<I',14)
        self.regs['X10']=0xdaf40

    def test_processing_slot_load_and_record_address_use_original_values(self):
        self.processing()
        row=self.observe(0x41170d5c)
        self.assertTrue(row['guard_passed']);self.assertFalse(row['native_load_completed'])
        row=self.observe(0x41170d60,X14=1)
        self.assertTrue(row['native_load_completed'])
        # At d84 the original d7c/d80 have repurposed X14 as the NEXT
        # record field offset. The record index0 only existed earlier.
        row=self.observe(0x41170d84,X14=0xdaf6c)
        self.assertEqual(row['record_read_address'],hex(self.base+0xdaf66))
        self.assertEqual(row['native_record_offsets'],dict(current='0xdaf66',next='0xdaf6c'))
        self.assertTrue(self.g.data['capture_resource_inputs']['inputs']['processing']['actual_lookup_verified'])

    def test_processing_rejects_bad_map_slot_result_descriptor_and_record_offsets(self):
        self.processing()
        for pc,change in ((0x41170d5c,dict(X08=14)),(0x41170d5c,dict(X14=self.base+14)),
                          (0x41170d60,dict(X14=0)),
                          (0x41170d84,dict(X13=self.base+32,X14=0xdaf6c)),
                          (0x41170d84,dict(X14=0)),
                          (0x41170d84,dict(X14=0xdaf6c,X15=0xdaf64))):
            with self.subTest(pc=pc,change=change),self.assertRaises(ValueError):self.observe(pc,**change)
        self.memory[0xa0291da98]=struct.pack('<Q',self.base+8)
        with self.assertRaises(ValueError):self.observe(0x41170d5c)

    def test_other_processing_mode_does_not_access_target_or_claim_verified(self):
        self.memory[self.obj]=struct.pack('<I',13)
        row=self.observe(0x41170d5c,X08=0)
        self.assertEqual(row['status'],'not_applicable')
        self.assertNotIn((0x47f5af4f,1),self.reads)

    def test_bad_point_running_and_short_or_mmio_pointer_rejected(self):
        with self.assertRaises(ValueError):self.observe(0x40000000)
        with self.assertRaises(ValueError):self.observe(0x41172254,X19=0x15480000)
        self.memory[self.obj]=bytes(2)
        with self.assertRaises(ValueError):self.observe(0x41172254)
        self.g.qmp.command=lambda command:dict(running=True)
        with self.assertRaises(ValueError):self.observe(0x41170d5c)

    def test_probe_peer_passes_native_registers_to_enabled_guard_without_guest_writes(self):
        commands=[]
        def hmp(command,cpu):
            regs={f'X{i:02}':0 for i in range(31)}
            regs.update(self.regs,SP=0xa06c4efe0,X30=0x40c00294,
                        PC=0x41172258 if cpu==0 else 0x40000000+cpu*4)
            return ' '.join(f'{key}={value:x}' for key,value in regs.items())
        original_command=self.g.qmp.command
        self.g.qmp=SimpleNamespace(hmp=hmp,command=original_command)
        self.g.rsp=commands.append;self.g.data['capture_trace']={}
        peer=probe.CapturePeer.__new__(probe.CapturePeer)
        peer.g=self.g;peer.points={};peer.record={'events':[]}
        peer.cseq=probe.CseqObserver();peer.observation_count=0
        peer.journal=probe.TraceJournal('resource-test');peer.now=lambda:100
        peer.arm(0x41172258,'capture_resource_timing_loaded')
        self.assertTrue(peer.handle())
        event=peer.journal.events[-1]
        self.assertTrue(event['resource_input']['native_load_completed'])
        self.assertEqual(commands,['Z1,41172258,4','z1,41172258,4'])

    def test_peer_installs_observers_only_for_enabled_resources(self):
        def init(peer,guest,**kwargs):peer.g=guest
        for enabled in (False,True):
            g=self.g if enabled else SimpleNamespace(data={})
            with self.subTest(enabled=enabled),patch.object(probe.Peer,'__init__',init), \
                 patch.object(probe,'load_bindings',return_value={}), \
                 patch.object(probe,'load_configuration',return_value={'boot':{'points':{'boot4':0x40000000}}}), \
                 patch.object(probe,'capture_bindings',return_value={}), \
                 patch.object(probe,'observation_points',return_value={}):
                peer=probe.CapturePeer(g,probe.TraceJournal('synthetic'))
                self.assertEqual(peer.trace_points.get(0x41172258),
                                 'resource_timing_loaded' if enabled else None)
                self.assertEqual(peer.trace_points.get(0x41170d60),
                                 'resource_processing_loaded' if enabled else None)


if __name__=='__main__': unittest.main()
