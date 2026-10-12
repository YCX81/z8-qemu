# SPDX-License-Identifier: GPL-2.0-or-later
"""Firmware-free identity, lifecycle and evidence tests for Z8 bring-up."""
import hashlib
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import z8_bringup as bringup


class BringupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.code = bytes.fromhex('00000014') + bytes(60)
        self.firmware = self.root / 'main.bin'
        self.firmware.write_bytes(self.code)
        self.profile = dict(model='Nikon Z8', version='synthetic test only',
                            bytes=len(self.code), sha256=hashlib.sha256(self.code).hexdigest(),
                            load_address='0x40160000', entry_point='0x40160000')
        self.profile_path = self.root / 'profile.json'
        self.profile_path.write_text(json.dumps(self.profile))

    def test_exact_input_is_accepted_without_changing_instructions(self):
        result = bringup.validate_firmware(self.firmware, self.profile)
        self.assertEqual(result['sha256'], self.profile['sha256'])
        self.assertEqual(self.firmware.read_bytes(), self.code)

    def test_wrong_size_is_rejected(self):
        self.firmware.write_bytes(self.code[:-1])
        with self.assertRaisesRegex(ValueError, 'size'):
            bringup.validate_firmware(self.firmware, self.profile)

    def test_same_size_wrong_hash_is_rejected(self):
        self.firmware.write_bytes(self.code[:-1] + b'\x01')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            bringup.validate_firmware(self.firmware, self.profile)

    def test_public_identity_uses_full_digest_and_length_without_raw_bytes(self):
        profile = bringup.load_profile()
        self.assertNotIn('entry_prefix_hex', profile)
        self.assertEqual(profile['bytes'], 81807709)
        self.assertEqual(profile['sha256'],
                         '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853')

    def test_missing_identity_fields_never_disable_validation(self):
        for field in ('bytes', 'sha256'):
            profile = dict(self.profile)
            del profile[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                bringup.validate_firmware(self.firmware, profile)

    def test_other_load_or_entry_addresses_are_rejected(self):
        for field in ('load_address', 'entry_point'):
            with self.subTest(field=field):
                profile = dict(self.profile, **{field: '0x40160004'})
                with self.assertRaisesRegex(ValueError, '0x40160000'):
                    bringup.validate_firmware(self.firmware, profile)

    def test_runtime_bounds_include_nan_and_infinity(self):
        for seconds in (0, .09, 120.01, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                bringup.validate_runtime(seconds)
        for seconds in (.1, 120):
            self.assertEqual(bringup.validate_runtime(seconds), seconds)

    def test_existing_output_is_not_overwritten(self):
        output = self.root / 'output'
        output.mkdir()
        marker = output / 'result.json'
        marker.write_text('existing user evidence')
        with self.assertRaises(FileExistsError):
            bringup.create_output(output)
        self.assertEqual(marker.read_text(), 'existing user evidence')

    def test_fixed_profile_is_validated(self):
        with patch.object(bringup, 'PROFILE', self.profile_path):
            self.assertEqual(bringup.load_profile()['model'], 'Nikon Z8')
            for field, value in (('sha256', 'wrong'), ('sha256', None),
                                 ('bytes', -1), ('version', '')):
                malformed = dict(self.profile, **{field: value})
                self.profile_path.write_text(json.dumps(malformed))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    bringup.load_profile()

    def fake_qemu(self, stop=True, fail_on=None):
        # A protocol test double, not evidence of QEMU or camera operation.
        executable = self.root / 'fake-qemu'
        executable.write_text('#!' + sys.executable + '\n' + f'''import json,socket,sys
args=sys.argv
path=args[args.index('-qmp')+1].split(':',1)[1].split(',')[0]
server=socket.socket(socket.AF_UNIX);server.bind(path);server.listen(1)
connection,_=server.accept();stream=connection.makefile('rwb',buffering=0)
stream.write(json.dumps({{"QMP":{{"version":{{}},"capabilities":[]}}}}).encode()+b'\\n')
running=False
while True:
    raw=stream.readline()
    if not raw:break
    request=json.loads(raw);op=request['execute'];result={{}}
    print(json.dumps(request),flush=True)
    if op=={fail_on!r}:
        stream.write(b'{{"error":{{"class":"GenericError","desc":"test protocol failure"}}}}\\n')
        continue
    if op=='query-status':result={{'running':running,'status':'running' if running else 'debug'}}
    elif op=='query-cpus-fast':result=[{{'cpu-index':i}} for i in range(4)]
    elif op=='human-monitor-command':result='PC=0000000040160000 X00=0000000000000000'
    elif op=='cont':
        running={not stop!r}
        if not running:
            print('Z9_UNKNOWN cpu=0 pc=0x40160000 read addr=0xdead0000 size=4 value=0x0',file=sys.stderr,flush=True)
            stream.write(b'{{"event":"STOP"}}\\n')
    elif op=='stop':running=False
    stream.write(json.dumps({{'return':result}}).encode()+b'\\n')
    if op=='quit':break
''')
        executable.chmod(0o700)
        return executable

    def test_unknown_stop_preserves_evidence_and_owned_process_is_closed(self):
        binary = self.fake_qemu()
        output = self.root / 'evidence'
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, binary, output, .1)
        self.assertEqual(result['stop_reason'], 'unknown_mmio')
        self.assertTrue(result['owned_qemu_stopped'])
        self.assertFalse(result['menu_verified'])
        self.assertFalse(result['cpu_register_redirection'])
        self.assertEqual(result['firmware_patches'], [])
        self.assertEqual(result['machine_properties'], [])
        self.assertEqual(result['command'][result['command'].index('-M') + 1], 'nikon-z8-bringup')
        self.assertTrue(result['assumptions'])
        self.assertEqual(len(result['final_cpu_registers']), 4)
        self.assertIn('Z9_UNKNOWN', (output / 'stderr.log').read_text())
        self.assertEqual(self.firmware.read_bytes(), self.code)
        self.assertTrue(result['input_unchanged'])
        self.assertIn('STOP', [event['event'] for event in result['qmp_events']])
        self.assertEqual(json.loads((output / 'result.json').read_text())['stop_reason'], 'unknown_mmio')

    def test_explicit_bootstrap_fixture_is_the_only_added_machine_property(self):
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(),
                                 self.root / 'bootstrap', .1, bootstrap_fixture=True)
        self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on'])
        self.assertEqual(result['command'][result['command'].index('-M') + 1],
                         'nikon-z8-bringup,bootstrap-fixtures=on')
        assumptions = ' '.join(result['assumptions'])
        self.assertIn('0x0d150000', assumptions)
        self.assertIn('0x10000000', assumptions)
        self.assertIn('unmeasured', assumptions)
        self.assertIn('static', assumptions)
        self.assertFalse(result['physical_hardware_verified'])
        self.assertFalse(result['menu_verified'])
        self.assertEqual(result['stop_reason'], 'unknown_mmio')
        self.assertTrue(result['owned_qemu_stopped'])
        self.assertTrue(result['input_unchanged'])

    def test_runtime_limit_stops_only_the_owned_guest(self):
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(stop=False),
                                 self.root / 'limited', .1)
        self.assertEqual(result['stop_reason'], 'runtime_limit')
        self.assertTrue(result['owned_qemu_stopped'])
        self.assertFalse(result['final_status']['running'])

    def test_bus_window_fixture_requires_bootstrap_before_creating_output(self):
        output = self.root / 'missing-bootstrap'
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(bringup.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'requires.*bootstrap'):
                bringup.run(self.firmware, self.fake_qemu(), output, .1,
                            bus_window_fixture=True)
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_bus_window_fixture_reports_verified_layout_and_assumed_initial_values(self):
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(), self.root / 'bus-window',
                                 .1, bootstrap_fixture=True, bus_window_fixture=True)
        self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on', 'bus-window-fixture=on'])
        self.assertEqual(result['command'][result['command'].index('-M') + 1],
                         'nikon-z8-bringup,bootstrap-fixtures=on,bus-window-fixture=on')
        assumptions = ' '.join(result['assumptions'])
        for fact in ('0x44b7dcb4', '0x41185b94', '128 MiB', '512 MiB', 'bootloader'):
            self.assertIn(fact, assumptions)
        self.assertFalse(result['physical_hardware_verified'])
        self.assertFalse(result['menu_verified'])
        self.assertTrue(result['owned_qemu_stopped'])

    def test_serial_fixture_requires_both_dependencies_before_launch(self):
        for bootstrap, windows in ((False, False), (True, False), (False, True)):
            output = self.root / f'missing-serial-dependency-{bootstrap}-{windows}'
            with self.subTest(bootstrap=bootstrap, windows=windows), patch.object(bringup, 'PROFILE', self.profile_path), patch.object(bringup.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'requires'):
                    bringup.run(self.firmware, self.fake_qemu(), output, .1,
                                bootstrap_fixture=bootstrap, bus_window_fixture=windows,
                                serial_fixture=True)
                launch.assert_not_called()
                self.assertFalse(output.exists())

    def test_serial_fixture_enables_only_the_model_without_clocks_or_replies(self):
        output = self.root / 'serial-model'
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(), output, .1,
                                 bootstrap_fixture=True, bus_window_fixture=True, serial_fixture=True)
        properties = ['bootstrap-fixtures=on', 'bus-window-fixture=on', 'serial-fixture=on']
        self.assertEqual(result['machine_properties'], properties)
        self.assertEqual(result['command'][result['command'].index('-M') + 1],
                         ','.join(['nikon-z8-bringup', *properties]))
        assumptions = ' '.join(result['assumptions'])
        for fact in ('11 serial', '24 PDMA', 'no external replies', 'unmeasured'):
            self.assertIn(fact, assumptions)
        self.assertFalse(result['serial_host_clock_supplied'])
        self.assertFalse(result['serial_communication_verified'])
        requests = [json.loads(line) for line in (output / 'stdout.log').read_text().splitlines()]
        self.assertNotIn('qom-set', [request['execute'] for request in requests])
        self.assertEqual(result['virtual_rom_inputs'], [])
        self.assertFalse(result['menu_verified'])
        self.assertTrue(result['owned_qemu_stopped'])

    def test_nand_fixture_requires_bootstrap_before_launch(self):
        output = self.root / 'nand-no-bootstrap'
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(bringup.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ValueError, 'nand-fixture requires.*bootstrap'):
                bringup.run(self.firmware, self.fake_qemu(), output, .1, nand_fixture=True)
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_nand_fixture_reports_volatile_blank_media_and_reads_status(self):
        output = self.root / 'nand-model'
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(), output, .1,
                                 bootstrap_fixture=True, bus_window_fixture=True,
                                 serial_fixture=True, nand_fixture=True)
        properties = ['bootstrap-fixtures=on', 'bus-window-fixture=on', 'serial-fixture=on', 'nand-fixture=on']
        self.assertEqual(result['machine_properties'], properties)
        self.assertEqual(result['command'][result['command'].index('-M') + 1],
                         ','.join(['nikon-z8-bringup', *properties]))
        assumptions = ' '.join(result['assumptions'])
        for fact in ('2048', '64', 'volatile', '0xff', '0x89', 'no real flash dump'):
            self.assertIn(fact, assumptions)
        self.assertFalse(result['nand_flash_dump_loaded'])
        self.assertEqual(set(result['final_nand_status']),
                         {'command_status', 'interrupt_enable', 'error_pending', 'complete_pending',
                          'other_pending', 'error_mask', 'other_mask'})
        self.assertFalse(result['menu_verified'])
        self.assertTrue(result['owned_qemu_stopped'])
        self.assertTrue(result['input_unchanged'])

    def test_invalid_firmware_never_creates_output_or_starts_process(self):
        self.firmware.write_bytes(b'bad')
        output = self.root / 'invalid'
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(bringup.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                bringup.run(self.firmware, self.fake_qemu(), output, .1)
        launch.assert_not_called()
        self.assertFalse(output.exists())

    def test_qmp_failure_still_collects_registers_and_closes_owned_process(self):
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, self.fake_qemu(fail_on='cont'),
                                 self.root / 'protocol-error', .1)
        self.assertEqual(result['stop_reason'], 'runner_error')
        self.assertIn('test protocol failure', result['error'])
        self.assertEqual(len(result['final_cpu_registers']), 4)
        self.assertTrue(result['owned_qemu_stopped'])

    def test_early_process_exit_is_recorded_without_claiming_a_guest_stop(self):
        binary = self.root / 'exits-before-qmp'
        binary.write_text('#!' + sys.executable + '\nraise SystemExit(7)\n')
        binary.chmod(0o700)
        with patch.object(bringup, 'PROFILE', self.profile_path):
            result = bringup.run(self.firmware, binary, self.root / 'startup-error', .1)
        self.assertEqual(result['stop_reason'], 'runner_error')
        self.assertEqual(result['process_returncode'], 7)
        self.assertTrue(result['owned_qemu_stopped'])
        self.assertFalse(result['menu_verified'])

    def test_cli_reports_partial_bringup_without_menu_success(self):
        output = self.root / 'cli-run'
        command = ['z8_bringup.py', '--firmware', str(self.firmware),
                   '--qemu', str(self.fake_qemu()), '--output', str(output), '--seconds', '.1']
        captured = io.StringIO()
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command), contextlib.redirect_stdout(captured):
            bringup.main()
        self.assertFalse(json.loads(captured.getvalue())['menu_verified'])

    def test_cli_rejects_unbounded_runtime_and_identity_override(self):
        command = ['z8_bringup.py', '--firmware', str(self.firmware),
                   '--qemu', str(self.fake_qemu()), '--output', str(self.root / 'rejected')]
        for arguments in (['--seconds', 'inf'], ['--force'], ['--profile', str(self.profile_path)]):
            with self.subTest(arguments=arguments), patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command + arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bringup.main()

    def test_cli_bootstrap_fixture_remains_an_explicit_opt_in(self):
        for enabled in (False, True):
            output = self.root / ('cli-bootstrap' if enabled else 'cli-default')
            command = ['z8_bringup.py', '--firmware', str(self.firmware),
                       '--qemu', str(self.fake_qemu()), '--output', str(output), '--seconds', '.1']
            if enabled:
                command.append('--bootstrap-fixture')
            with self.subTest(enabled=enabled), patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command), contextlib.redirect_stdout(io.StringIO()):
                bringup.main()
            result = json.loads((output / 'result.json').read_text())
            self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on'] if enabled else [])

    def test_cli_bus_window_dependency_and_explicit_activation(self):
        output = self.root / 'cli-bus-window'
        command = ['z8_bringup.py', '--firmware', str(self.firmware), '--qemu', str(self.fake_qemu()),
                   '--output', str(output), '--seconds', '.1', '--bus-window-fixture']
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            bringup.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(output.exists())
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command + ['--bootstrap-fixture']), contextlib.redirect_stdout(io.StringIO()):
            bringup.main()
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on', 'bus-window-fixture=on'])

    def test_cli_serial_fixture_dependency_and_explicit_activation(self):
        output = self.root / 'cli-serial'
        command = ['z8_bringup.py', '--firmware', str(self.firmware), '--qemu', str(self.fake_qemu()),
                   '--output', str(output), '--seconds', '.1', '--serial-fixture']
        for partial in ([], ['--bootstrap-fixture'], ['--bus-window-fixture']):
            with self.subTest(partial=partial), patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command + partial), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                bringup.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(output.exists())
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command + ['--bootstrap-fixture', '--bus-window-fixture']), contextlib.redirect_stdout(io.StringIO()):
            bringup.main()
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on', 'bus-window-fixture=on', 'serial-fixture=on'])

    def test_cli_nand_needs_only_its_declared_bootstrap_dependency(self):
        output = self.root / 'cli-nand'
        command = ['z8_bringup.py', '--firmware', str(self.firmware), '--qemu', str(self.fake_qemu()),
                   '--output', str(output), '--seconds', '.1', '--nand-fixture']
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            bringup.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(output.exists())
        with patch.object(bringup, 'PROFILE', self.profile_path), patch.object(sys, 'argv', command + ['--bootstrap-fixture']), contextlib.redirect_stdout(io.StringIO()):
            bringup.main()
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['machine_properties'], ['bootstrap-fixtures=on', 'nand-fixture=on'])


if __name__ == '__main__':
    unittest.main()
