# SPDX-License-Identifier: GPL-2.0-or-later
"""Observed Z8 C3.11 selector/button inputs for owned simulation runs.

The original 41020844 decoder maps selector 0x57 to payload halfword +0x26
and record button 9 to SW1 bit0x2000 (native label ID_SW1_MOVIE).
Input delivery does not establish a successful recording or physical protocol.
"""


def input_body(mode,key=None):
    """Keep the physical selector state in every press and release frame."""
    if not isinstance(mode,str) or mode not in ('photo','video'):
        raise ValueError('Use explicit photo or video mode')
    keys={'record':9,'half':7,'menu':0x43}
    if key is not None and (not isinstance(key,str) or key not in keys):
        raise ValueError('Use record, half, menu or release')
    data=bytearray(124);data[0]=data[4]=1;data[0x57]=int(mode=='video')
    if key is not None:data[keys[key]]=1
    return bytes(data)
