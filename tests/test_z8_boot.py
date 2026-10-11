# SPDX-License-Identifier: GPL-2.0-or-later
"""Version gating and native-stage accounting without running camera code."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import receive_mcu_tx
import z8_boot
from mcu_frames import frames


PROFILE = json.loads(z8_boot.PROFILE.read_text())


def checked_guest():
    guest = SimpleNamespace(data={'checks': []}, rsp=Mock(), log=lambda: '',
                            read_bytes=Mock(return_value=bytes(8)), qmp=Mock())

    def check(name, passed):
        guest.data['checks'].append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)

    guest.check = check
    return guest


class BootContract(unittest.TestCase):
    def test_wrong_version_rejected_before_any_guest_action(self):
        with tempfile.TemporaryDirectory() as folder:
            firmware = Path(folder) / 'wrong.bin'
            firmware.write_bytes(b'wrong original version')
            guest = checked_guest()
            guest.firmware = firmware
            with self.assertRaisesRegex(ValueError, 'pinned original'):
                z8_boot.boot(guest, progress=lambda _: None)
            guest.rsp.assert_not_called()
            self.assertNotIn('z8_boot', guest.data)

    def test_identity_gate_compares_content_not_only_length(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            firmware = root / 'authored.bin'
            firmware.write_bytes(b'aaaa')
            profile = dict(PROFILE, main_bytes=4,
                           main_sha256=hashlib.sha256(b'bbbb').hexdigest())
            path = root / 'profile.json'
            path.write_text(json.dumps(profile))
            guest = SimpleNamespace(firmware=firmware)
            with self.assertRaises(ValueError):
                z8_boot.load_configuration(guest, path)
            firmware.write_bytes(b'bbbb')
            self.assertEqual(z8_boot.load_configuration(guest, path)['bindings'], PROFILE['bindings'])

    def run_boot(self, wrong_event=False, strict_after_ready=False, wrong_rx_handle=False,
                 final_rx_state=0, event_address=0xa00001000):
        guest = checked_guest()
        points = PROFILE['boot']['points']
        guest.ram_mib = 1024
        guest.rd = Mock(side_effect=lambda address: final_rx_state if address == 0xa02e804f8 else 0)

        def read_bytes(address, size):
            values = {0xa02e80520: 0 if wrong_rx_handle else 0xa02809148,
                      0xa02809168: 0xa0280c1e0, 0xa02e804f0: 0x41010000}
            if address == 0xa00001000:
                return struct.pack('<3I', 3, 1, 7)
            return struct.pack('<Q', values.get(address, 0))

        guest.read_bytes.side_effect = read_bytes

        def stop(_guest, pc):
            if pc == points['rx_start_ready']:
                if strict_after_ready:
                    guest.log = lambda: 'Z9_UNKNOWN cpu=0 pc=0x1 addr=0x2'
                return 0, {'X19': 1}
            if pc == points['power_status']:
                return 0, {'X01': 0x03060002 if wrong_event else 0x03040002}
            if pc == points['system_power_event']:
                return 1, {'X00': 0x05020101, 'X01': event_address}
            return 0, {'X01': 0x1000}

        wire = frames(bytes([1]) + bytes(15), family=13)[0]
        with patch.object(z8_boot, 'load_configuration', return_value=PROFILE), \
                patch.object(z8_boot, 'settle') as settle, \
                patch.object(z8_boot, 'deliver') as deliver, \
                patch.object(z8_boot, 'receive', return_value=wire) as receive, \
                patch.object(z8_boot, 'at', side_effect=stop), \
                patch.object(z8_boot, 'observe') as observe:
            error = None
            try:
                z8_boot.boot(guest, progress=lambda _: None)
            except (AssertionError, RuntimeError) as exc:
                error = exc
            self.assertNotIn(20, [call.args[1] for call in settle.call_args_list])
        return guest, error, deliver, receive, observe

    def test_native_stages_require_z8_event_and_explicit_bindings(self):
        guest, error, deliver, receive, observe = self.run_boot()
        self.assertIsNone(error)
        self.assertTrue(guest.data['z8_boot']['complete'])
        self.assertFalse(guest.data['z8_boot']['menu_verified'])
        self.assertFalse(guest.data['z8_boot']['liveview_verified'])
        self.assertEqual(len(guest.data['z8_boot']['stages']), 8)
        self.assertEqual(guest.data['z8_boot']['system_power_event']['payload_words'], [3, 1, 7])
        self.assertEqual(guest.data['z8_boot']['stages'][-1], 'capture_composition_rx_retired')
        self.assertEqual(guest.data['z8_boot']['rx_start']['gic_enable_word'], '0x0')
        for call in deliver.call_args_list + receive.call_args_list:
            self.assertEqual(call.kwargs['bindings'], PROFILE['bindings'])
        self.assertEqual([c.args[1] for c in observe.call_args_list], [
            PROFILE['boot']['observe_after_lens'], PROFILE['boot']['observe_after_half_key']])
        self.assertEqual(deliver.call_args_list[0].args[1][8 + 0x1c], 2)
        self.assertEqual(deliver.call_args_list[-2].args[1][8 + 7], 1)

    def test_old_model_event_does_not_advance_boot_stage(self):
        guest, error, deliver, receive, _ = self.run_boot(wrong_event=True)
        self.assertIsInstance(error, AssertionError)
        self.assertFalse(guest.data['z8_boot']['complete'])
        self.assertEqual(guest.data['z8_boot']['stages'], ['rx_task_irq_initialized'])
        self.assertEqual(deliver.call_count, 1)
        receive.assert_not_called()

    def test_strict_fault_prevents_supplying_first_frame(self):
        guest, error, deliver, receive, _ = self.run_boot(strict_after_ready=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(guest.data['z8_boot']['stages'], [])
        deliver.assert_not_called()
        receive.assert_not_called()

    def test_wrong_native_gpio_handle_prevents_frame_injection(self):
        guest, error, deliver, receive, _ = self.run_boot(wrong_rx_handle=True)
        self.assertIsInstance(error, AssertionError)
        self.assertEqual(guest.data['z8_boot']['stages'], [])
        deliver.assert_not_called()
        receive.assert_not_called()

    def test_busy_final_rx_state_prevents_continuous_peer_handoff(self):
        guest, error, _, _, _ = self.run_boot(final_rx_state=2)
        self.assertIsInstance(error, AssertionError)
        self.assertFalse(guest.data['z8_boot']['complete'])
        self.assertEqual(guest.data['z8_boot']['stages'][-1], 'capture_composition_transport')

    def test_event_payload_outside_guest_ram_is_recorded_without_read(self):
        guest, error, _, _, _ = self.run_boot(event_address=0x0d15100c)
        self.assertIsNone(error)
        self.assertIn('payload_not_read', guest.data['z8_boot']['system_power_event'])
        self.assertNotIn(0x0d15100c, [call.args[0] for call in guest.read_bytes.call_args_list])

    def test_observe_strict_fault_removes_all_owned_breakpoints(self):
        guest = checked_guest()
        guest.log = lambda: 'Z9_UNKNOWN cpu=1'
        with self.assertRaisesRegex(RuntimeError, 'Unmodeled'):
            z8_boot.observe(guest, [0x1000, 0x2000])
        guest.qmp.command.assert_not_called()
        commands = {call.args[0] for call in guest.rsp.call_args_list}
        self.assertEqual(commands, {'Z1,1000,4', 'Z1,2000,4', 'z1,1000,4', 'z1,2000,4'})


class TransmitBindings(unittest.TestCase):
    def test_incomplete_bindings_rejected_before_hardware_access(self):
        guest = checked_guest()
        with self.assertRaises(ValueError):
            receive_mcu_tx.receive(guest, prefix='bad-', bindings={})
        guest.qmp.command.assert_not_called()

    def test_original_and_z8_paths_validate_same_complete_wire(self):
        old = dict(gpio_tx_isr=0x40c160d8, tx_request=0x40c93c48,
                   serial_prefill=0x40c1498c, serial_drain=0x40c149d0,
                   tx_return=0x40c93c4c, tx_ack_read=0x40c93d34, tx_finished=0x40c93d9c)
        wire = frames(bytes([8, 0, 0, 0, 8]))[0]
        for bindings, points in ((None, old), (PROFILE['bindings'], PROFILE['bindings'])):
            with self.subTest(bindings='Z9 default' if bindings is None else 'Z8'), tempfile.TemporaryDirectory() as folder:
                guest = checked_guest()
                guest.out = Path(folder)
                guest.rd = Mock(return_value=0)
                guest.wr = Mock()
                guest.qt_command = Mock()
                chunks = iter((wire[:128].hex(), wire[128:].hex()))

                def command(name, **kwargs):
                    if name == 'qom-list':
                        if kwargs['path'] == '/machine/unattached':
                            return [{'name': 'gic', 'type': 'child<arm-gicv3>'}]
                        return [{'name': 'unnamed-gpio-in[25]'}]
                    if name == 'qom-get' and kwargs['property'] == 'serial-state':
                        return json.dumps([dict(pio_active=True, tx_queued=64, read=64)])
                    if name == 'qom-get' and kwargs['property'] == 'serial-last-tx':
                        return next(chunks)

                guest.qmp.command.side_effect = command

                def stop(_guest, pc):
                    return 0, {'X00': int(pc == points['tx_ack_read']), 'X01': 0x3000}

                def save(_guest, label, _address, size):
                    return struct.pack('<QQ', 0x1000, 0x2000) + bytes(16) if size == 0x20 else wire

                with patch.object(receive_mcu_tx, 'at', side_effect=stop) as at, \
                        patch.object(receive_mcu_tx, 'at_channel', return_value=(0, {})) as at_channel, \
                        patch.object(receive_mcu_tx, 'save', side_effect=save):
                    self.assertEqual(receive_mcu_tx.receive(guest, prefix='tx-', bindings=bindings), wire)
                self.assertTrue(guest.data['frame_outputs'][0]['complete'])
                self.assertEqual([call.args[1] for call in at.call_args_list], [
                    points[n] for n in ('gpio_tx_isr', 'tx_request', 'tx_return',
                                       'gpio_tx_isr', 'tx_ack_read', 'tx_finished')])
                self.assertEqual([call.args[1] for call in at_channel.call_args_list], [
                    points['serial_prefill'], points['serial_drain']])
                for call in at_channel.call_args_list:
                    self.assertIs(call.kwargs['bindings'], bindings)


if __name__ == '__main__':
    unittest.main()
