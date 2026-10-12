# SPDX-License-Identifier: GPL-2.0-or-later
"""Original release-mode button/dial input bindings; mode selection not assumed.

C3.11 decoder 41020844 maps body10 to SW1 bit27 and signed body0b to word4.
Only the original UI can establish which release mode a dial step selected.
"""
import struct


def release_contact(held, delta=0):
    if (type(held) is not bool or type(delta) is not int or delta not in (-1, 0, 1)
            or (delta and not held)):
        raise ValueError('Use a Boolean release-mode hold and one held dial step')
    body = bytearray(124)
    body[0] = body[4] = 1
    body[0x10] = int(held)
    body[0x0b] = delta & 255
    expected = struct.pack('<11I', 0x200, 0x08000000 if held else 0,
                           0, 0, delta & 0xffffffff, *([0] * 6))
    return bytes(body), expected


def mode_dial_sequence(steps):
    if type(steps) is not int or not -6 <= steps <= 6:
        raise ValueError('Release-mode navigation requires at most six signed steps')
    contacts = [(True, 0, 'release-mode-hold')]
    for _ in range(abs(steps)):
        contacts += [(True, 1 if steps > 0 else -1, 'release-mode-dial'),
                     (True, 0, 'release-mode-neutral')]
    contacts.append((False, 0, 'release-mode-release'))
    result = []
    for held, delta, label in contacts:
        body, expected = release_contact(held, delta)
        result.append(dict(label=label, body=body, expected=expected,
                           gap=400_000_000 if held else 2_000_000_000,
                           half=False, full=False))
    return result
