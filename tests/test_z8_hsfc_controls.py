# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded release-mode inputs; optional original-decoder execution is local.

Z8_MAIN enables the pinned private firmware test. It executes only its key
decoder in Unicorn, with ordinary memset supplied; no UI/capture acceptance.
"""
import os
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from z8_hsfc_controls import release_contact, mode_dial_sequence


class ReleaseControls(unittest.TestCase):
    def test_unheld_dial_cannot_accidentally_change_exposure(self):
        for held, delta in ((False, 1), (False, -1), (1, 0), (True, True),
                            (True, 2), (True, -2), (True, 0.0)):
            with self.assertRaises(ValueError):
                release_contact(held, delta)

    def test_sequence_is_bounded_and_releases_the_mode_key(self):
        for steps in (-6, -1, 0, 1, 6):
            rows = mode_dial_sequence(steps)
            self.assertEqual(len(rows), 2 + 2 * abs(steps))
            self.assertEqual(rows[-1]['expected'], struct.pack('<11I', 0x200, *([0] * 10)))
            self.assertTrue(all(row['half'] is False and row['full'] is False for row in rows))
        for steps in (True, 7, -7, '1'):
            with self.assertRaises(ValueError):
                mode_dial_sequence(steps)

    @unittest.skipUnless(os.environ.get('Z8_MAIN'), 'Private original MAIN not supplied')
    def test_actual_original_decoder_agrees_for_all_supported_contacts(self):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn import arm64_const as reg
        from z8_bringup import load_profile, validate_firmware
        path = Path(os.environ['Z8_MAIN'])
        validate_firmware(path, load_profile())
        data = path.read_bytes(); base = 0x40160000
        for held, delta in ((False, 0), (True, -1), (True, 0), (True, 1)):
            body, expected = release_contact(held, delta)
            with self.subTest(held=held, delta=delta):
                u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                u.mem_map(base, (len(data) + 4095) & ~4095); u.mem_write(base, data)
                u.mem_map(0xa00010000, 0x10000); u.mem_map(0xa03e17000, 0x2000)
                u.mem_write(0xa00011000, body)
                for register, value in ((reg.UC_ARM64_REG_X0, 0xa00011000),
                    (reg.UC_ARM64_REG_X1, len(body)), (reg.UC_ARM64_REG_X2, 0xa00012000),
                    (reg.UC_ARM64_REG_SP, 0xa00018000), (reg.UC_ARM64_REG_X30, 0xa00010000),
                    (reg.UC_ARM64_REG_CPACR_EL1, 3 << 20)):
                    u.reg_write(register, value)
                def memset(uc, address, size, unused):
                    if address == 0x4127ab40:
                        uc.mem_write(uc.reg_read(reg.UC_ARM64_REG_X0),
                            bytes((uc.reg_read(reg.UC_ARM64_REG_W1) & 255,)) * uc.reg_read(reg.UC_ARM64_REG_X2))
                        uc.reg_write(reg.UC_ARM64_REG_PC, uc.reg_read(reg.UC_ARM64_REG_X30))
                u.hook_add(UC_HOOK_CODE, memset)
                u.emu_start(0x41020844, 0xa00010000, count=5000)
                self.assertEqual(u.reg_read(reg.UC_ARM64_REG_PC), 0xa00010000)
                self.assertEqual(bytes(u.mem_read(0xa03e17ae8, 44)), expected)


if __name__ == '__main__':
    unittest.main()
